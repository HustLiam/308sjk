#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
requirement_spec 校验器（契约①的可执行权威，gc 拥有）。

结构规则与 schemas/requirement_spec.schema.json 逐条对应；Schema 文件表达不了的
跨字段语义规则（S1~S4）在此实现。两者必须同一 RFC 内同步修改（主方案 §8.3）：

  S1  唯一性：io_list.name、acceptance.id、constraints.id 各自不得重复
  S2  量程：type=INT 必须带 range=[min,max] 且 min<max，且落在 16 位寄存器域
      [-32768,65535]（lx 位宽表 %QW 承载 INT/UINT/WORD 的并集域——lx 评审建议，
      v1.0.0-draft.2 落实）；type=BOOL 禁止带 range
  S3  信号引用：event_delay 的 from/to、forbidden_state 的 when/forbid 中
      的 signal 必须逐字存在于 io_list（判定引擎按 trace 通道名查找）
  S4  时间阈值：event_delay.value >= 0.1（>=100ms，通信时序约束，主方案 §7）

用法:
    from spec_validator import validate_requirement_spec
    problems = validate_requirement_spec(spec)   # 空 list = 通过
"""

import re

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

# S2：INT 量程的 16 位寄存器域（lx 位宽表：一个 %QW 字承载 INT/UINT/WORD 的并集）
WORD_DOMAIN_MIN, WORD_DOMAIN_MAX = -32768, 65535

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
        _err(problems, path, "dir 必须是 input|output（input=Isaac→PLC，output=PLC→Isaac），实际 %r"
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
        elif not (WORD_DOMAIN_MIN <= rng[0] and rng[1] <= WORD_DOMAIN_MAX):
            _err(problems, path, "range [%r,%r] 超出 16 位寄存器域 [%d,%d]（lx 位宽表："
                 "%%QW 承载 INT/UINT/WORD，并集域为 INT16~UINT16）"
                 % (rng[0], rng[1], WORD_DOMAIN_MIN, WORD_DOMAIN_MAX))
    elif vtype == "BOOL" and rng is not None:
        _err(problems, path, "type=BOOL 不允许带 range")
    if not isinstance(item.get("device"), str) or not item.get("device"):
        _err(problems, path, "device 必须是非空字符串（供场景侧选组件）")
    unit = item.get("unit")
    if unit is not None and not isinstance(unit, str):
        _err(problems, path, "unit 必须是字符串")


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


def _check_signal_value(problems, node, path, io_names):
    if not isinstance(node, dict):
        return _err(problems, path, "必须是 {signal, equals} 对象")
    _check_signal_ref(problems, path + ".signal", node.get("signal"), io_names)
    if not isinstance(node.get("equals"), (bool, int, float)) or isinstance(node.get("equals"), str):
        _err(problems, path, "equals 必须是布尔或数值")


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
        if not (isinstance(center, (list, tuple)) and len(center) == 3
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in center)):
            _err(problems, path, "region_center 必须是 [x,y,z] 三个数")
        tol = item.get("tolerance")
        if not isinstance(tol, (int, float)) or isinstance(tol, bool) or tol <= 0:
            _err(problems, path, "tolerance 必须是 >0 的数")
        if item.get("check_at") != "end":
            _err(problems, path, "check_at v1 冻结为 'end'（扩展走 RFC），实际 %r" % (item.get("check_at"),))

    elif ac_type == "forbidden_state":
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
