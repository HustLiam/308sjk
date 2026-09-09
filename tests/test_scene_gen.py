# -*- coding: utf-8 -*-
"""
②b 场景描述生成器单测（scene_gen.py——契约 v1.1 驱动、确定性、产物自检、R5 腿、降级路径）。

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
    load_contract, validate_scene_outputs)

PLOTTER_AML = REPO / "examples" / "aml" / "plotter3axis_station.aml"
PLOTTER_SPEC = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json").read_text(encoding="utf-8"))
PLOTTER_XML = REPO / "src" / "plc" / "plotter3axis.xml"


def plotter_model():
    model, problems = parse_aml(PLOTTER_AML)
    assert problems == []
    return model


class TestContractLoading:
    def test_registry_from_contract_package(self):
        """注册表运行时加载自 contract/（类型封闭集=契约 15 类型，非硬编码目录）。"""
        version, registry = load_contract()
        assert version == CONTRACT_VERSION == "1.1"
        assert "linear_axis" in registry and "gantry_xyz" in registry
        assert registry["linear_axis"]["quantities"] == {
            "cmd": ("in", "float"), "pos": ("out", "float")}
        assert "hmi_panel" in registry and not registry["hmi_panel"]["quantities"]

    def test_missing_contract_dir_raises(self, tmp_path):
        try:
            load_contract(tmp_path)
            assert False, "缺契约包必须报错"
        except FileNotFoundError as exc:
            assert "components.v" in str(exc)


class TestGenerate:
    def test_plotter_contract_shape_and_r5(self):
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        scene = out["scene"]
        assert scene["scene_id"] == PLOTTER_SPEC["task_id"]
        assert scene["spec_version"] == CONTRACT_VERSION  # spec 版本随契约走
        assert scene["io_map"] is out["io_map"]           # io_map 内嵌 scene.spec
        ids = {a["id"] for a in scene["assets"]}
        assert {"x_axis", "y_axis", "z_axis", "plot_head", "pen", "panel"} <= ids
        z = next(a for a in scene["assets"] if a["id"] == "z_axis")
        assert z["params"]["stroke"] == [0.0, 10.0] and z["params"]["unit"] == "mm"
        assert z["params"]["scale_m_per_unit"] == 0.001
        assert None not in z["params"].values()           # null 参数会被契约闸门拒绝
        ok, problems = consistency_check(PLOTTER_XML, PLOTTER_SPEC["io_list"], out["io_map"])
        assert ok, problems
        assert not any(p.startswith("SKIP") for p in problems)  # R5 腿激活，无 SKIP

    def test_io_map_physical_channels_only(self):
        """io_map 只含可绑物理通道：fb→pos（SI range）；按钮/灯/NC/sp/sw/v 不进。"""
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        entries = {e["plc_var"]: e for e in out["io_map"]}
        assert set(entries) == {"x_fb", "y_fb", "z_fb"}
        assert entries["x_fb"] == {
            "plc_var": "x_fb", "dir": "input", "type": "float",
            "bind": {"asset": "x_axis", "quantity": "pos", "range": [0.0, 1.0]}}
        assert entries["z_fb"]["bind"]["range"] == [0.0, 0.01]  # 10mm × 0.001
        for e in out["io_map"]:
            assert set(e) == {"plc_var", "dir", "type", "bind"}  # 无地址字段（地址归 csk build-mjcf）

    def test_panel_buttons_lamps_from_io_list(self):
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        panel = next(a for a in out["scene"]["assets"] if a["type"] == "hmi_panel")
        assert panel["params"]["buttons"] == sorted(
            p["name"] for p in PLOTTER_SPEC["io_list"]
            if p["dir"] == "input" and p["type"] == "BOOL")
        assert panel["params"]["lamps"] == sorted(
            p["name"] for p in PLOTTER_SPEC["io_list"]
            if p["dir"] == "output" and p["type"] == "BOOL")

    def test_cmd_channel_routed_to_cmd_quantity(self):
        """<axis>_cmd（dir=output）路由到 cmd（direction=in）——范本指令通道形态。"""
        spec = {"task_id": "cmd_route_demo", "io_list": [
            {"name": "x_cmd", "dir": "output", "type": "INT", "range": [0, 100], "unit": "%"},
            {"name": "x_fb", "dir": "input", "type": "INT", "range": [0, 100], "unit": "%"},
        ]}
        out = SceneSpecGenerator().generate(spec, None)
        entries = {e["plc_var"]: e for e in out["io_map"]}
        assert entries["x_cmd"] == {
            "plc_var": "x_cmd", "dir": "output", "type": "float",
            "bind": {"asset": "x_axis", "quantity": "cmd", "range": [0.0, 1.0]}}

    def test_deterministic(self):
        model = plotter_model()
        a = SceneSpecGenerator().generate(PLOTTER_SPEC, model)
        b = SceneSpecGenerator().generate(PLOTTER_SPEC, model)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

    def test_degraded_path_without_device_model(self):
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, None)
        ids = {a["id"] for a in out["scene"]["assets"]}
        assert {"x_axis", "z_axis", "plot_head"} <= ids  # 轴资产从 io_list 推断
        z = next(a for a in out["scene"]["assets"] if a["id"] == "z_axis")
        assert z["params"]["stroke"] == [0, 10]           # 量程来自 <axis>_fb range
        assert "vmax" not in z["params"]                  # 缺省参数不落盘（取注册表默认）


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
        scene["io_map"][0]["bind"]["quantity"] = "cmd"  # input 通道绑了 in 量
        assert any("V4" in p and "direction" in p for p in validate_scene_outputs(scene, io_list))

    def test_routed_channel_dropped(self):
        scene, io_list = self.base()
        scene["io_map"] = scene["io_map"][:-1]
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
        x = next(a for a in scene["assets"] if a["id"] == "x_axis")
        x["params"].update(vmax=None, axis="w", mystery=1)
        problems = validate_scene_outputs(scene, io_list)
        assert any("vmax" in p and "数字" in p for p in problems)      # null 拒绝
        assert any("枚举" in p for p in problems)                      # 非法枚举
        assert any("未知参数" in p and "mystery" in p for p in problems)

    def test_parent_not_declared(self):
        scene, io_list = self.base()
        scene["assets"].append({"id": "stow", "type": "tool_head",
                                "parent": "nowhere", "pose": {"position": [0, 0, 0]},
                                "params": {"carries": "pen"}})
        assert any("V1" in p and "parent" in p for p in validate_scene_outputs(scene, io_list))

    def test_contract_example_files_pass_self_check(self):
        """契约包自带范本（绘图工位/example1）在 ②b 自检语义下干净通过。"""
        for name in ("scene.spec.example.json", "example1.json"):
            spec = json.loads((REPO / "contract" / name).read_text(encoding="utf-8"))
            io_list = [{"name": e["plc_var"], "dir": e["dir"],
                        "type": {"float": "INT", "bool": "BOOL"}[e["type"]]}
                       for e in spec.get("io_map", [])]
            assert validate_scene_outputs(spec, io_list) == [], name
