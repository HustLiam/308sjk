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
import time
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import xml2st  # noqa: E402

from .consistency_check import consistency_check  # noqa: E402
from .config import MODEL, generation_options
from .patternlib import pattern_cards, render_cards  # noqa: E402

SKILL_PATH = Path(__file__).resolve().parent / "prompts" / "plcgen_skill.md"

# 混合模式规则（2026-09-24 负责人指令：确定性部分代码化，灵活部分 agent 化）——
# FB 库（INTERP/DRIVE402/MC_* 十二块）由代码逐字节拼装，LLM 只输出 PLC_PRG
HYBRID_RULES_V2 = r"""

## 混合生成模式 v2（本次任务的输出形态——重大变化）

FB 定义层与轴栈接线层已由代码确定性生成（变量声明/使能链/go/rel/hm/jog 调用/
插补引擎/驱动器/状态聚合——全部按 spec.axes 与 io_list 逐字展开）。
**你不要输出任何 XML、不要写变量声明、不要写轴实例调用。**

你只输出**工艺层逻辑**，单个 \`\`\`st 代码块（纯 ST 语句，无 PROGRAM/END 包装）。
可用的上下文（代码已提供，直接引用）：
- 全部 io_list 定位变量（run/cmd_draw/x_sp/x_fb/…）+ 轴目标总线 x_tgt/y_tgt/z_tgt
- 触发线 go_x_exe/hm_x_exe/…（置 TRUE 启动对应轴命令，完成自动清零）
- 完成信号 go_x.Done/ix_x.Busy/… + 汇总 all_oe/any_moving/move_done/fault_any
- 你自己声明的变量写在 ST 开头（VAR 块）——代码会并入变量区

工艺层职责：从 spec.constraints 与 trajectory 步表生成序列器 CASE/互锁条件/
模式切换。这是你唯一的输出——约 30~60 行，把预算留给逻辑正确性。
"""

_FENCE_RE = re.compile(r"```(?:xml)?\s*([\s\S]*?)```", re.IGNORECASE)
_PROJECT_RE = re.compile(r"<project[\s\S]*?</project>")


_ST_FENCE_RE = re.compile(r"```(?:st)?\s*([\s\S]*?)```", re.IGNORECASE)


def _extract_st(text):
    """从回复中提取 ```st 工艺层代码块；找不到返回 None。"""
    m = _ST_FENCE_RE.search(text)
    if not m:
        return None
    st = m.group(1).strip()
    return st if st and "PROGRAM" not in st.splitlines()[0] else None


def extract_xml(text):
    """从模型回复中提取 <project> XML；找不到返回 None。

    serve.py 部署端要求 body 以 <?xml 声明开头，而本函数按 <project> 片段
    提取会丢声明——统一补回（坑 P15：LLM 产物首次打到真部署端时暴露）。
    """
    m = _FENCE_RE.search(text)
    candidate = m.group(1) if m else text
    m2 = _PROJECT_RE.search(candidate)
    if not m2:
        return None
    project = m2.group(0)
    if not project.lstrip().startswith("<?xml"):
        project = '<?xml version="1.0" encoding="UTF-8"?>\n' + project
    return project


