"""scenegen 回归测试：python scenegen/tests/test_scenegen.py（无 pytest 依赖）。

场景对齐（2026-09-07 负责人指令）：现役场景 = 运动控制 motion3axis（PLC 侧，双链路联调基准）
+ 三轴绘图仪（gantry_xyz，本侧仿真场景）。滚筒/传送带分拣线等示例已删除；
②b LLM 生成本体归 gc，本侧不带 agent。

"""

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
EXAMPLE = os.path.normpath(os.path.join(HERE, "..", "examples", "gantry_plotter.json"))

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
    """规范示例：三轴绘图仪（examples/gantry_plotter.json，与 out/gantry 同源）。"""
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

    # ---- 校验器（组件级规则用微型夹具覆盖，非场景） ----
    spec = load_example()
    errs = validate(spec)
    assert errs == [], f"三轴绘图仪示例应通过校验: {errs}"
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][0]["type"] = "warp_drive"
    expect_error(bad, "未知组件类型")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][0]["params"]["travel_x"] = 0          # 行程下界（开区间）
    expect_error(bad, "超出区间")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"].append({                              # 枚举校验：借用气缸组件的 axis 枚举
        "id": "cyl_probe", "type": "pneumatic_cylinder",
        "pose": {"position": [2.0, 2.0, 0]},
        "params": {"axis": "w", "stroke": 0.2}})
    expect_error(bad, "不在枚举")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][0]["params"]["no_such"] = 1
    expect_error(bad, "未知参数")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"].append({**bad["assets"][0], "id": "gantry_2"})   # 同位 → 穿模
    expect_error(bad, "布局穿模")
    n += 1

    bad = copy.deepcopy(spec)
    bad["io_map"][0]["dir"] = "input"                   # 指令量被声明为输入
    expect_error(bad, "应绑定 direction=out")
    n += 1

    bad = copy.deepcopy(spec)
    bad["io_map"][0]["type"] = "bool"                   # bool 指令绑 float 量
    expect_error(bad, "与 quantity x_cmd")
    n += 1

    bad = copy.deepcopy(spec)
    bad["assets"][0]["parent"] = "ghost"
    expect_error(bad, "未声明")
    n += 1

    # ---- 构建 + Modbus 分配 + 结构冒烟（三轴绘图仪） ----
    spec = copy.deepcopy(GANTRY_SPEC)
    result = build_usd.build(spec, tmp)
    io_map = result["io_map"]
    by_var = {e["plc_var"]: e for e in io_map}
    assert by_var["AxisX_cmd"]["modbus"]["plc_addr"] == "%QW0"
    assert by_var["AxisY_cmd"]["modbus"]["plc_addr"] == "%QW2"
    assert by_var["AxisZ_cmd"]["modbus"]["plc_addr"] == "%QW4"
    assert by_var["AxisX_pos"]["modbus"]["server_register"] == 0
    assert by_var["AxisX_pos"]["modbus"]["length"] == 2
    assert by_var["AxisX_cmd"]["usd_prim"] == "/World/gantry_1/joint_x"
    assert by_var["AxisZ_pos"]["usd_prim"] == "/World/gantry_1/joint_z"
    n += 1

    st = result["st_declaration"]
    assert "AT %QW0 : REAL" in st and "AT %IW0 : REAL" in st
    n += 1

    summary = result["modbus_summary"]
    assert summary["plc_output_registers"] == 6
    assert summary["sensor_block_registers"] == 6
    assert summary["openplc_polling"]["length"] == 6
    n += 1

    issues = smoke.structural_check(result["scene_usd"], io_map)
    assert issues == [], f"结构冒烟应通过: {issues}"
    n += 1

    # 确定性：同 spec 重建，关键 schema 属性一致
    result2 = build_usd.build(spec, tmp)
    assert [e["modbus"] for e in result2["io_map"]] == [e["modbus"] for e in io_map]
    n += 1

    # ---- 龙门三轴：关节链完整性与 Z 轴语义（回归"部件飘移/够不到纸面"） ----
    assert validate(copy.deepcopy(GANTRY_SPEC)) == []
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

    # Z 轴：q=0 落笔（笔尖距台面 2mm，即开场静置位）、q=tz 抬笔；
    # 所有驱动目标开场必须为 0（零初始误差）——实机教训：开场非零目标会力饱和弹射
    joint_z = stage.GetPrimAtPath("/World/gantry_1/joint_z")
    assert abs(joint_z.GetAttribute("limit:transZ:physics:low").Get() - 0.0) < 1e-6
    assert abs(joint_z.GetAttribute("limit:transZ:physics:high").Get() - tz) < 1e-6
    for j in ("joint_x", "joint_y", "joint_z"):
        jt = stage.GetPrimAtPath(f"/World/gantry_1/{j}")
        axis = j[-1].upper()
        assert abs(jt.GetAttribute(f"drive:trans{axis}:physics:targetPosition").Get() - 0.0) < 1e-6, \
            f"{j} 开场驱动目标必须为 0"
    pen_cz = 0.04 + 0.002 + 0.27 / 2.0
    z_pos = stage.GetPrimAtPath("/World/gantry_1/z_carriage").GetAttribute("xformOp:translate").Get()
    assert abs((z_pos[2] - 0.135) - 0.042) < 1e-6, "落笔位笔尖应距台面 2mm"
    assert stage.GetPrimAtPath("/World/gantry_1/z_carriage/pen").HasAPI("PhysicsCollisionAPI"), \
        "笔应有碰撞（故障时停在纸面而非穿透）"
    n += 1

    # 显式三轴刚体齐全，io_map 绑定的关节 prim 存在且驱动可写
    for name in ("x_carriage", "y_carriage", "z_carriage"):
        assert stage.GetPrimAtPath(f"/World/gantry_1/{name}").HasAPI("PhysicsRigidBodyAPI"), name
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
