# -*- coding: utf-8 -*-
"""
轴对象展开器等价契约测试（生成方案 §6.3，RFC 2026-09-15 通过）。

基准 1（motion3axis）：AML 提取的 axis_objects 经确定性模板展开的 XML 与现行
已验收 motion3axis v4.0 **逐字段等价**——实测字节级一致（蕴含树等价，§6.3
"逐字段等价"取最强形态）；展开产物同时过 xml2st 静态闸门与一致性检查
（R2~R8 全项，含 R8 轴参数比对），证明映射链无损。模板/FB 库漂移（种子演进
模板未跟、或反之）在本测试变红。
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from agent.aml_parser import parse_aml  # noqa: E402
from agent.axis_expand import AxisExpandError, expand_project  # noqa: E402
from agent.consistency_check import consistency_check  # noqa: E402

SEED = REPO / "src" / "plc" / "motion3axis.xml"
AML = REPO / "examples" / "aml" / "motion3axis_station.aml"
SPEC = json.loads((REPO / "examples" / "specs" / "motion3axis.spec.json").read_text(encoding="utf-8"))

DEVICE_MODEL, _ = parse_aml(AML)


def test_expand_equals_seed():
    got = expand_project(DEVICE_MODEL, prog_id=1)
    assert got == SEED.read_text(encoding="utf-8")


def test_expand_passes_gates():
    # 展开产物过 xml2st 结构闸门（R1 经 consistency_check 复用）+ R2~R8 全项
    got = expand_project(DEVICE_MODEL, prog_id=1)
    ok, problems = consistency_check(got, SPEC["io_list"], device_model=DEVICE_MODEL)
    assert ok, problems


def test_expand_deterministic():
    a = expand_project(DEVICE_MODEL, prog_id=1)
    b = expand_project(DEVICE_MODEL, prog_id=1)
    assert a == b


def test_defaults_over_limits_rejected():
    dm = json.loads(json.dumps(DEVICE_MODEL))
    dm["kinematics"]["axes"][0]["defaults"]["velocity"] = 41.0  # > limits.vmax=40
    try:
        expand_project(dm, prog_id=1)
    except AxisExpandError as exc:
        assert "defaults.velocity=41.0" in str(exc) and "limits.vmax=40" in str(exc)
    else:
        raise AssertionError("超限 defaults 未被拒绝（RFC 修订②）")


def test_incomplete_io_rejected():
    dm = json.loads(json.dumps(DEVICE_MODEL))
    dm["kinematics"]["axes"][1]["io"] = None
    try:
        expand_project(dm, prog_id=1)
    except AxisExpandError as exc:
        assert "io 角色绑定不完整" in str(exc)
    else:
        raise AssertionError("io 缺失轴未被拒绝")


def test_poswin_substitution_reaches_fb():
    # poswin/行程代入 FB 体字面常量（全轴一致时）——改 AML 后展开产物随之变化
    dm = json.loads(json.dumps(DEVICE_MODEL))
    for a in dm["kinematics"]["axes"]:
        a["poswin"] = 3.0
        a["stroke"] = [0.0, 90.0]
    got = expand_project(dm, prog_id=1)
    assert '<simpleValue value="3.0" /></initialValue>' in got              # INTERP POSWIN
    assert "pos_target &gt; 90 OR pos_target &lt; 0" in got               # INTERP 越程
    assert "Position &lt; 0 OR Position &gt; 90" in got                   # MC 越程
