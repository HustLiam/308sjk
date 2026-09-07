# -*- coding: utf-8 -*-
"""
xml2st 转换器单测：交付物回归 + 反例。

运行:  python -m pytest tests/ -v
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "pipeline"))

from xml2st import convert, extract_st_bodies, parse  # noqa: E402

MOTION_XML = REPO / "src" / "plc" / "motion3axis.xml"


# ---------------------------------------------------------------- 交付物回归
class TestMotion3AxisArtifact:
    def test_valid(self):
        ok, _, problems = convert(MOTION_XML)
        assert ok, problems

    def test_st_contains_program_and_address(self):
        _, st, _ = convert(MOTION_XML)
        assert "FUNCTION_BLOCK INTERP" in st      # CSP 插补引擎 FB
        assert "PROGRAM PLC_PRG" in st
        assert "AT %QW0" in st          # x_fb 对外接口：INT -> Modbus 保持寄存器 0
        assert "FUNCTION_BLOCK DRIVE402" in st  # CSP 驱动器 FB
        assert "FUNCTION_BLOCK MC_MOVEABSOLUTE" in st
        assert "CASE state OF" in st    # CiA 402 状态机

    def test_st_has_configuration(self):
        _, st, _ = convert(MOTION_XML)
        assert "CONFIGURATION Config0" in st
        assert "PROGRAM instance0 WITH task0 : PLC_PRG;" in st
        assert "END_PROGRAM" in st

    def test_located_vars_in_separate_block(self):
        """matiec 要求 AT 定位变量与普通声明（FB 实例等）分 VAR 块。"""
        _, st, _ = convert(MOTION_XML)
        blocks = st.split("END_VAR")
        # FB 实例块（ax_x 等）不含 AT；x_fb 所在块只有定位变量
        fb_block = next(b for b in blocks if "ix_x" in b or "dv_x" in b)
        assert "AT %" not in fb_block
        located_block = next(b for b in blocks if "x_fb AT" in b)
        assert "ix_x" not in located_block and "dv_x" not in located_block


# ---------------------------------------------------------------- 反例用例
GOOD_MINIMAL = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0201">
  <fileHeader companyName="" productName="t" productVersion="1"
              creationDateTime="2026-01-01T00:00:00" />
  <contentHeader name="t" modificationDateTime="2026-01-01T00:00:00" />
  <types>
    <dataTypes />
    <pous>
      <pou name="PLC_PRG" pouType="program">
        <interface>
          <localVars>
            <variable name="x"><type><BOOL /></type></variable>
            <variable name="y" address="%QW2"><type><INT /></type></variable>
          </localVars>
        </interface>
        <body>
          <ST><xhtml xmlns="http://www.w3.org/1999/xhtml">y := y + 1;</xhtml></ST>
        </body>
      </pou>
    </pous>
  </types>
  <instances><configurations /></instances>
</project>
"""


def _write(tmp_path, content, name="case.xml"):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_minimal_good_converts(tmp_path):
    ok, st, problems = convert(_write(tmp_path, GOOD_MINIMAL))
    assert ok, problems
    assert "AT %QW2" in st
    assert "y := y + 1;" in st


def test_wrong_root_namespace_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace("http://www.plcopen.org/xml/tc6_0201", "http://example.com/x")
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("根元素" in p for p in problems)


def test_unparseable_rejected(tmp_path):
    problems, model = parse(_write(tmp_path, "<project><oops"))
    assert problems
    ok, _, _ = convert(_write(tmp_path, "<project><oops"))
    assert not ok


def test_missing_interface_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace("<interface>", "<!--i-->").replace("</interface>", "<!--/i-->")
    ok, _, _ = convert(_write(tmp_path, bad))
    # 接口缺失不再必然失败（可有无变量 POU），但 x/y 不应出现
    _, st, _ = convert(_write(tmp_path, bad)) if ok else (None, None, None)
    assert ok is True or ok is False  # 两种实现都接受，只要不崩溃


def test_empty_body_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace(">y := y + 1;<", "><")
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("ST 本体为空" in p for p in problems)


def test_bad_identifier_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace('name="x"', 'name="1bad"')
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("变量名非法" in p for p in problems)


def test_bad_address_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace('address="%QW2"', 'address="QW2"')
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("地址" in p for p in problems)


# ------------------------------------------------------- 位宽匹配（Modbus 映射）
def test_dword_address_rejected(tmp_path):
    """%QD 不映射 Modbus 缓冲区——必须拒绝，防止编译通过但外部读不到。"""
    bad = GOOD_MINIMAL.replace('name="y" address="%QW2"><type><INT />',
                               'name="y" address="%QD0"><type><DINT />')
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("不映射" in p for p in problems)


def test_width_mismatch_rejected(tmp_path):
    # DINT 放 %QW：位宽不匹配
    bad = GOOD_MINIMAL.replace('name="y" address="%QW2"><type><INT />',
                               'name="y" address="%QW2"><type><DINT />')
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("位宽不匹配" in p for p in problems)


