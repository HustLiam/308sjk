#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""领域知识库加载器（P3 消费接线）：按任务选域 → 条目摘要供 prompt 节选注入。

设计约束：token 预算——骨架类硬规则仍在 skill 内联（那里是权威全文），本加载器
只注入**条目索引 + 首句摘要**（一行一条），长尾知识按任务类型选域；归因 LLM
的 diagnosis 可引用条目号（ST-xx/PX-xx/AML-xx/MC-xx）供反馈包溯源。
"""

import re
from pathlib import Path

DOMAIN_DIR = Path(__file__).resolve().parent

# 任务关键词 → 域文件（多关键词任一命中即选入；无命中给通用缺省集）
_DOMAIN_KEYWORDS = {
    "st.md": ("互锁", "急停", "状态机", "序列", "延时", "case", "密码", "泵", "阀"),
    "motion_control.md": ("轴", "运动", "定位", "插补", "画", "draw", "plot",
                          "square", "circle", "home", "回零", "回参考", "jog", "龙门"),
    "automationml.md": ("aml", "设备描述", "io 清单", "通道"),
    "plcopen_xml.md": ("xml", "工程结构"),
}
_DEFAULT_DOMAINS = ("st.md", "motion_control.md", "plcopen_xml.md")


def load_entries(domain_file):
    """域文档 → [(条目号, 首句)]。条目行格式：- **ST-01**（标签）正文首句..."""
    text = (DOMAIN_DIR / domain_file).read_text(encoding="utf-8")
    entries = []
    for m in re.finditer(r"^- \*\*(\w+-\d{2,})\*\*（[^）]*）(.+)$", text, re.M):
        body = m.group(2).strip()
        first = re.split(r"[。；;（]", body, 1)[0][:120]
        first = first.replace("**", "").replace("`", "")
        entries.append((m.group(1), first))
    return entries


def domain_digest(task_text, max_entries=14):
    """任务文本 → 选域条目索引摘要（拼进 system prompt 的节选块；空文本返回 ''）。"""
    if not task_text:
        return ""
    text = str(task_text).lower()
    picked = []
    for fname, kws in _DOMAIN_KEYWORDS.items():
        if any(k.lower() in text for k in kws):
            picked.append(fname)
    files = picked or list(_DEFAULT_DOMAINS)
    lines, seen = [], set()
    for fname in files:
        for eid, first in load_entries(fname):
            if eid not in seen and len(lines) < max_entries:
                seen.add(eid)
                lines.append("· %s %s" % (eid, first))
    if not lines:
        return ""
    return ("## 领域知识条目索引（完整条目见 knowledge/domain/；诊断可引用条目号）\n"
            + "\n".join(lines))


def entry_index():
    """全量条目号+标题索引（归因 LLM 用：diagnosis 引用条目号的能力底座）。"""
    out = []
    for f in sorted(DOMAIN_DIR.glob("*.md")):
        if f.name == "README.md":
            continue
        out.extend(load_entries(f.name))
    return out
