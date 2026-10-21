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
from .xml_normalization import normalize_program_io

SKILL_PATH = Path(__file__).resolve().parent / "prompts" / "plcgen_skill.md"

_FENCE_RE = re.compile(r"```(?:xml)?\s*([\s\S]*?)```", re.IGNORECASE)
_PROJECT_RE = re.compile(r"<project[\s\S]*?</project>")


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
                 no_cards=False, generation_mode="full_xml", position_mode=False, prog_id=2):
        if generation_mode != "full_xml":
            raise ValueError("已恢复更新前完整 XML 生成流程，不支持 hybrid 模式")
        self.generation_mode = generation_mode
        self.evidence_dir = None
        self.call_count = 0
        self._failed_candidates = set()
        self.stop_reason = None
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
        self.position_mode = position_mode
        self.prog_id = prog_id
        # 进度播报回调 report(msg: str)——生成期间实时向控制台回报（对话风格）
        self.report = report or (lambda msg: None)

    # ---------------- LLM 调用 ----------------
    def _call(self, messages, on_chunk=None, prefer_stream=False):
        payload = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 16384,
            "temperature": 0.3,   # 恢复远端更新前的 PLC 生成采样参数
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
        errors = ["[consistency] %s" % p for p in cproblems if not p.startswith("SKIP")]
        if self.position_mode:
            from .position_control import position_program_problems
            errors += ['[CSP] '+p for p in position_program_problems(xml_text,self.address_table,self.prog_id,io_list)]
            from .position_control import svg_program_problems
            errors += ['[SVG] '+p for p in svg_program_problems(xml_text,getattr(self,'_trajectory',None))]
        return ok and not errors, errors

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
        if trajectory:
            from .trajectory import summarize_for_prompt
            user += ("\n" + summarize_for_prompt(trajectory)
                     + "\n（步表是确定性规划的权威产物：坐标、顺序、笔态逐字落地，"
                       "不得增删路点或改动数值；插补 FB 参数按速度上限配置。）\n")
        if constraints:
            user += "约束清单：%s\n" % json.dumps(constraints, ensure_ascii=False)
        if self.address_table:
            user += '\nAML 定位地址（逐字遵守）：\n'+json.dumps(self.address_table,ensure_ascii=False,indent=2)
        if self.position_mode:
            from .position_control import position_scales
            scales=position_scales(spec.get('io_list',[]))
            user += ('\n本任务使用外部驱动闭位置环的 CSP 位置输出。保留原 MC/INTERP 轴栈与 SVG 步序。'
                'x_cmd/y_cmd/z_cmd 为实际驱动输入，逐扫描直接输出原 dv_x/y/z.Setpoint 输入使用的同一位置量：'
                'INT 型直接赋值：x_cmd := ix_x.Setpoint; y_cmd := ix_y.Setpoint; z_cmd := ix_z.Setpoint; '
                '若有 x_interp/y_interp/z_interp 点动位置选择则使用该位置。'
                '只有位置量是 REAL 时才 REAL_TO_INT，按 AML 地址绑定。'
                'run/all_oe 未使能、quickstop 或 fault_any 时，cmd 写相应 fb 保持当前位置。'
                'x_v/y_v/z_v 保留诊断用途，不能连接到 cmd，也不能把 HMI 的 sp 直接当插补输出。'
                'DRIVE402 的 v_cmd 仅保留状态/诊断，不经速度通道驱动仿真。'
                'prog_id 必须为 AT %QW20 的 INT，每扫描写 '+str(self.prog_id)+'。\n')
            if any(v!=1 for v in scales.values()):
                user+='\n高精度编码优先于上述通用规则：'+str(scales)+'。cmd 输出必须直接取 ix_x.pos/ix_y.pos/ix_z.posz 的 REAL 状态乘倍率后 REAL_TO_INT，禁止先取已取整的 Setpoint。停止保持也须将 fb 乘倍率。SVG 目标与 pos_target 使用 REAL，保留全部小数路点。\n'
                user+='首次 XY 定位前必须先原地抬笔，待 Z 到位再横移；空闲使能期间保持 fb，防旧插补状态拉回原点。XY 线段需共享进度，按各轴位移比例缩放速度与加减速；不能让短轴先到导致曲线偏折。遵守 AML 的 0.01 到位窗口，REAL 比较使用 100.0/0.0 等实数常量，与 MC 的 INT/IN_OUT 边界显式转换。\n'
        if test_plan is not None:
            user += "\n本任务测试计划（保持工艺与判据不变）：\n"+json.dumps(test_plan,ensure_ascii=False,indent=2)
        if feedback:
            user += ("\n--- 上一轮反馈（必须全部修复）---\n%s\n" % feedback)
        system = self.skill_prompt
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
        self._trajectory = trajectory if self.seed_xml is None else None
        io_list = spec.get("io_list", [])

        if self.seed_xml is not None:  # 种子模式：不调 LLM，产物仍过双闸门
            self.report("使用已验收种子工程，正在过静态闸门…")
            ok, errors = self.gate(self.seed_xml, io_list)
            return {"ok": ok, "xml": self.seed_xml, "rounds": 0,
                    "history": [{"round": 0, "ok": ok, "errors": errors}], "errors": errors}

        messages = self.build_messages(spec, feedback, trajectory, test_plan=test_plan)
        if experience_hint:
            messages[0]["content"] += (
                "\n\n## 历史经验要点（本站同类任务自动化学到的教训——生成时就遵守，"
                "不要等问题出现再修）\n%s" % experience_hint)
        history, errors = [], []
        for rnd in range(1, self.max_rounds + 1):
            self.report("正在联系模型%s生成完整 PLCopen 工程…"
                        % ("第 %d 次，" % rnd if rnd > 1 else ""))
            reply = self._call(messages, prefer_stream=True)
            self.report("模型回复完毕，正在提取工程并校验…")
            xml = extract_xml(reply)
            if xml is not None:
                xml, moved = normalize_program_io(xml)
                if moved:
                    self.report("已修正 %d 个程序定位 IO 的声明分组，工艺逻辑保持原样。" % moved)
            if xml is None:
                errors = [reply] if reply.startswith("[output_limit]") else ["[extract] 回复中未找到 <project> XML（只输出一个 ```xml 代码块）"]
            else:
                ok, errors = self.gate(xml, io_list)
                if ok:
                    history.append({"round": rnd, "ok": True, "errors": []})
                    self.report("静态闸门通过（xml2st 契约 + 三方一致性），工程可用。")
                    return {"ok": True, "xml": xml, "rounds": rnd, "history": history, "errors": []}

            history.append({"round": rnd, "ok": False, "errors": list(errors)})
            from .repair_feedback import static_feedback
            details = static_feedback(self, errors, xml)
            self.report("校验发现 %d 处问题，正在带错误清单让模型修复…" % len(errors))
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": (
                "校验失败，共 %d 处错误：\n%s\n\n"
                "请修复全部错误后，重新输出**完整**的 PLCopen XML 工程（仍在单个 ```xml 代码块中）。"
                % (len(errors), details)
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
        self.repair_base_used = previous_xml
        self._trajectory = trajectory
        io_list = spec.get("io_list", [])
        messages = self.build_messages(spec, trajectory=trajectory, test_plan=test_plan)
        messages.append({"role": "assistant",
                         "content": "```xml\n%s\n```" % previous_xml})
        messages.append({"role": "user", "content": (
            "上一轮工程（见上）是本轮修复基底，失败闸门以证据为准。"
            "修复后须重新通过静态契约、编译与验收。失败证据与归因：\n%s\n\n"
            "请做**最小修改**满足全部失败项：未提及的内容逐字保留；"
            "修复后重新输出**完整**工程（单个 ```xml 代码块）。" % (feedback or "（未提供明细）"))})

        history, errors = [], ["（未获得模型输出）"]
        for rnd in range(1, attempts + 1):
            self.report("正在让模型做定向修复%s（基于上一轮工程的最小修改）…"
                        % ("（第 %d 次）" % rnd if rnd > 1 else ""))
            reply = self._call(messages, prefer_stream=True)
            self.report("修复稿收到，正在提取并校验…")
            xml = extract_xml(reply)
            if xml is not None:
                xml, moved = normalize_program_io(xml)
                if moved:
                    self.report("已修正 %d 个程序定位 IO 的声明分组，工艺逻辑保持原样。" % moved)
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
            from .repair_feedback import static_feedback
            details = static_feedback(self, errors, xml)
            self.report("修复稿仍有 %d 处问题，继续回灌…" % len(errors))
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": (
                "修复后工程仍有 %d 处静态校验错误：\n%s\n\n"
                "请修复后重新输出完整工程（最小修改，其余保持不变）。"
                % (len(errors), details))})
        return {"ok": False, "xml": None, "rounds": attempts,
                "history": history, "errors": errors, "mode": "repair"}