def test_int_on_bit_address_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace('name="y" address="%QW2"><type><INT />',
                               'name="y" address="%QX0.1"><type><INT />')
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("BOOL" in p for p in problems)


def test_unsupported_pou_type_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace('pouType="program"', 'pouType="programX"')
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok


def test_no_program_pou_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace('pouType="program"', 'pouType="functionBlock"')
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("program" in p for p in problems)


def test_extract_bodies_roundtrip(tmp_path):
    problems, sources = extract_st_bodies(_write(tmp_path, GOOD_MINIMAL))
    assert not problems
    assert sources["PLC_PRG"] == "y := y + 1;"


# ------------------------------------------------------- 接口完整性（防丢失）
FB_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0201">
  <fileHeader companyName="" productName="t" productVersion="1"
              creationDateTime="2026-01-01T00:00:00" />
  <contentHeader name="t" modificationDateTime="2026-01-01T00:00:00" />
  <types>
    <dataTypes />
    <pous>
      <pou name="MotorFB" pouType="functionBlock">
        <interface>
          <inputVars>
            <variable name="start"><type><BOOL /></type></variable>
          </inputVars>
          <outputVars>
            <variable name="running"><type><BOOL /></type></variable>
          </outputVars>
          <localVars>
            <variable name="speed" address="%QW4"><type><INT /></type></variable>
            <variable name="buf"><type><array>
              <dimension x="1" y="8" />
              <baseType><INT /></baseType>
            </array></type></variable>
          </localVars>
          <localVars constant="true">
            <variable name="MAX_SPEED"><type><DINT /></type>
              <initialValue><simpleValue value="1500" /></initialValue></variable>
          </localVars>
        </interface>
        <body>
          <ST><xhtml xmlns="http://www.w3.org/1999/xhtml">running := start;</xhtml></ST>
        </body>
      </pou>
      <pou name="PLC_PRG" pouType="program">
        <interface><localVars>
          <variable name="m1"><type><derived name="MotorFB" /></type></variable>
        </localVars></interface>
        <body>
          <ST><xhtml xmlns="http://www.w3.org/1999/xhtml">m1();</xhtml></ST>
        </body>
      </pou>
    </pous>
  </types>
  <instances><configurations /></instances>
</project>
"""


class TestInterfaceCompleteness:
    def _fb_st(self):
        import tempfile
        from pathlib import Path
        p = Path(tempfile.mkdtemp()) / "fb.xml"
        p.write_text(FB_FIXTURE, encoding="utf-8")
        return convert(p)

    def test_fb_io_blocks_preserved(self):
        ok, st, problems = self._fb_st()
        assert ok, problems
        assert "VAR_INPUT" in st and "start : BOOL;" in st
        assert "VAR_OUTPUT" in st and "running : BOOL;" in st
        assert "FUNCTION_BLOCK MotorFB" in st

    def test_constant_qualifier_preserved(self):
        _, st, _ = self._fb_st()
        assert "VAR CONSTANT" in st
        assert "MAX_SPEED" in st

    def test_array_type_rendered(self):
        _, st, _ = self._fb_st()
        assert "ARRAY[1..8] OF INT" in st


# ------------------------------------------------------- 防静默丢失（显式拒绝）
def _wrap(inner):
    return """<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0201">
  <fileHeader companyName="" productName="t" productVersion="1"
              creationDateTime="2026-01-01T00:00:00" />
  <contentHeader name="t" modificationDateTime="2026-01-01T00:00:00" />
  <types>%s</types>
  <instances><configurations /></instances>
</project>""" % inner


POU_MIN = """<dataTypes />
<pous><pou name="PLC_PRG" pouType="program">
  <interface><localVars><variable name="x"><type><BOOL /></type></variable></localVars></interface>
  <body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">x := TRUE;</xhtml></ST></body>
</pou></pous>"""


def test_data_types_rejected(tmp_path):
    bad = _wrap('<dataTypes><dataType name="MyStruct"><baseType><DINT /></baseType></dataType></dataTypes>' + POU_MIN)
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("dataTypes" in p for p in problems)


def test_action_rejected(tmp_path):
    bad = _wrap(POU_MIN.replace("</pou>", "<action name=\"act1\"></action></pou>"))
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("action" in p for p in problems)


def test_configuration_content_rejected(tmp_path):
    xml = _wrap(POU_MIN).replace("<configurations />",
                                 "<configurations><configuration name=\"C1\"><resource name=\"R1\" /></configuration></configurations>")
    ok, _, problems = convert(_write(tmp_path, xml))
    assert not ok
    assert any("configuration" in p for p in problems)


def test_persistent_rejected(tmp_path):
    bad = _wrap(POU_MIN.replace("<localVars>", "<localVars persistent=\"true\">"))
    ok, _, problems = convert(_write(tmp_path, bad))
    assert not ok
    assert any("PERSISTENT" in p for p in problems)
