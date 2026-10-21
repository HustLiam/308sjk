#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端闭环编排器（solve 循环的权威实现骨架，gc 文档 §4）。

当前形态：**半环**（不含 Isaac 仿真侧）——
  ① spec 装载 + 契约校验（需求理解 LLM 澄清后续接入，人工介入点 1 保留为文件确认）
  ② PLC 代码生成（PLCGenerator，LLM 或种子模式）
  ②b 场景描述生成（SceneSpecGenerator，确定性：scene.spec.json 内嵌 io_map——契约 v1.1）
  闸门1 xml2st 本地契约校验（毫秒级，失败即短路不进下一环）
  闸门2 三方一致性（XML 定位变量 ≡ io_list；提供 io_map 后 R5 检查激活）
  闸门2b scene 闸门（②b 产物自检 + R5 检查复跑：XML ≡ io_list ≡ scene.io_map ⊆ io_list）
  闸门3 部署（可选，POST /deploy :8600 真编译；服务不在线记为 skipped，不阻塞；
       成功后 GET /status 做运行时观测——仅记录不参与裁定，程序身份校验仍在
       验收脚本 require_program 内，闸门4 消费语义 lx 已确认，编排器不重复校验）
  闸门4 链路 B 验收（可选，scenario_<场景>.py 在线验收；OpenPLC 不在线记 skipped）
  通过 → final/ 冻结；MAX_ITERS(6) 未过 → best_effort（通过准则数最多一轮 + 失败报告）

全环（build_usd → run_isaac_headless → evaluate → verdict 归因路由）在仿真侧
接口就绪后接入（csk 文档 §7.4 表），本骨架已预留挂点。

产物落盘（gc 文档 §4，全量入 git）：
  runs/<task_id>/requirements/{requirement_spec.json, <起点 AML 拷贝>} +
  iter_NNN/{plcopen.xml, plc.st, scene.spec.json（内嵌 io_map）, gate.json} +
  final/ + summary.md

用法:
    python -m src.agent.orchestrator examples/specs/motion3axis.spec.json        # LLM 生成
    python -m src.agent.orchestrator examples/specs/motion3axis.spec.json --seed src/plc/motion3axis.xml
    python -m src.agent.orchestrator spec.json --deploy --acceptance             # 闸门3+4：需 OpenPLC 在线
    python -m src.agent.orchestrator --aml examples/aml/plotter3axis_station.aml \
        --request "三轴绘图仪：……" --seed src/plc/plotter3axis.xml --acceptance   # ⓪→① 全链
