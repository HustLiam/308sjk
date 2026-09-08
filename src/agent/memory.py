#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Agent 记忆（两层，零新依赖）：

  · 程序性知识（committed，契约性质）：src/agent/knowledge/pitfalls.json
    ——错误签名→诊断→修法，人工策展随验收累积（改动走 RFC）；
  · 情景记忆（本机，gitignore）：workspace/memory/fixes.json
    ——闸门失败→采取的修复→结果 的修复对，跨会话按签名检索。

检索为确定性签名匹配（关键词计分，无向量/无嵌入）——记忆只给反馈包加
"上次怎么解的"，不参与闸门裁定（LLM 不判卷红线不变）。
"""

import json
import re
from pathlib import Path

KNOWLEDGE_DIR = Path(__file__).resolve().parent / "knowledge"
_EPISODIC_PATH = Path(__file__).resolve().parents[2] / "workspace" / "memory" / "fixes.json"

# 签名匹配时的噪声词（不计分）
_NOISE = {"the", "and", "for", "with", "error", "errors", "statement", "variable"}


def _tokens(text):
    return {w for w in re.findall(r"[A-Za-z_#\.\-]+", str(text).lower())
            if len(w) > 2 and w not in _NOISE}


class MemoryStore:
    def __init__(self, kb_path=KNOWLEDGE_DIR / "pitfalls.json", episodic_path=_EPISODIC_PATH):
        self.kb_path = Path(kb_path)
        self.episodic_path = Path(episodic_path)
        self._kb_mtime = None
        self.pitfalls = []
        self._load_kb()

    def _load_kb(self):
        """读坑库；文件 mtime 变化时热重载——知识更新对在跑的闭环即时生效。"""
        try:
            mtime = self.kb_path.stat().st_mtime
        except OSError:
            return
        if mtime == self._kb_mtime:
            return
        try:
            doc = json.loads(self.kb_path.read_text(encoding="utf-8"))
            self.pitfalls = doc.get("pitfalls", [])
            self._kb_mtime = mtime
        except (OSError, ValueError):
            pass

    # ---------------- 程序性知识：签名匹配 ----------------
    def match_pitfalls(self, errors, top=5):
        """错误文本列表 → 命中的知识条目（按签名得分降序）。

        top=5：症状相近的坑（如全冻结 P18 与画图段卡死 P22）常同时命中，
        多给两条参考无害（归因只进反馈不裁定），过紧会把真根因挤出列表。
        """
        text = " \n ".join(str(e) for e in errors).lower()
        scored = []
        for p in self.pitfalls:
            score = 0
            for sig in p.get("signatures", []):
                s = sig.lower()
                if s in text:
                    score += len(s.split()) + 2      # 短语签名权重高
                else:
                    score += sum(1 for t in _tokens(s) if t in text) * 0.3
            if score >= 1:
                scored.append((score, p))
        scored.sort(key=lambda x: -x[0])
        return [p for _s, p in scored[:top]]

    # ---------------- 情景记忆：修复对 ----------------
    def _load_episodic(self):
        if self.episodic_path.is_file():
            try:
                return json.loads(self.episodic_path.read_text(encoding="utf-8"))
            except ValueError:
                return []
        return []

    def record_fix(self, gate, errors, action, outcome):
        """记录一次修复对（gate 失败→action→outcome=resolved|final|abandoned）。"""
        entries = self._load_episodic()
        entries.append({"gate": gate,
                        "errors": [str(e)[:200] for e in errors][:6],
                        "action": str(action)[:300],
                        "outcome": outcome})
        try:
            self.episodic_path.parent.mkdir(parents=True, exist_ok=True)
            self.episodic_path.write_text(
                json.dumps(entries, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass  # 记忆写失败不影响主流程
        return len(entries)

    def match_fixes(self, errors, top=2):
        """按错误文本相似度检索历史修复对（仅取 resolved/final 的成功样本）。"""
        q = _tokens(" ".join(str(e) for e in errors))
        if not q:
            return []
        scored = []
        for e in self._load_episodic():
            if e.get("outcome") not in ("resolved", "final"):
                continue
            d = _tokens(" ".join(e.get("errors", [])))
            if not d:
                continue
            overlap = len(q & d) / max(1, len(q | d))
            if overlap >= 0.25:
                scored.append((overlap, e))
        scored.sort(key=lambda x: -x[0])
        return [e for _s, e in scored[:top]]
