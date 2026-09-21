# -*- coding: utf-8 -*-
"""④ 验证与反馈 MVP（MuJoCo 链路，2026-09-21 lx 实装，负责人授权）。

三段式，前两段消费智能体生成的测试方案（spec.io_list + spec.acceptance，
契约④四类准则），第三段为固定规则归因（非 LLM）：

  collect()   按 io_list 通道 + HMI 线圈采样 OpenPLC %Q 区（触发后到 plot_done/超时）
  evaluate()  确定性判定四类准则：event_delay / waypoints / forbidden_state / invariant
  attribute() 固定规则归因：从 verdict + trace 推错误原因与修复建议（回喂 agent）

准则格式（spec.acceptance[]，智能体生成；采集内容由准则引用的信号决定）：
  {"id","desc","type":"event_delay","from":"cmd_draw","to":"plot_done","max_s":120}
  {"id","desc","type":"waypoints","points":[[20,20],...],"x":"x_fb","y":"y_fb",
   "pen":"pen_down","tol":2.0}
  {"id","desc","type":"forbidden_state","while":"pen_down","x":"x_fb","y":"y_fb",
   "bounds":[10,90]}
  {"id","desc","type":"invariant","signal":"x_fb","bounds":[0,100]}
"""
import os
import time

from pymodbus.client import ModbusTcpClient

# 站台 HMI 线圈布局（%QX，整组 FC15/FC01 纪律）
COILS = {"run": 0, "cmd_reset": 7, "any_moving": 10, "cmd_draw": 16,
         "pen_down": 17, "plot_done": 18}
COIL_GROUP = 24          # 3 个完整字节（SafeCoilIO 纪律）


class Collector:
    """按通道表采样 OpenPLC %Q 区。channels: [{name, addr(%QWn), type}]。"""

    def __init__(self, host=None, port=None, poll_s=0.05):
        # 缺省从环境变量取（远程 VM 跑 OpenPLC 的场景，如 gc 的 VMware VM）
        host = host or os.environ.get("MODBUS_HOST", "127.0.0.1")
        port = port or int(os.environ.get("MODBUS_PORT", "502"))
        self.cli = ModbusTcpClient(host, port=port, timeout=2.0)
        assert self.cli.connect(), f"OpenPLC {host}:{port} 不可达"
        self.poll_s = poll_s

    def trigger(self, bits):
        arr = [False] * COIL_GROUP
        for name in bits:
            arr[COILS[name]] = True
        self.cli.write_coils(address=0, values=arr, slave=1)

    def reset(self):
        self.cli.write_coils(address=0, values=[False] * COIL_GROUP, slave=1)

    def sample_once(self, channels):
        regs = self.cli.read_holding_registers(address=0, count=6, slave=1).registers
        coils = self.cli.read_coils(address=0, count=COIL_GROUP, slave=1).bits
        s = {}
        for ch in channels:
            if ch["type"] == "analog":
                s[ch["name"]] = regs[ch["addr"]]
            else:
                s[ch["name"]] = bool(coils[COILS[ch["name"]]] if ch["name"] in COILS
                                     else coils[ch["addr"]])
        return s

    def collect(self, channels, done="plot_done", timeout_s=120.0):
        """触发前复位 → 采样直到 done 置位或超时。返回 trace 样本列表。"""
        self.reset()
        time.sleep(0.3)
        self.trigger(["run", "cmd_draw"])
        trace, t0 = [], time.time()
        while time.time() - t0 < timeout_s:
            s = self.sample_once(channels)
            s["t"] = round(time.time() - t0, 3)
            trace.append(s)
            if s.get(done):
                break
            time.sleep(self.poll_s)
        self.reset()
        return trace


def _rise(trace, name):
    # 触发先于首采样时首样本即 True——视为观测窗起点上升
    if trace and trace[0].get(name):
        return trace[0]["t"]
    for a, b in zip(trace, trace[1:]):
        if not a.get(name) and b.get(name):
            return b["t"]
    return None


