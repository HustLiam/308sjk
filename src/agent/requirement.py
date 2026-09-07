#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
① 需求理解模块（gc 文档 §2 的实现，主方案 §3.1）。

输入：用户自然语言指令 + ⓪ 的 device_model（可选）；
输出：requirement_spec（通过 spec_validator 契约校验）+ 组装报告。

三种工作形态：
  · LLM 模式（client 传入）：规格主体由 LLM 组织，io_list **逐字锚定**设备模型
    预填（主方案 §3.1："LLM 校验和补充而非从零生成"——本实现收紧为不得增删改
    name/dir/type/range，工艺语义只在 device 描述层润色），校验失败回灌修复，
    直到通过或达轮数上限；
  · 模板模式（无 client，离线回归）：io_list 预填 + task_goal=用户原文 + 最小
    acceptance（sim_health 兜底）——结构合法但工艺空白，供 CI 冒烟；
  · 澄清问题：build_io_list 的 pending（如 INT 缺量程）随报告返回（人工介入点 1
    的机器侧形态，LLM 多轮澄清后续接入）。

用法:
    from agent.requirement import RequirementUnderstander
    u = RequirementUnderstander(client=BigModelClient(key))
    result = u.understand("三轴绘图仪：抬笔→定位→落笔画正方形→回中心",
                          device_model=model)
    spec, report = result["spec"], result["report"]

