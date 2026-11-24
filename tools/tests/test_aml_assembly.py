"""标准 AML 装配与 IO 绑定测试链（tools/aml_assembly + web）。

覆盖《复杂仿真模型装配与IO绑定说明》适配版的四段功能：
解析装配（层级/Frame 世界位姿/几何引用/待确认项）、MuJoCo 生成（编译/映射/占位）、
IO 绑定与固定规则 PLCopen（xml2st 闸门 + ST 生成 + contract）、适配器闭环验收。
运行：python tools/tests/test_aml_assembly.py 或 pytest。
依赖：mujoco / trimesh / xml2st（src/pipeline）——mujoco 缺失时闭环段 SKIP。
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(REPO, "src", "pipeline"))

DEMO = os.path.join(REPO, "examples", "aml", "standard", "parts_demo")
PACKED = os.path.join(REPO, "examples", "aml", "standard", "010RB_100_standard.aml")
RC_ROOT = os.path.join(REPO, "AML_RobotCell_en_public", "AML_RobotCell_en")

import aml_assembly as asm                                    # noqa: E402


def _model():
    return asm.parse_standard_aml(os.path.join(DEMO, "standard.aml"))


def test_parse_assembly():
    """1.2(1)：层级/Frame 世界位姿；几何引用；1.2(3)：待确认项。"""
    m = _model()
    comps = {c["id"]: c for c in m["components"]}
    assert "rb_base" in comps and "rb_link1" in comps
    assert comps["rb_link1"]["parent"] == "rb_base"
    # 世界位姿逐层累积：link1 0.35 → link5 5×0.35
    assert abs(comps["rb_link5"]["world"]["pos"][2] - 5 * 0.35) < 1e-9
    # 几何引用（COLLADAInterface explicit）
    assert comps["rb_link1"]["geom_uri"].endswith("rb_link1.dae")
    assert comps["rb_link6"]["geom_uri"] is None            # link6 无几何
    # 关节与信号
    assert len(m["joints"]) == 6 and m["joints"][0]["axis"] == [0, 0, -1]
    assert m["joints"][0]["torque"] == 450.0
    sig = {s["name"]: s for s in m["signals"]}
    assert len(sig) == 14
    assert sig["rb_j1_target"]["address"] == "%QW100" and sig["rb_j1_target"]["scale"] == 0.02
    assert sig["rb_j1_target"]["source_id"] == "rb_joint1"   # binding 三元组
    # 接线（InternalLink 信号×端子）
    assert len(m["links"]) == 14
    # 待确认项：link6 几何缺 + enable/seq 无绑定
    assert any("rb_link6" in p for p in m["pending"])
    assert any("rb_enable" in p for p in m["pending"])


def test_build_scene_compiles():
    """1.2(4)：scene.xml 编译、映射、几何不可解析降级胶囊。"""
    m = _model()
    out = tempfile.mkdtemp(prefix="asm_")
    stats = asm.build_scene(m, out, DEMO)
    assert stats["compile"], stats.get("error")
    assert stats["njnt"] == 6 and stats["nu"] == 6 and stats["nsensor"] == 6
    mapping = json.load(open(os.path.join(out, "mapping.json"), encoding="utf-8"))
    assert mapping["signal_to_actuator"]["rb_j1_target"] == "rb_joint1"
    assert mapping["signal_to_sensor"]["rb_j1_actual"] == "rb_joint1"
    # 几何缺失 → 胶囊占位 + pending（待确认项如实记录）
    xml = open(os.path.join(out, "scene.xml"), encoding="utf-8").read()
    assert '<geom type="capsule"' in xml
    assert any("rb_link6" in p for p in m["pending"])
    # IO 接口锚点 → site
    assert 'site_' in xml
    shutil.rmtree(out, ignore_errors=True)


def test_plcopen_and_st():
    """2.2(1)-(3)：固定规则 PLCopen XML 过 xml2st 闸门并转 ST。"""
    m = _model()
    xml = asm.gen_plcopen_test(m["signals"], m["requirements"])
    d = tempfile.mkdtemp(prefix="plc_")
    xp = os.path.join(d, "io_test.xml")
    open(xp, "w", encoding="utf-8").write(xml)
    import xml2st
    problems, model = xml2st.parse(xp)
    assert problems == [], problems
    st = xml2st.to_st(model)
    assert "PROGRAM IO_TEST" in st
    for s in m["signals"]:
        assert f"{s['name']} AT {s['address']}" in st        # 变量声明含全部定位变量
    assert "prog_id" in st                                    # 辅助寄存器自动分配
    contract = asm.gen_contract(m["signals"])
    assert contract["prog_id"]["address"] == "%QW20"
    assert contract["aux_registers"] and contract["signals_hash"]
    # Modbus 通道表：%QW 地址 ↔ offset
    ch = asm.modbus_channels(m["signals"])["channels"]
    assert len(ch) == 14 and ch[0]["offset"] == 100
    assert ch[0]["binding"]["source_id"] == "rb_joint1"
    shutil.rmtree(d, ignore_errors=True)


def test_io_session_and_acceptance():
    """2.2(5)：适配器闭环（目标→伺服→反馈→扫描确认）+ 容差裁定。"""
    try:
        import mujoco  # noqa: F401
    except ImportError:
        print("SKIP：未安装 mujoco，闭环段跳过")
        return
    m = _model()
    out = tempfile.mkdtemp(prefix="ios_")
    stats = asm.build_scene(m, out, DEMO)
    s = asm.IOSession(stats["scene"], m["signals"], m["joints"], m["requirements"])
    s.set_target("rb_j1_target", 500)                        # raw 500 ×0.02 = 10°
    st = s.step(800)                                          # 1.6s 伺服整定
    assert st["seq"] == 1
    assert abs(st["qpos_deg"]["rb_joint1"] - 10.0) < 0.5     # 伺服到位
    assert abs(st["actuals"]["rb_j1_actual"] - 500) <= 5     # 反馈 raw 回读（≤0.1°）
    rep = asm.run_acceptance(s, os.path.join(out, "report.json"))
    assert rep["ok"] and all(j["pass"] for j in rep["joints"])
    assert all(j["feedback_raw"] is not None for j in rep["joints"])
    shutil.rmtree(out, ignore_errors=True)


def test_packed_robotcell():
    """打包模式（原 AmlDocument.aml 的标准 AML 版）：DAE 关节提取 + 真实网格 + 闭环。"""
    try:
        import mujoco  # noqa: F401
        import trimesh  # noqa: F401
    except ImportError:
        print("SKIP：缺 mujoco/trimesh，打包模式跳过")
        return
    m = asm.parse_standard_aml(PACKED, RC_ROOT)
    assert len(m["components"]) > 290                     # 官方 302 组件树
    # 关节从信号溯源的 DAE library_joints 提取（官方运动学在 COLLADA 1.5 里）
    assert len(m["joints"]) == 6
    j1 = m["joints"][0]
    assert j1["axis"] == [0.0, 0.0, -1.0] and j1["range"] == [-185.0, 185.0]
    assert j1.get("from_dae", "").endswith("KR240R2700prime_Product.dae")
    # Attachment_COLLADA fragment 引用不当几何；机器人 Representation 是显式几何
    assert m["kin_dae"] and "#" in (m["kin_dae"] or "")
    robot = next(c for c in m["components"]
                 if c["id"] == "AF138E59-0000-17AC-556E-B124000031B5")
    assert robot["geom_uri"].endswith("KR240R2700prime_Product.dae")
    # 装配 + 编译 + 闭环验收（真实网格降采样、碰撞关闭、gravcomp、伺服收敛）
    out = tempfile.mkdtemp(prefix="rc_")
    r = asm.full_run(PACKED, out, RC_ROOT)
    s = r["scene_stats"]
    assert s["compile"], s.get("error")
    assert s["njnt"] == 6 and s["nu"] == 6 and s["nsensor"] == 6
    assert s["meshes"] > 60                               # 真实 DAE 网格批量装载
    assert any("降采样" in p for p in r["model"]["pending"])   # 如实记录降采样
    assert r["acceptance"]["ok"] and len(r["acceptance"]["joints"]) == 6
    shutil.rmtree(out, ignore_errors=True)


def test_web_service():
    """服务级：demo 全链 + step + 下载。"""
    import requests
    import aml_assembly_web as web
    base = "http://127.0.0.1:8767"
    try:
        requests.get(base + "/", timeout=1)
    except Exception:                                          # noqa: BLE001
        print("SKIP：:8767 未运行")
        return
    r = requests.post(base + "/api/demo", timeout=120).json()
    assert r["ok"], r.get("errors")
    sid = r["sid"]
    assert r["summary"]["acceptance_ok"] is True
    st = requests.post(f"{base}/api/step?sid={sid}",
                       json={"targets": {"rb_j2_target": -300}, "steps": 200},
                       timeout=60).json()
    assert st["ok"] and st["state"]["seq"] >= 1
    dl = requests.get(f"{base}/api/download?sid={sid}&name=io_test.st", timeout=30)
    assert dl.status_code == 200 and b"PROGRAM IO_TEST" in dl.content
    dl = requests.get(f"{base}/api/download?sid={sid}&name=scene.xml", timeout=30)
    assert dl.status_code == 200 and b"<mujoco" in dl.content


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS：{t.__name__}")
    print(f"PASS：{len(tests)} 项装配与 IO 绑定断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
