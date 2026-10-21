# -*- coding: utf-8 -*-
"""④ 验证与反馈 MVP（MuJoCo 链路，2026-09-21 lx 实装；2026-09-22 采集通用化）。

三段式，全部由智能体产出的测试方案（spec）+ 场景接线（io_map）数据驱动，
不假设轴数、运动形态（平动/转动）或站台信号命名：

  build_channels()  spec.io_list + io_map → 采集通道表 + 开跑触发 + 完成信号
  collect()         进场复位 → 置触发 → 采样至完成信号或超时 → 复位
  evaluate()        确定性判定四类准则：event_delay / region_containment /
                    forbidden_state / sim_health
  attribute()       固定规则归因 R1~R4（非 LLM），回喂 agent

通道地址的单一源头（谁在哪个寄存器/线圈）：
  模拟量（INT@%QW）：io_map 分配（场景侧接线，plc_link 同源换算）；
  线圈（BOOL@%QX）：spec.io_list 条目的 address 字段（"%QX2.0"，站台 HMI 约定）；
  开跑触发（stimulus）：io_list 中 dir=input 且 trigger=true 的 BOOL 线圈；
  完成信号：acceptance 中 event_delay 的 to 信号（任一置位即收卷）。

区域判据分量与通道的对应次序：先按轴标注（bind.quantity/asset 首字母 X/Y/Z）
排序，无轴标注的通道按 %QW 地址升序排在轴标注通道之后。

准则格式（spec.acceptance[]，智能体生成；采集内容由准则引用的信号决定）：
  {"id","desc","type":"event_delay","from":{"signal","edge"},"to":{...},"op","value","unit"}
  {"id","desc","type":"region_containment","asset","region_center":[x,y,z],"tolerance","check_at"}
  {"id","desc","type":"forbidden_state","when":{"signal","equals"},"forbid":{...}}
  {"id","desc","type":"sim_health"}
"""
import os
import math
import re
import time

from pymodbus.client import ModbusTcpClient

_BIT_ADDR_RE = re.compile(r"^%QX(\d+)\.([0-7])$")
AXIS_ORDER = ("X", "Y", "Z")


def _bit_of(addr):
    """%QXn.m → 线圈位索引（字节×8 + 位）。"""
    m = _BIT_ADDR_RE.match(str(addr))
    if not m:
        raise ValueError("线圈地址须形如 %%QX2.0，实际 %r" % (addr,))
    return int(m.group(1)) * 8 + int(m.group(2))


def _axis_of(entry):
    """轴名推导（与 runtime/plc_link._axis_of 同一约定）：bind.quantity 首字母
    X/Y/Z，或 bind.asset 形如 x_axis。推导不出返回 None（按地址序参与排序）。"""
    bind = entry.get("bind", {}) or {}
    q = str(bind.get("quantity", "") or "")
    if q[:1].upper() in AXIS_ORDER:
        return q[:1].upper()
    a = str(bind.get("asset", "") or "")
    if a.endswith("_axis") and a[:1].upper() in AXIS_ORDER:
        return a[:1].upper()
    return None


class AcceptanceConfigurationError(ValueError):
    """验收规格不能驱动采集；修改 PLC 源码不能修复该错误。"""