CLI 入口: python -m src.agent.requirement request.txt --aml station.aml [--out spec.json]
"""

import json
import re
from pathlib import Path

from .aml_parser import build_io_list
from .spec_validator import TASK_ID_RE, validate_requirement_spec

SKILL_PATH = Path(__file__).resolve().parent / "prompts" / "specgen_skill.md"
SCHEMA_VERSION = "1.0.0-draft.3"

_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)```", re.IGNORECASE)


def extract_json(text):
    """从模型回复中提取 JSON 对象文本；找不到返回 None。"""
    m = _FENCE_RE.search(text)
    candidate = m.group(1) if m else text
    start = candidate.find("{")
    if start < 0:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(candidate)):
        ch = candidate[i]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return candidate[start:i + 1]
    return None


def _derive_task_id(device_model, fallback="task"):
    """station 名派生 task_id（Plotter3AxisStation → plotter3axis）；不合法则用兜底。"""
    station = (device_model or {}).get("station") or ""
    stem = re.sub(r"(?i)station$", "", station)
    candidate = re.sub(r"(?<!^)(?=[A-Z])", "_", stem).lower()
    candidate = re.sub(r"(?<=\d)_", "", candidate).strip("_")  # plotter3_axis → plotter3axis
    return candidate if TASK_ID_RE.match(candidate) else fallback


def _device_summary(device_model):
    """device_model 的 LLM 可读摘要（不注入全量 JSON，控制上下文规模）。"""
    if not device_model:
        return "（未提供设备模型——纯自然语言需求，io_list 由需求文本推断）"
    axes = device_model.get("kinematics", {}).get("axes", [])
    axis_lines = [
        "  - %s: %s，行程 %s～%s %s，VMAX=%s，ACCEL=%s，POSWIN=%s" % (
            a.get("axis"), a.get("type"), (a.get("stroke") or [None, None])[0],
            (a.get("stroke") or [None, None])[1], a.get("unit") or "",
            a.get("vmax"), a.get("accel"), a.get("poswin"))
        for a in axes]
    return "\n".join([
        "station: %s" % device_model.get("station"),
        "设备: %s" % ", ".join(d.get("name", "?") for d in device_model.get("devices", [])),
        "拓扑连接: %s" % ", ".join("%s↔%s" % (l.get("a"), l.get("b"))
                                    for l in device_model.get("topology", {}).get("links", [])),
        "轴参数：", *axis_lines,
    ])


def _anchor_problems(spec, io_items):
    """io_list 锚定检查：与设备模型预填逐字一致（name/dir/type/range 四元组）。"""
    problems = []
    prefill = {p["name"]: p for p in io_items}
    got = {}
    for idx, item in enumerate(spec.get("io_list") or []):
        name = item.get("name")
        got[name] = item
        base = prefill.get(name)
        if base is None:
            problems.append("io_list[%d] %r: 不在设备模型预填中——禁止发明 IO"
                            % (idx, name))
            continue
        for key in ("dir", "type", "range"):
            if item.get(key) != base.get(key):
                problems.append("io_list[%d] %r: %s 必须逐字保留预填值 %r（实际 %r）"
                                % (idx, name, key, base.get(key), item.get(key)))
    for name in prefill:
        if name not in got:
            problems.append("io_list 缺少设备模型点位 %r（预填条目不得删减）" % name)
    return problems


class RequirementUnderstander:
    """① 需求理解：自然语言 + 设备模型 → requirement_spec。"""

    def __init__(self, client=None, model="glm-5.3", max_rounds=3):
        self.client = client
        self.model = model
        self.max_rounds = max_rounds
        self.skill_prompt = SKILL_PATH.read_text(encoding="utf-8")

    # ---------------- LLM 调用 ----------------
    def _call(self, messages):
        payload = {"model": self.model, "messages": messages, "max_tokens": 8192,
                   "temperature": 0.2, "thinking": {"type": "disabled"}}
        try:
            data = self.client.chat_completions(payload)
        except RuntimeError as exc:
            if "thinking" in str(exc) or "400" in str(exc):
                payload.pop("thinking")
                data = self.client.chat_completions(payload)
            else:
                raise
        return data["choices"][0]["message"].get("content") or ""

    def _build_messages(self, request_text, io_items, device_summary, task_id, feedback=None):
        user = [
            "用户需求（自然语言）：", request_text, "",
            "设备模型摘要（⓪ 确定性解析）：", device_summary, "",
            "io_list 预填（设备模型点位，逐字锚定）：",
            json.dumps(io_items, ensure_ascii=False, indent=2), "",
            "task_id 建议：%s（可改，须 ^[a-z][a-z0-9_]*$）" % task_id,
        ]
        if feedback:
            user += ["", "--- 上一轮反馈（必须全部修复）---", feedback]
        return [{"role": "system", "content": self.skill_prompt},
                {"role": "user", "content": "\n".join(user)}]

    # ---------------- 模板模式（离线） ----------------
    def _template_spec(self, request_text, io_items, device_model, task_id):
        goal = request_text.strip()
        station = (device_model or {}).get("station")
        if station:
            goal = "%s（设备站：%s）" % (goal, station)
        return {
            "schema_version": SCHEMA_VERSION,
            "task_id": task_id,
            "task_goal": goal,
            "io_list": io_items,
            "constraints": [],
            "acceptance": [{"id": "AC1", "desc": "仿真无发散、各轴行程未越界",
                            "type": "sim_health"}],
        }

    # ---------------- 主入口 ----------------
    def understand(self, request_text, device_model=None, task_id=None):
        """返回 {spec, report}。spec 为 None 表示组装失败（报告含全部问题）。"""
        io_items, pending = ([], [])
        if device_model:
            io_items, pending = build_io_list(device_model)
        task_id = task_id or _derive_task_id(device_model)

        if self.client is None:  # 模板模式
            spec = self._template_spec(request_text, io_items, device_model, task_id)
            problems = validate_requirement_spec(spec)
            return {"spec": spec if not problems else None,
                    "report": {"mode": "template", "problems": problems, "pending": pending}}

        messages = self._build_messages(
            request_text, io_items, _device_summary(device_model), task_id)
        history, problems = [], ["（未获得模型输出）"]
        for rnd in range(1, self.max_rounds + 1):
            reply = self._call(messages)
            text = extract_json(reply)
            if text is None:
                problems = ["[extract] 回复中未找到 JSON 对象（只输出一个 ```json 代码块）"]
            else:
                try:
                    spec = json.loads(text)
                except ValueError as exc:
                    problems = ["[json] 解析失败: %s" % exc]
                else:
                    problems = validate_requirement_spec(spec)
                    if io_items:
                        problems += ["[anchor] %s" % p for p in _anchor_problems(spec, io_items)]
                    if not problems:
                        history.append({"round": rnd, "ok": True})
                        return {"spec": spec, "report": {
                            "mode": "llm", "rounds": rnd, "history": history,
                            "pending": pending}}
            history.append({"round": rnd, "ok": False, "problems": list(problems)})
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": (
                "规格校验失败，共 %d 处问题：\n%s\n\n请修复全部问题后重新输出**完整** "
                "requirement_spec JSON（仍在单个 ```json 代码块中）。"
                % (len(problems), "\n".join("- %s" % p for p in problems[:15])))})

        return {"spec": None, "report": {"mode": "llm", "rounds": self.max_rounds,
                                         "history": history, "problems": problems,
                                         "pending": pending}}

    # ---------------- 修正回合（人工介入点 1 的对话形态） ----------------
    def refine(self, spec, request_text, device_model=None, correction=None):
        """用户对上轮规格的修正：上轮 spec 作 assistant 上下文 + 修正指令，
        **定向最小修改**（实测远稳于从头重生成——同 prog_id 补丁的经验）。

        返回同 understand 的 {spec, report}；io_list 锚定与契约校验照常执行，
        失败自动回灌（≤max_rounds 轮）。
        """
        if self.client is None:
            return {"spec": None, "report": {"mode": "template", "problems":
                    ["修正回合需要 LLM（未配置 client）"]}}
        io_items, pending = ([], [])
        if device_model:
            io_items, pending = build_io_list(device_model)
        messages = self._build_messages(
            request_text, io_items, _device_summary(device_model),
            (spec or {}).get("task_id") or _derive_task_id(device_model))
        messages.append({"role": "assistant",
                         "content": "```json\n%s\n```" % json.dumps(spec, ensure_ascii=False)})
        messages.append({"role": "user", "content": (
            "用户对上轮规格提出修正：\n%s\n\n请做**最小修改**满足修正：未提及的内容"
            "逐字保留；io_list 仍逐字锚定预填；验收准则保持四类封闭结构与健全谓词。"
            "重新输出完整 requirement_spec JSON（单个 ```json 代码块）。" % correction)})

        history, problems = [], ["（未获得模型输出）"]
        for rnd in range(1, self.max_rounds + 1):
            reply = self._call(messages)
            text = extract_json(reply)
            if text is None:
                problems = ["[extract] 回复中未找到 JSON 对象"]
            else:
                try:
                    new_spec = json.loads(text)
                except ValueError as exc:
                    problems = ["[json] 解析失败: %s" % exc]
                else:
                    problems = validate_requirement_spec(new_spec)
                    if io_items:
                        problems += ["[anchor] %s" % p for p in _anchor_problems(new_spec, io_items)]
                    if not problems:
                        history.append({"round": rnd, "ok": True})
                        return {"spec": new_spec, "report": {
                            "mode": "llm-refine", "rounds": rnd, "history": history,
                            "pending": pending}}
            history.append({"round": rnd, "ok": False, "problems": list(problems)})
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": (
                "修正后规格校验失败，共 %d 处问题：\n%s\n\n请修复后重新输出完整 JSON。"
                % (len(problems), "\n".join("- %s" % p for p in problems[:15])))})
        return {"spec": None, "report": {"mode": "llm-refine", "rounds": self.max_rounds,
                                         "history": history, "problems": problems,
                                         "pending": pending}}


def main():
    import argparse
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from .aml_parser import parse_aml
    from .client import BigModelClient
    from .config import MODEL, get_api_key

    parser = argparse.ArgumentParser(description="① 需求理解：AML+自然语言 → requirement_spec")
    parser.add_argument("request", help="自然语言需求文本，或 .txt 文件路径")
    parser.add_argument("--aml", default=None, help="AutomationML 设备描述文件（⓪ 输入）")
    parser.add_argument("--out", default=None, help="输出 spec JSON 路径（默认打印）")
    parser.add_argument("--task-id", default=None)
    args = parser.parse_args()

    req = Path(args.request)
    request_text = req.read_text(encoding="utf-8").strip() if req.is_file() else args.request

    device_model = None
    if args.aml:
        device_model, problems = parse_aml(args.aml)
        if problems:
            print("AML 解析存在问题（best-effort 继续）：")
            for p in problems:
                print("  - %s" % p)

    client = BigModelClient(get_api_key()) if get_api_key() else None
    print("模式：%s" % ("llm" if client else "template（无 API Key）"))
    u = RequirementUnderstander(client=client, model=MODEL)
    result = u.understand(request_text, device_model=device_model, task_id=args.task_id)
    report = result["report"]
    if report.get("pending"):
        print("待澄清（人工介入点 1）：")
        for p in report["pending"]:
            print("  - %s" % p)
    if result["spec"] is None:
        print("规格组装失败：")
        for p in (report.get("problems") or report.get("history", [{}])[-1].get("problems", [])):
            print("  - %s" % p)
        return 1
    out = json.dumps(result["spec"], ensure_ascii=False, indent=2)
    if args.out:
        Path(args.out).write_text(out + "\n", encoding="utf-8")
        print("已生成: %s（rounds=%s）" % (args.out, report.get("rounds", 0)))
    else:
        print(out)
    return 0


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
