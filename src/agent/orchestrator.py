#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端闭环编排器（solve 循环的权威实现骨架，gc 文档 §4）。

当前形态：**半环**（不含 Isaac 仿真侧）——
  ① spec 装载 + 契约校验（需求理解 LLM 澄清后续接入，人工介入点 1 保留为文件确认）
  ② PLC 代码生成（PLCGenerator，LLM 或种子模式）
  ②b 场景描述生成（SceneSpecGenerator，确定性：scene.spec.json + io_map.json）
  闸门1 xml2st 本地契约校验（毫秒级，失败即短路不进下一环）
  闸门2 三方一致性（XML 定位变量 ≡ io_list；提供 io_map 后 R5 全腿激活）
  闸门2b scene 闸门（②b 产物自检 + R5 全腿复跑：XML ≡ io_list ≡ io_map）
  闸门3 部署（可选，POST /deploy :8600 真编译；服务不在线记为 skipped，不阻塞；
       成功后 GET /status 做运行时观测——仅记录不参与裁定，程序身份兜底仍在
       验收脚本 require_program 内，闸门4 消费语义 lx 已确认，编排器不重复校验）
  闸门4 链路 B 验收（可选，scenario_<场景>.py 在线验收；OpenPLC 不在线记 skipped）
  通过 → final/ 冻结；MAX_ITERS(6) 未过 → best_effort（通过准则数最多一轮 + 失败报告）

全环（build_usd → run_isaac_headless → evaluate → verdict 归因路由）在仿真侧
接口就绪后接入（csk 文档 §7.4 表），本骨架已预留挂点。

产物落盘（gc 文档 §4，全量入 git）：
  runs/<task_id>/request.json + iter_NNN/{plcopen.xml, plc.st, scene.spec.json,
  io_map.json, gate.json} + final/ + summary.md

用法:
    python -m src.agent.orchestrator examples/specs/motion3axis.spec.json        # LLM 生成
    python -m src.agent.orchestrator examples/specs/motion3axis.spec.json --seed src/plc/motion3axis.xml
    python -m src.agent.orchestrator spec.json --deploy --acceptance             # 闸门3+4：需 OpenPLC 在线
    python -m src.agent.orchestrator --aml examples/aml/plotter3axis_station.aml \
        --request "三轴绘图仪：……" --seed src/plc/plotter3axis.xml --acceptance   # ⓪→① 全链