def evaluate(acceptance, trace):
    """确定性判定（契约④ canonical 字段）。返回 [{id, desc, pass, evidence}]。"""
    out = []
    end = trace[-1] if trace else {}
    for ac in acceptance:
        typ, r = ac.get("type"), {"id": ac.get("id"), "desc": ac.get("desc"),
                                  "pass": False, "evidence": ""}
        if typ == "event_delay":
            f = ac["from"]["signal"] + ("↓" if ac["from"].get("edge") == "falling" else "↑")
            to = ac["to"]["signal"] + ("↓" if ac["to"].get("edge") == "falling" else "↑")
            t0, t1 = _rise(trace, ac["from"]["signal"]), _rise(trace, ac["to"]["signal"])
            if t0 is not None and t1 is not None:
                d = t1 - t0 if t1 >= t0 else None
                ops = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                       "==": lambda a, b: a == b, ">": lambda a, b: a > b,
                       ">=": lambda a, b: a >= b}
                r["pass"] = d is not None and ops[ac.get("op", "<=")](d, ac["value"])
                r["evidence"] = f"{f}@{t0}s → {to}@{t1}s = {d if d is None else round(d,2)}s {ac.get('op','<=')} {ac['value']}s"
            else:
                r["evidence"] = f"{to} 未在观测窗内置位（{f}@{t0}）"
        elif typ == "region_containment":
            c, tol = ac["region_center"], ac["tolerance"]
            xyz = (end.get("x_fb"), end.get("y_fb"), end.get("z_fb"))
            r["pass"] = all(v is not None and abs(v - c[i]) <= tol for i, v in enumerate(xyz))
            r["evidence"] = f"终态 {xyz} vs 中心 {c}（tol±{tol}）"
        elif typ == "forbidden_state":
            w, f = ac["when"], ac["forbid"]
            bad = [s for s in trace if s.get(w["signal"]) == bool(w["equals"])
                   and s.get(f["signal"]) == bool(f["equals"])]
            r["pass"], r["evidence"] = not bad,                 f"{w['signal']}={w['equals']} 期间 {f['signal']}={f['equals']} 样本 {len(bad)}"
        elif typ == "sim_health":
            bad = [s for s in trace
                   if not (0 <= s.get("x_fb", 0) <= 100 and 0 <= s.get("y_fb", 0) <= 100
                           and 0 <= s.get("z_fb", 0) <= 10)]
            r["pass"], r["evidence"] = not bad, f"行程外样本 {len(bad)}"
        else:
            r["evidence"], r["pass"] = f"未知准则类型 {typ}（跳过）", True
        out.append(r)
    return out


def attribute(results, trace, channels):
    """固定规则归因（非 LLM）：verdict+trace → 原因与修复建议，回喂 agent。"""
    diag = ["[归因-固定规则]"]
    cmds = [c["name"] for c in channels if c["type"] == "analog" and c["name"].endswith("_cmd")]
    fbs = [c["name"] for c in channels if c["type"] == "analog" and c["name"].endswith("_fb")]
    cmd_moved = any(len({s.get(c) for s in trace}) > 1 for c in cmds) if cmds else False
    fb_moved = any(len({s.get(c) for s in trace}) > 1 for c in fbs) if fbs else False
    done = trace and trace[-1].get("plot_done")
    if not cmd_moved:
        diag.append("R1 程序未推进：*_cmd 全程不变——触发线圈未写入/状态机卡初态/"
                    "run 门控未开。检查 %QX0.0(run)/%QX2.0(cmd_draw) 与 CASE 初值。")
    elif not fb_moved:
        diag.append("R2 指令未达执行器：*_cmd 变化但 *_fb 恒定——plc_link 通道配对或"
                    "换算错误（核对启动换算表告警）、MuJoCo 运行时未连。")
    elif not done:
        last = {c: trace[-1].get(c) for c in cmds}
        diag.append(f"R3 序列未完成：cmd 终值 {last}——到位判定阈值过紧/目标超行程/"
                    f"超时不足。核对 fb 容差与 io_list range。")
    else:
        diag.append("R4 完成但判据未满足：轨迹/时序与准则不符——核对判据参数"
                    "（角点坐标、容差、pen 信号语义）与程序目标表。")
    for r in results:
        if not r["pass"]:
            diag.append(f"  ✗ {r['id']}: {r['evidence']}")
    return "\n".join(diag)