class PLCGenerator:
    """PLCopen XML 生成器。

    client: BigModelClient 实例；传入 seed_xml 时进入种子模式（不调 LLM，
    直接返回种子，用于编排器联调/回归——必须仍过双闸门）。
    """

    def __init__(self, client, model=MODEL, max_rounds=3, seed_xml=None,
                 generic_patterns_only=False, address_table=None, report=None,
                 no_cards=False, generation_mode="full_xml"):
        if generation_mode not in ("full_xml", "hybrid"):
            raise ValueError("generation_mode 必须为 full_xml 或 hybrid")
        self.generation_mode = generation_mode
        self.evidence_dir = None
        self.call_count = 0
        self.client = client
        self.model = model
        self.max_rounds = max_rounds
        self.seed_xml = Path(seed_xml).read_text(encoding="utf-8") if seed_xml else None
        self.seed_path = str(seed_xml) if seed_xml else None  # 策展登记用
        self.skill_prompt = SKILL_PATH.read_text(encoding="utf-8")
        # True=仅静态 CATALOG 通用原语（泛化验证：不借助自动策展的同构场景卡）
        self.generic_patterns_only = generic_patterns_only
        # True=完全不注入模式卡（自动化学习路线：LLM 只靠 skill 契约+硬规则+
        # 经验库相似度借鉴，杜绝 few-shot 直接套用）
        self.no_cards = no_cards
        # ⓪ 侧地址分配表（AML 通道 → %Q 地址）：设备契约，前置注入 prompt，
        # 让首次生成即按站约定表分配（错配仍由 R6 检查拦截）
        self.address_table = address_table or {}
        # 进度播报回调 report(msg: str)——生成期间实时向控制台回报（对话风格）
        self.report = report or (lambda msg: None)

    # ---------------- LLM 调用 ----------------
    def _call(self, messages, on_chunk=None, prefer_stream=False):
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 16384,
            "temperature": 0.1,   # 结构化代码生成：严格遵循骨架/规则优先（0.3 实测
                                  # 随机单点缺陷多，2026-09-23 训练循环调优）
            "thinking": {"type": "disabled"},  # 结构化代码生成不需要思考链
        }
        payload.update(generation_options(16384))
        if hasattr(self.client, "prepare_payload"):
            payload = self.client.prepare_payload(payload)
        self.call_count += 1
        evidence = Path(self.evidence_dir) if self.evidence_dir else None
        if evidence:
            evidence.mkdir(parents=True, exist_ok=True)
            (evidence / ("call_%03d.request.json" % self.call_count)).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        last_progress = time.monotonic()
        def progress(piece, count):
            nonlocal last_progress
            if on_chunk:
                on_chunk(piece, count)
            if time.monotonic() - last_progress >= 15:
                self.report("模型正在输出完整 XML（已收到 %d 字符）…" % count)
                last_progress = time.monotonic()
        try:
            data = self.client.chat_completions(payload, on_chunk=progress,
                                                prefer_stream=prefer_stream)
        except RuntimeError as exc:
            if "thinking" in str(exc) or "400" in str(exc):
                payload.pop("thinking")
                data = self.client.chat_completions(payload)
            else:
                raise
        reply = data["choices"][0]["message"].get("content") or ""
        if data["choices"][0].get("finish_reason") == "length":
            self.report("模型输出达到额度上限，拒绝使用截断工程；后续允许的重试将为完整交付物保留输出空间。")
            reply = "[output_limit] 上一回复因输出额度耗尽而截断，不是已确认的PLC逻辑错误；重新输出完整工程并保持全部原始工艺和验收要求。"
        if evidence:
            (evidence / ("call_%03d.response.json" % self.call_count)).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return reply

    # ---------------- 双闸门 ----------------
    def gate(self, xml_text, io_list):
        """闸门 a（xml2st 契约）+ 闸门 b（三方一致性）。返回 (ok, errors)。"""
        problems, _model = self._parse_text(xml_text)
        errors = ["[xml2st] %s" % p for p in problems]
        if errors:
            return False, errors
        ok, cproblems = consistency_check(xml_text, io_list)
        if not ok and any("[consistency]" not in p and "R2:" in p for p in cproblems):
            if "<address>" in xml_text:
                cproblems.append('定位地址必须写为 variable 的属性，例如 <variable name="run" address="%QX0.0">；禁止 <address> 子元素。')
        return ok, ["[consistency] %s" % p for p in cproblems if not p.startswith("SKIP")]

    @staticmethod
    def _parse_text(xml_text):
        import tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False, encoding="utf-8") as fh:
            fh.write(xml_text)
        try:
            return xml2st.parse(fh.name)
        finally:
            Path(fh.name).unlink(missing_ok=True)

    # ---------------- prompt 拼装 ----------------
    def build_messages(self, spec, feedback=None, trajectory=None, test_plan=None):
        io_list = spec.get("io_list", [])
        axes = spec.get("axes") or []
        csp = bool(axes) and all((a.get("io") or {}).get("cmd")
                                and not (a.get("io") or {}).get("v") for a in axes)
        if self.no_cards:
            cards = ""      # 自动化学习路线：不注入任何模式卡（经验库借鉴替代）
        else:
            cards = render_cards(pattern_cards(spec.get("task_goal", ""), io_list,
                                               include_curated=not self.generic_patterns_only))
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
        if axes and not csp:
            user += ("4. 顶层 axes 是轴参数权威表：go_<轴> 的 Velocity/Acceleration/"
                     "Deceleration 字面量与 ix_<轴> 的 POSWIN、越程界限，逐字取该轴 "
                     "defaults/poswin/stroke 的值，不得沿用示例值或自编；"
                     "io_list 条目的 address 即该变量的 AT 地址。\n")
        if trajectory:
            from .trajectory import summarize_for_prompt
            user += ("\n" + summarize_for_prompt(trajectory)
                     + "\n（步表是确定性规划的权威产物：坐标、顺序、笔态逐字落地，"
                       "不得增删路点或改动数值；插补 FB 参数按速度上限配置。）\n")
        if test_plan is not None:
            user += ("\n已由测试规划 Agent 生成并校验的测试计划：\n"
                     + json.dumps(test_plan, ensure_ascii=False, indent=2)
                     + "\n按实际激励顺序响应启动/复位；path_checks 是所要求的物理路径。"
                       "从反馈判断到位，不得伪造反馈或直接置完成灯。仍须输出完整PLCopen XML，"
                       "不要输出测试脚本，不得改变测试计划、工艺形状或路点。"
                       "绘图线段需要同步插补：两轴位移不等时，用同一进度计算两轴中间目标，"
                       "不要把终点阶跃给两轴独立限速而画成折线。插补速率低于轴限速，"
                       "每个顶点应等待真实反馈到位并保持至少100ms以供采样，抬笔到位后再返程。\n")
        if constraints:
            user += "约束清单：%s\n" % json.dumps(constraints, ensure_ascii=False)
        if feedback:
            user += ("\n--- 上一轮反馈（必须全部修复）---\n%s\n" % feedback)
        axes = spec.get("axes") or []
        csp = axes and all((a.get("io") or {}).get("cmd")
                           and not (a.get("io") or {}).get("v") for a in axes)
        system = (SKILL_PATH.with_name("plcgen_csp_skill.md").read_text(encoding="utf-8")
                  if csp else self.skill_prompt)
        system += ("\n\n通用扫描周期检查（所有设备适用）：每个状态变量在一次扫描中的所有赋值必须有明确优先级。"
                   "CASE 后的 IF/独立调度不得覆盖刚写入的退出或安全状态；内部步索引到末尾时必须存在可达出口。"
                   "到位布尔量若在目标赋值之前计算，不能拿旧目标的到位结果跳过新动作，应在下一扫描或按新目标反馈确认。"
                   "定时器必须每扫描调用并在新动作正确复位。完成条件须由实际反馈和全部收尾步骤满足后产生。"
                   "不得为了通过测试改变传感器/工艺状态的物理含义；只能输出完整 XML，不使用预制程序替代。")
        if not self.no_cards:
            system += "\n\n## 参考模式（已验收场景，勿照抄变量名）\n" + cards
        # 领域知识条目索引（P3 接线）：按任务选域节选——长尾知识打底，骨架仍在 skill
        try:
            from .knowledge.domain.loader import domain_digest
            digest = domain_digest(spec.get("task_goal", ""))
            if digest:
                system += "\n\n" + digest
        except Exception:  # 知识库缺失不阻塞生成
            pass
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return messages

    # ---------------- 主入口 ----------------
    def generate(self, spec, feedback=None, trajectory=None, experience_hint=None, test_plan=None):
        """生成并过闸。返回 {ok, xml, rounds, history, errors}。"""
        io_list = spec.get("io_list", [])

        if self.seed_xml is not None:  # 种子模式：不调 LLM，产物仍过双闸门
            self.report("使用已验收种子工程，正在过静态闸门…")
            ok, errors = self.gate(self.seed_xml, io_list)
            return {"ok": ok, "xml": self.seed_xml, "rounds": 0,
                    "history": [{"round": 0, "ok": ok, "errors": errors}], "errors": errors}

        messages = self.build_messages(spec, feedback, trajectory, test_plan)
        if experience_hint:
            messages[0]["content"] += (
                "\n\n## 历史经验要点（本站同类任务自动化学到的教训——生成时就遵守，"
                "不要等问题出现再修）\n%s" % experience_hint)
        hybrid = self.generation_mode == "hybrid" and bool(spec.get("axes"))
        # 无关——那是模式卡开关；hb4 实测画方走轨迹路线时 no_cards=True 误关 v2）
        if hybrid:
            messages[0]["content"] += HYBRID_RULES_V2
        history, errors = [], []
        for rnd in range(1, self.max_rounds + 1):
            self.report("正在联系模型%s生成%s…"
                        % ("第 %d 次，" % rnd if rnd > 1 else "",
                           "PLC_PRG 工艺层（FB 库由代码拼装）" if hybrid
                           else "完整 PLCopen 工程"))
            reply = self._call(messages, prefer_stream=True)
            self.report("模型回复完毕，正在提取工程并校验…")
            if hybrid:
                # v2：LLM 只出工艺层 ST（```st）。extract_xml 不再先行——hb3
                # 实测 LLM 回复带 XML 壳时 extract_xml 抢先成功绕过 v2 路径
                proc = _extract_st(reply)
                if proc is None:
                    errors = ["[extract] 未找到 ```st 工艺层代码块（不要输出 XML）"]
                else:
                    try:
                        from .stack_gen import merge_v2
                        xml = merge_v2(proc, spec)
                    except Exception as exc:
                        errors = ["[hybrid] 合并失败: %s" % exc]
                    else:
                        ok, errors = self.gate(xml, io_list)
                        if ok:
                            history.append({"round": rnd, "ok": True, "errors": []})
                            self.report("静态闸门通过（xml2st 契约 + 三方一致性），工程可用。")
                            return {"ok": True, "xml": xml, "rounds": rnd,
                                    "history": history, "errors": []}
                history.append({"round": rnd, "ok": False, "errors": list(errors)})
                self.report("校验发现 %d 处问题，正在带错误清单让模型修复…" % len(errors))
                messages.append({"role": "assistant", "content": reply})
                messages.append({"role": "user", "content": (
                    "校验失败，共 %d 处错误：\n%s\n\n"
                    "请修复后重新输出**工艺层 ST**（单个 ```st 代码块，不要 XML）。"
                    % (len(errors), "\n".join("- %s" % e for e in errors[:15]))
                )})
                continue
            xml = extract_xml(reply)
            if xml is None:
                errors = [reply] if reply.startswith("[output_limit]") else ["[extract] 回复中未找到 <project> XML（只输出一个 ```xml 代码块）"]
            else:
                ok, errors = self.gate(xml, io_list)
                if ok:
                    history.append({"round": rnd, "ok": True, "errors": []})
                    self.report("静态闸门通过（xml2st 契约 + 三方一致性），工程可用。")
                    return {"ok": True, "xml": xml, "rounds": rnd, "history": history, "errors": []}

            history.append({"round": rnd, "ok": False, "errors": list(errors)})
            self.report("校验发现 %d 处问题，正在带错误清单让模型修复…" % len(errors))
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": (
                "校验失败，共 %d 处错误：\n%s\n\n"
                "请修复全部错误后，重新输出%s（仍在单个 ```xml 代码块中）。"
                % (len(errors), "\n".join("- %s" % e for e in errors[:15]),
                   "**PLC_PRG 单 POU**" if hybrid else "**完整**的 PLCopen XML 工程")
            )})

        return {"ok": False, "xml": None, "rounds": self.max_rounds,
                "history": history, "errors": errors}

    # ---------------- 定向修复（LLM 迭代默认策略） ----------------
    def repair(self, previous_xml, spec, feedback, attempts=None, trajectory=None,
               experience_hint=None, test_plan=None):
        """定向修复：上一轮产物作 assistant 上下文 + 失败证据/归因的最小修改。

        三次实证（prog_id 补丁/画圆地址修复/spec refine）：远稳于从头重生成——
        重生成会随机丢失已修复项（温度采样），最小修改保真未提及内容。
        返回结构同 generate；静态双闸门照常把关，失败自动回灌（≤attempts 轮）。
        （experience_hint 仅首轮 fresh 使用，repair 走 match_lessons 失败匹配借鉴。）
        """
        attempts = attempts or max(2, self.max_rounds - 1)
        io_list = spec.get("io_list", [])
        messages = self.build_messages(spec, trajectory=trajectory, test_plan=test_plan)
        messages.append({"role": "assistant",
                         "content": "```xml\n%s\n```" % previous_xml})
        messages.append({"role": "user", "content": (
            "上一轮工程（见上）已通过静态契约校验（xml2st + 三方一致性），"
            "但在后续闸门失败。失败证据与归因：\n%s\n\n"
            "请做**最小修改**满足全部失败项：未提及的内容逐字保留；"
            "修复后重新输出**完整**工程（单个 ```xml 代码块）。" % (feedback or "（未提供明细）"))})

        history, errors = [], ["（未获得模型输出）"]
        for rnd in range(1, attempts + 1):
            self.report("正在让模型做定向修复%s（基于上一轮工程的最小修改）…"
                        % ("（第 %d 次）" % rnd if rnd > 1 else ""))
            reply = self._call(messages, prefer_stream=True)
            self.report("修复稿收到，正在提取并校验…")
            xml = extract_xml(reply)
            if xml is None:
                errors = [reply] if reply.startswith("[output_limit]") else ["[extract] 回复中未找到 <project> XML（只输出一个 ```xml 代码块）"]
            else:
                ok, errors = self.gate(xml, io_list)
                if ok:
                    history.append({"round": rnd, "ok": True, "errors": []})
                    self.report("修复稿通过静态闸门。")
                    return {"ok": True, "xml": xml, "rounds": rnd,
                            "history": history, "errors": [], "mode": "repair"}
            history.append({"round": rnd, "ok": False, "errors": list(errors)})
            self.report("修复稿仍有 %d 处问题，继续回灌…" % len(errors))
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": (
                "修复后工程仍有 %d 处静态校验错误：\n%s\n\n"
                "请修复后重新输出完整工程（最小修改，其余保持不变）。"
                % (len(errors), "\n".join("- %s" % e for e in errors[:15])))})
        return {"ok": False, "xml": None, "rounds": attempts,
                "history": history, "errors": errors, "mode": "repair"}
