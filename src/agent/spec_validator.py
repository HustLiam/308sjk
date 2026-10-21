#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
requirement_spec 校验器（契约①的可执行权威，gc 拥有）。

结构规则与 schemas/requirement_spec.schema.json 逐条对应；Schema 文件表达不了的
跨字段语义规则（S1~S4）在此实现。两者必须同一 RFC 内同步修改（主方案 §8.3）：

  S1  唯一性：io_list.name、acceptance.id、constraints.id 各自不得重复
  S2  量程：type=INT 必须带 range=[min,max] 且 min<max，且**完整落入**有符号域
      [-32768,32767] 或无符号域 [0,65535] 之一（lx 位宽表 %QW 承载 INT/UINT/WORD；
      lx 2026-09-03 收紧建议，v1.0.0-draft.3 落实——跨域混合量程单个 %QW 无法用
      同一符号解释承载）；type=BOOL 禁止带 range
  S3  信号引用：event_delay 的 from/to、forbidden_state 的 when/forbid 中
      的 signal 必须逐字存在于 io_list（判定引擎按 trace 通道名查找）
  S4  时间阈值：event_delay.value >= 0.1（>=100ms，通信时序约束，主方案 §7）
  S5  PLC 侧地址与采样扩展（2026-09-22 分层指令：spec 自足）：io_list 条目可带
      address（BOOL=%QXn.m 位号 / INT=%QWn 字号，⓪ 预填注入——②a 生成与
      ④采集器同源，不再回读 AML）与 trigger（input BOOL 的开跑 stimulus）
  S6  axes（可选顶层段，⓪ 确定性注入）：非空数组，每项 axis/stroke/poswin
      结构合法，io 角色通道必须逐字落在 io_list 内
  S7  hmi（可选顶层段，① LLM 设计、契约管形式）：elements 数组，每项
      kind∈{BUTTON,LAMP,GAUGE}、BUTTON.type∈{trigger,toggle}、signal 必须
      逐字落在 io_list 且 kind 与信号方向/类型匹配（BUTTON→input BOOL、
      LAMP→output BOOL、GAUGE→INT）、pos=[row,col] 网格坐标非负整数且
      不重叠——控件选择/类型语义/摆放由 agent 按需求判断（语义域不管）

用法:
    from spec_validator import validate_requirement_spec
    problems = validate_requirement_spec(spec)   # 空 list = 通过