"""

import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import xml2st  # noqa: E402

# 本地服务直连（绕过 Windows 系统代理——同 client.py 的教训：注册表代理会
# 掐断/劫持 127.0.0.1 请求）
_LOCAL = requests.Session()
_LOCAL.trust_env = False

from .attribution import AttributionEngine  # noqa: E402
from .config import PROJECT_ROOT, RUNS_DIR, get_api_key  # noqa: E402
from .consistency_check import consistency_check  # noqa: E402
from .hmi_gen import build_hmi  # noqa: E402
from .memory import normalize_errors  # noqa: E402
from .pipeline import PLCGenerator  # noqa: E402
from .progress import ProgressTracker  # noqa: E402
from .scene_gen import SceneSpecGenerator  # noqa: E402
from .spec_validator import validate_requirement_spec  # noqa: E402
from .program_review import scan_order_review  # noqa: E402
from .run_metrics import record_solve  # noqa: E402

MAX_ITERS = 6
DEPLOY_URL = "http://127.0.0.1:8600/deploy"
ACCEPTANCE_TIMEOUT_S = 600
FEEDBACK_TAIL_LINES = 40   # 反馈包失败证据尾部行数上限（全量证据落 gate.json）

# 闸门名 → 对话化说法（叙述器用）
_GATE_NAMES = {"generate": "代码生成", "xml2st": "格式契约", "consistency": "三方一致性",
               "scene": "场景描述", "deploy": "真机编译", "acceptance": "在线验收"}


def narrate(event, payload, out=print):
    """编排器事件 → 对话化播报（chat 与编排器 CLI 共用，统一风格）。

    out 可注入（chat 的控制台 / 测试的收集器）；机器可读面（gate.json 等
    落盘文件）保持结构化不变——本函数只负责"人说的话"。
    """
    if event == "iter_start":
        out("── 第 %s 轮尝试 ──" % payload.get("iter"))
    elif event == "addr_fixed":
        out("  · 我按 AML 设备契约自动对齐了 %s 个变量的地址。" % payload.get("count"))
    elif event == "deploy_start":
        out("  · 正在部署到 OpenPLC 做真实编译（约 20~40 秒），请稍候…")
    elif event == "deploy_done":
        out("  · 编译%s。" % ("通过了，程序已在运行时上运行"
                              if payload.get("state") == "ok" else "结果异常"))
    elif event == "acceptance_start":
        out("  · 开始在线验收（约 1~2 分钟），结果逐条实时显示：")
    elif event == "acceptance_line":
        text = str(payload)
        if text.strip():
            out("  │ " + text)
    elif event == "gate_failed":
        gate = _GATE_NAMES.get(payload.get("gate"), payload.get("gate"))
        out("  ✗ 这一轮没过「%s」关——失败原因我已归因并反馈给生成器，准备下一轮。"
            % gate)
    elif event == "switch_fresh":
        out("  ⟳ 连续两轮失败证据同质（零推进），本轮切换全新生成策略。")
    elif event == "final":
        out("  ✓ 全部闸门通过！正在冻结交付物…")
    elif event == "best_effort":
        out("  △ 本次尚未完全收敛——已保留当前最佳版和失败分析。")
    elif event == "configuration_error":
        out("  ✗ 运行或验收配置错误，任务已中止；不会让模型重写 PLC 来修复环境配置。")
    elif event == "candidate_selected":
        out("  · 当前最佳版：第%s轮；%s" % (payload.get("best_iter"), "；".join(payload.get("reasons", []))))
    elif event == "candidate_rejected":
        out("  · 候选未替换最佳版，下一轮仍基于第%s轮：%s" % (payload.get("best_iter"), "；".join(payload.get("reasons", []))))
    elif event == "no_progress":
        out("  △ 连续候选没有可靠推进，停止自动重写，保留最佳版与待处理问题。")
    elif event == "api_blocked":
        out("  ✗ 模型接口余额或鉴权阻断，停止调用；最佳版本和过程证据已保存。")


class Orchestrator:
    def __init__(self, runs_root=None, deploy_url=DEPLOY_URL, max_iters=MAX_ITERS,
                 project_root=PROJECT_ROOT, acceptance_timeout=ACCEPTANCE_TIMEOUT_S,
                 attribution_engine=None):
        self.runs_root = Path(runs_root) if runs_root else RUNS_DIR
        self.deploy_url = deploy_url
        self.max_iters = max_iters
        self.project_root = Path(project_root)
        self.acceptance_timeout = acceptance_timeout
        # 归因引擎（确定性故障库优先，无命中时转 LLM 归因）——只进反馈包，不参与闸门裁定
        self.attribution = attribution_engine or AttributionEngine()
        self._addr_table = None
        self._evidence_dir = None

    # ---------------- 归因与失败处理 ----------------
    @staticmethod
    def _fail_signature(errors):
        """errors → 归一化签名（连续同质失败判定，md5 前 16 位）。"""
        import hashlib
        return hashlib.md5(normalize_errors(errors).encode(
            "utf-8", "ignore")).hexdigest()[:16]

    @staticmethod
    def _st_bodies(xml_text):
        """XML 文本 → {POU 名: ST 本体文本}（diff 用；失败返回 {}）。"""
        import tempfile
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                             encoding="utf-8") as fh:
                fh.write(xml_text)
            _probs, model = xml2st.parse(fh.name)
            import os as _os
            _os.unlink(fh.name)
            return {p["name"]: p.get("body", "") for p in model.get("pous", [])}
        except Exception:
            return {}

    @classmethod
    def _diff_skeleton(cls, old_xml, new_xml, max_lines=30):
        """两版工程 ST 本体差异 → 骨架级行 diff（经验库借鉴用，非全文）。"""
        import difflib
        old, new = cls._st_bodies(old_xml or ""), cls._st_bodies(new_xml or "")
        hunks = []
        for pou in sorted(set(old) | set(new)):
            a = (old.get(pou) or "").splitlines()
            b = (new.get(pou) or "").splitlines()
            if a == b:
                continue
            diff = [l for l in difflib.unified_diff(a, b, lineterm="", n=0)
                    if l[:1] in "+-" and not l.startswith(("+++", "---"))]
            if diff:
                hunks.append("[%s]\n%s" % (pou, "\n".join(diff[:max_lines])))
        return hunks[:4]

    def _fail(self, iter_dir, i, gate, errors, history):
        """统一闸门失败处理：归因 → 增强反馈包 → 留档。返回 feedback。"""
        errors = [str(e) for e in (errors or ["（闸门失败但未携带错误详情）"])]
        context = {}
        st_path = Path(iter_dir) / "plc.st"
        if st_path.is_file():
            source = st_path.read_text(encoding="utf-8")
            context["program_st"] = source[:48000]
            context["scan_order_review"] = scan_order_review(source)
            context["program_truncated"] = len(source) > 48000
        for name, path in (("requirement_spec", Path(iter_dir).parent / "requirements/requirement_spec.json"),
                           ("test_plan", Path(iter_dir) / "test_plan.json")):
            if path.is_file():
                context[name] = json.loads(path.read_text(encoding="utf-8"))
        verdict_path = Path(iter_dir) / "verdict.json"
        if verdict_path.is_file():
            verdict = json.loads(verdict_path.read_text(encoding="utf-8"))
            context["failed_checks"] = [r for r in verdict.get("details", []) if r.get("pass") is not True]
            context["unobserved_checks"] = [r for r in verdict.get("details", []) if r.get("observed") is False]
        trace_path = Path(iter_dir) / "trace.json"
        if trace_path.is_file():
            trace = json.loads(trace_path.read_text(encoding="utf-8"))
            if trace:
                context["trace_summary"] = {
                    name: {"first": trace[0].get(name), "last": trace[-1].get(name),
                           "changes": sum(a.get(name) != b.get(name) for a, b in zip(trace, trace[1:]))}
                    for name in trace[0] if name != "t"}
                for name, summary in context["trace_summary"].items():
                    values = [s[name] for s in trace if isinstance(s.get(name), (int, float)) and not isinstance(s.get(name), bool)]
                    if values:
                        summary.update(min=min(values), max=max(values))
        attribution = self.attribution.attribute(gate, errors, context=context or None)
        (Path(iter_dir) / "diagnostic_context.json").write_text(
            json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
        feedback = self._pack_feedback(errors, history, attribution)
        lessons = self.attribution.memory.match_lessons(errors, gate=gate)
        if lessons:
            feedback += "\n" + self._render_lessons(lessons)
        history.append({"iter": i, "gate": gate, "errors": errors})
        self._dump_gate(iter_dir, gate, ok=False, errors=errors,
                        attribution=attribution, lessons=lessons)
        self._refresh_replay(Path(iter_dir).parent)
        return feedback

    @staticmethod
    def _refresh_replay(run_dir):
        """回放是观测产物，不参与闸门裁定。"""
        from .replay import write_replay
        try:
            write_replay(run_dir)
        except (OSError, ValueError, TypeError) as exc:
            (Path(run_dir) / "replay-error.txt").write_text(str(exc), encoding="utf-8")

    @staticmethod
    def _render_lessons(lessons):
        """经验库借鉴段（相似度分级：detail 级带变更骨架，方向级只给修法）。"""
        lines = ["历史相似修复（经验库借鉴——骨架级参考，按当前工程实际调整，禁止整段照抄）："]
        for ls in lessons:
            lines.append("▸ 相似度 %.2f｜闸门 %s｜来自任务 %s（%s%s）"
                         % (ls["sim"], ls.get("gate"), ls.get("task") or "?",
                            "已验证通过" if ls.get("outcome") == "resolved" else "终验通过",
                            "｜LLM 提炼" if ls.get("kind") == "distilled" else ""))
            lines.append("  修法：%s" % ls.get("fix_summary", ""))
            for hunk in ls.get("diff_hunks", []):
                lines.append("  变更骨架：\n%s" % hunk)
        lines.append("（以上为经验借鉴，不得违反 skill 硬规则——FB 骨架冻结 / 序列器模板 /"
                     " 状态机显式初值；越界修改会被闸门拒绝。）")
        return "\n".join(lines)

    # ---------------- 自动化学习记录（战役结束：A 确定性翻转记录 + B LLM 提炼） ----------------
    def _learn_from_outcome(self, spec, history, base_xml, final_xml,
                            final_mode, ok, shape=None):
        """final / best_effort 后的知识记录。

        A（确定性）：成功且经 repair 修复 → 记录"失败→修复骨架"经验对
        （history 末位失败即被修复对象，diff 取修复基底→final 产物）。
        B（LLM）：连续 ≥2 轮同质失败族 → 归因引擎提炼泛化经验（advisory；
        无 client 自动跳过）。失败经验也记录（outcome 标注未收敛，不参与检索）。
        """
        mem = self.attribution.memory
        task = spec.get("task_id")
        fails = [h for h in history if not h.get("ok") and h.get("errors")]
        if ok and fails and final_mode == "repair" and base_xml:
            last = fails[-1]
            hunks = self._diff_skeleton(base_xml, final_xml)
            mem.record_lesson(
                last["gate"], last["errors"],
                "上轮失败经定向修复后全过（变更骨架见 diff_hunks）",
                diff_hunks=hunks, outcome="resolved", task=task, shape=shape)
        elif fails:
            mem.record_lesson(
                fails[-1]["gate"], fails[-1]["errors"],
                "此役未收敛（教训：见诊断）", outcome="abandoned", task=task,
                shape=shape)
        # B：同质失败族（连续 ≥2 轮同签名）→ LLM 蒸馏泛化经验
        family = []
        for h in fails:
            if family and self._fail_signature(family[-1]["errors"]) == \
                    self._fail_signature(h["errors"]) and family[-1]["gate"] == h["gate"]:
                family.append(h)
                continue
            if len(family) >= 2:
                self._distill_family(mem, task, family, ok, shape)
            family = [h]
        if len(family) >= 2:
            self._distill_family(mem, task, family, ok, shape)

    def _distill_family(self, mem, task, family, ok, shape=None):
        rep = family[0]
        resolution = ("该失败族最终被修复（本役 %s）" % ("通过验收" if ok else "未收敛"))
        distilled = self.attribution.distill_lesson(
            rep["gate"], rep["errors"], resolution=resolution)
        if distilled:
            mem.record_lesson(
                rep["gate"], rep["errors"], distilled["fix"],
                diagnosis=distilled["diagnosis"], task=task, shape=shape,
                kind="distilled", outcome="resolved" if ok else "abandoned")

    # ---------------- 闸门 ----------------
    def build_artifacts(self, scene, spec, iter_dir):
        """执行端可覆盖构建器，以消费该任务冻结的组件与物理资产。"""
        from .scene_gen import build_scene_artifacts
        return build_scene_artifacts(scene, spec, iter_dir)

    def deploy_gate(self, xml_path):
        """闸门3：POST /deploy 真编译。返回 (state, detail)：
        state ∈ ok / failed / skipped（服务不在线，半环不阻塞）。"""
        try:
            resp = _LOCAL.post(self.deploy_url, data=xml_path.read_bytes(),
                               headers={"Content-Type": "application/xml"}, timeout=120)
        except requests.RequestException as exc:
            return "skipped", "deploy 服务不在线（%s）——半环跳过真编译" % exc.__class__.__name__
        if resp.status_code != 200:
            # 500 多为编译失败：body 即 deploy_result.json（errors 字段是回喂主体），
            # 尽量透传；仅非 JSON 时降级为文本
            try:
                return "failed", resp.json()
            except ValueError:
                return "failed", "HTTP %d: %s" % (resp.status_code, resp.text[:500])
        result = resp.json()
        status = result.get("status")
        if str(status).lower() == "ok" or status is True:
            return "ok", result
        return "failed", result  # errors 字段原样进反馈包（lx 约定）

    def _run_acceptance(self, script):
        """跑验收脚本子进程（独立方法便于测试注入）。

        默认**逐行直播 + 段级 fail-fast**：PYTHONUNBUFFERED 强制子进程实时
        flush（否则管道模式下 print 全缓冲，结果最后一次性涌出）；逐行解析
        段标题 [N] 与 FAIL 行，某段失败且该段检查完毕（下一段标题出现）即
        kill 子进程——后续段不再执行，等修复重跑（脚本幂等，lx 已验证）。
        """
        live = getattr(self, "_acceptance_live", None)
        env = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONIOENCODING="utf-8")
        env.update(getattr(self, "_acceptance_context", {}))
        if self._evidence_dir is not None:
            env["AGENT_EVIDENCE_DIR"] = str(Path(self._evidence_dir).resolve())
        proc = subprocess.Popen(
            [sys.executable, str(script)], cwd=str(self.project_root),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", env=env)
        lines = []
        sec_re = re.compile(r"^\[(\d+)\]")
        cur_sec, failed_sec, passed_secs = None, None, []
        killed = False
        expired = threading.Event()
        def timeout():
            expired.set()
            if proc.poll() is None:
                proc.kill()
        timer = threading.Timer(self.acceptance_timeout, timeout)
        timer.daemon = True
        timer.start()
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                stripped = line.strip()
                m = sec_re.match(stripped)
                if m:                                   # 新段标题
                    if failed_sec is not None:          # 上一段已判死 → 暂停
                        note = ("（验收暂停：段 [%d] 存在失败，后续段未执行；"
                                "已通过段：%s——先修复本段，通过后重跑继续）"
                                % (failed_sec,
                                   " ".join("[%d]" % s for s in passed_secs) or "无"))
                        lines.append(note)
                        if live:
                            live(note)
                        proc.kill()
                        killed = True
                        break
                    if cur_sec is not None:
                        passed_secs.append(cur_sec)
                    cur_sec = int(m.group(1))
                elif (failed_sec is None and cur_sec is not None
                      and stripped.startswith("FAIL")):
                    failed_sec = cur_sec
                lines.append(line)
                if live:
                    live(line)
            if not killed:
                proc.wait(timeout=self.acceptance_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            raise
        finally:
            timer.cancel()
            evidence = self._evidence_dir or getattr(self, "_acceptance_context", {}).get("AGENT_EVIDENCE_DIR")
            if evidence:
                (Path(evidence) / "acceptance.log").write_text("\n".join(lines), encoding="utf-8")
        if expired.is_set():
            proc.wait()
            raise subprocess.TimeoutExpired(proc.args, self.acceptance_timeout)
        return subprocess.CompletedProcess(proc.args, proc.returncode,
                                           stdout="\n".join(lines), stderr="")

    def acceptance_gate(self, scenario, runtime_expected=False):
        """闸门4：链路 B 在线验收——跑 lx 的 src/pipeline/scenario_<场景>.py。

        脚本一身两角（主站 + 被控对象仿真），自身经 require_program 校验 %QW20 程序
        身份。返回 (state, detail)：state ∈ ok / failed / skipped / env_down——
        OpenPLC/Modbus 不在线记 skipped（半环语义：环境缺失不阻塞 final）；
        **runtime_expected=True（本轮部署刚通过，运行时中途掉线）时记 env_down**
        ——环境故障判失败并中止战役，杜绝"skipped 照发 final"的假阳性
        （2026-09-22 两次实测教训，看板问题 #4）。
        """
        script = self.project_root / "src" / "pipeline" / ("scenario_%s.py" % scenario)
        if not script.is_file():
            return "skipped", "无验收脚本 %s（场景未登记 scenario_*.py）" % script.name
        try:
            proc = self._run_acceptance(script)
        except subprocess.TimeoutExpired:
            return "failed", ["验收脚本超时（>%ss）" % self.acceptance_timeout]
        out = "\n".join(t for t in ((proc.stdout or "").strip(), (proc.stderr or "").strip()) if t)
        if proc.returncode != 0 and "AcceptanceConfigurationError" in out:
            return "configuration_error", out.splitlines()
        if "无法连接" in out or "ConnectionError" in out:
            if runtime_expected:
                return "env_down", ["OpenPLC/Modbus 中途掉线（本轮部署刚通过、验收连接失败）——"
                                    "环境故障：中止战役判失败，不再按半环 skipped 处理"]
            return "skipped", "OpenPLC/Modbus 不在线——半环跳过在线验收"
        if proc.returncode == 0:
            return "ok", (out.splitlines() or ["验收通过"])[-1]
        # 全量行返回（头部使能段等证据不再丢弃）：gate.json 落全量，
        # LLM 反馈包在 _pack_feedback 统一截尾并注明
        return "failed", out.splitlines() or ["验收失败（无输出）"]

    def status_probe(self):
        """GET /status（lx serve.py）：运行时状态 + 程序身份观测。

        仅记录、不参与闸门裁定——require_program 已内置于验收脚本做身份校验
        （闸门4 消费语义 lx 2026-09-03 确认，编排器不重复校验）。服务不在线
        返回 None。
        """
        url = self.deploy_url.rsplit("/", 1)[0] + "/status"
        try:
            resp = _LOCAL.get(url, timeout=5)
            if resp.status_code == 200:
                return resp.json()
        except requests.RequestException:
            pass
        return None

    # ---------------- 地址自动纠偏（设备契约的机械执行） ----------------
    @staticmethod
    def fix_addresses(xml_path, address_table):
        """按 ⓪ 地址表改写 XML 定位变量地址属性。返回修正的变量数。

        地址是确定性契约（R6 的权威表），LLM 生成后机械对齐——不进反馈回路
        （画圆验证实证：LLM 修地址会打转，一次改一条还引入新的错位）。
        """
        if not address_table:
            return 0
        import xml.etree.ElementTree as ET
        NS = "http://www.plcopen.org/xml/tc6_0201"
        tree = ET.parse(xml_path)
        root = tree.getroot()
        fixed = 0
        for var in root.findall(".//{%s}variable" % NS):
            name = var.get("name")
            if name in address_table and var.get("address") != address_table[name]:
                var.set("address", address_table[name])
                fixed += 1
        if fixed:
            ET.register_namespace("", NS)
            tree.write(xml_path, encoding="UTF-8", xml_declaration=True)
        return fixed

    # ---------------- 运行时透视（诊断口自动注入与采集） ----------------
    _DIAG_KEYWORDS = ("step", "seq", "state", "ck", "phase", "exe", "busy",
                      "done", "edge", "armed", "pen", "latch", "count")
    _DIAG_BASE_REG = 16   # %QW16.. 诊断口（站表未分配区，跳过 prog_id@%QW20）

    def _pick_diag_vars(self, xml_path, limit=6):
        """从 PLC_PRG 内部变量挑诊断观察对象（步号/触发线/状态类命名启发）。"""
        import re as _re
        try:
            problems, model = xml2st.parse(xml_path)
        except Exception:
            return []
        out = []
        for pou in model.get("pous", []):
            if pou["name"] != "PLC_PRG":
                continue
            for line in pou.get("iface", []):
                m = _re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*(AT\s+%Q\S+)?\s*:\s*(\w+)", line)
                if not m:
                    continue
                name, at, typ = m.group(1), m.group(2), m.group(3)
                if at or typ not in ("INT", "BOOL", "WORD"):
                    continue
                if any(k in name.lower() for k in self._DIAG_KEYWORDS):
                    out.append((name, typ))
        # 步号/状态类最关键，排前（诊断时间线的可读性）
        out.sort(key=lambda nt: 0 if any(k in nt[0].lower()
                                         for k in ("step", "seq", "state", "phase"))
                 else 1)
        return out[:limit]

    def _runtime_probe(self, iter_dir, xml_text, samples=14, span_s=7.0):
        """给 XML 注入诊断口 → 部署 → enable+draw 探针采集内部状态时间线。

        返回时间线行列表（进入反馈包）；失败返回 []（不影响主流程）。
        采集后重新部署原始 XML（探针部署不留痕）。
        """
        import tempfile
        import time as _time
        import xml.etree.ElementTree as ET

        NS = "http://www.plcopen.org/xml/tc6_0201"
        xml_path = Path(iter_dir) / "plcopen.xml"
        picks = self._pick_diag_vars(xml_path)
        if not picks:
            return []
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False,
                                         encoding="utf-8") as fh:
            fh.write(xml_text)
            src = Path(fh.name)
        try:
            ET.register_namespace("", NS)
            tree = ET.parse(src)
            root = tree.getroot()
            prg = next((p for p in root.findall(".//{%s}pou" % NS)
                        if p.get("name") == "PLC_PRG"), None)
            if prg is None:
                return []
            iface = prg.find("{%s}interface" % NS)
            blk = ET.SubElement(iface, "{%s}localVars" % NS)
            def diag_addr(idx):
                reg = self._DIAG_BASE_REG + idx
                return reg + 1 if reg >= 20 else reg       # 跳过 prog_id@%QW20

            assigns = []
            for idx, (name, typ) in enumerate(picks):
                st = typ if typ != "BOOL" else "INT"
                v = ET.SubElement(blk, "{%s}variable" % NS,
                                  {"name": "dbg_%d" % idx})
                v.set("address", "%%QW%d" % diag_addr(idx))
                ET.SubElement(ET.SubElement(v, "{%s}type" % NS), "{%s}%s" % (NS, st))
                assigns.append("dbg_%d := %s;" % (idx, name) if typ != "BOOL"
                               else "dbg_%d := BOOL_TO_INT(%s);" % (idx, name))
            body_st = prg.find(".//{%s}body/{%s}ST" % (NS, NS))
            if body_st is None:
                return []
            xh = body_st.find("{http://www.w3.org/1999/xhtml}xhtml")
            tail = "\n" + "\n".join(assigns)
            if xh is not None:
                xh.text = (xh.text or "") + tail
            else:
                body_st.text = (body_st.text or "") + tail
            probe_xml = Path(iter_dir) / "plcopen_diag.xml"
            tree.write(probe_xml, encoding="UTF-8", xml_declaration=True)
            ok, _st, probs = xml2st.convert(probe_xml)
            if not ok:
                return []
            state, _detail = self.deploy_gate(probe_xml)
            if state != "ok":
                return []
            lines = self._probe_collect([n for n, _t in picks])
            # 恢复原始程序（诊断部署不留痕）
            self.deploy_gate(xml_path)
            return lines
        finally:
            try:
                src.unlink()
            except OSError:
                pass

    def _probe_collect(self, names, samples=14):
        """探针采集：enable → 触发绘图 → 采样 %QW16+ 内部状态时间线。"""
        import os
        import time as _time
        sys.path.insert(0, str(self.project_root / "src" / "pipeline"))
        try:
            from modbus_io import SafeCoilIO, connect, read_reg
        except ImportError:
            return []
        host = os.environ.get("MODBUS_HOST", "127.0.0.1")
        try:
            m = connect(host=host,
                        port=int(os.environ.get("MODBUS_PORT", "502")))
        except Exception:
            return []
        lines = []
        try:
            io = SafeCoilIO(m)
            io.write(0, True)                     # run
            _time.sleep(1.0)
            io.write(16, True)                    # cmd_draw（站表约定）
            _time.sleep(0.2)
            io.write(16, False)
            def diag_addr(j):
                reg = self._DIAG_BASE_REG + j
                return reg + 1 if reg >= 20 else reg

            for k in range(samples):
                _time.sleep(0.5)
                vals = [read_reg(m, diag_addr(j)) & 0xFFFF
                        for j in range(len(names))]
                lines.append("    [diag t=%.1fs] %s" % (
                    (k + 1) * 0.5,
                    " ".join("%s=%d" % (n, v) for n, v in zip(names, vals))))
            io.write(0, False)
        except Exception:
            pass
        finally:
            try:
                m.close()
            except Exception:
                pass
        return lines

    # ---------------- 主循环 ----------------
    @record_solve
    def solve(self, spec, generator, deploy=False, acceptance=None, echo=None,
              scene_generator=None, device_model=None, address_table=None,
              trajectory=None, aml_path=None, require_online=False, frame_output=None, test_plan=None):
        """执行完整循环（生成→检查→部署→验收，失败回喂重试）。返回 {status: final|best, iter, run_dir}。

        acceptance: 场景名（None=不跑闸门4）——用于定位 src/pipeline/scenario_<名>.py；
        deploy: 是否先过闸门3（真编译）。两闸门独立可选，验收脚本内 require_program
        自带程序身份校验，直接跑旧部署程序不会误判。
        scene_generator: ②b 场景描述生成器（None=跳过 ②b；默认建议
        SceneSpecGenerator()，确定性产物激活一致性 R5 全部检查项）。
        device_model: ⓪ 的设备模型（供 ②b 取 IO 地址与轴参数；None=降级分配）。
        echo: 可选回调 fn(event, payload)，供 CLI/测试观察循环过程。
        trajectory: 轨迹规划参数（trajectory.plan_* 产物；None=常规生成）。
        注入生成 prompt 作权威步表并落盘 run_dir/trajectory.json。
        aml_path: 任务起点的 AML 设备描述路径（--aml 模式）——原样拷入
        requirements/ 留档（None=种子/无 AML 模式，不拷）。
        """
        if require_online:
            if not deploy or not acceptance or scene_generator is None:
                raise ValueError("完整闭环必须启用场景生成、真实部署和在线验收")
            if getattr(generator, "seed_xml", None) is not None:
                raise ValueError("完整 Agent 闭环不能使用种子替代模型生成")
            if getattr(generator, "generation_mode", "full_xml") != "full_xml":
                raise ValueError("完整 Agent 闭环要求 LLM 生成完整 XML")
        tracker = None
        def notify(event, payload):
            if echo:
                echo(event, payload)

        def stop_for_api(error, attempted_iter):
            record = {"status": "api_blocked", "attempted_iter": attempted_iter, "error": str(error),
                      "best_iter": (tracker.best or {}).get("iter") if tracker else None}
            (run_dir / "api_error.json").write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
            self._write_summary(run_dir, history, tracker.best if tracker else None)
            notify("api_blocked", record)
            return {"status": "api_blocked", "iter": record["best_iter"], "attempted_iter": attempted_iter, "run_dir": run_dir}

        def fail(iter_dir, i, gate, errs, mode):
            nonlocal repair_fails, last_fail_sig, force_fresh, repair_base
            sig = (gate, self._fail_signature(errs))
            if sig == last_fail_sig and tracker is None:
                # 连续两轮同闸门同证据（归一化后）＝零推进：下一轮强制全新生成
                force_fresh = True
            last_fail_sig = sig
            if mode == "repair":
                repair_fails += 1   # 修复模式失败计数（≥3 回退全新生成）
            feedback = self._fail(iter_dir, i, gate, errs, history)
            if tracker is not None:
                decision = tracker.consider(iter_dir, i, gate)
                if tracker.best is not None:
                    repair_base = (run_dir / tracker.best["checkpoint"] / "plcopen.xml").read_text(encoding="utf-8")
                if decision["decision"] == "promote":
                    repair_fails = 0
                notify("candidate_selected" if decision["decision"] == "promote" else "candidate_rejected",
                       {"best_iter": (tracker.best or {}).get("iter"), "reasons": decision["reasons"]})
                feedback = tracker.feedback(feedback)
                if getattr(getattr(generator, "client", None), "blocked_error", None):
                    tracker.stop_reason = "api_blocked"
            return feedback

        problems = validate_requirement_spec(spec)
        if problems:
            raise ValueError("requirement_spec 校验失败（人工介入点 1）: %s" % problems)
        if require_online and any(not p.get("address") for p in spec["io_list"]):
            raise ValueError("严格闭环要求每条 spec IO 明确 PLC 地址")
        if test_plan is not None:
            from .test_plan import validate_test_plan
            validate_test_plan(test_plan, spec)
        elif require_online and acceptance == "mujoco":
            from mujoco_verdict import validate_collection_spec
            validate_collection_spec(spec)
        check_model = device_model
        if require_online and check_model is None:
            check_model = {"io_points": spec["io_list"], "kinematics": {"axes": []}}
        # 分层（2026-09-22 负责人指令）：地址优先取自 spec（① 注入，spec 自足）；
        # 老 spec 无地址时回退设备模型——阅卷侧（R6/R8）仍可拿 AML 当标准答案
        self._addr_table = address_table or (
            {p["name"]: p.get("address") for p in spec.get("io_list", []) if p.get("address")}
            or {p["name"]: p["address"] for p in (device_model or {}).get("io_points", [])
               if p.get("address")} or None)

        task_id = spec["task_id"]
        run_dir = self.runs_root / task_id
        req_dir = run_dir / "requirements"   # 需求侧产物：spec 留档 + 起点 AML 拷贝
        req_dir.mkdir(parents=True, exist_ok=True)
        (req_dir / "requirement_spec.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
        if aml_path and os.path.isfile(str(aml_path)):
            shutil.copy(str(aml_path), req_dir / Path(str(aml_path)).name)
        if trajectory is not None:   # 轨迹参数是任务级权威产物，留档供复现
            (run_dir / "trajectory.json").write_text(
                json.dumps(trajectory, ensure_ascii=False, indent=2),
                encoding="utf-8")
        if test_plan is not None:
            (run_dir / "test_plan.json").write_text(
                json.dumps(test_plan, ensure_ascii=False, indent=2), encoding="utf-8")
            tracker = ProgressTracker(run_dir, spec, test_plan, self.project_root, strict=require_online)

        history = []          # 迭代记忆：改了什么 → 哪条闸门翻转
        from .repair_feedback import GenerationStalled, source_signature
        generator.memory = self.attribution.memory
        generator._failed_candidates = set()
        generator.stop_reason = None
        candidates_seen = {}
        if getattr(generator, "initial_candidate", None) and not getattr(generator, "resume_validation", False):
            candidates_seen[source_signature(generator.initial_candidate)] = "续修基底"
        stop_reason = None
        feedback = None       # 上轮反馈包（程序拼装，不靠 LLM 现场发挥）
        best = None           # best-effort：进展最多的一轮
        repair_base = None    # 最近一次过静态闸门的产物（定向修复的基础）
        repair_fails = 0      # 修复模式连续失败计数（≥3 回退全新生成，防死循环）
        last_fail_sig = None  # 上轮失败签名 (gate, 归一化哈希)——同质即零推进
        force_fresh = False   # 零推进提前熔断：连续 2 轮失败证据同质 → 下轮强制 fresh

        for i in range(1, self.max_iters + 1):
            if tracker is not None and tracker.stop_reason:
                if tracker.stop_reason != "api_blocked":
                    notify("no_progress", {"reason": tracker.stop_reason})
                break
            iter_dir = run_dir / ("iter_%03d" % i)
            iter_dir.mkdir(exist_ok=True)
            if test_plan is not None:
                shutil.copy2(run_dir / "test_plan.json", iter_dir / "test_plan.json")
            if hasattr(generator, "evidence_dir"):
                generator.evidence_dir = iter_dir / "generation"
            # HMI 产物：① 设计的 spec.hmi 优先（信号→地址确定性补全），
            # 无 hmi 段的老 spec 由 build_hmi 规则兜底（2026-09-23 HMI v2）
            hmi_doc = build_hmi(spec)
            if spec.get("hmi"):
                addr = {p.get("name"): p.get("address")
                        for p in spec.get("io_list", []) if p.get("address")}
                for el in spec["hmi"].get("elements", []):
                    el.setdefault("address", addr.get(el.get("signal")))
                hmi_doc = {"hmi_version": "2", "task_id": spec.get("task_id"),
                           "elements": spec["hmi"]["elements"]}
            (iter_dir / "hmi.json").write_text(
                json.dumps(hmi_doc, ensure_ascii=False, indent=2),
                encoding="utf-8")   # 任务级产物每轮随盘（final/ 随目录拷贝）
            notify("iter_start", {"iter": i, "hmi": True})
            entry_base = repair_base   # 进循环时的修复基底（= 上轮产物，学习 diff 用）
            if tracker is not None and tracker.contract() is not None:
                (iter_dir / "repair_contract.json").write_text(
                    json.dumps(tracker.contract(), ensure_ascii=False, indent=2), encoding="utf-8")

            # ---- 生成策略：LLM 可用且有修复基础（上轮过静态闸门、后续闸门失败）
            #    → 定向修复（最小修改，三次实证远稳于从头重生成）；否则全新生成。
            #    零推进熔断：连续 2 轮失败签名同质时提前放弃 repair，强制 fresh ----
            would_repair = (repair_base is not None and feedback is not None
                            and getattr(generator, "client", None) is not None
                            and repair_fails < 3)
            traj_kw = {"trajectory": trajectory} if trajectory is not None else {}
            if test_plan is not None:
                traj_kw["test_plan"] = test_plan
            # 经验预注入（自动化学习）：首轮 fresh 生成前，把同形状任务的
            # 历史 resolved 教训注入 system prompt——借鉴前置到生成时，
            # 直指首轮质量（数据实证：10 役 9 役首轮烧在 generate/deploy）
            if i == 1 and trajectory is not None:
                hints = self.attribution.memory.recent_lessons(
                    shape=trajectory.get("shape"))
                if hints:
                    traj_kw["experience_hint"] = "\n".join(
                        "·（%s%s）%s%s" % (
                            h.get("task") or "历史任务",
                            "｜诊断：" + h["diagnosis"] if h.get("diagnosis") else "",
                            h.get("fix_summary", ""),
                            "\n" + "\n".join(h.get("diff_hunks", [])[:2]) if h.get("diff_hunks") else "") for h in hints)
            try:
                if would_repair and not force_fresh:
                    gen = generator.repair(repair_base, spec, feedback, **traj_kw)
                    mode = "repair"
                else:
                    if would_repair:
                        notify("switch_fresh", {"iter": i})
                    gen = generator.generate(spec, feedback=feedback, **traj_kw)
                    mode = "fresh"
                    force_fresh = False
            except GenerationStalled as exc:
                stop_reason = "no_progress"
                errors = [str(exc)]
                history.append({"iter": i, "gate": "generate", "ok": False, "errors": errors})
                self._dump_gate(iter_dir, "generate", ok=False, errors=errors)
                notify("no_progress", {"iter": i, "reason": stop_reason, "errors": errors})
                break
            except RuntimeError as exc:
                if not re.search(r"HTTP\s+(?:401|402)\b", str(exc)):
                    raise
                return stop_for_api(exc, i)
            xml_text = gen.get("xml")
            mode = gen.get("mode", mode)
            if mode == "repair":
                entry_base = getattr(generator, "repair_base_used", entry_base)
            if not xml_text:
                feedback = fail(iter_dir, i, "generate", gen.get("errors", []), mode)
                notify("gate_failed", {"iter": i, "gate": "generate", "mode": mode})
                continue
            (iter_dir / "plcopen.xml").write_text(xml_text, encoding="utf-8")

            # 地址自动纠偏：按 ⓪ 表机械对齐（契约执行，非 LLM 工作）
            addr_fixed = (0 if require_online else self.fix_addresses(
                iter_dir / "plcopen.xml", getattr(self, "_addr_table", None)))
            if addr_fixed:
                xml_text = (iter_dir / "plcopen.xml").read_text(encoding="utf-8")
                notify("addr_fixed", {"iter": i, "count": addr_fixed})

            # 闸门1+2 已在 generator.gate 内完成（xml2st + 一致性），此处复跑留档：
            ok, st_text, problems1 = xml2st.convert(iter_dir / "plcopen.xml")
            if not ok:
                feedback = fail(iter_dir, i, "xml2st", problems1, mode)
                notify("gate_failed", {"iter": i, "gate": "xml2st", "mode": mode})
                continue
            (iter_dir / "plc.st").write_text(st_text, encoding="utf-8")

            ok2, problems2 = consistency_check(iter_dir / "plcopen.xml", spec["io_list"],
                                      device_model=check_model)
            hard2 = [p for p in problems2 if not p.startswith("SKIP")]
            if not ok2 or hard2:
                feedback = fail(iter_dir, i, "consistency", hard2, mode)
                notify("gate_failed", {"iter": i, "gate": "consistency", "mode": mode})
                continue

            # A previously tested source cannot justify another compile/physical run.
            if getattr(generator, "client", None) is not None and not getattr(generator, "seed_xml", None):
                signature = source_signature(xml_text)
                if signature in candidates_seen:
                    stop_reason = "no_progress"
                    errors = ["候选与%s程序相同（忽略排版、注释和时间戳），停止重复编译与验收。" % candidates_seen[signature]]
                    history.append({"iter": i, "gate": "generate", "ok": False, "errors": errors})
                    self._dump_gate(iter_dir, "generate", ok=False, errors=errors)
                    notify("no_progress", {"iter": i, "reason": stop_reason, "errors": errors})
                    break
                candidates_seen[signature] = "第 %d 轮" % i

            # 静态闸门全过 → 本产物成为后续定向修复的基础（后续闸门失败也不丢）
            if tracker is None:
                repair_base = xml_text
                repair_fails = 0

            gates = {"xml2st": True, "consistency": [p for p in problems2 if p.startswith("SKIP")] or True,
                     "generation": {"mode": "seed" if getattr(generator, "seed_xml", None)
                                    else getattr(generator, "generation_mode", "full_xml"),
                                    "rounds": gen.get("rounds"), "history": gen.get("history", [])}}

            # ---- ②b 场景描述生成（确定性，契约 v1.1）+ 闸门2b：R5 检查（XML ≡ io_list ≡ scene.io_map）----
            if scene_generator is not None:
                try:
                    scene_out = scene_generator.generate(spec, device_model)
                except ValueError as exc:  # 生成器自检失败（spec 异常或内部回归）
                    feedback = fail(iter_dir, i, "scene", [str(exc)], mode)
                    notify("gate_failed", {"iter": i, "gate": "scene", "mode": mode})
                    continue
                (iter_dir / "scene.spec.json").write_text(
                    json.dumps(scene_out["scene"], ensure_ascii=False, indent=2), encoding="utf-8")
                ok5, problems5 = consistency_check(iter_dir / "plcopen.xml",
                                                   spec["io_list"], scene_out["io_map"],
                                                   device_model=check_model,
                                                   scene=scene_out["scene"])
                hard5 = [p for p in problems5 if not p.startswith("SKIP")]
                if not ok5 or hard5:
                    feedback = fail(iter_dir, i, "scene", hard5, mode)
                    notify("gate_failed", {"iter": i, "gate": "scene", "mode": mode})
                    continue
                gates["scene"] = {"ok": True,
                                  "assets": len(scene_out["scene"]["assets"]),
                                  "io_map_vars": len(scene_out["io_map"]),
                                  "r5": "active"}
                if require_online or acceptance in ("mujoco","workbench"):
                    try:
                        self.build_artifacts(scene_out["scene"], spec, iter_dir)
                    except (ValueError, RuntimeError) as exc:
                        feedback = fail(iter_dir, i, "scene", [str(exc)], mode)
                        notify("gate_failed", {"iter": i, "gate": "scene"})
                        continue

            if deploy:
                notify("deploy_start", {"iter": i})
                state, detail = self.deploy_gate(iter_dir / "plcopen.xml")
                probe = self.status_probe()  # 观测性：运行时状态/程序身份（不裁定）
                if probe is not None:
                    detail = dict(detail) if isinstance(detail, dict) else {"result": detail}
                    detail["runtime_status"] = probe
                gates["deploy"] = {"state": state, "detail": detail}
                if isinstance(detail, dict) and detail.get("compiler"):
                    (iter_dir / "compiler.json").write_text(
                        json.dumps(detail["compiler"], ensure_ascii=False, indent=2), encoding="utf-8")
                if state == "failed":
                    if isinstance(detail, dict) and detail.get("status") == "CONFIGURATION_ERROR":
                        errors = detail.get("errors", [])
                        self._dump_gate(iter_dir, "deploy", ok=False, gates=gates, errors=errors)
                        notify("configuration_error", {"iter": i, "errors": errors})
                        self._write_summary(run_dir, history, {"iter": i, "gate": "deploy", "errors": errors})
                        return {"status": "configuration_error", "iter": i, "run_dir": run_dir}
                    errs = detail.get("errors") if isinstance(detail, dict) else [str(detail)]
                    if isinstance(errs, dict):
                        errs = [str(errs)]
                    feedback = fail(iter_dir, i, "deploy", errs, mode)
                    notify("gate_failed", {"iter": i, "gate": "deploy"})
                    continue
                notify("deploy_done", {"iter": i, "state": state})
                if require_online and state != "ok":
                    fail(iter_dir, i, "deploy", ["严格闭环：真实部署未执行，禁止交付 final"], mode)
                    break

            if acceptance:
                self._acceptance_context = {
                    "AGENT_REQUIREMENT_SPEC": str((req_dir / "requirement_spec.json").resolve()),
                    "AGENT_SCENE": str((iter_dir / "scene.spec.json").resolve()),
                    "AGENT_IO_MAP": str((iter_dir / "io_map.json").resolve()),
                    "AGENT_EVIDENCE_DIR": str(iter_dir.resolve()),
                    "AGENT_PROG_ID": str(spec.get("prog_id", 4)),
                    "AGENT_TEST_PLAN": str((iter_dir / "test_plan.json").resolve()) if test_plan is not None else "",
                    "AGENT_TRAJECTORY": str((run_dir / "trajectory.json").resolve())
                                        if trajectory is not None else ""}
                if frame_output:
                    self._acceptance_context["AGENT_FRAME_OUTPUT"] = str(Path(frame_output).resolve())
                notify("acceptance_start", {"iter": i, "scenario": acceptance})
                if deploy and (gates.get("deploy") or {}).get("state") == "skipped":
                    # 部署被跳过（deploy 服务不在线）时运行时里是残留旧程序——
                    # 验收对象非本轮产物，跳过以防"测鬼影"（2026-09-22 实测教训：
                    # 四场战役十几轮验收全在测旧程序，回喂追不存在的缺陷）
                    gates["acceptance"] = {
                        "state": "skipped",
                        "detail": "部署未执行（deploy 服务不在线）——运行时为残留"
                                  "旧程序，验收对象非本轮产物，跳过"}
                    self._dump_gate(iter_dir, "acceptance", ok=False, gates=gates,
                                    errors=[gates["acceptance"]["detail"]])
                else:
                    self._acceptance_live = (
                        lambda ln: notify("acceptance_line", ln)) if echo else None
                    state, detail = self.acceptance_gate(
                        acceptance,
                        runtime_expected=bool(deploy)
                        and gates.get("deploy", {}).get("state") == "ok")
                    self._acceptance_live = None
                    gates["acceptance"] = {"state": state, "detail": detail}
                    if state == "configuration_error":
                        errors = detail if isinstance(detail, list) else [str(detail)]
                        history.append({"iter": i, "gate": "acceptance", "ok": False,
                                        "configuration_error": True, "errors": errors})
                        self._dump_gate(iter_dir, "acceptance", ok=False, gates=gates, errors=errors)
                        self._write_summary(run_dir, history, history[-1])
                        notify("configuration_error", {"iter": i, "errors": errors})
                        return {"status": "configuration_error", "iter": i, "run_dir": run_dir}
                    if state == "env_down":
                        stop_reason = "env_down"
                        errors=detail if isinstance(detail,list) else [str(detail)]
                        self._dump_gate(iter_dir,"acceptance",ok=False,gates=gates,errors=errors)
                        notify("gate_failed", {"iter": i, "gate": "acceptance(env_down)"})
                        history.append({"iter": i, "gate": "acceptance", "ok": False,
                                        "env_down": True,"errors":errors})
                        break        # 环境故障：重试无意义，中止战役（结果 best_effort）
                    if state == "failed":
                        errs = detail if isinstance(detail, list) else [str(detail)]
                        # 运行时透视：验收行为失败时自动注入诊断口采集序列器内部状态，
                        # 把"卡在 (51,26,9)"变成"pl_step 停在 2 / exe=1 / Busy=1"级证据
                        try:
                            diag = [] if acceptance == "mujoco" else self._runtime_probe(iter_dir, xml_text)
                            if diag:
                                errs = errs + ["运行时内部状态时间线（诊断口自动采集）："] + diag
                        except Exception:
                            pass
                        feedback = fail(iter_dir, i, "acceptance", errs, mode)
                        notify("gate_failed", {"iter": i, "gate": "acceptance"})
                        continue
                if require_online and gates["acceptance"]["state"] != "ok":
                    fail(iter_dir, i, "acceptance", ["严格闭环：在线验收未执行，禁止交付 final"], mode)
                    break

            self._dump_gate(iter_dir, "all", ok=True, gates=gates)
            if tracker is not None:
                decision = tracker.consider(iter_dir, i, "all")
                if decision["decision"] != "promote":
                    feedback = tracker.feedback(self._fail(iter_dir, i, "acceptance", decision["reasons"], history))
                    notify("candidate_rejected", {"best_iter": (tracker.best or {}).get("iter"), "reasons": decision["reasons"]})
                    continue
            self._refresh_replay(run_dir)
            history.append({"iter": i, "gate": "all", "ok": True})
            self._learn_from_outcome(spec, history, entry_base, xml_text, mode, ok=True,
                                      shape=(trajectory or {}).get("shape"))
            self._consolidate(generator, spec, gates, history)
            self._finalize(run_dir, iter_dir, i, history)
            notify("final", {"iter": i, "run_dir": str(run_dir)})
            return {"status": "final", "iter": i, "run_dir": run_dir}

        # ---- best-effort：闸门推进最远的一轮 + 失败报告（人工介入点 2）----
        if tracker is not None and tracker.stop_reason == "api_blocked":
            return stop_for_api(generator.client.blocked_error, history[-1]["iter"])
        order = {"generate": 0, "xml2st": 1, "consistency": 2, "scene": 3, "deploy": 4,
                 "acceptance": 5, "all": 6}
        best = (next((h for h in history if h.get("iter") == tracker.best["iter"]), None)
                if tracker is not None and tracker.best else
                max(history, key=lambda h: order.get(h.get("gate"), -1)) if history else None)
        if best and not best.get("ok"):
            self.attribution.memory.record_fix(
                best.get("gate"), best.get("errors", []),
                "迭代上限未收敛（人工介入点 2），best=%s" % best.get("gate"), "abandoned")
        self._write_summary(run_dir, history, best)
        if tracker is None and stop_reason not in ("no_progress", "env_down"):
            self._learn_from_outcome(spec, history, None, None, "fresh", ok=False,
                                      shape=(trajectory or {}).get("shape"))
        notify("best_effort", {"run_dir": str(run_dir)})
        return {"status": "best_effort", "iter": (best or {}).get("iter"), "run_dir": run_dir,
                "stop_reason": stop_reason or (tracker.stop_reason if tracker and tracker.stop_reason else "iteration_limit")}

    # ---------------- 产物 ----------------
    @staticmethod
    def _pack_feedback(errors, history, attribution=None):
        """反馈包：失败证据原文 + 归因（故障库/历史修复/无命中时 LLM 归因）+ 迭代记忆。

        失败证据超 FEEDBACK_TAIL_LINES 行时截尾并注明（LLM token 预算不变，
        全量证据落该轮 gate.json——证据不再丢失）。
        """
        passed = [h for h in history if h.get("ok")]
        lines = ["失败证据（原样）："]
        if len(errors) > FEEDBACK_TAIL_LINES:
            lines.append("（输出已截断至尾部 %d 行，全量见 gate.json）"
                         % FEEDBACK_TAIL_LINES)
            errors = errors[-FEEDBACK_TAIL_LINES:]
        lines += ["- %s" % e for e in errors]
        if attribution:
            extra = AttributionEngine.format_feedback(attribution)
            if extra:
                lines += ["归因（只供修复参考，不改变闸门结论）：", extra]
        if passed:
            lines.append("迭代记忆：以下修改已通过对应闸门，禁止回退——")
            lines += ["- iter %s: %s" % (h["iter"], h.get("gate")) for h in passed]
        return "\n".join(lines)

    @staticmethod
    def _dump_gate(iter_dir, gate, ok, errors=None, gates=None, attribution=None,
                   lessons=None):
        payload = {"gate": gate, "ok": ok}
        if errors:
            payload["errors"] = errors
        if gates:
            payload["gates"] = gates
        if attribution:
            payload["attribution"] = attribution
        if lessons:
            payload["lessons"] = lessons       # 经验库借鉴留档（自动化学习可观测性）
        (iter_dir / "gate.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---------------- 知识记录（final 后） ----------------
    def _consolidate(self, generator, spec, gates, history):
        """final 后的知识记录：情景记忆（修复对）+ 模式卡自动策展。

        策展条件从严：种子模式 + 在线验收 ok（skipped 不入模式库——未在线
        验证的场景不构成"已验收模式"）。
        """
        acc = gates.get("acceptance") or {}
        acc_ok = isinstance(acc, dict) and acc.get("state") == "ok"
        seed_path = getattr(generator, "seed_path", None)
        if not (acc_ok and seed_path):
            return None
        key = Path(seed_path).stem
        goal = spec.get("task_goal", "")[:80]
        self.attribution.memory.record_fix(
            "all", ["final iter=%d" % len(history)],
            "种子 %s 通过全部闸门（含在线验收）" % key, "final")
        tags = [w for w in ("三轴", "绘图", "画", "运动", "定位", "轴", "互锁",
                            "序列", "笔", "plot", "draw", "square")
                if w in goal or w in goal.lower()]
        try:
            from . import patternlib
            patternlib.register_pattern(key, seed_path, goal, tags,
                                        provenance="orchestrator-final")
        except Exception:
            pass  # 策展失败不影响交付
        return key

    def _finalize(self, run_dir, iter_dir, i, history):
        final_dir = run_dir / "final"
        if final_dir.exists():
            shutil.rmtree(final_dir)
        shutil.copytree(iter_dir, final_dir)
        # 最新冻结交付物指针：runs/latest/ 只保留两份交付文件，每次成功覆盖
        # （战役全量证据仍在各自 runs/<task_id>/final/，此目录是"最新一份"快捷取用处）
        latest_dir = self.runs_root / "latest"
        latest_dir.mkdir(parents=True, exist_ok=True)
        for name in ("plcopen.xml", "scene.spec.json", "hmi.json", "io_map.json", "scene.xml", "test_plan.json"):
            src = final_dir / name
            if src.exists():
                shutil.copy2(src, latest_dir / name)
        self._write_summary(run_dir, history, {"iter": i, "gate": "all", "ok": True})

    @staticmethod
    def _write_summary(run_dir, history, best):
        lines = ["# solve 运行总结", "", "## 迭代历史", ""]
        for h in history:
            lines.append("- iter %s: 闸门 **%s** %s" % (
                h.get("iter"), h.get("gate"), "通过" if h.get("ok") else "失败"))
            for e in h.get("errors", [])[:8]:
                lines.append("  - %s" % e)
        lines += ["", "## 结论", "", "best = %s" % json.dumps(best, ensure_ascii=False, default=str)]
        selection = run_dir / "progress_history.json"
        if selection.is_file():
            lines += ["", "## 候选与最佳版比较", "", "| 轮次 | 选择 | 最佳轮次 | 原因 |", "|---|---|---|---|"]
            for record in json.loads(selection.read_text(encoding="utf-8")):
                selected = record["iter"] if record["decision"] == "promote" else record.get("baseline_iter")
                lines.append("| %s | %s | %s | %s |" % (record["iter"], record["decision"], selected,
                    "；".join(record["reasons"]).replace("|", "/").replace("\n", " ")))
            lines += ["", "best.json指向完整检查点；best/是便于浏览的副本。未全部通过的版本不是final。"]
        (run_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


def _terminal_confirm(questions):
    """轨迹参数终端确认（--confirm-params）：逐项 input，回车=默认值。"""
    print("请确认绘图参数（直接回车 = 采纳括号内默认/站标准值）：")
    answers = {}
    for q in questions:
        raw = input("  %s: " % q["question"]).strip()
        if not raw:
            continue
        key = q["key"]
        try:
            if key == "center":
                parts = [float(v) for v in raw.replace("，", ",").split(",")]
                if len(parts) == 2:
                    answers[key] = parts
            else:
                answers[key] = float(raw) if "." in raw else int(raw)
        except ValueError:
            print("  （%r 无法解析为数值，该项仍用默认值）" % raw)
    return answers


def main():
    import argparse
    parser = argparse.ArgumentParser(description="gc 闭环编排器（半环骨架）")
    parser.add_argument("spec", nargs="?", default=None,
                        help="requirement_spec JSON 路径（与 --aml 二选一）")
    parser.add_argument("--seed", default=None, help="种子模式：指定已验收 XML 当生成产物（联调/回归）")
    parser.add_argument("--deploy", action="store_true", help="启用闸门3（POST /deploy 真编译）")
    parser.add_argument("--acceptance", action="store_true",
                        help="启用闸门4（链路 B 在线验收 scenario_<场景>.py；OpenPLC 离线记 skipped）")
    parser.add_argument("--scenario", default=None,
                        help="验收场景名（缺省：--seed 的文件名去扩展，否则 spec.task_id）")
    parser.add_argument("--max-iters", type=int, default=MAX_ITERS)
    parser.add_argument("--runs-root", default=None, help="runs/ 根目录（默认仓库 runs/）")
    parser.add_argument("--no-scene", action="store_true",
                        help="跳过 ②b 场景描述生成（默认启用：scene.spec.json 含内嵌 io_map）")
    parser.add_argument("--aml", default=None,
                        help="AutomationML 设备描述（⓪）——前置 ① 需求理解，需配合 --request")
    parser.add_argument("--modbus-host", default=None,
                        help="链路 B Modbus 主机（缺省 127.0.0.1；远程/VM 运行时传 IP，注入环境供验收子进程）")
    parser.add_argument("--modbus-port", type=int, default=None,
                        help="链路 B Modbus 端口（缺省 502）")
    parser.add_argument("--no-curated-patterns", action="store_true",
                        help="生成仅用静态 CATALOG 通用原语（排除自动策展场景卡）——泛化验证口径")
    parser.add_argument("--no-attribution", action="store_true",
                        help="关闭归因引擎（默认启用：故障库签名匹配 + LLM 兜底，只进反馈不裁定）")
    parser.add_argument("--request", default=None,
                        help="自然语言需求文本（或 .txt 文件路径），配合 --aml 使用")
    parser.add_argument("--confirm-params", action="store_true",
                        help="轨迹参数逐项终端确认（缺省自动采纳站标准值；非标准参数验收按标准几何判定）")
    parser.add_argument("--answers", help="澄清问题的 JSON 答案文件")
    parser.add_argument("--task-id", default=None,
                        help="覆盖 spec.task_id（区分同站多役的 runs 目录；^[a-z][a-z0-9_]*$）")
    parser.add_argument("--prog-id", type=int, default=None,
                        help="覆盖程序身份 prog_id@%%QW20（画方=2/画圆=3，进 spec 供生成器落身份）")
    from .svg_input import add_svg_arguments
    add_svg_arguments(parser)
    args = parser.parse_args()
    if args.svg and args.seed:
        parser.error("SVG 绘图必须按路点生成代码，不能用 --seed 原样复用旧程序")
    if args.svg and args.aml and not args.spec and not args.request:
        args.request = "按照 SVG 路径描画轮廓，保留路径次序与抬落笔动作。"

    from .aml_parser import parse_aml
    from .client import BigModelClient
    from .config import MODEL

    trajectory = None
    prepared = None
    device_model = None
    if args.aml and args.request:
        if args.spec:
            print("--aml/--request 与 spec 文件二选一（--aml 仅作设备模型时不要带 --request）。")
            return 2
        device_model, problems = parse_aml(args.aml)
        if problems:
            print("AML 解析存在问题（best-effort 继续）：")
            for p in problems:
                print("  - %s" % p)
        req = Path(args.request)
        request_text = req.read_text(encoding="utf-8").strip() if req.is_file() else args.request
        client = BigModelClient(get_api_key()) if get_api_key() else None
        from .requirement import RequirementUnderstander
        understander = RequirementUnderstander(client=client, model=MODEL)

        # 与对话入口共用主动澄清；非交互模式默认值留档，必需歧义不能跳过。
        from .clarification import ClarificationRequired, extract_explicit_geometry, prepare_request
        from .chat import ask_clarification
        try:
            answers = json.loads(Path(args.answers).read_text(encoding="utf-8-sig")) if args.answers else {}
            if args.svg:
                from .svg_input import prepare_svg_request
                prepared = prepare_svg_request(args, request_text, device_model, answers)
            else:
                ex = extract_explicit_geometry(request_text)
                if ex["shape"] == "unknown" and not ex["multiple_shapes"]:
                    ex = understander.extract_shape(request_text)
                prepared = prepare_request(request_text, device_model, shape_info=ex, answers=answers,
                                           ask=ask_clarification if args.confirm_params else None,
                                           accept_defaults=not args.confirm_params)
        except ClarificationRequired as exc:
            print(json.dumps({"status": "needs_clarification", "questions": exc.questions}, ensure_ascii=False, indent=2))
            return 2
        except (ValueError, OSError) as exc:
            print("澄清参数无法使用：%s" % exc)
            return 2
        request_text, trajectory = prepared["request_text"], prepared["trajectory"]

        print("① 需求理解：模式=%s" % ("llm" if client else "template"))
        res = understander.understand(request_text, device_model=device_model,
                                      task_id=args.task_id)
        report = res["report"]
        for p in report.get("pending", []):
            print("  待澄清 - %s" % p)
        if res["spec"] is None:
            print("规格组装失败：")
            for p in (report.get("problems")
                      or report.get("history", [{}])[-1].get("problems", [])):
                print("  - %s" % p)
            return 2
        spec = res["spec"]
        if args.task_id:
            spec["task_id"] = args.task_id
        if args.prog_id is not None:
            spec["prog_id"] = args.prog_id    # 程序身份（进 prompt，生成器落 %QW20）
        print("① 完成（rounds=%s，io_list=%d 条，prog_id=%s）"
              % (report.get("rounds", 0), len(spec["io_list"]), spec.get("prog_id", "-")))
    elif args.aml:
        # --aml 仅提供设备模型（⓪）：spec 走冻结文件（可复现联调路径），模型供 ②b 取址
        if not args.spec:
            print("--aml 不带 --request 时需要 spec 文件参数（仅作设备模型注入）。")
            return 2
        device_model, problems = parse_aml(args.aml)
        if problems:
            print("AML 解析存在问题（best-effort 继续）：")
            for p in problems:
                print("  - %s" % p)
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
    else:
        if not args.spec:
            print("需要 spec 文件或 --aml/--request 输入。")
            return 2
        spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))

    address_table = {}
    if args.svg and prepared is None:
        from .svg_input import prepare_svg_request
        try:
            answers = json.loads(Path(args.answers).read_text(encoding="utf-8-sig")) if args.answers else {}
            prepared = prepare_svg_request(args, spec["task_goal"], device_model, answers)
        except (ValueError, OSError) as exc:
            print("SVG 输入无法使用：%s" % exc)
            return 2
        trajectory, spec["task_goal"] = prepared["trajectory"], prepared["request_text"]
    if args.task_id:
        spec["task_id"] = args.task_id
    if prepared is not None:
        from .chat import save_preparation
        from .spec_validator import TASK_ID_RE
        if not TASK_ID_RE.fullmatch(spec.get("task_id", "")):
            print("task_id 必须是小写字母开头的 [a-z0-9_]")
            return 2
        try:
            preview = save_preparation(prepared, Path(args.runs_root or RUNS_DIR) / spec["task_id"])
        except (ValueError, OSError) as exc:
            print("轨迹归档失败：%s" % exc)
            return 2
        if preview:
            print("轨迹预览：%s" % preview.resolve())
    if device_model:
        address_table = {p["name"]: p["address"] for p in device_model.get("io_points", [])
                         if p.get("address")}
    from .position_control import position_io
    position_mode=position_io(device_model or {})
    if args.seed:
        generator = PLCGenerator(client=None, seed_xml=args.seed,
                                 address_table=address_table,position_mode=position_mode,prog_id=spec.get('prog_id',2))
    else:
        api_key = get_api_key()
        if not api_key:
            print("未配置 API Key（ZHIPUAI_API_KEY）且未指定 --seed；退出。")
            return 2
        # 轨迹参数化路线：LLM 按权威步表现场生成，**不注入任何模式卡**
        # （自动化学习口径：只靠 skill 契约+硬规则+经验库相似度借鉴，
        # 卡注入=把答案放进 few-shot，无法度量真实学习能力）
        generator = PLCGenerator(client=BigModelClient(api_key), model=MODEL,
                                 no_cards=(args.no_curated_patterns
                                           or trajectory is not None),
                                 address_table=address_table,position_mode=position_mode,prog_id=spec.get('prog_id',2))

    scenario = args.scenario
    if scenario is None:
        scenario = (Path(args.seed).stem if args.seed else spec["task_id"])
    if args.modbus_host:
        os.environ["MODBUS_HOST"] = args.modbus_host   # 验收子进程经 connect() 读 env
    if args.modbus_port:
        os.environ["MODBUS_PORT"] = str(args.modbus_port)

    orch = Orchestrator(runs_root=args.runs_root, max_iters=args.max_iters,
                        attribution_engine=(
                            None if args.no_attribution
                            else AttributionEngine(
                                client=BigModelClient(get_api_key())
                                if get_api_key() else None)))
    scene_gen = None if args.no_scene else SceneSpecGenerator()
    result = orch.solve(spec, generator, deploy=args.deploy,
                        acceptance=scenario if args.acceptance else None,
                        echo=narrate, trajectory=trajectory,
                        scene_generator=scene_gen, device_model=device_model,
                        aml_path=args.aml)
    print("\nRESULT: %s (iter=%s) -> %s" % (result["status"], result["iter"], result["run_dir"]))
    return 0 if result["status"] == "final" else 1


if __name__ == "__main__":
    sys.exit(main())
