"""标准 AML 配置器测试链（tools/aml_configurator + aml_to_mjcf + aml_export）。

覆盖：审查规则负测试、缺省补充、组装确定性、全链自检（XSD+⓪回读）、
仿真侧 mujoco 编译闭环、PLC/通讯侧导出一致性。
运行：python tools/tests/test_aml_configurator.py 或 pytest。
依赖：xmlschema / mujoco（缺失项自动 SKIP，不影响其余）。
"""

import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "tools"))

BASE = os.path.join(REPO, "AML_RobotCell_en_public", "AML_RobotCell_en", "AmlDocument.aml")
IO = os.path.join(REPO, "examples", "robotcell", "010RB_100.io.json")
ADAPTER = os.path.join(REPO, "examples", "robotcell", "010RB_100.adapter.json")
MOTION = os.path.join(REPO, "examples", "robotcell", "010RB_100.motion.json")
REQ = os.path.join(REPO, "examples", "robotcell", "010RB_100.requirements.json")

import aml_configurator as cfg  # noqa: E402  (tools/ 在 sys.path 下则直接可导)


def _load_io():
    return json.load(open(IO, encoding="utf-8"))["io_list"]


def _base_tree():
    import xml.etree.ElementTree as ET
    return ET.parse(BASE)


def _audit(bad_io=None, adapter=None, motion=None, requirements=None):
    return cfg.audit(_base_tree().getroot(),
                     bad_io if bad_io is not None else _load_io(),
                     adapter, motion, requirements, BASE)


def test_audit_rejects():
    io = _load_io()
    bad = copy.deepcopy(io); bad[0]["component_id"] = "NOT_EXIST"
    errs, _ = _audit(bad_io=bad)
    assert any("不在基座 AML" in e for e in errs)

    bad = copy.deepcopy(io); bad[1]["address"] = bad[0]["address"]
    errs, _ = _audit(bad_io=bad)
    assert any("地址" in e and "冲突" in e for e in errs)

    bad = copy.deepcopy(io); bad[0]["address"] = "%QD0"
    errs, _ = _audit(bad_io=bad)
    assert any("%QD 禁用" in e for e in errs)

    bad = copy.deepcopy(io); bad[0]["dir"] = "sideways"
    errs, _ = _audit(bad_io=bad)
    assert any("不在枚举 input/output" in e for e in errs)

    bad = copy.deepcopy(io); bad[0]["type"] = "REAL"
    errs, _ = _audit(bad_io=bad)
    assert any("位宽集" in e for e in errs)

    bad = copy.deepcopy(io); del bad[0]["source_id"]
    errs, _ = _audit(bad_io=bad)
    assert any("source_file/source_id 必须成对" in e for e in errs)

    bad = copy.deepcopy(io); bad[0]["range"] = [-100, 65535]
    errs, _ = _audit(bad_io=bad)
    assert any("完整落入" in e for e in errs)

    bad = copy.deepcopy(io); bad[0]["name"] = bad[1]["name"]
    errs, _ = _audit(bad_io=bad)
    assert any("重复" in e for e in errs)

    errs, _ = _audit(adapter={"cycle_hz": -1})
    assert any("cycle_hz" in e for e in errs)


def test_audit_ok_and_normalize_defaults():
    errs, warns = _audit()
    assert errs == [], f"真实五件套应通过审查: {errs}"
    assert len(warns) >= 14          # 14 条无 range 警告 + 三件可选警告

    io2, ad2, mo2, req2, notes = cfg.normalize(_load_io(), None, None, None)
    assert ad2["cycle_hz"] == 30 and mo2["max_speed_deg_s"] == 30
    assert req2["format"].startswith("308sjk.simulation_requirements/")
    assert any("word 域" in n for n in notes)
    assert any("INT16 满域" in n for n in notes)
    j1 = [i for i in io2 if i["name"] == "rb100_j1_target"][0]
    assert j1["range"] == [-655, 655]          # 32767×0.02 取整


