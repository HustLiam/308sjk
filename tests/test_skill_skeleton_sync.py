# -*- coding: utf-8 -*-
"""
skill 骨架同步单测（防漂移）：lx 登记的同步请求配套护栏。

背景：plcgen_skill「基础 FB 骨架冻结」曾落后 master motion3axis 一个版本
（v3.0 形态 vs v4.0 总线动力学+全签名 MC 层），根因是没有任何同步校验。
本测试从**已验收种子**（src/plc/motion3axis.xml，v4.0）提取 FB 接口指纹，
与 skill 内联骨架逐块比对——master 骨架演进而 skill 未跟随时此处变红。
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from pipeline import xml2st  # noqa: E402

SKILL = (REPO / "src" / "agent" / "prompts" / "plcgen_skill.md").read_text(encoding="utf-8")
SEED_XML = REPO / "src" / "plc" / "motion3axis.xml"

# 完整内联（接口声明逐字进 skill）的块——签名必须与种子完全一致
FULL_INLINE = ("INTERP", "MC_MOVEABSOLUTE", "MC_HALT", "MC_POWER")
# 摘要内联的块——skill 必须提到块名与其全部端口名
SUMMARY_INLINE = {
    "MC_MOVERELATIVE": ["fire", "Distance", "ActualPosition", "Velocity",
                        "Acceleration", "Deceleration", "interp_exe",
                        "CommandAborted", "ErrorID"],
    "MC_READSTATUS": ["Enable", "Valid", "Moving", "StandStill", "Disabled", "ErrorStop"],
    "MC_READACTUALPOSITION": ["Enable", "ActualPosition", "Valid", "Position"],
    "MC_POWER": ["Enable", "Status"],
    "MC_STOP": ["fire", "v_act"],
    "MC_HOME": ["fire"],
    "MC_MOVEJOG": ["JogForward", "JogBackward", "cur_pos"],
}


def _fb_signatures(st_text):
    """ST 文本 → {FB 名: {var_kind: set(端口名)}}。"""
    out = {}
    for m in re.finditer(r"FUNCTION_BLOCK\s+(\w+)(.*?)END_FUNCTION_BLOCK", st_text, re.S):
        name, body = m.group(1), m.group(2)
        ports = {"input": set(), "output": set(), "in_out": set()}
        kind = None
        for line in body.split("\n"):
            s = line.strip()
            if re.match(r"VAR_INPUT\b", s):
                kind = "input"
            elif re.match(r"VAR_OUTPUT\b", s):
                kind = "output"
            elif re.match(r"VAR_IN_OUT\b", s):
                kind = "in_out"
            elif s.startswith("END_VAR"):
                kind = None
            elif kind:
                # 一行可含多条 "name : type;" 声明（skill 骨架为省行数并排）
                for decl in s.split(";"):
                    if ":" in decl:
                        ports[kind].add(decl.split(":")[0].strip())
        out[name] = ports
    return out


def _skill_st_blocks():
    """skill 内 ```st 代码块拼接（含 FUNCTION_BLOCK 声明的骨架块）。"""
    return "\n".join(re.findall(r"```st\n(.*?)```", SKILL, re.S))


def test_full_inline_blocks_match_seed_signatures():
    ok, seed_st, problems = xml2st.convert(str(SEED_XML))
    assert ok and not problems, problems
    seed = _fb_signatures(seed_st)
    skill = _fb_signatures(_skill_st_blocks())
    for fb in FULL_INLINE:
        assert fb in skill, "skill 缺 %s 完整骨架（同步落后于 master 种子）" % fb
        assert skill[fb] == seed.get(fb), (
            "%s 签名与种子不一致（skill 漂移）：\n skill=%s\n seed =%s" % (fb, skill[fb], seed.get(fb)))


def test_summary_blocks_mention_all_ports():
    ok, seed_st, _ = xml2st.convert(str(SEED_XML))
    seed = _fb_signatures(seed_st)
    for fb, ports in SUMMARY_INLINE.items():
        assert fb in SKILL, "skill 未提及 %s" % fb
        seed_ports = seed.get(fb)
        if seed_ports:  # 端口清单以种子为准，摘要素材过时时同步本测试
            expected = {p for p in ports if p in (seed_ports["input"] | seed_ports["output"]
                                                  | seed_ports["in_out"])}
        else:
            expected = set(ports)
        missing = [p for p in expected if not re.search(r"\b%s\b" % re.escape(p), SKILL)]
        assert not missing, "skill 提及 %s 但缺端口 %s" % (fb, missing)


def test_v4_specific_semantics_present():
    """v4.0 关键语义关键词必须在场（总线动力学/中止/新块/常量修订）。"""
    for kw in ("vel_req", "acc_req", "dec_req", "Aborted", "CommandAborted",
               "ErrorID", "MC_HALT", "MC_MOVERELATIVE", "MC_READSTATUS",
               "MC_READACTUALPOSITION", "dyn_vel", "KP 25.0"):
        assert kw in SKILL, "skill 缺 v4.0 关键语义 %r" % kw


def test_p23_no_else_reset_fallback_in_skeleton():
    """P23 联动：skill 内联骨架不得出现 ELSE 复位兜底形态。"""
    blocks = _skill_st_blocks()
    assert not re.search(r"ELSE\s*\n\s*state := 1;", blocks), "P23 违例：ELSE state := 1"
    assert not re.search(r"ELSE\s*\n\s*sw := 16#0000;", blocks), "P23 违例：ELSE sw := 0"
