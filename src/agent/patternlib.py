#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ST 模式库（gc 文档 §3.1：知识资产，当前种子 = motion3axis 三轴运动控制场景）。

设计：不复制代码——直接从 src/plc/*.xml 提取 ST 本体与定位变量接口，
按关键词选卡注入生成器 prompt（RAG-lite：关键词命中 + 固定兜底）。
lx 侧新增/修改场景 XML 后，模式卡内容自动跟随，无第二份拷贝。
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from xml2st import extract_st_bodies  # noqa: E402

from .config import PLC_DIR  # noqa: E402

# 自动策展注册表：验收通过的场景经 register_pattern() 登记（key 与 CATALOG 不重复），
# pattern_cards 同时读取两处——新知识无需改代码
REGISTRY_PATH = Path(__file__).resolve().parent / "knowledge" / "patterns.json"

# 模式目录：key -> (文件名, 摘要, 命中关键词)。摘要与场景 fileHeader 对齐。
CATALOG = [
    ("motion3axis", "motion3axis.xml",
     "三轴运动控制（PTP 点到点定位）：逐轴死区闭环、Z 安全区互锁（Z 在上部安全区才允许 X/Y 运动）、"
     "双驱互斥、到位汇总 in_pos 与停止安全态",
     ["三轴", "运动", "定位", "轴", "motion", "gantry", "PTP", "点位", "龙门", "伺服",
      "插补", "平移", "行程", "互锁"]),
]

DEFAULT_PICKS = ("motion3axis",)  # 无命中时的兜底：当前唯一种子

# 上下文预算：模式卡注入总量上限（字符）。超出时丢卡保 prompt 可用性，
# 并在渲染末尾注明被丢弃的卡（LLM 仍可凭 skill 摘要工作）
MAX_CARDS_CHARS = 24000


def _catalog(include_curated=True):
    """静态 CATALOG + 自动策展注册表（合并视图）。

    include_curated=False → 仅静态 CATALOG（lx 审定的通用运动原语）——
    用于验证 Agent 的真实泛化生成：新任务不借助同构场景的自动策展卡
    （那等于把答案放进 few-shot）。
    """
    entries = list(CATALOG)
    if not include_curated:
        return entries
    if REGISTRY_PATH.is_file():
        try:
            reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except ValueError:
            reg = []
        for e in reg.get("patterns", []):
            entries.append((e["key"], e["file"], e.get("summary", ""),
                            e.get("tags", [])))
    return entries


def register_pattern(key, xml_path, summary, tags, provenance="auto"):
    """验收通过的场景登记为模式卡（防重复：CATALOG/注册表已有 key 或文件即跳过）。
    登记前校验 XML 仍过闸门——注册表不收坏种子。返回是否新增。"""
    entries = _catalog()
    fname = str(xml_path).replace("\\", "/").rsplit("/", 1)[-1]
    if any(e[0] == key or e[1] == fname for e in entries):
        return False
    src = Path(xml_path) if Path(xml_path).is_absolute() else PLC_DIR / fname
    try:
        problems, _bodies = extract_st_bodies(src)
    except Exception:
        return False
    if problems:
        return False
    reg = {"patterns": []}
    if REGISTRY_PATH.is_file():
        try:
            reg = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        except ValueError:
            reg = {"patterns": []}
    reg.setdefault("patterns", []).append({
        "key": key, "file": fname,
        "summary": summary, "tags": list(tags), "provenance": provenance})
    REGISTRY_PATH.write_text(json.dumps(reg, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return True


def pattern_cards(task_goal, io_list=None, picks=2, include_curated=True):
    """按需求关键词选模式卡。返回 [{key, summary, st, io}]。

    io_list 也参与匹配（device 语义），task_goal 优先；
    include_curated=False 排除自动策展卡（泛化验证口径）。
    """
    text = str(task_goal or "")
    if io_list:
        text += " " + " ".join(str(p.get("device", "")) for p in io_list)
    text_lc = text.lower()

    scored = []
    for key, fname, summary, tags in _catalog(include_curated):
        score = sum(1 for t in tags if t.lower() in text_lc)
        if score:
            scored.append((score, key))
    scored.sort(reverse=True)
    keys = [k for _s, k in scored[:picks]]
    for fallback in DEFAULT_PICKS:  # 命中不足时用通用卡补齐
        if len(keys) >= picks:
            break
        if fallback not in keys:
            keys.append(fallback)

    by_key = {e[0]: e for e in _catalog()}
    cards = []
    for key in keys:
        _k, fname, summary, _tags = by_key[key]
        xml_path = PLC_DIR / fname
        if not xml_path.is_file():
            continue
        problems, bodies = extract_st_bodies(xml_path)
        if problems:  # 种子必须永远可过闸门；失败说明仓库被破坏，直接暴露
            raise RuntimeError("模式库种子 %s 校验失败: %s" % (fname, problems))
        cards.append({
            "key": key,
            "summary": summary,
            "st": "\n\n".join("--- POU %s ---\n%s" % (n, b) for n, b in bodies.items()),
        })
    return cards


def render_cards(cards, max_chars=MAX_CARDS_CHARS):
    """把模式卡渲染为 prompt 片段（上下文预算：超限丢卡并在末尾注明）。"""
    chunks, used, dropped = [], 0, []
    for card in cards:
        piece = "### 模式卡：%s\n%s\n```st\n%s\n```" % (
            card["key"], card["summary"], card["st"])
        if used + len(piece) > max_chars and chunks:
            dropped.append(card["key"])
            continue
        chunks.append(piece)
        used += len(piece)
    if dropped:
        chunks.append("（上下文预算：%s 模式卡未注入，仅凭以上模式与契约摘要工作）"
                      % "、".join(dropped))
    return "\n\n".join(chunks)
