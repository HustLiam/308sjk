# -*- coding: utf-8 -*-
"""
validate_xml 的单元测试 + counter.xml 交付物回归测试。

运行:  python -m pytest tests/ -v
"""

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src" / "pipeline"))

from validate_xml import validate  # noqa: E402

COUNTER_XML = REPO / "src" / "plc" / "counter.xml"


# ---------------------------------------------------------------- 交付物回归
class TestCounterArtifact:
    def test_valid(self):
        ok, problems, _ = validate(COUNTER_XML)
        assert ok, problems

    def test_contains_expected_program(self):
        _, _, sources = validate(COUNTER_XML)
        assert "PLC_PRG" in sources

    def test_counter_logic_present(self):
        _, _, sources = validate(COUNTER_XML)
        st = sources["PLC_PRG"]
        # 每秒自增：TON 产生 1s 脉冲 + cnt 加一
        assert "T#1S" in st
        assert "cnt := cnt + 1" in st
        assert "TON" in st or "pulse" in st


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
          </localVars>
        </interface>
        <body>
          <ST><xhtml xmlns="http://www.w3.org/1999/xhtml">x := TRUE;</xhtml></ST>
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


def test_minimal_good_passes(tmp_path):
    ok, problems, _ = validate(_write(tmp_path, GOOD_MINIMAL))
    assert ok, problems


def test_wrong_root_namespace_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace("http://www.plcopen.org/xml/tc6_0201", "http://example.com/other")
    ok, problems, _ = validate(_write(tmp_path, bad))
    assert not ok
    assert any("根元素" in p for p in problems)


def test_unparseable_rejected(tmp_path):
    ok, problems, _ = validate(_write(tmp_path, "<project><oops"))
    assert not ok
    assert any("无法解析" in p for p in problems)


def test_missing_interface_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace("<interface>", "<!--interface-->").replace(
        "</interface>", "<!--/interface-->")
    ok, problems, _ = validate(_write(tmp_path, bad))
    assert not ok
    assert any("interface" in p for p in problems)


def test_empty_body_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace(">x := TRUE;<", "><")
    ok, problems, _ = validate(_write(tmp_path, bad))
    assert not ok
    assert any("ST 本体为空" in p for p in problems)


def test_bad_identifier_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace('name="x"', 'name="1bad"')
    ok, problems, _ = validate(_write(tmp_path, bad))
    assert not ok
    assert any("变量名非法" in p for p in problems)


def test_unsupported_pou_type_rejected(tmp_path):
    bad = GOOD_MINIMAL.replace('pouType="program"', 'pouType="programX"')
    ok, problems, _ = validate(_write(tmp_path, bad))
    assert not ok
    assert any("pouType" in p for p in problems)


def test_no_pou_rejected(tmp_path):
    empty = GOOD_MINIMAL.split("<pou ")[0] + "</pous></types>" \
        + "<instances><configurations /></instances></project>"
    ok, problems, _ = validate(_write(tmp_path, empty))
    assert not ok
    assert any("未找到任何" in p for p in problems)
