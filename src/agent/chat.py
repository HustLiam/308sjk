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
    lines = ["── 需求规格（我整理的版本，请核对）────────────────────"]
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
        print("我读这份 AML 时发现了问题，先不往下走了：")
        for p in problems:
            print("   - %s" % p)
        return 2
    n_io = len(device_model["io_points"])
    n_ax = len(device_model["kinematics"]["axes"])
    print("AML 我读完了：这是「%s」，一共 %d 台设备、%d 个 IO 通道、%d 根运动轴。"
          % (device_model["station"], len(device_model["devices"]), n_io, n_ax))

    # ---- ① 需求理解 + 回显/修正循环（人工介入点 1） ----
    understander = RequirementUnderstander(
        client=BigModelClient(get_api_key()) if get_api_key() else None, model=MODEL)
    request_text = args.request
    if request_text is None:
        request_text = read_multiline("请描述你的需求（多行，空行结束；写清工艺意图和安全要求就行，数值细节可以不写）：")
    if not request_text.strip():
        print("需求是空的，我先退出了。")
        return 2
    if understander.client is None:
        print("（没找到 ZHIPUAI_API_KEY，我先退化为模板模式——只有设备 IO 预填，工艺部分会是空白。）")

    # ---- 轨迹参数化路线：绘图类需求先确认几何参数（确定性轨迹规划） ----
    trajectory = None
    ex = understander.extract_shape(request_text)
    if ex["shape"] in ("square", "circle"):
        from .orchestrator import _terminal_confirm
        from .trajectory import goal_text, plan_with_confirm
        trajectory = plan_with_confirm(ex["shape"], ex["params"],
                                       confirm=_terminal_confirm)
        print("轨迹我规划好了：%s。序列器会严格按这份步表走。" % goal_text(trajectory))
        request_text = "%s。已确认几何：%s" % (request_text, goal_text(trajectory))

    print("明白了，我来把你的需求整理成正式规格…")
    result = understander.understand(request_text, device_model=device_model)
    spec = result["spec"]
    if spec is None:
        print("规格没组装成功，问题如下：")
        for p in (result["report"].get("problems")
                  or result["report"].get("history", [{}])[-1].get("problems", [])):
            print("   - %s" % p)
        return 2
    print("规格整理好了（模型用了 %s 轮），请你过目：" % result["report"].get("rounds", 0))

    while True:
        print(render_spec(spec, result["report"].get("pending", [])))
        if args.confirm:
            break
        ans = input("规格没问题的话回车，我就开始写代码；哪里不对直接告诉我改；q 退出: ").strip()
        if ans in ("", "y", "Y", "是", "确认", "没问题"):
            break
        if ans in ("q", "Q", "quit", "exit"):
            print("好的，就此打住（规格没有下发生成）。")
            return 0
        print("收到，我按你的意见只改相关部分，其他保持原样…")
        result = understander.refine(spec, request_text,
                                     device_model=device_model, correction=ans)
        if result["spec"] is None:
            print("这次修正没成功，先保留上一版规格。问题在：")
            for p in (result["report"].get("problems") or [])[:5]:
                print("   - %s" % p)
            continue
        spec = result["spec"]
        print("改好了（%s 轮），再请你过目：" % result["report"].get("rounds", 0))

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
    talk = lambda msg: print("  · " + msg)   # 生成器的实时进度播报（对话风格）
    generator = (PLCGenerator(client=client, seed_xml=seed,
                              generic_patterns_only=(args.no_curated_patterns
                                                     or trajectory is not None),
                              address_table=address_table, report=talk)
                 if (seed or client) else None)
    if generator is None:
        print("既无种子也无 API Key，无法生成。")
        return 2
    print("好的，我将用%s来写控制程序。" % ("已验收的种子工程（最快最稳）" if seed
                                          else "LLM 现场编写——过程会实时汇报进度"))

    # ---- 闸门环境探测 ----
    import os
    if args.modbus_host:
        os.environ["MODBUS_HOST"] = args.modbus_host
    serve_ok, modbus_ok = probe_gate_env(args.modbus_host)
    deploy = False if args.no_deploy else (args.deploy or serve_ok)
    acceptance = False if args.no_acceptance else (args.acceptance or modbus_ok)
    print("我探测了一下环境：编译服务%s、PLC 运行时%s——所以这一轮会%s。"
          % ("在线" if serve_ok else "离线", "在线" if modbus_ok else "离线",
             ("做真实编译" if deploy else "跳过") + (" + 在线验收" if acceptance else "")))

    # ---- solve 闭环（自主迭代，全程播报） ----
    from .orchestrator import narrate

    orch = Orchestrator(max_iters=args.max_iters,
                        attribution_engine=AttributionEngine(
                            client=BigModelClient(get_api_key())
                            if get_api_key() else None))
    res = orch.solve(spec, generator, deploy=deploy,
                     acceptance=args.scenario if acceptance else None,
                     echo=narrate, trajectory=trajectory,
                     scene_generator=SceneSpecGenerator(), device_model=device_model)

    run_dir = Path(res["run_dir"])
    print("\n" + "=" * 56)
    if res["status"] == "final":
        final = run_dir / "final"
        print("✅ 搞定了！第 %s 轮全部通过。你要的交付物都在这里：" % res["iter"])
        for name, desc in (("plcopen.xml", "PLC 控制代码（IEC 61131-10）"),
                           ("scene.spec.json", "仿真场景描述"),
                           ("io_map.json", "IO 映射表"),
                           ("gate.json", "全部验证证据")):
            print("   %-16s %s\n     %s" % (name, desc, final / name))
    else:
        print("⚠ 这一轮没有完全通过（保留到第 %s 轮的成绩）。"
              "失败原因分析我写在了这里：\n   %s" % (res.get("iter"), run_dir / "summary.md"))
    if not args.confirm:
        again = input("还想调整需求再跑一遍吗？直接说改哪里；回车就到这里: ").strip()
        if again:
            r2 = understander.refine(spec, request_text, device_model=device_model,
                                     correction=again)
            if r2["spec"]:
                print("新规格已经准备好（task_id=%s），重新运行我就能带着新要求再来一轮。" % r2["spec"]["task_id"])
    return 0 if res["status"] == "final" else 1


if __name__ == "__main__":
    sys.exit(main())