def test_full_pipeline_and_deterministic():
    """两次组装 byte 一致（确定性），且 verify 全绿（well_formed+XSD+⓪回读 14/14）。"""
    outs = []
    for i in range(2):
        out = os.path.join(tempfile.mkdtemp(prefix="aml_cfg_"), f"std{i}.aml")
        r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "aml_configurator.py"),
                            "--aml", BASE, "--io", IO, "--adapter", ADAPTER,
                            "--motion", MOTION, "--requirements", REQ,
                            "-o", out], capture_output=True, text=True)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "逐字一致=True" in r.stdout
        outs.append(out)
    b1, b2 = (open(o, "rb").read() for o in outs)
    assert b1 == b2, "同输入必须同输出（可 diff 的确定性产物）"

    report = {"roundtrip": {"ok": True}}
    v = cfg.verify(outs[0], _load_io(),
                   xsd_path=os.path.join(os.path.dirname(BASE), "CAEX_ClassModel_V2.15.xsd"))
    assert v["well_formed"]
    assert v["xsd"]["ok"]
    assert v["roundtrip"]["ok"] and v["roundtrip"]["io_readback"] == 14
    assert v["self_contained"]["ok"], v["self_contained"]["unresolved"]   # 打包模式同样自包含


def test_parts_mode_kin_self_contained_physical():
    """散装四件 → 标准 AML：COLLADA 1.5 kin DAE、内联类库自包含、物理/扭矩落产物。"""
    import xml.etree.ElementTree as ET
    parts_dir = os.path.join(HERE, "fixtures", "parts")
    xsd = os.path.join(os.path.dirname(BASE), "CAEX_ClassModel_V2.15.xsd")
    out_dir = tempfile.mkdtemp(prefix="aml_parts_")
    out = os.path.join(out_dir, "std.aml")
    r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "aml_configurator.py"),
                        "--parts-dir", parts_dir, "--io", os.path.join(parts_dir, "io.json"),
                        "-o", out, "--xsd", xsd], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "kin DAE" in r.stdout and "自包含类引用 过" in r.stdout

    io_list = json.load(open(os.path.join(parts_dir, "io.json"), encoding="utf-8"))["io_list"]
    v = cfg.verify(out, io_list, xsd_path=xsd,
                   kin={"rel": "assets/kinematics.dae", "model_id": "kinematics_model",
                        "joints": 6})
    assert v["xsd"]["ok"]
    assert v["roundtrip"]["ok"] and v["roundtrip"]["io_readback"] == 14
    assert v["self_contained"]["ok"], v["self_contained"]["unresolved"]
    assert v["kinematics"]["ok"] and v["kinematics"]["dae_joints"] == 6

    root = ET.parse(out).getroot()
    libs = [e.get("Name") for e in root if e.tag in ("InterfaceClassLib", "RoleClassLib")]
    assert "AutomationMLInterfaceClassLib" in libs and "AutomationMLBaseRoleClassLib" in libs
    wh = next(e for e in root.iter("WriterHeader"))
    assert wh.findtext("WriterName") == "308sjk aml_configurator"

    # kin DAE：COLLADA 1.5，6 关节，link 树覆盖全部组件，AML 侧 implicit 引用
    kroot = ET.parse(os.path.join(out_dir, "assets", "kinematics.dae")).getroot()
    assert kroot.get("version") == "1.5.0"
    local = lambda e: e.tag.split("}")[-1]  # noqa: E731
    assert sum(1 for e in kroot.iter() if local(e) == "joint") == 6
    km = next(e for e in kroot.iter() if local(e) == "kinematics_model")
    links = {e.get("name") for e in km.iter() if local(e) == "link"}
    assert links == {"rb_base"} | {f"rb_link{i}" for i in range(1, 7)}
    kin_if = next(e for e in root.iter("ExternalInterface") if e.get("Name") == "Kinematics")
    assert kin_if.find("Attribute[@Name='refType']/Value").text == "implicit"
    assert "#kinematics_model" in kin_if.find("Attribute[@Name='refURI']/Value").text

    # 物理/扭矩：AML 属性 + MJCF inertial/forcerange
    ph = next(a for a in root.iter("Attribute") if a.get("Name") == "Physical")
    vals = {x.get("Name"): x.findtext("Value") for x in ph.iter("Attribute")}
    assert vals["mass"] == "12.5" and vals["com"] == "0.0 0.0 0.15"
    j1 = next(a for a in root.iter("Attribute") if a.get("Name") == "Kinematics:rb_joint1")
    assert {x.get("Name"): x.findtext("Value") for x in j1.iter("Attribute")}["torque"] == "450"

    import aml_to_mjcf as m2j  # noqa: E402
    joints_aml = m2j.extract_aml_joints(out)
    assert joints_aml["rb_joint1"]["torque"] == 450.0
    assert joints_aml["rb_joint1"]["component"] == "rb_link1"
    phys = m2j.extract_physical(out)
    assert phys["rb_link1"]["mass"] == 12.5 and phys["rb_link1"]["com"] == [0.0, 0.0, 0.15]
    mjcf, _ = m2j.build_mjcf(m2j.extract_io(out), out_dir,
                             aml_joints=joints_aml, physical=phys)
    inert = mjcf.find("worldbody/body/inertial")
    assert inert is not None and inert.get("mass") == "12.5"
    assert inert.get("fullinertia") == "0.35 0.28 0.12 0.0 0.0 0.0"
    pos_act = mjcf.find("actuator/position")
    assert pos_act.get("forcerange") == "-450 450"


