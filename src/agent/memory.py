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
# 自动化学习经验库（v2）：失败签名 + 修复骨架，跨任务/跨重启持久（本机）
_LESSONS_PATH = Path(__file__).resolve().parents[2] / "workspace" / "experience" / "lessons.json"

# 签名匹配时的噪声词（不计分）
_NOISE = {"the", "and", "for", "with", "error", "errors", "statement", "variable"}

# 失败证据归一化：剥离采样时刻/时间戳/行号/st 文件名等易变成分——同质失败
# （仅数字时刻不同）折叠为同一签名文本；状态数值（pl_step=1→2）保留（是推进
# 证据不是噪声）。零推进熔断与经验库检索共用这一套指纹。
_SIG_VOLATILE = [
    (re.compile(r"t=\d+(?:\.\d+)?s"), "t=?s"),                    # [trace t=36s]/[diag t=3.5s]
    (re.compile(r"\b\d{2}:\d{2}:\d{2}\b"), "TS"),                 # 时:分:秒
    (re.compile(r"\b\d{4}-\d{2}-\d{2}\b"), "DATE"),               # 日期
    (re.compile(r"(?<=\w):\d+(?::\d+)?"), ":N"),                  # 文件:行(列) 号
    (re.compile(r"\bline\s+\d+", re.IGNORECASE), "line N"),
    (re.compile(r"[\w./\\-]*\w+\.st\b", re.IGNORECASE), "F.st"),  # st 文件名（含临时路径）
    (re.compile(r"iter_\d+"), "iter_N"),
]


def normalize_errors(errors):
    """errors → 归一化签名文本（零推进判定与经验检索的统一指纹）。"""
    norm = "\n".join(str(e) for e in (errors or []))
    for pat, sub in _SIG_VOLATILE:
        norm = pat.sub(sub, norm)
    return norm


def _tokens(text):
    return {w for w in re.findall(r"[A-Za-z_#\.\-]+", str(text).lower())
            if len(w) > 2 and w not in _NOISE}


def _lines_sim(a, b):
    """归一化行集合的 Jaccard 相似度（0~1）。"""
    la = {l.strip() for l in str(a).splitlines() if l.strip()}
    lb = {l.strip() for l in str(b).splitlines() if l.strip()}
    if not la or not lb:
        return 0.0
    return len(la & lb) / len(la | lb)


class MemoryStore:
    def __init__(self, kb_path=None, episodic_path=None, lessons_path=None):
        # 默认路径运行时解析（非签名期绑定）——测试经 monkeypatch 模块级
        # 常量即可整体隔离（防 pytest 写污染真实经验库，2026-09-09 实证教训）
        self.kb_path = Path(kb_path) if kb_path else KNOWLEDGE_DIR / "pitfalls.json"
        self.episodic_path = (Path(episodic_path) if episodic_path
                              else _EPISODIC_PATH)
        self.lessons_path = (Path(lessons_path) if lessons_path
                             else _LESSONS_PATH)
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

    # ---------------- 自动化学习经验库（v2） ----------------
    def _load_lessons(self):
        if self.lessons_path.is_file():
            try:
                data = json.loads(self.lessons_path.read_text(encoding="utf-8"))
                return data.get("lessons", []) if isinstance(data, dict) else []
            except (OSError, ValueError):
                return []
        return []

    def record_lesson(self, gate, errors, fix_summary, diff_hunks=None,
                      outcome="resolved", task=None, kind="flip", diagnosis=None,
                      shape=None):
        """记录一条学习经验（战役内翻转自动记录 / 战役级 LLM 提炼）。

        条目以**归一化签名文本**为检索键——同质失败（仅采样时刻不同）可精确
        命中；outcome=abandoned 的条目不参与检索（防无效借鉴）。
        shape：任务图形类型（square/circle）——供生成前的经验预注入按
        任务相似度筛选（借鉴程度与相似度相关）。
        """
        entry = {
            "kind": kind, "gate": str(gate), "task": str(task or ""),
            "shape": str(shape or "") or None,
            "sig_norm": normalize_errors(errors),
            "errors": [str(e)[:200] for e in (errors or [])][:10],
            "fix_summary": str(fix_summary)[:400],
            "diff_hunks": [str(h)[:600] for h in (diff_hunks or [])][:8],
            "outcome": outcome,
        }
        if diagnosis:
            entry["diagnosis"] = str(diagnosis)[:400]
        lessons = self._load_lessons()
        lessons.append(entry)
        try:
            self.lessons_path.parent.mkdir(parents=True, exist_ok=True)
            self.lessons_path.write_text(
                json.dumps({"lessons": lessons}, ensure_ascii=False, indent=1),
                encoding="utf-8")
        except OSError:
            pass  # 经验写失败不影响主流程
        return len(lessons)

    def match_lessons(self, errors, gate=None, top=2, high=0.55, low=0.30):
        """新失败 → 相似历史经验（相似度 = 归一化行集合 Jaccard，同闸门优先）。

        借鉴程度与相似度相关（用户口径）：
          sim ≥ high → detail 级（fix_summary + diff_hunks 骨架）；
          low ≤ sim < high → 方向级（仅 fix_summary）；
          sim < low → 不返回（避免无效借鉴）。
        """
        sig = normalize_errors(errors)
        scored = []
        for e in self._load_lessons():
            if e.get("outcome") not in ("resolved", "final"):
                continue
            sim = _lines_sim(sig, e.get("sig_norm", ""))
            if e.get("gate") != gate:
                sim *= 0.5                # 跨闸门经验降权（可用但非同构场景）
            if sim >= low:
                scored.append((sim, e))
        scored.sort(key=lambda x: -x[0])
        out = []
        for sim, e in scored[:top]:
            item = {"sim": round(sim, 2), "gate": e.get("gate"),
                    "task": e.get("task", ""), "fix_summary": e.get("fix_summary", ""),
                    "outcome": e.get("outcome"), "kind": e.get("kind")}
            if sim >= high:
                item["diff_hunks"] = e.get("diff_hunks", [])
            out.append(item)
        return out

    def recent_lessons(self, shape=None, limit=4, outcome="resolved"):
        """同形状任务最近的 resolved 经验（生成前预注入用）。

        相似度口径 = 任务图形类型一致（画圆任务借鉴画圆经验，不借鉴画方）；
        返回按记录序倒序（最新优先），空 shape 视为不匹配（保守）。
        """
        out = []
        for e in reversed(self._load_lessons()):
            if e.get("outcome") != outcome or e.get("shape") != shape:
                continue
            item = {"task": e.get("task", ""), "fix_summary": e.get("fix_summary", "")}
            if e.get("diagnosis"):
                item["diagnosis"] = e["diagnosis"]
            out.append(item)
            if len(out) >= limit:
                break
        return out
