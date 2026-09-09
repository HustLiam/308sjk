#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
归因引擎（gc 文档 §3.2 的落地 v0：确定性 KB 优先、LLM 兜底）。

红线（主方案 §3.5）：归因**不改变 PASS/FAIL 裁定**——闸门结论永远确定性；
归因输出只进反馈包，为下一轮生成提供修复方向。

分层策略：
  1. 确定性：错误文本 → memory.match_pitfalls（避坑知识库签名匹配，毫秒级）
     + memory.match_fixes（跨会话修复对）——绝大多数失败是已知模式；
  2. LLM 兜底：KB 未命中且配置了 client 时，小上下文诊断一次（结构化输出
     root_cause/repair_hints），仍标注 advisory。
"""

import json
import re

from .memory import MemoryStore

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)

_DIAG_PROMPT = """你是工业 PLC 代码生成闭环的归因工程师。闸门「%s」失败，错误证据如下：

%s

当前知识库没有命中已知模式。请输出 JSON（单个 ```json 代码块）：
{
  "root_cause": "一句话根因（代码问题/场景问题/环境问题 三选一开头）",
  "repair_hints": ["给下一轮生成的具体修复指令，1~3 条，可执行"]
}
不确定就写"证据不足"。只依据证据，不臆测。"""


class AttributionEngine:
    def __init__(self, memory=None, client=None, model="glm-5.3", enabled=True):
        self.memory = memory or MemoryStore()
        self.client = client
        self.model = model
        self.enabled = enabled

    # ---------------- 主入口 ----------------
    def attribute(self, gate, errors, context=None):
        """返回归因字典（供反馈包与 gate.json 留档）。"""
        errors = [str(e) for e in errors]
        result = {"gate": gate, "errors": errors[:8]}
        if not self.enabled:
            result["disabled"] = True
            return result

        pitfalls = self.memory.match_pitfalls(errors)
        fixes = self.memory.match_fixes(errors)
        result["pitfalls"] = [{"id": p["id"], "title": p["title"],
                               "diagnosis": p["diagnosis"], "fix": p["fix"]}
                              for p in pitfalls]
        result["similar_fixes"] = [{"gate": f.get("gate"), "action": f.get("action"),
                                    "outcome": f.get("outcome")} for f in fixes]

        result["repair_hints"] = [p["fix"] for p in pitfalls]
        if fixes:
            result["repair_hints"] += ["历史成功修复：%s" % f.get("action") for f in fixes]

        if not pitfalls and not fixes and self.client is not None and errors:
            llm = self._diagnose(gate, errors, context)
            if llm:
                result["llm_diagnosis"] = llm
                result["repair_hints"] += llm.get("repair_hints", [])
        return result

    # ---------------- LLM 兜底 ----------------
    def distill_lesson(self, gate, errors, resolution=None, diff_hunks=None):
        """战役级经验提炼（自动化学习 B 机制）：失败证据 → 泛化经验。

        输入：同质失败族的代表证据 +（可选）结局说明与修复骨架；
        输出 {"diagnosis", "fix"}（advisory，进经验库供相似度检索）。
        无 client / 解析失败返回 None（学习退化不阻塞主流程）。
        """
        if not (self.enabled and self.client and errors):
            return None
        evidence = "\n".join("- %s" % str(e)[:300] for e in errors[:10])
        context = ""
        if resolution:
            context += "\n结局：%s" % str(resolution)[:300]
        if diff_hunks:
            context += "\n修复变更骨架：\n%s" % "\n".join(str(h)[:400] for h in diff_hunks[:4])
        prompt = (
            "你是 PLC 生成闭环的知识蒸馏工程师。闸门「%s」出现连续同质失败：\n\n%s%s\n\n"
            "请把它提炼为一条**可复用的泛化经验**（不是针对本工程的补丁），输出 JSON"
            "（单个 ```json 代码块）：\n"
            '{"diagnosis": "这类失败的症状与根因（一句话，面向未来同类任务）",\n'
            ' "fix": "修法（面向生成器的可执行指令，1~3 条）"}\n'
            "只依据证据，不臆测；证据不足就写证据不足。" % (gate, evidence, context))
        payload = {"model": self.model,
                   "messages": [{"role": "user", "content": prompt}],
                   "max_tokens": 1024, "temperature": 0.2,
                   "thinking": {"type": "disabled"}}
        try:
            data = self.client.chat_completions(payload)
        except RuntimeError as exc:
            if "thinking" in str(exc) or "400" in str(exc):
                payload.pop("thinking")
                try:
                    data = self.client.chat_completions(payload)
                except RuntimeError:
                    return None
            else:
                return None
        text = (data["choices"][0]["message"].get("content") or "")
        m = _FENCE_RE.search(text)
        candidate = m.group(1) if m else text
        start = candidate.find("{")
        if start < 0:
            return None
        try:
            out = json.loads(candidate[start:candidate.rfind("}") + 1])
        except ValueError:
            return None
        if out.get("diagnosis") and out.get("fix"):
            return {"diagnosis": str(out["diagnosis"]), "fix": str(out["fix"])}
        return None
    def _diagnose(self, gate, errors, context=None):
        evidence = "\n".join("- %s" % e[:300] for e in errors[:10])
        if context:
            evidence = "上下文：%s\n%s" % (str(context)[:400], evidence)
        payload = {"model": self.model,
                   "messages": [{"role": "user",
                                 "content": _DIAG_PROMPT % (gate, evidence)}],
                   "max_tokens": 1024, "temperature": 0.2,
                   "thinking": {"type": "disabled"}}
        try:
            data = self.client.chat_completions(payload)
        except RuntimeError as exc:
            if "thinking" in str(exc) or "400" in str(exc):
                payload.pop("thinking")
                try:
                    data = self.client.chat_completions(payload)
                except RuntimeError:
                    return None
            else:
                return None
        text = (data["choices"][0]["message"].get("content") or "")
        m = _FENCE_RE.search(text)
        candidate = m.group(1) if m else text
        start = candidate.find("{")
        if start < 0:
            return None
        try:
            out = json.loads(candidate[start:candidate.rfind("}") + 1])
        except ValueError:
            return None
        out["advisory"] = True   # LLM 结论仅供提示，不参与裁定
        return out

    # ---------------- 反馈渲染 ----------------
    @staticmethod
    def format_feedback(attribution):
        """归因 → 反馈包片段（拼在错误证据之后）。"""
        chunks = []
        for p in attribution.get("pitfalls", []):
            chunks.append("▣ 已知坑 %s %s：%s → 修法：%s"
                          % (p["id"], p["title"], p["diagnosis"], p["fix"]))
        for f in attribution.get("similar_fixes", []):
            chunks.append("▣ 历史相似修复（%s，结果 %s）：%s"
                          % (f.get("gate"), f.get("outcome"), f.get("action")))
        if attribution.get("llm_diagnosis"):
            d = attribution["llm_diagnosis"]
            chunks.append("▣ LLM 兜底归因（advisory）：%s" % d.get("root_cause", ""))
            for h in d.get("repair_hints", [])[:3]:
                chunks.append("   - %s" % h)
        return "\n".join(chunks)
