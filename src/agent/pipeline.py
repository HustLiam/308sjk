#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PLC 代码生成器（② 的 LLM 本体，框架沿用 308sjk_history/agent/pipeline.py）。

生成-校验回灌循环：
  1. system = plcgen_skill 提示词 + 模式卡（模式库按需求关键词选取）；
  2. 模型输出 PLCopen XML -> 提取 -> 双闸门：
       闸门 a: lx 的 xml2st 契约校验（本地毫秒级，--check 语义）；
       闸门 b: 三方一致性检查（XML 定位变量 ≡ io_list）；
  3. 失败则把结构化错误清单回灌到同一对话要求修复，直到通过或达轮数上限。
"""

import json
import re
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import xml2st  # noqa: E402

from .consistency_check import consistency_check  # noqa: E402
from .patternlib import pattern_cards, render_cards  # noqa: E402

SKILL_PATH = Path(__file__).resolve().parent / "prompts" / "plcgen_skill.md"

_FENCE_RE = re.compile(r"```(?:xml)?\s*([\s\S]*?)```", re.IGNORECASE)
_PROJECT_RE = re.compile(r"<project[\s\S]*?</project>")


def extract_xml(text):
    """从模型回复中提取 <project> XML；找不到返回 None。"""
    m = _FENCE_RE.search(text)
    candidate = m.group(1) if m else text
    m2 = _PROJECT_RE.search(candidate)
    return m2.group(0) if m2 else None


class PLCGenerator:
    """PLCopen XML 生成器。

    client: BigModelClient 实例；传入 seed_xml 时进入种子模式（不调 LLM，
    直接返回种子，用于编排器联调/回归——必须仍过双闸门）。
    """

    def __init__(self, client, model="glm-5.3", max_rounds=3, seed_xml=None):
        self.client = client
        self.model = model
        self.max_rounds = max_rounds
        self.seed_xml = Path(seed_xml).read_text(encoding="utf-8") if seed_xml else None
        self.skill_prompt = SKILL_PATH.read_text(encoding="utf-8")

    # ---------------- LLM 调用 ----------------
    def _call(self, messages):
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 16384,
            "temperature": 0.3,
            "thinking": {"type": "disabled"},  # 结构化代码生成不需要思考链
        }
        try:
            data = self.client.chat_completions(payload)
        except RuntimeError as exc:
            if "thinking" in str(exc) or "400" in str(exc):
                payload.pop("thinking")
                data = self.client.chat_completions(payload)
            else:
                raise
        return data["choices"][0]["message"].get("content") or ""

    # ---------------- 双闸门 ----------------
    def gate(self, xml_text, io_list):
        """闸门 a（xml2st 契约）+ 闸门 b（三方一致性）。返回 (ok, errors)。"""
        problems, _model = self._parse_text(xml_text)
        errors = ["[xml2st] %s" % p for p in problems]
        if errors:
            return False, errors
        ok, cproblems = consistency_check(xml_text, io_list)
        return ok, ["[consistency] %s" % p for p in cproblems if not p.startswith("SKIP")]

    @staticmethod
    def _parse_text(xml_text):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as fh:
            fh.write(xml_text)
        return xml2st.parse(fh.name)

    # ---------------- prompt 拼装 ----------------
    def build_messages(self, spec, feedback=None):
        io_list = spec.get("io_list", [])
        cards = render_cards(pattern_cards(spec.get("task_goal", ""), io_list))
        io_rendered = json.dumps(io_list, ensure_ascii=False, indent=2)
        constraints = spec.get("constraints", [])
        user = (
            "需求规格（requirement_spec）：\n"
            + json.dumps(spec, ensure_ascii=False, indent=2)
            + "\n\n落地要求：\n"
            "1. io_list 的 %d 个变量全部落为 AT 定位变量，名称逐字一致、位宽遵守 BOOL↔%%QX / INT↔%%QW、地址不冲突；\n"
            "2. 每条约束都体现在逻辑中；acceptance 的时序/联锁在逻辑上可达；\n"
            "3. constraints 中的时序值（如延时 300ms）用 TON 的 PT 落地。\n" % len(io_list)
        )
        if constraints:
            user += "约束清单：%s\n" % json.dumps(constraints, ensure_ascii=False)
        if feedback:
            user += ("\n--- 上一轮反馈（必须全部修复）---\n%s\n" % feedback)
        messages = [
            {"role": "system", "content": self.skill_prompt + "\n\n## 参考模式（已验收场景，勿照抄变量名）\n" + cards},
            {"role": "user", "content": user},
        ]
        return messages

    # ---------------- 主入口 ----------------
    def generate(self, spec, feedback=None):
        """生成并过闸。返回 {ok, xml, rounds, history, errors}。"""
        io_list = spec.get("io_list", [])

        if self.seed_xml is not None:  # 种子模式：不调 LLM，产物仍过双闸门
            ok, errors = self.gate(self.seed_xml, io_list)
            return {"ok": ok, "xml": self.seed_xml, "rounds": 0,
                    "history": [{"round": 0, "ok": ok, "errors": errors}], "errors": errors}

        messages = self.build_messages(spec, feedback)
        history, errors = [], []
        for rnd in range(1, self.max_rounds + 1):
            reply = self._call(messages)
            xml = extract_xml(reply)
            if xml is None:
                errors = ["[extract] 回复中未找到 <project> XML（只输出一个 ```xml 代码块）"]
            else:
                ok, errors = self.gate(xml, io_list)
                if ok:
                    history.append({"round": rnd, "ok": True, "errors": []})
                    return {"ok": True, "xml": xml, "rounds": rnd, "history": history, "errors": []}

            history.append({"round": rnd, "ok": False, "errors": list(errors)})
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": (
                "校验失败，共 %d 处错误：\n%s\n\n"
                "请修复全部错误后，重新输出**完整**的 PLCopen XML 工程（仍在单个 ```xml 代码块中）。"
                % (len(errors), "\n".join("- %s" % e for e in errors[:15]))
            )})

        return {"ok": False, "xml": None, "rounds": self.max_rounds,
                "history": history, "errors": errors}
