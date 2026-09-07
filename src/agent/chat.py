#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
对话式 CLI（Agent 的用户交互形态；人工介入点 1 的落地，gc 文档 §2 / 待办 #8）。

交互流（用户只说需求，Agent 干其余的）：
    1. 输入 AML 设备描述路径（回车用示例绘图仪站）
    2. 输入自然语言需求（多行，空行结束；可粗粒度）
    3. ① 需求理解（LLM，io_list 锚定设备模型）→ **规格回显**
    4. 用户：回车确认 / 直接输入修正文本（定向最小修改，再次回显）/ q 放弃
    5. 确认后自主执行 solve 闭环（②a 生成→闸门→②b→部署→验收→final）
    6. 展示交付物（IEC 61131-10 XML + 场景 JSON）；可改需求重跑或退出

闸门环境（serve :8600 / OpenPLC Modbus）自动探测：可达则启用对应闸门，
不可达自动降级（部署/验收记 skipped，不阻塞 final——半环语义）。

用法（交互）:
    python -m src.agent.chat
用法（脚本化演示/测试，跳过交互）:
    python -m src.agent.chat --aml examples/aml/plotter3axis_station.aml \
        --request "在绘图平台上自动画一个正方形……" --confirm --seed src/plc/plotter3axis.xml \
        --deploy --acceptance --modbus-host 192.168.12.131