"""

import re
import math

SCHEMA_VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+(-draft\.[0-9]+)?$")
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
TASK_ID_RE = re.compile(r"^[a-z][a-z0-9_]*$")
AC_ID_RE = re.compile(r"^AC[0-9]+$")
C_ID_RE = re.compile(r"^C[0-9]+$")

# 对外类型封闭集（lx 位宽契约：BOOL↔%QX、INT↔%QW；模拟量 INT@%QW 定点换算）
IO_TYPES = {"BOOL", "INT"}
IO_DIRS = {"input", "output"}
CONSTRAINT_KINDS = {"timing", "interlock", "exception"}
ACCEPTANCE_TYPES = {"event_delay", "region_containment", "forbidden_state", "sim_health"}
EDGES = {"rising", "falling"}
OPS = {"<=", "<", ">=", ">", "=="}
MIN_TIME_S = 0.1  # S4

# S2：INT 量程的 16 位寄存器域（lx 位宽表：一个 %QW 字承载 INT/UINT/WORD）。
# draft.3 收紧（lx 建议）：range 须完整落入有符号或无符号域之一——跨域混合量程
# （如 [-100,65535]）在单个 %QW 无法用同一符号解释承载
WORD_DOMAINS = {"有符号 INT16": (-32768, 32767), "无符号 UINT16": (0, 65535)}

TOP_REQUIRED = ("schema_version", "task_id", "task_goal", "io_list", "constraints", "acceptance")


def _err(problems, path, msg):
    problems.append("%s: %s" % (path, msg))


def _is_ident(value):
    return isinstance(value, str) and bool(IDENT_RE.match(value))


def _check_signal_ref(problems, path, signal, io_names):
    """S3：信号引用必须落在 io_list 内。"""
    if not _is_ident(signal):
        _err(problems, path, "signal %r 不是合法标识符" % (signal,))
        return
    if signal not in io_names:
        _err(problems, path, "signal %r 不在 io_list 中（判定引擎按 trace 通道名查找，逐字一致）"
             % (signal,))


def _check_io_point(problems, item, idx):
    path = "io_list[%d]" % idx
    if not isinstance(item, dict):
        return _err(problems, path, "必须是对象")
    for key in ("name", "dir", "type", "device"):
        if key not in item:
            _err(problems, path, "缺少必填字段 %r" % key)
    name = item.get("name")
    if not _is_ident(name):
        _err(problems, path, "name %r 不是合法标识符（与 ST 定位变量/io_map 对账键）" % (name,))
    if item.get("dir") not in IO_DIRS:
        _err(problems, path, "dir 必须是 input|output（input=仿真→PLC，output=PLC→仿真），实际 %r"
             % (item.get("dir"),))
    vtype = item.get("type")
    if vtype not in IO_TYPES:
        _err(problems, path, "type 必须是 BOOL|INT（对外冻结；REAL 仅限 POU 内部计算），实际 %r"
             % (vtype,))
    rng = item.get("range")
    if vtype == "INT":
        if not (isinstance(rng, (list, tuple)) and len(rng) == 2
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in rng)):
            _err(problems, path, "type=INT 必须带 range=[min,max]（io_map 定点换算的 raw 区间）")
        elif not rng[0] < rng[1]:
            _err(problems, path, "range 必须 min<max，实际 %r" % (list(rng),))
        elif not any(lo <= rng[0] and rng[1] <= hi for lo, hi in WORD_DOMAINS.values()):
            _err(problems, path, "range [%r,%r] 未完整落入有符号域 [-32768,32767] 或"
                 "无符号域 [0,65535] 之一（lx 位宽表：单个 %%QW 只能用一种符号解释承载，"
                 "S2 draft.3）" % (rng[0], rng[1]))
    elif vtype == "BOOL" and rng is not None:
        _err(problems, path, "type=BOOL 不允许带 range")
    if not isinstance(item.get("device"), str) or not item.get("device"):
        _err(problems, path, "device 必须是非空字符串（供场景侧选组件）")
    unit = item.get("unit")
    if unit is not None and not isinstance(unit, str):
        _err(problems, path, "unit 必须是字符串")
    # S5：PLC 侧地址与开跑 stimulus——BOOL 用 %QXn.m 位号、INT 用 %QWn 字号
    addr = item.get("address")
    if addr is not None:
        if vtype == "BOOL" and not re.match(r"^%QX\d+\.[0-7]$", str(addr)):
            _err(problems, path, "BOOL 的 address 须形如 %%QX2.0（字节.位），实际 %r" % (addr,))
        elif vtype == "INT" and not re.match(r"^%QW\d+$", str(addr)):
            _err(problems, path, "INT 的 address 须形如 %%QW3（字号），实际 %r" % (addr,))
    if item.get("trigger") is not None:
        if not (vtype == "BOOL" and item.get("dir") == "input" and addr is not None):
            _err(problems, path, "trigger 仅允许带 address 的 input BOOL（采集器开跑 stimulus）")


def _check_constraint(problems, item, idx):
    path = "constraints[%d]" % idx
    if not isinstance(item, dict):
        return _err(problems, path, "必须是对象")
    cid = item.get("id")
    if not (isinstance(cid, str) and C_ID_RE.match(cid)):
        _err(problems, path, "id 必须形如 C1/C2…，实际 %r" % (cid,))
    if item.get("kind") not in CONSTRAINT_KINDS:
        _err(problems, path, "kind 必须是 timing|interlock|exception，实际 %r" % (item.get("kind"),))
    desc = item.get("desc")
    if not isinstance(desc, str) or len(desc) < 4:
        _err(problems, path, "desc 必须是不少于 4 字的说明")


def _check_signal_edge(problems, node, path, io_names):
    if not isinstance(node, dict):
        return _err(problems, path, "必须是 {signal, edge} 对象")
    _check_signal_ref(problems, path + ".signal", node.get("signal"), io_names)
    if node.get("edge") not in EDGES:
        _err(problems, path, "edge 必须是 rising|falling，实际 %r" % (node.get("edge"),))
    if node.get("occurrence", "first") not in ("first", "last"):
        _err(problems, path, "occurrence 必须是 first|last")


def _check_signal_value(problems, node, path, io_names):
    if not isinstance(node, dict):
        return _err(problems, path, "必须是 {signal, equals} 对象")
    _check_signal_ref(problems, path + ".signal", node.get("signal"), io_names)
    if "equals" in node:
        if set(node) - {"signal", "equals", "tolerance"}:
            _err(problems, path, "equals 条件只允许 signal/equals/tolerance")
        if not isinstance(node.get("equals"), (bool, int, float)):
            _err(problems, path, "equals 必须是布尔或数值")
        if "tolerance" in node and (isinstance(node["equals"], bool) or
                not isinstance(node["tolerance"], (int, float)) or isinstance(node["tolerance"], bool)
                or not 0 < node["tolerance"] < float("inf")):
            _err(problems, path, "tolerance 仅用于数值，必须为有限正数")
    elif set(node) != {"signal", "op", "value"} or node.get("op") not in ("<", "<=", ">", ">="):
        _err(problems, path, "阈值条件必须为 signal/op/value，op为<、<=、>、>=")
    elif (not isinstance(node["value"], (int, float)) or isinstance(node["value"], bool)
          or not -float("inf") < node["value"] < float("inf")):
        _err(problems, path, "value 必须为有限数值")


def _check_acceptance(problems, item, idx, io_names):
    path = "acceptance[%d]" % idx
    if not isinstance(item, dict):
        return _err(problems, path, "必须是对象")
    acid = item.get("id")
    if not (isinstance(acid, str) and AC_ID_RE.match(acid)):
        _err(problems, path, "id 必须形如 AC1/AC2…，实际 %r" % (acid,))
    desc = item.get("desc")
    if not isinstance(desc, str) or len(desc) < 4:
        _err(problems, path, "desc 必须是不少于 4 字的说明")
    ac_type = item.get("type")
    if ac_type not in ACCEPTANCE_TYPES:
        return _err(problems, path, "type 封闭四类 %s，实际 %r（落不进四类的需求须重新组织）"
                    % (sorted(ACCEPTANCE_TYPES), ac_type))

    if ac_type == "event_delay":
        for key in ("from", "to", "op", "value", "unit"):
            if key not in item:
                _err(problems, path, "event_delay 缺少必填字段 %r" % key)
        _check_signal_edge(problems, item.get("from"), path + ".from", io_names)
        _check_signal_edge(problems, item.get("to"), path + ".to", io_names)
        if item.get("op") not in OPS:
            _err(problems, path, "op 必须是 %s 之一，实际 %r" % (sorted(OPS), item.get("op")))
        value, unit = item.get("value"), item.get("unit")
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            _err(problems, path, "value 必须是数值")
        elif value < MIN_TIME_S:
            _err(problems, path, "时间阈值 %.4gs < %.1gs（通信时序约束下限 100ms，主方案 §7）"
                 % (value, MIN_TIME_S))
        if unit != "s":
            _err(problems, path, "unit 冻结为 's'，实际 %r" % (unit,))

    elif ac_type == "region_containment":
        for key in ("asset", "region_center", "tolerance", "check_at"):
            if key not in item:
                _err(problems, path, "region_containment 缺少必填字段 %r" % key)
        if not isinstance(item.get("asset"), str) or not item.get("asset"):
            _err(problems, path, "asset 必须是非空字符串（SceneSpec 资产名）")
        center = item.get("region_center")
        size = len(item['signals']) if isinstance(item.get('signals'), list) else 3
        if not (isinstance(center, (list, tuple)) and len(center) == size and 1 <= size <= 64
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in center)):
            _err(problems, path, "region_center须与signals长度相等；旧格式须为[x,y,z]三个有限数")
        tol = item.get("tolerance")
        if not isinstance(tol, (int, float)) or isinstance(tol, bool) or not math.isfinite(tol) or tol <= 0:
            _err(problems, path, "tolerance 必须是 >0 的数")
        if item.get("check_at") != "end":
            _err(problems, path, "check_at v1 冻结为 'end'（扩展走 RFC），实际 %r" % (item.get("check_at"),))

    elif ac_type == "forbidden_state":
        response = item.get("response_s", 0)
        if (not isinstance(response, (int, float)) or isinstance(response, bool) or not 0 <= response <= .04):
            _err(problems, path, "response_s必须在0..0.04s内（用户批准的派生状态响应预算）")
        if item.get("purpose", "safety") not in ("safety", "derived_state_consistency"):
            _err(problems, path, "purpose必须为safety或derived_state_consistency")
        if response and item.get("purpose") != "derived_state_consistency":
            _err(problems, path, "安全互锁不得使用派生状态的响应预算")
        if response and item.get("response_source") not in [item.get(k, {}).get("signal") for k in ("when", "forbid") if isinstance(item.get(k), dict)]:
            _err(problems, path, "派生状态response_source必须引用when/forbid中的传感器输入")
        for key in ("when", "forbid"):
            if key not in item:
                _err(problems, path, "forbidden_state 缺少必填字段 %r" % key)
        _check_signal_value(problems, item.get("when"), path + ".when", io_names)
        _check_signal_value(problems, item.get("forbid"), path + ".forbid", io_names)

    # sim_health：无类型专属字段


def validate_requirement_spec(spec):
    """校验 requirement_spec，返回问题列表（空 = 通过）。"""
    problems = []
    if not isinstance(spec, dict):
        return ["根节点必须是 JSON 对象"]
    for key in TOP_REQUIRED:
        if key not in spec:
            _err(problems, "$", "缺少必填字段 %r" % key)

    version = spec.get("schema_version")
    if not (isinstance(version, str) and SCHEMA_VERSION_RE.match(version)):
        _err(problems, "schema_version", "必须是语义化版本（如 1.0.0 / 1.0.0-draft.1），实际 %r" % (version,))
    task_id = spec.get("task_id")
    if not (isinstance(task_id, str) and TASK_ID_RE.match(task_id)):
        _err(problems, "task_id", "必须是小写字母开头的 [a-z0-9_]（编排器目录名），实际 %r" % (task_id,))
    goal = spec.get("task_goal")
    if not isinstance(goal, str) or len(goal) < 10:
        _err(problems, "task_goal", "工艺描述过短（>=10 字，供②③共享理解）")

    io_list = spec.get("io_list")
    io_names = set()
    if not isinstance(io_list, list) or not io_list:
        _err(problems, "io_list", "必须是非空数组（三方一致性唯一源头）")
    else:
        for idx, item in enumerate(io_list):
            _check_io_point(problems, item, idx)
            if isinstance(item, dict) and _is_ident(item.get("name")):
                if item["name"] in io_names:  # S1
                    _err(problems, "io_list[%d]" % idx, "name %r 重复" % item["name"])
                io_names.add(item["name"])

    constraints = spec.get("constraints")
    if not isinstance(constraints, list):
        _err(problems, "constraints", "必须是数组（无约束时为空数组）")
    else:
        seen = set()
        for idx, item in enumerate(constraints):
            _check_constraint(problems, item, idx)
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                if item["id"] in seen:  # S1
                    _err(problems, "constraints[%d]" % idx, "id %r 重复" % item["id"])
                seen.add(item["id"])

    acceptance = spec.get("acceptance")
    if not isinstance(acceptance, list) or not acceptance:
        _err(problems, "acceptance", "必须是非空数组（验收准则覆盖率 100% 的前提）")
    else:
        seen = set()
        for idx, item in enumerate(acceptance):
            _check_acceptance(problems, item, idx, io_names)
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                if item["id"] in seen:  # S1
                    _err(problems, "acceptance[%d]" % idx, "id %r 重复" % item["id"])
                seen.add(item["id"])

    axes = spec.get("axes")
    physical = {a.get("io", {}).get("fb"): a.get("poswin", 0)
                for a in (axes or []) if isinstance(a, dict) and isinstance(a.get("io", {}), dict)} if isinstance(axes, list) else {}
    for i, ac in enumerate(acceptance if isinstance(acceptance, list) else []):
        if not isinstance(ac, dict) or ac.get("type") != "forbidden_state":
            continue
        if ac.get("response_s"):
            source = ac.get("response_source")
            point = next((p for p in (io_list if isinstance(io_list, list) else [])
                          if isinstance(p, dict) and p.get("name") == source), {})
            if point.get("dir") != "input" or point.get("trigger") is True:
                _err(problems, "acceptance[%d]" % i, "response_source必须为传感器输入，不能为输出或触发命令")
            other_names = [ac.get(k, {}).get("signal") for k in ("when", "forbid")
                           if isinstance(ac.get(k), dict) and ac[k].get("signal") != source]
            others = [p for p in (io_list if isinstance(io_list, list) else [])
                      if isinstance(p, dict) and p.get("name") in other_names]
            if not any(p.get("dir") == "output" and p.get("type") == "BOOL" for p in others):
                _err(problems, "acceptance[%d]" % i, "派生状态一致性必须关联传感器输入与PLC BOOL状态输出")
            controls = {e.get("signal") for e in (spec.get("hmi") or {}).get("elements", [])
                        if isinstance(e, dict) and e.get("kind") == "BUTTON"} if isinstance(spec.get("hmi"), dict) else set()
            if source in controls:
                _err(problems, "acceptance[%d]" % i, "命令按钮不能作为派生状态的传感器响应源")
        for role in ("when", "forbid"):
            condition = ac.get(role, {})
            if not isinstance(condition, dict):
                continue
            point = next((p for p in (io_list if isinstance(io_list, list) else []) if isinstance(p, dict) and p.get("name") == condition.get("signal")), {})
            if "op" in condition and point.get("type") == "BOOL":
                _err(problems, "acceptance[%d].%s" % (i, role), "BOOL条件只能用equals")
            window = physical.get(condition.get("signal"), 0)
            if isinstance(window, (int, float)) and window > 0 and "equals" in condition:
                tol = condition.get("tolerance")
                if tol is None:
                    _err(problems, "acceptance[%d].%s" % (i, role),
                         "有到位误差的真实反馈不能以精确相等代表到位；显式使用阈值条件，或0<tolerance<=设备poswin，不得改变原始工艺含义")
                elif isinstance(tol, (int, float)) and tol > window:
                    _err(problems, "acceptance[%d].%s" % (i, role), "tolerance不得超过设备poswin")
    if axes is not None:
        if not isinstance(axes, list) or not axes:
            _err(problems, "axes", "必须是非空数组（无轴信息时省略该字段）")
        else:
            for idx, a in enumerate(axes):
                path = "axes[%d]" % idx
                if not isinstance(a, dict):
                    _err(problems, path, "必须是对象")
                    continue
                if not _is_ident(a.get("axis")):
                    _err(problems, path, "axis 必须是合法标识符（轴名）")
                stroke = a.get("stroke")
                if not (isinstance(stroke, (list, tuple)) and len(stroke) == 2
                        and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                                for v in stroke)):
                    _err(problems, path, "stroke 必须是 [lo,hi] 两个数")
                pw = a.get("poswin")
                if pw is not None and (not isinstance(pw, (int, float))
                                       or isinstance(pw, bool)):
                    _err(problems, path, "poswin 必须是数值")
                io = a.get("io") or {}
                if not isinstance(io, dict):
                    _err(problems, path, "io 必须是 {role: 通道名} 对象")
                else:
                    for role, var in io.items():
                        if not isinstance(var, str) or var not in io_names:
                            _err(problems, path + ".io.%s" % role,
                                 "通道 %r 不在 io_list 中（轴角色绑定须逐字一致）" % (var,))

    hmi = spec.get("hmi")
    if hmi is not None:
        io_by_name = {p.get("name"): p for p in (spec.get("io_list") or [])
                      if isinstance(p, dict)}
        if not isinstance(hmi, dict) or not isinstance(hmi.get("elements"), list) \
                or not hmi["elements"]:
            _err(problems, "hmi", "必须是含非空 elements 数组的对象")
        else:
            seen_pos = set()
            for idx, el in enumerate(hmi["elements"]):
                path = "hmi.elements[%d]" % idx
                if not isinstance(el, dict):
                    _err(problems, path, "必须是对象")
                    continue
                kind = el.get("kind")
                if kind not in ("BUTTON", "LAMP", "GAUGE"):
                    _err(problems, path, "kind 须为 BUTTON|LAMP|GAUGE，实际 %r" % (kind,))
                    continue
                sig = el.get("signal")
                base = io_by_name.get(sig)
                if base is None:
                    _err(problems, path, "signal %r 不在 io_list 中" % (sig,))
                    continue
                want = {"BUTTON": ("input", "BOOL"), "LAMP": ("output", "BOOL"),
                        "GAUGE": (None, "INT")}[kind]
                if want[0] and base.get("dir") != want[0]:
                    _err(problems, path, "%s 要求 dir=%s，%r 实为 %r"
                         % (kind, want[0], sig, base.get("dir")))
                if base.get("type") != want[1]:
                    _err(problems, path, "%s 要求 type=%s，%r 实为 %r"
                         % (kind, want[1], sig, base.get("type")))
                if kind == "BUTTON" and el.get("type") not in ("trigger", "toggle"):
                    _err(problems, path, "BUTTON.type 须为 trigger|toggle，实际 %r"
                         % (el.get("type"),))
                if not isinstance(el.get("label"), str) or not el.get("label"):
                    _err(problems, path, "label 必须是非空字符串")
                pos = el.get("pos")
                if not (isinstance(pos, list) and len(pos) == 2
                        and all(isinstance(v, int) and not isinstance(v, bool)
                                and v >= 0 for v in pos)):
                    _err(problems, path, "pos 必须是 [row,col] 非负整数")
                elif tuple(pos) in seen_pos:
                    _err(problems, path, "pos %r 与其他控件重叠" % (pos,))
                else:
                    seen_pos.add(tuple(pos))

    from src.pipeline.region_contract import terminal_region_problems
    problems.extend(terminal_region_problems(spec))
    return problems


def load_and_validate(path):
    """读入 JSON 文件并校验。返回 (spec, problems)；解析失败时 spec=None。"""
    import json
    from pathlib import Path
    try:
        spec = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return None, ["无法读取/解析 %s: %s" % (path, exc)]
    return spec, validate_requirement_spec(spec)
