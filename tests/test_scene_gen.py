# -*- coding: utf-8 -*-
"""
②b 场景描述生成器单测（scene_gen.py——契约 v1.1 驱动、gantry 路线、确定性、
产物自检、R5 腿、legacy 降级路径）。

契约权威：contract/components.v1.1.json（csk→gc 契约包）；参考形态
contract/scene.spec.example.json（绘图工位范本）与 contract/example1.json（龙门反向导出）。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.aml_parser import parse_aml  # noqa: E402
from agent.consistency_check import consistency_check  # noqa: E402
from agent.scene_gen import (  # noqa: E402
    CONTRACT_TYPES, CONTRACT_VERSION, SceneSpecGenerator,
    is_driver_channel, load_contract, validate_scene_outputs)

PLOTTER_AML = REPO / "examples" / "aml" / "plotter3axis_station.aml"
PLOTTER_SPEC = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json").read_text(encoding="utf-8"))
PLOTTER_XML = REPO / "src" / "plc" / "plotter3axis.xml"

SINGLE_AXIS_SPEC = {"task_id": "single_axis_demo", "io_list": [
    {"name": "x_fb", "dir": "input", "type": "INT", "range": [0, 100], "unit": "%"}]}


def plotter_model():
    model, problems = parse_aml(PLOTTER_AML)
    assert problems == []
    return model


class TestContractLoading:
    def test_registry_from_contract_package(self):
        """注册表运行时加载自 contract/（类型封闭集=契约 15 类型，非硬编码目录）。"""
        version, registry = load_contract()
        assert version == CONTRACT_VERSION == "1.1"
        assert "gantry_xyz" in registry and "linear_axis" in registry
        assert registry["gantry_xyz"]["quantities"] == {
            "x_cmd": ("in", "float"), "y_cmd": ("in", "float"), "z_cmd": ("in", "float"),
            "x_pos": ("out", "float"), "y_pos": ("out", "float"), "z_pos": ("out", "float")}
        assert "hmi_panel" in registry and not registry["hmi_panel"]["quantities"]

    def test_missing_contract_dir_raises(self, tmp_path):
        try:
            load_contract(tmp_path)
            assert False, "缺契约包必须报错"
        except FileNotFoundError as exc:
            assert "components.v" in str(exc)


class TestGenerate:
    def test_plotter_gantry_shape_and_r5(self):
        """三轴设备走 gantry_xyz 单资产路线（csk 组装器原生分支）。"""
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        scene = out["scene"]
        assert scene["scene_id"] == PLOTTER_SPEC["task_id"]
        assert scene["spec_version"] == CONTRACT_VERSION
        assert scene["io_map"] is out["io_map"]                # io_map 内嵌 scene.spec
        assert [(a["id"], a["type"]) for a in scene["assets"]] == [("gantry", "gantry_xyz")]
        g = scene["assets"][0]
        assert g["params"] == {"travel_x": 1.0, "travel_y": 1.0,
                               "travel_z": 0.011, "speed": 0.4}  # travel=stroke×scale；z 契约下界钳位
        assert g["pose"]["position"] == [0.0, 0.0, 0.0]          # 笔尖行程原点（扫掠区中心 0.5,0.5）
        ok, problems = consistency_check(PLOTTER_XML, PLOTTER_SPEC["io_list"], out["io_map"])
        assert ok, problems
        assert not any(p.startswith("SKIP") for p in problems)   # R5 腿激活，无 SKIP

    def test_io_map_fb_and_synth_cmd_channels(self):
        """fb→gantry.<axis>_pos；io_list 无位置指令变量时合成 <axis>_cmd 驱动通道。"""
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        entries = {e["plc_var"]: e for e in out["io_map"]}
        assert set(entries) == {"x_fb", "y_fb", "z_fb", "x_cmd", "y_cmd", "z_cmd"}
        assert entries["x_fb"]["bind"] == {"asset": "gantry", "quantity": "x_pos",
                                           "range": [0.0, 1.0]}
        assert entries["z_fb"]["bind"]["range"] == [0.0, 0.011]
        for axis in ("x", "y", "z"):
            cmd = entries["%s_cmd" % axis]
            assert cmd["dir"] == "output" and cmd["type"] == "float"
            assert cmd["bind"]["quantity"] == "%s_cmd" % axis   # 桥端按首字母推轴可直接消费
            assert is_driver_channel(cmd)                       # R5/V3 豁免形态
        for e in out["io_map"]:
            assert set(e) == {"plc_var", "dir", "type", "bind"}  # 无地址字段（地址归 csk build-mjcf）

    def test_real_cmd_channel_not_duplicated(self):
        """io_list 已有 <axis>_cmd 输出变量时按真实通道路由，不重复合成。"""
        spec = {"task_id": "cmd_real_demo", "io_list": [
            {"name": "x_cmd", "dir": "output", "type": "INT", "range": [0, 100], "unit": "%"},
            {"name": "x_fb", "dir": "input", "type": "INT", "range": [0, 100], "unit": "%"},
            {"name": "y_fb", "dir": "input", "type": "INT", "range": [0, 100], "unit": "%"},
            {"name": "z_fb", "dir": "input", "type": "INT", "range": [0, 10], "unit": "mm"},
        ]}
        out = SceneSpecGenerator().generate(spec, None)
        names = [e["plc_var"] for e in out["io_map"]]
        assert names.count("x_cmd") == 1
        assert set(names) == {"x_cmd", "x_fb", "y_fb", "z_fb", "y_cmd", "z_cmd"}  # y/z 仍合成

    def test_deterministic(self):
        model = plotter_model()
        a = SceneSpecGenerator().generate(PLOTTER_SPEC, model)
        b = SceneSpecGenerator().generate(PLOTTER_SPEC, model)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_degraded_path_without_device_model(self):
        """无 device_model：travel 从 io_list 的 <axis>_fb 量程推断；speed 取库缺省。"""
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, None)
        g = out["scene"]["assets"][0]
        assert g["params"]["travel_x"] == 1.0 and g["params"]["travel_z"] == 0.011
        assert g["params"]["speed"] == 0.5

    def test_partial_axes_fall_back_to_default_travel(self):
        """仅单轴信息时仍走 gantry 唯一路线，缺轴用库缺省行程补齐。"""
        spec = {"task_id": "single_axis_demo", "io_list": [
            {"name": "x_fb", "dir": "input", "type": "INT", "range": [0, 100], "unit": "%"}]}
        out = SceneSpecGenerator().generate(spec, None)
        g = out["scene"]["assets"][0]
        assert g["type"] == "gantry_xyz"
        assert g["params"]["travel_x"] == 1.0            # x 从 io_list 推断
        assert g["params"]["travel_y"] == 1.0 and g["params"]["travel_z"] == 0.011  # 缺省+钳位
        assert [e["plc_var"] for e in out["io_map"]] == ["x_fb", "x_cmd", "y_cmd", "z_cmd"]



class TestValidateSceneOutputs:
    def base(self):
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        return out["scene"], PLOTTER_SPEC["io_list"]

    def test_clean_passes(self):
        scene, io_list = self.base()
        assert validate_scene_outputs(scene, io_list) == []

    def test_dangling_asset(self):
        scene, io_list = self.base()
        scene["io_map"][0]["bind"]["asset"] = "ghost"
        assert any("V4" in p for p in validate_scene_outputs(scene, io_list))

    def test_quantity_direction_mismatch(self):
        scene, io_list = self.base()
        scene["io_map"][0]["bind"]["quantity"] = "x_cmd"  # input 通道绑了 in 量
        assert any("V4" in p and "direction" in p for p in validate_scene_outputs(scene, io_list))

    def test_routed_channel_dropped(self):
        scene, io_list = self.base()
        scene["io_map"] = scene["io_map"][:-1]            # 掉一条合成 cmd
        assert any("V3" in p and "未进 io_map" in p
                   for p in validate_scene_outputs(scene, io_list))

    def test_unknown_asset_type(self):
        scene, io_list = self.base()
        scene["assets"].append({"id": "ufo", "type": "anti_grav",
                                "pose": {"position": [0, 0, 0]}})
        assert any("V1" in p and "契约注册表" in p
                   for p in validate_scene_outputs(scene, io_list))

    def test_param_rule_violations(self):
        scene, io_list = self.base()
        g = scene["assets"][0]
        g["params"].update(travel_x=5.0, speed=0.0, mystery=1)  # 超上界 / 开下界 / 未知参数
        problems = validate_scene_outputs(scene, io_list)
        assert any("travel_x" in p and "契约区间" in p for p in problems)
        assert any("speed" in p and "契约区间" in p for p in problems)
        assert any("未知参数" in p and "mystery" in p for p in problems)

    def test_parent_not_declared(self):
        scene, io_list = self.base()
        scene["assets"].append({"id": "stow", "type": "tool_head",
                                "parent": "nowhere", "pose": {"position": [0, 0, 0]},
                                "params": {"carries": "pen"}})
        assert any("V1" in p and "parent" in p for p in validate_scene_outputs(scene, io_list))

    def test_synth_driver_channel_exempt_and_forgery_caught(self):
        """合成驱动通道豁免 ⊆ 检查；同名伪造（非豁免形态）仍报 V3。"""
        scene, io_list = self.base()
        scene["io_map"].append({"plc_var": "ghost_cmd", "dir": "output", "type": "float",
                                "bind": {"asset": "gantry", "quantity": "x_cmd",
                                         "range": [0.0, 1.0]}})
        problems = validate_scene_outputs(scene, io_list)
        assert any("V3" in p and "ghost_cmd" in p for p in problems)       # 伪造名不在豁免集
        assert not any("V3" in p and '"x_cmd"' in p for p in problems)     # 合成通道不报

    def test_contract_example_files_pass_self_check(self):
        """契约包自带范本（绘图工位/example1）在 ②b 自检语义下干净通过。"""
        for name in ("scene.spec.example.json", "example1.json"):
            spec = json.loads((REPO / "contract" / name).read_text(encoding="utf-8"))
            io_list = [{"name": e["plc_var"], "dir": e["dir"],
                        "type": {"float": "INT", "bool": "BOOL"}[e["type"]]}
                       for e in spec.get("io_map", [])]
            assert validate_scene_outputs(spec, io_list) == [], name