"""

import argparse
import json
import sys
from pathlib import Path

import requests

from .config import MODEL, PROJECT_ROOT, get_api_key
from .requirement import RequirementUnderstander

DEFAULT_AML = PROJECT_ROOT / "examples" / "aml" / "plotter3axis_station.aml"
# 可靠回显的验收准则要点（回显时提示用户重点核对——LLM 谓词健全性弱项）
_AC_WATCH = ("equals",)


# ---------------- 展示辅助 ----------------

def render_spec(spec, pending=()):
    """规格回显文本（人工介入点 1）：目标/约束/准则摘要 + 待澄清项。"""
    lines = ["── requirement_spec 回显（请核对）────────────────────"]
    lines.append("task_id: %s" % spec.get("task_id"))
    lines.append("工艺目标: %s" % spec.get("task_goal"))
    io = spec.get("io_list", [])
    di = sum(1 for i in io if i["dir"] == "input" and i["type"] == "BOOL")
    do = sum(1 for i in io if i["dir"] == "output" and i["type"] == "BOOL")
    ai = sum(1 for i in io if i["type"] == "INT")
    lines.append("io_list: %d 点（DI %d / DO %d / 模拟 %d，逐字锚定设备模型）"
                 % (len(io), di, do, ai))
    for c in spec.get("constraints", []):
        lines.append("  %s [%s] %s" % (c.get("id"), c.get("kind"), c.get("desc")))
    lines.append("验收准则:")
    for a in spec.get("acceptance", []):
        extra = ""
        if a.get("type") == "event_delay":
            extra = "%s%s→%s%s ≤ %ss" % (
                a["from"]["signal"], a["from"]["edge"][0], a["to"]["signal"],
                a["to"]["edge"][0], a["value"])
        elif a.get("type") == "region_containment":
            extra = "%s @%s 容差%s" % (a.get("asset"), a.get("region_center"), a.get("tolerance"))
        elif a.get("type") == "forbidden_state":
            extra = "当 %s=%s 时禁止 %s=%s" % (a["when"]["signal"], a["when"]["equals"],
                                              a["forbid"]["signal"], a["forbid"]["equals"])
        lines.append("  %s [%s] %s%s" % (a.get("id"), a.get("type"), a.get("desc"),
                                        ("（%s）" % extra) if extra else ""))
    for p in pending:
        lines.append("  待澄清: %s" % p)
    lines.append("──────────────────────────────────────────────")
    return "\n".join(lines)


def probe_gate_env(modbus_host=None):
    """探测闸门环境：serve /deploy 服务与 Modbus 运行时是否可达。"""
    import os
    s = requests.Session()
    s.trust_env = False  # Windows 系统代理会劫持 127.0.0.1（同 client.py 教训）
    serve_ok = False
    try:
        serve_ok = s.get("http://127.0.0.1:8600/health", timeout=3).status_code == 200
    except requests.RequestException:
        pass
    modbus_ok = False
    host = modbus_host or os.environ.get("MODBUS_HOST", "127.0.0.1")
    try:
        from pymodbus.client import ModbusTcpClient
        c = ModbusTcpClient(host, port=int(os.environ.get("MODBUS_PORT", "502")))
        modbus_ok = bool(c.connect())
        c.close()
    except Exception:
        pass
    return serve_ok, modbus_ok


def read_multiline(prompt):
    """多行输入：空行结束。"""
    print(prompt)
    lines = []
    while True:
        try:
            line = input("" if lines else "> ")
        except EOFError:
            break
        if not line.strip():
            break
        lines.append(line)
    return "\n".join(lines)


# ---------------- 主流程 ----------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    parser = argparse.ArgumentParser(description="gc 闭环 Agent 对话式 CLI")
    parser.add_argument("--aml", default=None, help="AML 路径（交互模式可回车用默认）")
    parser.add_argument("--request", default=None, help="自然语言需求（脚本化模式）")
    parser.add_argument("--confirm", action="store_true", help="脚本化模式：跳过回显确认")
    parser.add_argument("--seed", default=None, help="代码生成用种子 XML（省略则 LLM 现场生成）")
    parser.add_argument("--deploy", action="store_true", help="强制启用闸门3（默认按探测结果）")
    parser.add_argument("--no-deploy", action="store_true")
    parser.add_argument("--acceptance", action="store_true", help="强制启用闸门4（默认按探测结果）")
    parser.add_argument("--no-acceptance", action="store_true")
    parser.add_argument("--scenario", default="plotter3axis", help="验收场景名")
    parser.add_argument("--modbus-host", default=None)
    parser.add_argument("--max-iters", type=int, default=6)
    parser.add_argument("--no-curated-patterns", action="store_true",
                        help="生成仅用通用运动原语（排除自动策展场景卡）——泛化验证口径")
    args = parser.parse_args()

    from .aml_parser import parse_aml
    from .attribution import AttributionEngine
    from .client import BigModelClient
    from .orchestrator import Orchestrator
    from .pipeline import PLCGenerator
    from .scene_gen import SceneSpecGenerator

    print("=" * 56)
    print(" 308sjk 闭环 Agent（⓪→①→②a→②b→闸门→final）")
    print("=" * 56)

    # ---- ⓪ 设备描述 ----
    aml = args.aml
    if aml is None:
        aml = input("AML 设备描述路径 [回车=%s]: " % DEFAULT_AML.name).strip() or str(DEFAULT_AML)
    device_model, problems = parse_aml(aml)
    if problems:
        print("⓪ AML 解析存在问题：")
        for p in problems:
            print("   - %s" % p)
        return 2
    n_io = len(device_model["io_points"])
    n_ax = len(device_model["kinematics"]["axes"])
    print("⓪ 解析通过：%s（%d 设备 / %d IO / %d 轴）"
          % (device_model["station"], len(device_model["devices"]), n_io, n_ax))

    # ---- ① 需求理解 + 回显/修正循环（人工介入点 1） ----
    understander = RequirementUnderstander(
        client=BigModelClient(get_api_key()) if get_api_key() else None, model=MODEL)
    request_text = args.request
    if request_text is None:
        request_text = read_multiline("① 请描述需求（多行，空行结束；写清工艺意图与安全要求即可）：")
    if not request_text.strip():
        print("需求为空，退出。")
        return 2
    if understander.client is None:
        print("（未配置 ZHIPUAI_API_KEY：① 退化为模板模式——仅预填 io_list，工艺空白）")

    result = understander.understand(request_text, device_model=device_model)
    spec = result["spec"]
    if spec is None:
        print("① 规格组装失败：")
        for p in (result["report"].get("problems")
                  or result["report"].get("history", [{}])[-1].get("problems", [])):
            print("   - %s" % p)
        return 2
    print("① 规格生成（%s 轮）" % result["report"].get("rounds", 0))

    while True:
        print(render_spec(spec, result["report"].get("pending", [])))
        if args.confirm:
            break
        ans = input("确认生成代码？[回车=确认 / 输入修正文本 / q=退出]: ").strip()
        if ans in ("", "y", "Y", "是"):
            break
        if ans in ("q", "Q", "quit", "exit"):
            print("已退出（规格未下发生成）。")
            return 0
        print("①·refine 按你的修正定向更新规格…")
        result = understander.refine(spec, request_text,
                                     device_model=device_model, correction=ans)
        if result["spec"] is None:
            print("修正失败（保留上轮规格）：")
            for p in (result["report"].get("problems") or [])[:5]:
                print("   - %s" % p)
            continue
        spec = result["spec"]
        print("① 修正完成（%s 轮）" % result["report"].get("rounds", 0))

    # ---- 生成模式 ----
    seed = args.seed
    if seed is None and not args.confirm:
        pick = input("代码生成方式 [l=LLM 现场生成（每轮约5~8分钟） / 回车=用种子 %s]: "
                     % "src/plc/plotter3axis.xml").strip()
        if pick not in ("l", "L"):
            seed = "src/plc/plotter3axis.xml"
    client = BigModelClient(get_api_key()) if (seed is None and get_api_key()) else None
    address_table = {p["name"]: p["address"] for p in device_model.get("io_points", [])
                     if p.get("address")}
    generator = (PLCGenerator(client=client, seed_xml=seed,
                              generic_patterns_only=args.no_curated_patterns,
                              address_table=address_table)
                 if (seed or client) else None)
    if generator is None:
        print("既无种子也无 API Key，无法生成。")
        return 2
    print("②a 生成方式：%s" % ("种子（已验收基线）" if seed else "LLM 现场生成"))

    # ---- 闸门环境探测 ----
    import os
    if args.modbus_host:
        os.environ["MODBUS_HOST"] = args.modbus_host
    serve_ok, modbus_ok = probe_gate_env(args.modbus_host)
    deploy = False if args.no_deploy else (args.deploy or serve_ok)
    acceptance = False if args.no_acceptance else (args.acceptance or modbus_ok)
    print("闸门环境：serve(:8600)=%s → 闸门3%s；Modbus=%s → 闸门4%s"
          % ("在线" if serve_ok else "离线", "启用" if deploy else "跳过",
             "在线" if modbus_ok else "离线", "启用" if acceptance else "跳过"))

    # ---- solve 闭环（自主迭代） ----
    def on_event(ev, payload):
        if ev == "iter_start":
            print("── 迭代 %s ──" % payload.get("iter"))
        elif ev == "gate_failed":
            print("   ✗ 闸门 %s 失败（错误已回喂，进入下一迭代）" % payload.get("gate"))
        elif ev == "final":
            print("   ✓ 全部闸门通过")
        elif ev == "best_effort":
            print("   △ 达迭代上限，输出最优轮 + 失败报告")

    orch = Orchestrator(max_iters=args.max_iters,
                        attribution_engine=AttributionEngine(
                            client=BigModelClient(get_api_key())
                            if get_api_key() else None))
    res = orch.solve(spec, generator, deploy=deploy,
                     acceptance=args.scenario if acceptance else None,
                     echo=on_event,
                     scene_generator=SceneSpecGenerator(), device_model=device_model)

    run_dir = Path(res["run_dir"])
    print("\n" + "=" * 56)
    if res["status"] == "final":
        final = run_dir / "final"
        print("✅ 闭环收敛（iter=%s）。交付物：" % res["iter"])
        for name in ("plcopen.xml", "scene.spec.json", "io_map.json", "gate.json"):
            print("   %s" % (final / name))
        print("   （IEC 61131-10 代码 = plcopen.xml；场景描述 = scene.spec.json + io_map.json）")
    else:
        print("⚠ 未收敛（best_effort）。失败分析：%s" % (run_dir / "summary.md"))
    if not args.confirm:
        again = input("修改需求重跑？[输入新修正文本 / 回车退出]: ").strip()
        if again:
            r2 = understander.refine(spec, request_text, device_model=device_model,
                                     correction=again)
            if r2["spec"]:
                print("新规格 task_id=%s（请重跑本 CLI 继续闭环）" % r2["spec"]["task_id"])
    return 0 if res["status"] == "final" else 1


if __name__ == "__main__":
    sys.exit(main())