"""

import json
import os
import shutil
import subprocess
import sys
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
from .pipeline import PLCGenerator  # noqa: E402
from .scene_gen import SceneSpecGenerator  # noqa: E402
from .spec_validator import validate_requirement_spec  # noqa: E402

MAX_ITERS = 6
DEPLOY_URL = "http://127.0.0.1:8600/deploy"
ACCEPTANCE_TIMEOUT_S = 600


class Orchestrator:
    def __init__(self, runs_root=None, deploy_url=DEPLOY_URL, max_iters=MAX_ITERS,
                 project_root=PROJECT_ROOT, acceptance_timeout=ACCEPTANCE_TIMEOUT_S,
                 attribution_engine=None):
        self.runs_root = Path(runs_root) if runs_root else RUNS_DIR
        self.deploy_url = deploy_url
        self.max_iters = max_iters
        self.project_root = Path(project_root)
        self.acceptance_timeout = acceptance_timeout
        # 归因引擎（确定性坑库优先 / LLM 兜底）——只进反馈包，不参与闸门裁定
        self.attribution = attribution_engine or AttributionEngine()

    # ---------------- 归因与失败处理 ----------------
    def _fail(self, iter_dir, i, gate, errors, history):
        """统一闸门失败处理：归因 → 增强反馈包 → 留档。返回 feedback。"""
        errors = [str(e) for e in (errors or ["（闸门失败但未携带错误详情）"])]
        attribution = self.attribution.attribute(gate, errors)
        feedback = self._pack_feedback(errors, history, attribution)
        history.append({"iter": i, "gate": gate, "errors": errors})
        self._dump_gate(iter_dir, gate, ok=False, errors=errors,
                        attribution=attribution)
        return feedback

    # ---------------- 闸门 ----------------
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
        """跑验收脚本子进程（独立方法便于测试注入）。"""
        return subprocess.run(
            [sys.executable, str(script)], cwd=str(self.project_root),
            capture_output=True, text=True, timeout=self.acceptance_timeout,
            encoding="utf-8", errors="replace")

    def acceptance_gate(self, scenario):
        """闸门4：链路 B 在线验收——跑 lx 的 src/pipeline/scenario_<场景>.py。

        脚本一身两角（主站 + 被控对象仿真），自身经 require_program 校验 %QW20 程序
        身份。返回 (state, detail)：state ∈ ok / failed / skipped——OpenPLC/Modbus
        不在线记 skipped（与闸门3 同一半环语义：环境缺失不阻塞 final，真失败才回喂）。
        """
        script = self.project_root / "src" / "pipeline" / ("scenario_%s.py" % scenario)
        if not script.is_file():
            return "skipped", "无验收脚本 %s（场景未登记 scenario_*.py）" % script.name
        try:
            proc = self._run_acceptance(script)
        except subprocess.TimeoutExpired:
            return "failed", ["验收脚本超时（>%ss）" % self.acceptance_timeout]
        out = "\n".join(t for t in ((proc.stdout or "").strip(), (proc.stderr or "").strip()) if t)
        if "无法连接" in out or "ConnectionError" in out:
            return "skipped", "OpenPLC/Modbus 不在线——半环跳过在线验收"
        if proc.returncode == 0:
            return "ok", (out.splitlines() or ["验收通过"])[-1]
        return "failed", out.splitlines()[-40:] or ["验收失败（无输出）"]

    def status_probe(self):
        """GET /status（lx serve.py）：运行时状态 + 程序身份观测。

        仅记录、不参与闸门裁定——require_program 已内置于验收脚本做身份兜底
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

    # ---------------- 主循环 ----------------
    def solve(self, spec, generator, deploy=False, acceptance=None, echo=None,
              scene_generator=None, device_model=None):
        """执行闭环。返回 {status: final|best, iter, run_dir}。

        acceptance: 场景名（None=不跑闸门4）——用于定位 src/pipeline/scenario_<名>.py；
        deploy: 是否先过闸门3（真编译）。两闸门独立可选，验收脚本内 require_program
        自带程序身份校验，直接跑旧部署程序不会误判。
        scene_generator: ②b 场景描述生成器（None=跳过 ②b；默认建议
        SceneSpecGenerator()，确定性产物激活一致性 R5 全腿）。
        device_model: ⓪ 的设备模型（供 ②b 取 IO 地址与轴参数；None=降级分配）。
        echo: 可选回调 fn(event, payload)，供 CLI/测试观察循环过程。
        """
        def notify(event, payload):
            if echo:
                echo(event, payload)

        problems = validate_requirement_spec(spec)
        if problems:
            raise ValueError("requirement_spec 校验失败（人工介入点 1）: %s" % problems)

        task_id = spec["task_id"]
        run_dir = self.runs_root / task_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "request.json").write_text(
            json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")

        history = []          # 迭代记忆：改了什么 → 哪条闸门翻转
        feedback = None       # 上轮反馈包（程序拼装，不靠 LLM 现场发挥）
        best = None           # best-effort：进展最多的一轮

        for i in range(1, self.max_iters + 1):
            iter_dir = run_dir / ("iter_%03d" % i)
            iter_dir.mkdir(exist_ok=True)
            notify("iter_start", {"iter": i})

            gen = generator.generate(spec, feedback=feedback)
            xml_text = gen.get("xml")
            if not xml_text:
                feedback = self._fail(iter_dir, i, "generate", gen.get("errors", []), history)
                notify("gate_failed", {"iter": i, "gate": "generate"})
                continue
            (iter_dir / "plcopen.xml").write_text(xml_text, encoding="utf-8")

            # 闸门1+2 已在 generator.gate 内完成（xml2st + 一致性），此处复跑留档：
            ok, st_text, problems1 = xml2st.convert(iter_dir / "plcopen.xml")
            if not ok:
                feedback = self._fail(iter_dir, i, "xml2st", problems1, history)
                notify("gate_failed", {"iter": i, "gate": "xml2st"})
                continue
            (iter_dir / "plc.st").write_text(st_text, encoding="utf-8")

            ok2, problems2 = consistency_check(iter_dir / "plcopen.xml", spec["io_list"],
                                      device_model=device_model)
            hard2 = [p for p in problems2 if not p.startswith("SKIP")]
            if not ok2 or hard2:
                feedback = self._fail(iter_dir, i, "consistency", hard2, history)
                notify("gate_failed", {"iter": i, "gate": "consistency"})
                continue

            gates = {"xml2st": True, "consistency": [p for p in problems2 if p.startswith("SKIP")] or True}

            # ---- ②b 场景描述生成（确定性）+ 闸门2b：R5 全腿（XML ≡ io_list ≡ io_map）----
            if scene_generator is not None:
                try:
                    scene_out = scene_generator.generate(spec, device_model)
                except ValueError as exc:  # 生成器自检失败（spec 异常或内部回归）
                    feedback = self._fail(iter_dir, i, "scene", [str(exc)], history)
                    notify("gate_failed", {"iter": i, "gate": "scene"})
                    continue
                (iter_dir / "scene.spec.json").write_text(
                    json.dumps(scene_out["scene"], ensure_ascii=False, indent=2), encoding="utf-8")
                (iter_dir / "io_map.json").write_text(
                    json.dumps(scene_out["io_map"], ensure_ascii=False, indent=2), encoding="utf-8")
                ok5, problems5 = consistency_check(iter_dir / "plcopen.xml",
                                                   spec["io_list"], scene_out["io_map"],
                                                   device_model=device_model)
                hard5 = [p for p in problems5 if not p.startswith("SKIP")]
                if not ok5 or hard5:
                    feedback = self._fail(iter_dir, i, "scene", hard5, history)
                    notify("gate_failed", {"iter": i, "gate": "scene"})
                    continue
                gates["scene"] = {"ok": True,
                                  "assets": len(scene_out["scene"]["assets"]),
                                  "io_map_vars": len(scene_out["io_map"]["mappings"]),
                                  "r5": "active"}

            if deploy:
                state, detail = self.deploy_gate(iter_dir / "plcopen.xml")
                probe = self.status_probe()  # 观测性：运行时状态/程序身份（不裁定）
                if probe is not None:
                    detail = dict(detail) if isinstance(detail, dict) else {"result": detail}
                    detail["runtime_status"] = probe
                gates["deploy"] = {"state": state, "detail": detail}
                if state == "failed":
                    errs = detail.get("errors") if isinstance(detail, dict) else [str(detail)]
                    if isinstance(errs, dict):
                        errs = [str(errs)]
                    feedback = self._fail(iter_dir, i, "deploy", errs, history)
                    notify("gate_failed", {"iter": i, "gate": "deploy"})
                    continue

            if acceptance:
                state, detail = self.acceptance_gate(acceptance)
                gates["acceptance"] = {"state": state, "detail": detail}
                if state == "failed":
                    errs = detail if isinstance(detail, list) else [str(detail)]
                    feedback = self._fail(iter_dir, i, "acceptance", errs, history)
                    notify("gate_failed", {"iter": i, "gate": "acceptance"})
                    continue

            self._dump_gate(iter_dir, "all", ok=True, gates=gates)
            history.append({"iter": i, "gate": "all", "ok": True})
            self._consolidate(generator, spec, gates, history)
            self._finalize(run_dir, iter_dir, i, history)
            notify("final", {"iter": i, "run_dir": str(run_dir)})
            return {"status": "final", "iter": i, "run_dir": run_dir}

        # ---- best-effort：闸门推进最远的一轮 + 失败报告（人工介入点 2）----
        order = {"generate": 0, "xml2st": 1, "consistency": 2, "scene": 3, "deploy": 4,
                 "acceptance": 5, "all": 6}
        best = max(history, key=lambda h: order.get(h.get("gate"), -1)) if history else None
        if best and not best.get("ok"):
            self.attribution.memory.record_fix(
                best.get("gate"), best.get("errors", []),
                "迭代上限未收敛（人工介入点 2），best=%s" % best.get("gate"), "abandoned")
        self._write_summary(run_dir, history, best)
        notify("best_effort", {"run_dir": str(run_dir)})
        return {"status": "best_effort", "iter": (best or {}).get("iter"), "run_dir": run_dir}

    # ---------------- 产物 ----------------
    @staticmethod
    def _pack_feedback(errors, history, attribution=None):
        """反馈包：失败证据原文 + 归因（坑库/历史修复/LLM 兜底）+ 迭代记忆。"""
        passed = [h for h in history if h.get("ok")]
        lines = ["失败证据（原样）："] + ["- %s" % e for e in errors]
        if attribution:
            extra = AttributionEngine.format_feedback(attribution)
            if extra:
                lines += ["归因（只供修复参考，不改变闸门结论）：", extra]
        if passed:
            lines.append("迭代记忆：以下修改已通过对应闸门，禁止回退——")
            lines += ["- iter %s: %s" % (h["iter"], h.get("gate")) for h in passed]
        return "\n".join(lines)

    @staticmethod
    def _dump_gate(iter_dir, gate, ok, errors=None, gates=None, attribution=None):
        payload = {"gate": gate, "ok": ok}
        if errors:
            payload["errors"] = errors
        if gates:
            payload["gates"] = gates
        if attribution:
            payload["attribution"] = attribution
        (iter_dir / "gate.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    # ---------------- 知识沉淀（final 后） ----------------
    def _consolidate(self, generator, spec, gates, history):
        """final 后的知识沉淀：情景记忆（修复对）+ 模式卡自动策展。

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
        (run_dir / "summary.md").write_text("\n".join(lines), encoding="utf-8")


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
                        help="跳过 ②b 场景描述生成（默认启用：scene.spec.json + io_map.json）")
    parser.add_argument("--aml", default=None,
                        help="AutomationML 设备描述（⓪）——前置 ① 需求理解，需配合 --request")
    parser.add_argument("--modbus-host", default=None,
                        help="链路 B Modbus 主机（缺省 127.0.0.1；远程/VM 运行时传 IP，注入环境供验收子进程）")
    parser.add_argument("--modbus-port", type=int, default=None,
                        help="链路 B Modbus 端口（缺省 502）")
    parser.add_argument("--no-attribution", action="store_true",
                        help="关闭归因引擎（默认启用：坑库签名匹配 + LLM 兜底，只进反馈不裁定）")
    parser.add_argument("--request", default=None,
                        help="自然语言需求文本（或 .txt 文件路径），配合 --aml 使用")
    args = parser.parse_args()

    from .aml_parser import parse_aml
    from .client import BigModelClient
    from .config import MODEL

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
        print("① 需求理解：模式=%s" % ("llm" if client else "template"))
        res = RequirementUnderstander(client=client, model=MODEL).understand(
            request_text, device_model=device_model)
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
        print("① 完成（rounds=%s，io_list=%d 条）" % (report.get("rounds", 0), len(spec["io_list"])))
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

    if args.seed:
        generator = PLCGenerator(client=None, seed_xml=args.seed)
    else:
        api_key = get_api_key()
        if not api_key:
            print("未配置 API Key（ZHIPUAI_API_KEY）且未指定 --seed；退出。")
            return 2
        generator = PLCGenerator(client=BigModelClient(api_key), model=MODEL)

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
                        echo=lambda ev, p: print("[%s] %s" % (ev, p)),
                        scene_generator=scene_gen, device_model=device_model)
    print("\nRESULT: %s (iter=%s) -> %s" % (result["status"], result["iter"], result["run_dir"]))
    return 0 if result["status"] == "final" else 1


if __name__ == "__main__":
    sys.exit(main())