def validate_collection_spec(spec):
    """在生成/部署前检查采集所需的激励和结束条件。"""
    points = {p["name"]: p for p in spec.get("io_list", [])}
    triggers = [p for p in points.values() if p.get("trigger") is True]
    errors = []
    if not triggers:
        errors.append("缺少 trigger=true 的启动输入")
    for point in triggers:
        if (point.get("dir") != "input" or point.get("type") != "BOOL"
                or not _BIT_ADDR_RE.fullmatch(str(point.get("address", "")))):
            errors.append("触发信号 %s 必须是带 %%QX 地址的 input BOOL" % point["name"])
    events = [a for a in spec.get("acceptance", []) if a.get("type") == "event_delay"]
    if not events:
        errors.append("缺少 event_delay 完成条件")
    for event in events:
        target = event.get("to", {})
        point = points.get(target.get("signal"), {})
        if (point.get("dir") != "output" or point.get("type") != "BOOL"
                or not _BIT_ADDR_RE.fullmatch(str(point.get("address", "")))):
            errors.append("完成信号 %s 必须是带 %%QX 地址的 output BOOL" % target.get("signal"))
        if target.get("edge") != "rising":
            errors.append("当前采集结束条件只支持完成信号 rising，实际 %s" % target.get("edge"))
        source = points.get(event.get("from", {}).get("signal"), {})
        if source.get("dir") == "input" and source.get("trigger") is not True:
            errors.append("输入事件 %s 未配置 trigger=true" % source.get("name"))
    if errors:
        raise AcceptanceConfigurationError("验收配置错误（不能靠重写 PLC 修复）：" + "；".join(errors))


def build_channels(spec, io_map):
    """spec + io_map → (channels, triggers, done)。

    channels: [{name, type:"analog", addr(%QWn), dir, range?, axis?} |
               {name, type:"bool", bit(%QXn.m), dir}]
    triggers: 开跑要置 1 的线圈名列表；
    done:     收卷信号名列表（event_delay 的 to，任一置位即止）。"""
    io_by_name = {e.get("name"): e for e in spec.get("io_list", [])}
    channels = []
    for e in io_map or []:
        if e.get("type") != "float" or "modbus" not in e:
            continue
        name = e.get("plc_var")
        point = io_by_name.get(name, {})
        channels.append({"name": name, "type": "analog",
                         "addr": int(e["modbus"]["plc_addr"][3:]),
                         "dir": e.get("dir"),
                         "range": point.get("range"),
                         "axis": _axis_of(e)})
    for point in spec.get("io_list", []):
        if point.get("type") == "BOOL" and point.get("address"):
            channels.append({"name": point["name"], "type": "bool",
                             "bit": _bit_of(point["address"]),
                             "dir": point.get("dir")})
    triggers = [p["name"] for p in spec.get("io_list", [])
                if p.get("type") == "BOOL" and p.get("dir") == "input"
                and p.get("trigger") and p.get("address")]
    done = []
    for ac in spec.get("acceptance", []):
        if ac.get("type") == "event_delay":
            n = (ac.get("to") or {}).get("signal")
            if n and n not in done:
                done.append(n)
    return channels, triggers, done