def test_mjcf_and_exports():
    out_dir = tempfile.mkdtemp(prefix="aml_down_")
    std = os.path.join(out_dir, "std.aml")
    r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "aml_configurator.py"),
                        "--aml", BASE, "--io", IO, "--adapter", ADAPTER,
                        "--motion", MOTION, "--requirements", REQ, "-o", std],
                       capture_output=True, text=True)
    assert r.returncode == 0

    # PLC 侧：ST 声明含全部 14 个变量名（≡ AML 信号名）
    st = os.path.join(out_dir, "io.st")
    r = subprocess.run([sys.executable, os.path.join(REPO, "tools", "aml_export.py"),
                        std, "--st", st, "--modbus", os.path.join(out_dir, "mb.json")],
                       capture_output=True, text=True)
    assert r.returncode == 0
    st_text = open(st, encoding="utf-8").read()
    for it in _load_io():
        assert f"{it['name']:<24s} AT {it['address']}" in st_text, it["name"]

    # 通讯侧：通道表 14 条、地址/换算齐
    mb = json.load(open(os.path.join(out_dir, "mb.json"), encoding="utf-8"))
    assert len(mb["channels"]) == 14
    ch = {c["plc_var"]: c for c in mb["channels"]}
    assert ch["rb100_j1_target"]["offset"] == 100 and ch["rb100_j1_target"]["raw_to_eng"] == 0.02
    assert ch["rb100_feedback_seq"]["offset"] == 116

    # 仿真侧：MJCF 组装 + mujoco 编译闭环（无 mujoco 则 SKIP）
    try:
        import mujoco  # noqa: F401
    except ImportError:
        print("SKIP：未安装 mujoco，仿真侧冒烟跳过")
        return
    import aml_to_mjcf as m2j  # noqa: E402
    mjcf, axes = m2j.build_mjcf(m2j.extract_io(std),
                                os.path.join(REPO, "AML_RobotCell_en_public", "AML_RobotCell_en"))
    assert len(axes) == 6 and all(a["axis"] for a in axes)
    assert axes[0]["source_id"].endswith("Joint_1") and axes[0]["axis"] == [0.0, 0.0, -1.0]
    rep = os.path.join(out_dir, "smoke.json")
    xml_path = os.path.join(out_dir, "m.mjcf")
    import xml.etree.ElementTree as ET
    ET.ElementTree(mjcf).write(xml_path, encoding="utf-8", xml_declaration=True)
    result = m2j.smoke(xml_path, m2j.extract_io(std), "", rep)
    assert result["ok"], result
    assert result["actuator_names"][0] == "rb100_j1_target"       # 名称统一进 MJCF
    assert result["sensor_names"][0] == "rb100_j1_actual"
    assert result["closed_loop_j1"]["converged"]


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS：{t.__name__}")
    print(f"PASS：{len(tests)} 项配置器链断言全部通过")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.join(REPO, "tools"))
    sys.exit(main())
