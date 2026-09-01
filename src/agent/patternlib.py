#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ST 模式库（gc 文档 §3.1：知识资产，首批种子 = 已验收 6 场景）。

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
    ("counter", "counter.xml",
     "定时脉冲自增计数：TON 自激振荡 1s 脉冲 + INT 累加 @%QW（链路冒烟基准）",
     ["计数", "count", "统计", "脉冲", "自增", "counter"]),
    ("sorting", "sorting.xml",
     "分拣线：启停自锁+急停、上升沿 FB 计数、TON 延时 300ms 推出/500ms 保持的剔除时序、班统计",
     ["分拣", "剔除", "sorting", "传送带", "光电", "推杆", "气缸", "分类"]),
    ("pump_alternation", "pump_alternation.xml",
     "双泵交替液位控制：滞回带启停、运行时长均衡轮换、最短运行时间保护",
     ["泵", "pump", "液位", "交替", "轮换", "滞回", "互为备用"]),
    ("traffic_light", "traffic_light.xml",
     "十字路口交通灯：TON 链式状态机（绿-黄-红 + 全红清空间隔）",
     ["交通灯", "红绿灯", "traffic", "状态机", "时序", "路口", "灯"]),
    ("cylinder_seq", "cylinder_seq.xml",
     "双缸顺序控制：CASE 步进链 A伸→B伸→B缩→A缩循环、原位启动联锁、双电磁阀防冲突不变量",
     ["顺序", "步进", "cylinder", "气缸", "CASE", "循环", "sequence", "双缸"]),
    ("pid_tank", "pid_tank.xml",
     "单容水箱连续液位调节：位置式 PI + 条件积分抗饱和；INT 定点对外 / REAL 内部混合；偏差报警",
     ["PID", "PI", "调节", "pid", "水箱", "tank", "连续", "抗饱和", "积分", "恒温", "恒液"]),
]

DEFAULT_PICKS = ("sorting", "cylinder_seq")  # 无命中时的兜底：离散工艺最通用的两张卡


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