class Collector:
    """按通道表采样 OpenPLC %Q 区。读写跨度由通道地址推导，线圈整组写
    （SafeCoilIO 纪律，完整 16 位跨度，不破坏相邻位）。"""

    def __init__(self, host=None, port=None, poll_s=0.05):
        # 缺省从环境变量取（远程 VM 跑 OpenPLC 的场景，如 gc 的 VMware VM）
        host = host or os.environ.get("MODBUS_HOST", "127.0.0.1")
        port = port or int(os.environ.get("MODBUS_PORT", "502"))
        self.cli = ModbusTcpClient(host, port=port, timeout=2.0)
        assert self.cli.connect(), f"OpenPLC {host}:{port} 不可达"
        self.poll_s = poll_s

    # ---------- 线圈整组写（复位/触发共用） ----------

    def _write_group(self, channels, on_bits):
        bits = [c["bit"] for c in channels if c["type"] == "bool"]
        if not bits:
            return
        span = (max(bits) // 16 + 1) * 16
        arr = [False] * span
        for b in on_bits:
            arr[b] = True
        self.cli.write_coils(address=0, values=arr, slave=1)

    def reset(self, channels):
        self._write_group(channels, [])

    def trigger(self, channels, names):
        by_name = {c["name"]: c["bit"] for c in channels if c["type"] == "bool"}
        self._write_group(channels, [by_name[n] for n in names if n in by_name])

    # ---------- 采样 ----------

    def sample_once(self, channels):
        s = {}
        # Status is read before numeric feedback: sequential acquisition then
        # produces observable lag, rather than pairing old feedback with newer status.
        # This does not make Modbus reads atomic; declared response bounds still apply.
        bits = [c["bit"] for c in channels if c["type"] == "bool"]
        if bits:
            coils = self.cli.read_coils(
                address=0, count=(max(bits) // 16 + 1) * 16, slave=1).bits
            for c in channels:
                if c["type"] == "bool":
                    s[c["name"]] = bool(coils[c["bit"]])
        addrs = [c["addr"] for c in channels if c["type"] == "analog"]
        if addrs:
            regs = self.cli.read_holding_registers(
                address=0, count=max(addrs) + 1, slave=1).registers
            for c in channels:
                if c["type"] == "analog":
                    value = regs[c["addr"]]
                    if (c.get("range") or [0])[0] < 0 and value >= 32768:
                        value -= 65536
                    s[c["name"]] = value
        return s

    def collect(self, channels, triggers=(), done=(), timeout_s=120.0, settle_s=0.3):
        """触发前复位 → 稳定 → 置触发线圈 → 采样直到任一 done 信号置位或超时。
        返回 trace 样本列表。"""
        self.reset(channels)
        trace, t0 = [], time.monotonic()
        try:
            time.sleep(settle_s)
            before = self.sample_once(channels)
            before["t"] = 0.0
            trace.append(before)
            self.trigger(channels, triggers)
            t0 = time.monotonic()
            while time.monotonic() - t0 < timeout_s:
                s = self.sample_once(channels)
                s["t"] = round(time.monotonic() - t0, 3)
                trace.append(s)
                if done and all(s.get(n) for n in done):
                    break
                time.sleep(self.poll_s)
        finally:
            try:
                self.reset(channels)
            finally:
                self.cli.close()
        return trace


def _rise(trace, name):
    # 触发先于首采样时首样本即 True——视为观测窗起点上升
    if trace and trace[0].get(name):
        return trace[0]["t"]
    for a, b in zip(trace, trace[1:]):
        if not a.get(name) and b.get(name):
            return b["t"]
    return None


def _feedback_channels(channels):
    """区域判据用的反馈通道：模拟量 input（仿真→PLC），轴标注序优先。"""
    fbs = [c for c in (channels or [])
           if c.get("type") == "analog" and c.get("dir") == "input"]
    return sorted(fbs, key=lambda c: (AXIS_ORDER.index(c["axis"]) if c.get("axis")
                                      in AXIS_ORDER else len(AXIS_ORDER),
                                      c["addr"]))


def signal_matches(sample, condition):
    """Shared, explicit predicates for acceptance and progress comparison."""
    actual = sample.get(condition["signal"])
    if actual is None:
        return False
    if "equals" in condition:
        expected = condition["equals"]
        if "tolerance" in condition:
            return (not isinstance(actual, bool) and isinstance(actual, (int, float))
                    and abs(actual - expected) <= condition["tolerance"])
        return actual == expected
    value = condition.get("value")
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    operations = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                  ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
    return condition.get("op") in operations and operations[condition["op"]](actual, value)


def condition_violations(ac, trace):
    from condition_response import bounded_violations
    response = ac.get("response_s", 0)
    if response and (ac.get("purpose") != "derived_state_consistency" or response > .04):
        raise ValueError("非零响应预算只允许明确声明的派生状态一致性，且不得超过40ms")
    eligible = [s for s in trace if signal_matches(s, ac["when"])]
    mask = [signal_matches(s, ac["when"]) and signal_matches(s, ac["forbid"]) for s in trace]
    if not response:
        raw = [i for i, bad in enumerate(mask) if bad]
        return eligible, {"raw_indices": raw, "confirmed_indices": raw, "intervals": [],
                          "max_observed_duration_s": 0, "unresolved": bool(mask and mask[-1])}
    source = ac.get("response_source")
    reference = next((ac[key] for key in ("when", "forbid") if ac[key]["signal"] == source), None)
    if reference is None:
        raise ValueError("派生状态响应必须显式引用条件中的传感器源response_source")
    return eligible, bounded_violations(trace, mask, response,
                                       [signal_matches(sample, reference) for sample in trace])


def evaluate(acceptance, trace, channels=None):
    """确定性判定（契约④ canonical 字段）。返回 [{id, desc, pass, evidence}]。"""
    out = []
    end = trace[-1] if trace else {}
    for ac in acceptance:
        typ, r = ac.get("type"), {"id": ac.get("id"), "desc": ac.get("desc"),
                                  "pass": False, "evidence": ""}
        required = ([ac["from"]["signal"], ac["to"]["signal"]] if typ == "event_delay"
                    else [ac["when"]["signal"], ac["forbid"]["signal"]]
                    if typ == "forbidden_state" else [])
        if not trace or any(any(n not in sample for n in required) for sample in trace):
            r["evidence"] = "缺少有效 trace 或验收引用信号，不能判定通过"
            out.append(r)
            continue
        if typ == "event_delay":
            f = ac["from"]["signal"] + ("↓" if ac["from"].get("edge") == "falling" else "↑")
            to = ac["to"]["signal"] + ("↓" if ac["to"].get("edge") == "falling" else "↑")
            def edge_time(ref, after=0.0):
                from trace_query import edge_times
                times = [t for t in edge_times(trace, ref) if t >= after]
                occurrence = ref.get("occurrence", "first")
                if occurrence not in ("first", "last"):
                    return None
                return times[-1 if occurrence == "last" else 0] if times else None
            t0 = edge_time(ac["from"])
            t1 = edge_time(ac["to"], t0) if t0 is not None else None
            if t0 is not None and t1 is not None:
                d = t1 - t0 if t1 >= t0 else None
                ops = {"<": lambda a, b: a < b, "<=": lambda a, b: a <= b,
                       "==": lambda a, b: a == b, ">": lambda a, b: a > b,
                       ">=": lambda a, b: a >= b}
                r["pass"] = d is not None and ops[ac.get("op", "<=")](d, ac["value"])
                r["evidence"] = f"{f}@{t0}s → {to}@{t1}s = {d if d is None else round(d,2)}s {ac.get('op','<=')} {ac['value']}s"
                r["event_selection"] = {k: ac[k].get("occurrence", "first") for k in ("from", "to")}
            else:
                r["evidence"] = (f"未观测到真实的 {f} 边沿；首样本电平不代表边沿"
                                 if t0 is None else f"{to} 未在观测窗内置位（{f}@{t0}）")
        elif typ == "region_containment":
            from src.pipeline.region_contract import explicit_region_targets
            if "signals" in ac:
                try:
                    targets = explicit_region_targets(ac)
                except ValueError as exc:
                    r["evidence"] = "终态区域配置错误：%s" % exc
                    out.append(r)
                    continue
                r["signals"] = list(ac["signals"])
                r["pass"] = all(isinstance(end.get(n), (int, float)) and not isinstance(end[n], bool)
                    and math.isfinite(end[n]) and abs(end[n]-target) <= tol for n, target, tol in targets)
                r["evidence"] = "终态 %s；目标 %s（tol±%s）" % (
                    {n: end.get(n) for n, _, _ in targets}, {n: target for n, target, _ in targets}, ac["tolerance"])
                out.append(r)
                continue
            c, tol = ac["region_center"], ac["tolerance"]
            fbs = _feedback_channels(channels)
            if not fbs:
                r["evidence"] = "无反馈通道（模拟量 input）可判终点"
            else:
                names = [c_["name"] for c_ in fbs]
                vals = tuple(end.get(n) for n in names)
                pairs = [(n, v, c[i]) for i, (n, v) in enumerate(zip(names, vals))
                         if i < len(c)]
                r["pass"] = all(v is not None and abs(v - exp) <= tol
                                for _, v, exp in pairs) and bool(pairs)
                r["evidence"] = (f"终态 ({', '.join(f'{n}={v}' for n, v, _ in pairs)})"
                                 f" vs 中心 {c}（tol±{tol}）")
        elif typ == "forbidden_state":
            w, f = ac["when"], ac["forbid"]
            eligible, violations = condition_violations(ac, trace)
            bad = violations["confirmed_indices"]
            r["observed_samples"], r["violation_samples"] = len(eligible), len(bad)
            r["raw_violation_samples"] = len(violations["raw_indices"])
            r["response_s"] = ac.get("response_s", 0)
            r["response_intervals"] = violations["intervals"]
            r["max_observed_response_s"] = violations.get("max_response_delay_s", 0)
            r["unresolved"] = violations["unresolved"]
            r["observed"] = bool(eligible)
            def describe(ref):
                return (f"{ref['signal']}={ref['equals']}" + (f"±{ref['tolerance']}" if "tolerance" in ref else "")
                        if "equals" in ref else f"{ref['signal']}{ref['op']}{ref['value']}")
            r["pass"], r["evidence"] = not bad, f"{describe(w)} 期间 {describe(f)} 样本 {len(bad)}（前提样本 {len(eligible)}）"
            if ac.get("response_s"):
                r["evidence"] += ("；原始不一致%d样本，最长观测响应%.4fs，允许%.3fs%s" %
                                  (r["raw_violation_samples"], r["max_observed_response_s"], ac["response_s"],
                                   "，结束时仍未恢复" if r["unresolved"] else ""))
        elif typ == "sim_health":
            rngs = {c["name"]: c["range"] for c in (channels or [])
                    if c.get("type") == "analog" and c.get("range")}
            if not rngs:
                r["pass"], r["evidence"] = False, "未提供通道量程，无法验证仿真健康"
            else:
                bad = {n: sum(1 for s in trace
                              if s.get(n) is not None and not (lo <= s[n] <= hi))
                       for n, (lo, hi) in rngs.items()}
                missing = sum(1 for n in rngs for s in trace if s.get(n) is None)
                total = sum(bad.values()) + missing
                r["pass"] = total == 0
                r["evidence"] = "行程外样本 %d%s" % (
                    total, "".join("，%s:%d" % kv for kv in bad.items() if kv[1]))
        else:
            r["evidence"], r["pass"] = f"未知准则类型 {typ}，禁止判定通过", False
        out.append(r)
    return out


def attribute(results, trace, channels, done_names=()):
    """固定规则归因（非 LLM）：verdict+trace → 原因与修复建议，回喂 agent。"""
    diag = ["[归因-固定规则]"]
    outs = [c["name"] for c in channels if c["type"] == "analog" and c.get("dir") == "output"]
    ins = [c["name"] for c in channels if c["type"] == "analog" and c.get("dir") == "input"]
    out_moved = any(len({s.get(c) for s in trace}) > 1 for c in outs) if outs else False
    in_moved = any(len({s.get(c) for s in trace}) > 1 for c in ins) if ins else False
    done = bool(trace) and any(trace[-1].get(n) for n in done_names)
    if not out_moved:
        diag.append("R1 程序未推进：输出通道（%s）全程不变——触发信号未写入/状态机"
                    "卡初态/使能门控未开。核对触发线圈与 CASE 初值。"
                    % (", ".join(outs) or "无"))
    elif not in_moved:
        diag.append("R2 指令未达执行器：输出在动但反馈恒定——plc_link 通道配对或"
                    "换算错误（核对启动换算表告警）、MuJoCo 运行时未连。")
    elif not done:
        last = {c: trace[-1].get(c) for c in outs}
        unchanged = [c for c in outs if len({s.get(c) for s in trace}) == 1]
        diag.append(f"R3 序列未完成：输出终值 {last}；全程未变化的输出通道 {unchanged}。"
                    "这些观测不能确定是容差或时长问题；先检查停滞通道的状态转移、"
                    "联锁条件及编译前后控制逻辑是否一致，不应直接放宽验收。")
    else:
        diag.append("R4 完成但判据未满足：轨迹/时序与准则不符——核对判据参数"
                    "（目标坐标、容差、信号语义）与程序目标表。")
    for r in results:
        if not r["pass"]:
            diag.append(f"  ✗ {r['id']}: {r['evidence']}")
    return "\n".join(diag)
