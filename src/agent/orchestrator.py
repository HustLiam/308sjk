#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
端到端闭环编排器（solve 循环的权威实现骨架，gc 文档 §4）。

当前形态：**半环**（不含 Isaac 仿真侧）——
  ① spec 装载 + 契约校验（需求理解 LLM 澄清后续接入，人工介入点 1 保留为文件确认）
  ② PLC 代码生成（PLCGenerator，LLM 或种子模式）
  闸门1 xml2st 本地契约校验（毫秒级，失败即短路不进下一环）
  闸门2 三方一致性（XML 定位变量 ≡ io_list；io_map 腿仿真侧就绪后自动接入）
  闸门3 部署（可选，POST /deploy :8600 真编译；服务不在线记为 skipped，不阻塞）
  闸门4 链路 B 验收（可选，scenario_<场景>.py 在线验收；OpenPLC 不在线记 skipped）
  通过 → final/ 冻结；MAX_ITERS(6) 未过 → best_effort（通过准则数最多一轮 + 失败报告）

全环（gen_scene_spec → build_usd → run_isaac_headless → evaluate → verdict 归因路由）
在仿真侧接口就绪后接入（csk 文档 §7.4 表），本骨架已预留挂点。

产物落盘（gc 文档 §4，全量入 git）：
  runs/<task_id>/request.json + iter_NNN/{plcopen.xml, plc.st, gate.json} + final/ + summary.md

用法:
    python -m src.agent.orchestrator examples/specs/motion3axis.spec.json        # LLM 生成
    python -m src.agent.orchestrator examples/specs/motion3axis.spec.json --seed src/plc/motion3axis.xml
    python -m src.agent.orchestrator spec.json --deploy --acceptance             # 闸门3+4：需 OpenPLC 在线
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import xml2st  # noqa: E402

from .config import PROJECT_ROOT, RUNS_DIR, get_api_key  # noqa: E402
from .consistency_check import consistency_check  # noqa: E402
from .pipeline import PLCGenerator  # noqa: E402
from .spec_validator import validate_requirement_spec  # noqa: E402

MAX_ITERS = 6
DEPLOY_URL = "http://127.0.0.1:8600/deploy"
ACCEPTANCE_TIMEOUT_S = 600


