"""Agent 前置层 + 散装几何全链测试（L1 快查 / L2 Mock 问答 / L3 组装 / mjcf 回退）。

覆盖：散装四件（关系树+STL+位姿+关节）→ Agent 批处理 → 标准 AML（XSD+⓪回读）、
STL→DAE 单位换算（mm→m）、L1 硬错误拒绝、mujoco 散装回退闭环。
运行：python tools/tests/test_aml_agent_configurator.py 或 pytest。
"""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
TOOLS = os.path.join(REPO, "tools")
sys.path.insert(0, TOOLS)
sys.path.insert(0, REPO)

PARTS = os.path.join(TOOLS, "tests", "fixtures", "parts")
XSD = os.path.join(REPO, "AML_RobotCell_en_public", "AML_RobotCell_en",
                   "CAEX_ClassModel_V2.15.xsd")
AGENT = os.path.join(TOOLS, "aml_agent_configurator.py")


def _run_agent(out, extra=()):
    return subprocess.run([sys.executable, AGENT, "--parts-dir", PARTS,
                           "--io", os.path.join(PARTS, "io.json"),
                           "-o", out, "--mock", "--batch", *extra],
                          capture_output=True, text=True)


def test_parts_full_pipeline():
    out = os.path.join(tempfile.mkdtemp(prefix="agent_a_"), "std.aml")
    r = _run_agent(out, ("--keep-work", os.path.dirname(out) + os.sep + "work"))
    assert r.returncode == 0, r.stdout[-800:] + r.stderr[-500:]
    assert os.path.isfile(out)

    # XSD（散装产物过包内 2.15 XSD）
    import xmlschema
    xmlschema.XMLSchema(XSD).validate(out)

    # ⓪ 回读逐字一致
    from src.agent.aml_parser import parse_aml, build_io_list
    model, problems = parse_aml(out)
    io_rt, pending = build_io_list(model)
    src = {it["name"]: it for it in json.load(
        open(os.path.join(PARTS, "io.json"), encoding="utf-8"))["io_list"]}
    assert len(io_rt) == len(src) == 14 and not pending
    for it in io_rt:
        s = src[it["name"]]
        assert (it["dir"], it["type"], it.get("address")) == (s["dir"], s["type"], s["address"])

    # 结构：PLC 自动创建 / 6 关节属性 / 5 DAE 引用（link6 无几何）
    import xml.etree.ElementTree as ET
    root = ET.parse(out).getroot()
    assert any(e.get("Name") == "PLC_Main" for e in root.iter("InternalElement"))
    kin = [a.get("Name") for a in root.iter("Attribute")
           if (a.get("Name") or "").startswith("Kinematics:")]
    assert len(kin) == 6
    dae = [a for a in root.iter() if a.get("Name") == "refURI"]
    assert len(dae) == 5


def test_unit_conversion_mm_to_m():
    out = os.path.join(tempfile.mkdtemp(prefix="agent_b_"), "std.aml")
    r = _run_agent(out, ("--keep-work", os.path.dirname(out) + os.sep + "work"))
    assert r.returncode == 0
    import trimesh
    assets = os.path.join(os.path.dirname(out), "assets")
    m = trimesh.load(os.path.join(assets, "rb_link1.dae"), force="mesh")
    assert abs(m.bounding_box.extents[0] - 0.12) < 1e-3      # 120mm → 0.12m


def test_l1_rejects_unknown_component():
    bad = json.load(open(os.path.join(PARTS, "io.json"), encoding="utf-8"))
    bad["io_list"][0]["component_id"] = "NOT_IN_TREE"
    d = tempfile.mkdtemp(prefix="agent_c_")
    p = os.path.join(d, "io_bad.json")
    json.dump(bad, open(p, "w", encoding="utf-8"))
    r = subprocess.run([sys.executable, AGENT, "--parts-dir", PARTS, "--io", p,
                        "-o", os.path.join(d, "x.aml"), "--mock", "--batch"],
                       capture_output=True, text=True)
    assert r.returncode != 0
    assert "不在关系树" in r.stdout or "component_id" in r.stdout


def test_mjcf_parts_fallback_closed_loop():
    try:
        import mujoco  # noqa: F401
        import trimesh  # noqa: F401
    except ImportError:
        print("SKIP：缺 mujoco/trimesh")
        return
    out = os.path.join(tempfile.mkdtemp(prefix="agent_d_"), "std.aml")
    r = _run_agent(out, ("--keep-work", os.path.dirname(out) + os.sep + "work"))
    assert r.returncode == 0
    import aml_to_mjcf as m2j
    root_dir = os.path.dirname(out)
    mjcf, axes = m2j.build_mjcf(m2j.extract_io(out), root_dir,
                                aml_joints=m2j.extract_aml_joints(out))
    assert len(axes) == 6
    assert axes[0]["axis"] == [0.0, 0.0, -1.0]               # 取自 AML 关节属性
    import xml.etree.ElementTree as ET
    xp = os.path.join(out_dir := os.path.dirname(out), "m.mjcf")
    ET.ElementTree(mjcf).write(xp, encoding="utf-8", xml_declaration=True)
    rep = m2j.smoke(xp, m2j.extract_io(out), root_dir, os.path.join(out_dir, "smoke.json"))
    assert rep["ok"] and rep["actuator_names"][0] == "rb_j1_target"
    assert rep["closed_loop_j1"]["converged"]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS：{t.__name__}")
    print(f"PASS：{len(tests)} 项 Agent 前置层断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())


def test_minimal_subset_two_stl_only():
    """任意子集容忍：只给 2 个 STL + 3 条 io.json（无 tree/poses/joints）→ 全链出标准 AML。"""
    import trimesh
    d = tempfile.mkdtemp(prefix="agent_e_")
    trimesh.creation.box((0.2, 0.2, 0.1)).export(os.path.join(d, "bracket.stl"))
    trimesh.creation.cylinder(0.05, 0.3).export(os.path.join(d, "column.stl"))
    json.dump({"io_list": [
        {"name": "col_cmd", "role": "target", "dir": "output", "type": "INT",
         "address": "%QW1", "deg_per_count": 0.05, "component_id": "column",
         "source_file": "", "source_id": "col_joint"},
        {"name": "bracket_present", "role": "actual", "dir": "input", "type": "BOOL",
         "address": "%QX0.0", "component_id": "bracket"}]},
        open(os.path.join(d, "io.json"), "w", encoding="utf-8"), ensure_ascii=False)
    out = os.path.join(d, "std.aml")
    r = subprocess.run([sys.executable, AGENT, "--parts-dir", d,
                        "--io", os.path.join(d, "io.json"), "-o", out,
                        "--mock", "--batch", "--keep-work", os.path.join(d, "work")],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-600:]
    assert "2/2 逐字一致=True" in r.stdout
    # 单位强制问答：兜底生成的几何单位为 "?"，出题必须问（野生 STL 的 60 单位是 mm 不是 m）
    sup = json.load(open(os.path.join(d, "work", "supplement.json"), encoding="utf-8"))
    unit_qs = [a for a in sup["answers"].values() if "geometry_unit" in (a.get("apply") or "")]
    assert unit_qs, "兜底几何单位未知必须出题"
    assert all(a["answer"] in ("mm", "m") for a in unit_qs)
    import xml.etree.ElementTree as ET
    root = ET.parse(out).getroot()
    names = [e.get("Name") for e in root.iter("InternalElement")]
    assert "bracket" in names and "column" in names        # tree 自动推断
    import xmlschema
    xmlschema.XMLSchema(XSD).validate(out)
