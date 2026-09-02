"""scenegen 回归测试：python scenegen/tests/test_scenegen.py（无 pytest 依赖）。"""

import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from pxr import Usd  # noqa: E402

from scenegen import build_usd, smoke  # noqa: E402
from scenegen.validate import validate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLE = os.path.normpath(os.path.join(HERE, "..", "examples", "conveyor_sort.json"))

# 与 out/gantry 场景一致的龙门三轴规格（回归"部件飘移/够不到纸面"缺陷）
GANTRY_SPEC = {
    "scene_id": "gantry_test_001",
    "spec_version": "1.1",
    "units": "m",
    "physics": {"gravity": [0, 0, -9.81], "physics_dt": 0.0167, "solver": "tgs"},
    "ground": {"size": [10, 10], "friction": 0.8},
    "assets": [
        {"id": "gantry_1", "type": "gantry_xyz",
         "pose": {"position": [-0.3, -0.2, 0.0], "rpy_deg": [0, 0, 0]},
         "params": {"travel_x": 0.6, "travel_y": 0.4, "travel_z": 0.2, "speed": 0.5}},
    ],
    "io_map": [
        {"plc_var": "AxisX_cmd", "dir": "output", "type": "float",
         "bind": {"asset": "gantry_1", "quantity": "x_cmd", "range": [0, 0.6]}},
        {"plc_var": "AxisY_cmd", "dir": "output", "type": "float",
         "bind": {"asset": "gantry_1", "quantity": "y_cmd", "range": [0, 0.4]}},
        {"plc_var": "AxisZ_cmd", "dir": "output", "type": "float",
         "bind": {"asset": "gantry_1", "quantity": "z_cmd", "range": [0, 0.2]}},
        {"plc_var": "AxisX_pos", "dir": "input", "type": "float",
         "bind": {"asset": "gantry_1", "quantity": "x_pos", "range": [0, 0.6]}},
        {"plc_var": "AxisY_pos", "dir": "input", "type": "float",
         "bind": {"asset": "gantry_1", "quantity": "y_pos", "range": [0, 0.4]}},
        {"plc_var": "AxisZ_pos", "dir": "input", "type": "float",
         "bind": {"asset": "gantry_1", "quantity": "z_pos", "range": [0, 0.2]}},
    ],
    "script": {"spawn_schedule": [], "perturbations": [],
               "termination": {"max_sim_time": 10.0, "early_stop": "none"}},
}


def load_example() -> dict:
    with open(EXAMPLE, encoding="utf-8") as f:
        return json.load(f)


def expect_error(spec: dict, substring: str) -> None:
    errors = validate(spec)
    assert any(substring in e for e in errors), f"应包含 {substring!r}，实际: {errors}"