class Orchestrator:
    def __init__(self, runs_root=None, deploy_url=DEPLOY_URL, max_iters=MAX_ITERS,
                 project_root=PROJECT_ROOT, acceptance_timeout=ACCEPTANCE_TIMEOUT_S):
        self.runs_root = Path(runs_root) if runs_root else RUNS_DIR
        self.deploy_url = deploy_url
        self.max_iters = max_iters
        self.project_root = Path(project_root)
        self.acceptance_timeout = acceptance_timeout

    # ---------------- 闸门 ----------------
    def deploy_gate(self, xml_path):
        """闸门3：POST /deploy 真编译。返回 (state, detail)：
        state ∈ ok / failed / skipped（服务不在线，半环不阻塞）。"""
        try:
            resp = requests.post(self.deploy_url, data=xml_path.read_bytes(),
                                 headers={"Content-Type": "application/xml"}, timeout=120)
        except requests.RequestException as exc:
            return "skipped", "deploy 服务不在线（%s）——半环跳过真编译" % exc.__class__.__name__
        if resp.status_code != 200:
            return "failed", "HTTP %d: %s" % (resp.status_code, resp.text[:500])
        result = resp.json()
        status = result.get("status")
        if status == "ok" or status is True:
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

    # ---------------- 主循环 ----------------
    def solve(self, spec, generator, deploy=False, acceptance=None, echo=None):
        """执行闭环。返回 {status: final|best, iter, run_dir}。

        acceptance: 场景名（None=不跑闸门4）——用于定位 src/pipeline/scenario_<名>.py；
        deploy: 是否先过闸门3（真编译）。两闸门独立可选，验收脚本内 require_program
        自带程序身份校验，直接跑旧部署程序不会误判。
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
                history.append({"iter": i, "gate": "generate", "errors": gen.get("errors", [])})
                feedback = self._pack_feedback(gen.get("errors", []), history)
                self._dump_gate(iter_dir, "generate", ok=False, errors=gen.get("errors", []))
                notify("gate_failed", {"iter": i, "gate": "generate"})
                continue
            (iter_dir / "plcopen.xml").write_text(xml_text, encoding="utf-8")

            # 闸门1+2 已在 generator.gate 内完成（xml2st + 一致性），此处复跑留档：
            ok, st_text, problems1 = xml2st.convert(iter_dir / "plcopen.xml")
            if not ok:
                history.append({"iter": i, "gate": "xml2st", "errors": problems1})
                feedback = self._pack_feedback(problems1, history)
                self._dump_gate(iter_dir, "xml2st", ok=False, errors=problems1)
                notify("gate_failed", {"iter": i, "gate": "xml2st"})
                continue
            (iter_dir / "plc.st").write_text(st_text, encoding="utf-8")

            ok2, problems2 = consistency_check(iter_dir / "plcopen.xml", spec["io_list"])
            hard2 = [p for p in problems2 if not p.startswith("SKIP")]
            if not ok2 or hard2:
                history.append({"iter": i, "gate": "consistency", "errors": hard2})
                feedback = self._pack_feedback(hard2, history)
                self._dump_gate(iter_dir, "consistency", ok=False, errors=hard2)
                notify("gate_failed", {"iter": i, "gate": "consistency"})
                continue

            gates = {"xml2st": True, "consistency": [p for p in problems2 if p.startswith("SKIP")] or True}

            if deploy:
                state, detail = self.deploy_gate(iter_dir / "plcopen.xml")
                gates["deploy"] = {"state": state, "detail": detail}
                if state == "failed":
                    errs = detail.get("errors") if isinstance(detail, dict) else [str(detail)]
                    history.append({"iter": i, "gate": "deploy", "errors": errs})
                    feedback = self._pack_feedback(errs, history)
                    self._dump_gate(iter_dir, "deploy", ok=False, errors=errs)
                    notify("gate_failed", {"iter": i, "gate": "deploy"})
                    continue

            if acceptance:
                state, detail = self.acceptance_gate(acceptance)
                gates["acceptance"] = {"state": state, "detail": detail}
                if state == "failed":
                    errs = detail if isinstance(detail, list) else [str(detail)]
                    history.append({"iter": i, "gate": "acceptance", "errors": errs})
                    feedback = self._pack_feedback(errs, history)
                    self._dump_gate(iter_dir, "acceptance", ok=False, errors=errs)
                    notify("gate_failed", {"iter": i, "gate": "acceptance"})
                    continue

            self._dump_gate(iter_dir, "all", ok=True, gates=gates)
            history.append({"iter": i, "gate": "all", "ok": True})
            self._finalize(run_dir, iter_dir, i, history)
            notify("final", {"iter": i, "run_dir": str(run_dir)})
            return {"status": "final", "iter": i, "run_dir": run_dir}

        # ---- best-effort：闸门推进最远的一轮 + 失败报告（人工介入点 2）----
        order = {"generate": 0, "xml2st": 1, "consistency": 2, "deploy": 3,
                 "acceptance": 4, "all": 5}
        best = max(history, key=lambda h: order.get(h.get("gate"), -1)) if history else None
        self._write_summary(run_dir, history, best)
        notify("best_effort", {"run_dir": str(run_dir)})
        return {"status": "best_effort", "iter": (best or {}).get("iter"), "run_dir": run_dir}

    # ---------------- 产物 ----------------
    @staticmethod
    def _pack_feedback(errors, history):
        """反馈包：失败证据原文 + 迭代记忆（禁止回退已通过的修改）。"""
        passed = [h for h in history if h.get("ok")]
        lines = ["失败证据（原样）："] + ["- %s" % e for e in errors]
        if passed:
            lines.append("迭代记忆：以下修改已通过对应闸门，禁止回退——")
            lines += ["- iter %s: %s" % (h["iter"], h.get("gate")) for h in passed]
        return "\n".join(lines)

    @staticmethod
    def _dump_gate(iter_dir, gate, ok, errors=None, gates=None):
        payload = {"gate": gate, "ok": ok}
        if errors:
            payload["errors"] = errors
        if gates:
            payload["gates"] = gates
        (iter_dir / "gate.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

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
    parser.add_argument("spec", help="requirement_spec JSON 路径")
    parser.add_argument("--seed", default=None, help="种子模式：指定已验收 XML 当生成产物（联调/回归）")
    parser.add_argument("--deploy", action="store_true", help="启用闸门3（POST /deploy 真编译）")
    parser.add_argument("--acceptance", action="store_true",
                        help="启用闸门4（链路 B 在线验收 scenario_<场景>.py；OpenPLC 离线记 skipped）")
    parser.add_argument("--scenario", default=None,
                        help="验收场景名（缺省：--seed 的文件名去扩展，否则 spec.task_id）")
    parser.add_argument("--max-iters", type=int, default=MAX_ITERS)
    parser.add_argument("--runs-root", default=None, help="runs/ 根目录（默认仓库 runs/）")
    args = parser.parse_args()

    from .client import BigModelClient
    from .config import MODEL

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
    orch = Orchestrator(runs_root=args.runs_root, max_iters=args.max_iters)
    result = orch.solve(spec, generator, deploy=args.deploy,
                        acceptance=scenario if args.acceptance else None,
                        echo=lambda ev, p: print("[%s] %s" % (ev, p)))
    print("\nRESULT: %s (iter=%s) -> %s" % (result["status"], result["iter"], result["run_dir"]))
    return 0 if result["status"] == "final" else 1


if __name__ == "__main__":
    sys.exit(main())
