#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ST 模式库（gc 文档 §3.1：知识资产，当前种子 = motion3axis 三轴运动控制场景）。

设计：不复制代码——直接从 src/plc/*.xml 提取 ST 本体与定位变量接口，
按关键词选卡注入生成器 prompt（RAG-lite：关键词命中 + 固定兜底）。
lx 侧新增/修改场景 XML 后，模式卡内容自动跟随，无第二份拷贝。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from xml2st import extract_st_bodies  # noqa: E402

from .config import PLC_DIR  # noqa: E402

# 模式目录：key -> (文件名, 摘要, 命中关键词)。摘要与场景 fileHeader 对齐。
CATALOG = [
    ("motion3axis", "motion3axis.xml",
     "三轴运动控制（PTP 点到点定位）：逐轴死区闭环、Z 安全区互锁（Z 在上部安全区才允许 X/Y 运动）、"
     "双驱互斥、到位汇总 in_pos 与停止安全态",
     ["三轴", "运动", "定位", "轴", "motion", "gantry", "PTP", "点位", "龙门", "伺服",
      "插补", "平移", "行程", "互锁"]),
]

DEFAULT_PICKS = ("motion3axis",)  # 无命中时的兜底：当前唯一种子


def pattern_cards(task_goal, io_list=None, picks=2):
    """按需求关键词选模式卡。返回 [{key, summary, st, io}]。

    io_list 也参与匹配（device 语义），task_goal 优先。
    """
    text = str(task_goal or "")
    if io_list:
        text += " " + " ".join(str(p.get("device", "")) for p in io_list)
    text_lc = text.lower()

    scored = []
    for key, fname, summary, tags in CATALOG:
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

    by_key = {e[0]: e for e in CATALOG}
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


def render_cards(cards):
    """把模式卡渲染为 prompt 片段。"""
    chunks = []
    for card in cards:
        chunks.append("### 模式卡：%s\n%s\n```st\n%s\n```" % (card["key"], card["summary"], card["st"]))
    return "\n\n".join(chunks)
