# -*- coding: utf-8 -*-
"""
②b 场景描述生成器单测（scene_gen.py——确定性、产物自检、R5 全腿、降级路径）。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.aml_parser import parse_aml  # noqa: E402
from agent.consistency_check import consistency_check  # noqa: E402
from agent.scene_gen import (  # noqa: E402
    SceneSpecGenerator, _modbus_channel, validate_scene_outputs)

PLOTTER_AML = REPO / "examples" / "aml" / "plotter3axis_station.aml"
PLOTTER_SPEC = json.loads((REPO / "examples" / "specs" / "plotter3axis.spec.json").read_text(encoding="utf-8"))
PLOTTER_XML = REPO / "src" / "plc" / "plotter3axis.xml"


def plotter_model():
    model, problems = parse_aml(PLOTTER_AML)
    assert problems == []
    return model


class TestModbusChannel:
    def test_coil(self):
        assert _modbus_channel("%QX0.0") == {"area": "coils", "address": 0}
        assert _modbus_channel("%QX2.1") == {"area": "coils", "address": 17}

    def test_register(self):
        assert _modbus_channel("%QW13") == {"area": "holding_registers", "address": 13}

    def test_invalid(self):
        try:
            _modbus_channel("%IW3")
            assert False, "应拒绝 %I 区"
        except ValueError as exc:
            assert "%Q" in str(exc)


class TestGenerate:
    def test_plotter_full_leg_r5(self):
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        scene, io_map = out["scene"], out["io_map"]
        assert scene["scene_id"] == PLOTTER_SPEC["task_id"]
        ids = {a["id"] for a in scene["assets"]}
        assert {"x_axis", "y_axis", "z_axis", "plot_head", "pen", "panel"} <= ids
        z = next(a for a in scene["assets"] if a["id"] == "z_axis")
        assert z["params"]["stroke"] == [0, 10] and z["params"]["unit"] == "mm"
        assert len(io_map["mappings"]) == len(PLOTTER_SPEC["io_list"])
        ok, problems = consistency_check(PLOTTER_XML, PLOTTER_SPEC["io_list"], io_map)
        assert ok, problems
        assert not any(p.startswith("SKIP") for p in problems)  # R5 全腿激活，无 SKIP

    def test_addresses_come_from_device_model(self):
        """io_map 地址 = ⓪ 侧 AML 通道地址（%QX2.0 → 线圈 16 等）。"""
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        by_var = {m["plc_var"]: m for m in out["io_map"]["mappings"]}
        assert by_var["cmd_draw"]["io_channel"]["modbus"] == {"area": "coils", "address": 16}
        assert by_var["pen_down"]["io_channel"]["modbus"] == {"area": "coils", "address": 17}
        assert by_var["plot_done"]["io_channel"]["modbus"] == {"area": "coils", "address": 18}
        assert by_var["x_v"]["io_channel"]["plc_addr"] == "%QW13"

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


class TestValidateSceneOutputs:
    def base(self):
        out = SceneSpecGenerator().generate(PLOTTER_SPEC, plotter_model())
        return out["scene"], out["io_map"]

    def test_clean_passes(self):
        scene, io_map = self.base()
        assert validate_scene_outputs(scene, io_map, PLOTTER_SPEC["io_list"]) == []

    def test_dangling_prim(self):
        scene, io_map = self.base()
        io_map["mappings"][0]["bind"]["prim"] = "/World/ghost"
        assert any("V4" in p for p in validate_scene_outputs(scene, io_map, PLOTTER_SPEC["io_list"]))

    def test_missing_mapping(self):
        scene, io_map = self.base()
        io_map["mappings"] = io_map["mappings"][:-1]
        assert any("V3" in p for p in validate_scene_outputs(scene, io_map, PLOTTER_SPEC["io_list"]))

    def test_unknown_asset_type(self):
        scene, io_map = self.base()
        scene["assets"].append({"id": "ufo", "type": "anti_grav", "pose": {"position": [0, 0, 0]}})
        assert any("V1" in p for p in validate_scene_outputs(scene, io_map, PLOTTER_SPEC["io_list"]))