def assert_joints_on_rigid_bodies(usd_path: str) -> None:
    """黄金规则：任何关节的两端都必须是刚体——PhysX 拒用非刚体关节，链条会散架。"""
    stage = Usd.Stage.Open(usd_path)
    joints = 0
    for prim in stage.Traverse():
        if prim.GetTypeName() != "PhysicsPrismaticJoint":
            continue
        joints += 1
        for end in ("body0", "body1"):
            targets = prim.GetRelationship(f"physics:{end}").GetTargets()
            assert targets, f"{prim.GetPath()} {end} 未连接"
            target = stage.GetPrimAtPath(targets[0])
            assert target.HasAPI("PhysicsRigidBodyAPI"), (
                f"{prim.GetPath()} {end} 指向非刚体 {targets[0]}")
    assert joints > 0, "场景中应有平移关节"


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="scenegen_test_")
    n = 0

    # ---- 校验器 ----
    spec = load_example()
    errs = validate(spec)
    assert errs == [], f"示例场景应通过校验: {errs}"
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][1]["type"] = "warp_drive"
    expect_error(bad, "未知组件类型")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][1]["params"]["stroke"] = 0
    expect_error(bad, "超出区间")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][1]["params"]["axis"] = "w"
    expect_error(bad, "不在枚举")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][0]["params"]["no_such"] = 1
    expect_error(bad, "未知参数")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"].append({**bad["assets"][4], "id": "box_b",
                          "pose": {"position": [0, 0, 0.62]}})   # 与 box_a 同位 → 穿模
    expect_error(bad, "布局穿模")
    n += 1

    bad = copy.deepcopy(spec)
    bad["io_map"][0]["bind"] = {"asset": "cyl_1", "quantity": "extend_cmd"}  # 输入绑了指令型 quantity
    expect_error(bad, "应绑定 direction=out")
    n += 1

    bad = copy.deepcopy(spec)
    bad["io_map"][1]["type"] = "float"                            # bool 指令绑 float
    expect_error(bad, "type=float 与 quantity extend_cmd(bool) 不匹配")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][2]["parent"] = "ghost"
    expect_error(bad, "未声明")
    n += 1

    # ---- 构建 + Modbus 分配 + 结构冒烟 ----
    spec = load_example()
    result = build_usd.build(spec, tmp)
    io_map = result["io_map"]
    by_var = {e["plc_var"]: e for e in io_map}
    assert by_var["PE1_detected"]["modbus"]["plc_addr"] == "%IW0"
    assert by_var["Cyl1_pos"]["modbus"]["plc_addr"] == "%IW1"
    assert by_var["Cyl1_pos"]["modbus"]["length"] == 2
    assert by_var["Cyl1_extend"]["modbus"]["plc_addr"] == "%QX0.0"
    assert by_var["Belt1_run"]["modbus"]["plc_addr"] == "%QX0.1"
    n += 1

    assert by_var["Cyl1_extend"]["usd_prim"] == "/World/cyl_1/joint"
    assert by_var["Belt1_run"]["usd_prim"] == "/World/belt_1"
    n += 1

    st = result["st_declaration"]
    assert "AT %QX0.0 : BOOL" in st and "AT %IW0 : WORD" in st
    n += 1

    summary = result["modbus_summary"]
    assert summary["plc_output_coils"] == 2
    assert summary["sensor_block_registers"] == 3               # 1(bool) + 2(real)
    assert summary["openplc_polling"]["length"] == 3
    n += 1

    issues = smoke.structural_check(result["scene_usd"], io_map)
    assert issues == [], f"结构冒烟应通过: {issues}"
    n += 1

    # 确定性：同 spec 重建，关键 schema 属性一致
    result2 = build_usd.build(spec, tmp)
    assert [e["modbus"] for e in result2["io_map"]] == [e["modbus"] for e in io_map]
    n += 1

    # ---- 龙门三轴：关节链完整性与 Z 轴语义（回归"部件飘移/够不到纸面"） ----
    spec = copy.deepcopy(GANTRY_SPEC)
    assert validate(spec) == [], f"gantry spec 应通过校验: {validate(spec)}"
    result = build_usd.build(spec, tmp)
    n += 1

    assert_joints_on_rigid_bodies(result["scene_usd"])          # 关节两端全是刚体
    n += 1
    issues = smoke.structural_check(result["scene_usd"], result["io_map"])
    assert issues == [], f"gantry 结构冒烟应通过: {issues}"
    n += 1

    stage = Usd.Stage.Open(result["scene_usd"])
    tz = 0.2
    # 固定端 base：kinematic 锚刚体（不可动、无限质量）
    base = stage.GetPrimAtPath("/World/gantry_1/base")
    assert base.HasAPI("PhysicsRigidBodyAPI"), "base 应为刚体锚点"
    assert base.GetAttribute("physics:kinematicEnabled").Get() is True, "base 应为 kinematic"
    n += 1

    # Z 轴：q=0 落笔（笔尖距台面 2mm）、q=tz 抬笔；开场驱动目标 = 抬笔位
    joint_z = stage.GetPrimAtPath("/World/gantry_1/joint_z")
    assert abs(joint_z.GetAttribute("limit:transZ:physics:low").Get() - 0.0) < 1e-6
    assert abs(joint_z.GetAttribute("limit:transZ:physics:high").Get() - tz) < 1e-6
    assert abs(joint_z.GetAttribute("drive:transZ:physics:targetPosition").Get() - tz) < 1e-6, \
        "开场应保持抬笔（target=travel_z）"
    pen_cz = 0.04 + 0.002 + 0.27 / 2.0
    z_pos = stage.GetPrimAtPath("/World/gantry_1/z_carriage").GetAttribute("xformOp:translate").Get()
    assert abs((z_pos[2] - 0.135) - 0.042) < 1e-6, "落笔位笔尖应距台面 2mm"
    n += 1

    # 显式三轴刚体齐全，io_map 绑定的关节 prim 存在且驱动可写
    for name in ("x_carriage", "y_carriage", "z_carriage"):
        assert stage.GetPrimAtPath(f"/World/gantry_1/{name}").HasAPI("PhysicsRigidBodyAPI"), name
    by_var = {e["plc_var"]: e for e in result["io_map"]}
    assert by_var["AxisX_cmd"]["usd_prim"] == "/World/gantry_1/joint_x"
    assert by_var["AxisZ_pos"]["usd_prim"] == "/World/gantry_1/joint_z"
    assert by_var["AxisX_cmd"]["modbus"]["plc_addr"] == "%QW0"
    assert by_var["AxisX_pos"]["modbus"]["server_register"] == 0
    n += 1

    # simio:posBody/posRest 位置回读来源齐全（运行时桥依赖，按 x/y/z 顺序）
    root_prim = stage.GetPrimAtPath("/World/gantry_1")
    bodies = root_prim.GetAttribute("simio:posBody").Get()
    rests = root_prim.GetAttribute("simio:posRest").Get()
    assert list(bodies) == ["/World/gantry_1/x_carriage", "/World/gantry_1/y_carriage",
                            "/World/gantry_1/z_carriage"]
    assert abs(rests[0] - (-0.3)) < 1e-6 and abs(rests[2] - pen_cz) < 1e-6
    n += 1

    print(f"PASS：{n} 组断言全部通过（产物目录 {tmp}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
