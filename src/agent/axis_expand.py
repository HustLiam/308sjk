#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轴对象确定性展开器（②a，生成方案 §6.2 ②a 行；RFC 2026-09-15 评审通过）。

axis_objects（device_model v1.1，AML 唯一来源）→ PLCopen XML：

  - FB 库 POU（INTERP/DRIVE402/MC_* 十二块）取模板
    src/agent/templates/motion_stack_v4.xml——与已验收 motion3axis v4.0 种子
    逐字同源，模板漂移由等价契约测试把守（tests/test_axis_expand.py）；
  - PLC_PRG 的定位变量（io 角色接线 + 地址取 io_points，R6 同源）、内部变量、
    FB 实例组与全部接线按轴对象展开：defaults → MC 调用动力学与总线初值；
    poswin/行程界限在全轴一致时代入 FB 体（模板字面常量，lx 参数化为实例
    参数后改代入参数位），不一致拒绝生成；
  - 展开期校验 defaults ≤ limits（超限拒绝生成——RFC 修订②，v4.0 INTERP 为
    命令级动力学、limits 不接线）；
  - 工艺层（序列器/互锁/目标表）不在本层：process_vars/process_body 由调用方
    注入——LLM 只写工艺层，永不生成或改写轴参数（RFC 目标：单一事实源）。
"""

import re
from pathlib import Path

_TEMPLATE = Path(__file__).resolve().parent / "templates" / "motion_stack_v4.xml"

# 接线所需的站级命令/聚合通道（AML 文档序即模板序；缺项拒绝生成——这些名字
# 是 §2.2 模板的接线锚点，不猜）
_STATION_BOOL_IN = ("run", "cmd_home", "cmd_go", "jog_fwd", "jog_rev",
                    "quickstop", "inject_fault", "cmd_reset", "cmd_halt", "cmd_rel")
_STATION_BOOL_OUT = ("all_oe", "move_done", "any_moving", "fault_any")

_FB_BLOCKS = (
    # (实例前缀, FB 类型, 覆盖轴集)——G7 实例组顺序（模板 §2.2）
    ("pwr", "MC_POWER", "all"),
    ("rs", "MC_RESET", "reset"),          # 复位块仅复位轴（motion3axis 惯例：x）
    ("st", "MC_STOP", "all"),
    ("hlt", "MC_HALT", "all"),
    ("go", "MC_MOVEABSOLUTE", "all"),
    ("rel", "MC_MOVERELATIVE", "rel"),    # 相对块仅 rel_d 角色轴
    ("jg", "MC_MOVEJOG", "jog"),          # 点动块仅点动轴（单轴点动）
    ("hm", "MC_HOME", "all"),
    ("rds", "MC_READSTATUS", "all"),
    ("rap", "MC_READACTUALPOSITION", "all"),
    ("ix", "INTERP", "all"),
    ("dv", "DRIVE402", "all"),
)


class AxisExpandError(ValueError):
    """展开失败（轴对象不完整/参数越限/模板锚点缺失）。文本可直接进反馈包。"""


def _short(name):
    return name[:-len("_axis")] if name.endswith("_axis") else name


def _fmt(v):
    """数值 → ST/XML 字面量（REAL 保持小数点，INT 无小数点）。"""
    return str(v)


def _fmt_int_like(v):
    """ST 越程界限字面量形态：整数值不带小数点（种子惯例 100/0）。"""
    f = float(v)
    return str(int(f)) if f.is_integer() else str(f)


def _num(v, what):
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        raise AxisExpandError("轴参数 %s 缺失或非数值：%r" % (what, v))
    return v


def _prepare_axes(device_model):
    """轴对象集校验与归一（io 必须完整绑定；defaults ≤ limits）。"""
    axes = []
    for a in device_model.get("kinematics", {}).get("axes", []):
        io = a.get("io")
        if not io:
            raise AxisExpandError("轴 %r io 角色绑定不完整（⓪ 已记 problems，"
                                  "必需通道缺失不展开——生成方案 §6.1）" % a.get("axis"))
        short = _short(a.get("axis") or "")
        defaults = a.get("defaults") or {}
        limits = a.get("limits") or {}
        for field, lim in (("velocity", "vmax"), ("acceleration", "accel"),
                           ("deceleration", "accel")):
            d, l = defaults.get(field), limits.get(lim)
            if d is not None and l is not None and d > l:
                raise AxisExpandError("轴 %r defaults.%s=%s 超过 limits.%s=%s"
                                      "（展开期校验拒绝生成——RFC 修订②）"
                                      % (a.get("axis"), field, d, lim, l))
        axes.append({"short": short, "axis": a.get("axis"), "io": io,
                     "defaults": defaults, "poswin": a.get("poswin"),
                     "stroke": a.get("stroke")})
    if not axes:
        raise AxisExpandError("device_model 无可展开轴对象（kinematics.axes 为空）")
    return axes


def _station_points(device_model):
    """站级 BOOL 命令/聚合通道（名字→地址），缺锚点拒绝生成。"""
    points = {p["name"]: p for p in device_model.get("io_points", [])
              if p.get("type") == "BOOL" and p.get("address")}
    missing = [n for n in _STATION_BOOL_IN + _STATION_BOOL_OUT if n not in points]
    if missing:
        raise AxisExpandError("io_points 缺站级命令/聚合通道：%s（模板 §2.2 接线锚点）"
                              % ", ".join(missing))
    return points


def _subst_fb_constants(template, poswin, stroke):
    """全轴一致的 poswin/行程代入 FB 体字面常量（模板与种子的差异为零时为空操作）。

    lx 把两处常量参数化为实例 VAR 后，本函数改代入参数位（RFC 修订③收尾）。
    """
    lo, hi = stroke
    text = template
    lo_x = _escape(_fmt_int_like(lo))
    hi_x = _escape(_fmt_int_like(hi))

    def sub_in_pou(text, pou_name, pairs):
        m = re.search(r'(<pou name="%s".*?</pou>)' % pou_name, text, re.DOTALL)
        if not m:
            raise AxisExpandError("模板缺 POU %s（motion_stack_v4.xml 与种子漂移）" % pou_name)
        block = m.group(1)
        for old, new in pairs:
            if old not in block:
                raise AxisExpandError("模板 %s 内未找到字面量 %r（与种子漂移）"
                                      % (pou_name, old))
            block = block.replace(old, new, 1)
        return text[:m.start(1)] + block + text[m.end(1):]

    # INTERP：POSWIN 初值 + 体内越程界限（ST 在 XML 内为转义形态）
    text = sub_in_pou(text, "INTERP", [
        ('<variable name="POSWIN">\n              <type><REAL /></type>\n'
         '              <initialValue><simpleValue value="2.0" /></initialValue>\n'
         '            </variable>',
         '<variable name="POSWIN">\n              <type><REAL /></type>\n'
         '              <initialValue><simpleValue value="%s" /></initialValue>\n'
         '            </variable>' % _fmt(poswin)),
        ("pos_target &gt; 100 OR pos_target &lt; 0",
         "pos_target &gt; %s OR pos_target &lt; %s" % (hi_x, lo_x)),
    ])
    # MC_MOVEABSOLUTE：越程拒绝界限
    text = sub_in_pou(text, "MC_MOVEABSOLUTE", [
        ("Position &lt; 0 OR Position &gt; 100",
         "Position &lt; %s OR Position &gt; %s" % (lo_x, hi_x)),
    ])
    return text


def _var(name, vtype, address=None, initial=None, derived=False):
    attr = ' name="%s"' % name + (' address="%s"' % address if address else "")
    if derived:
        body = "<type><derived name=\"%s\" /></type>" % vtype
    else:
        body = "<type><%s /></type>" % vtype
    if initial is not None:
        body += '\n              <initialValue><simpleValue value="%s" /></initialValue>' % _fmt(initial)
    return '            <variable%s>\n              %s\n            </variable>' % (attr, body)


def _block(vars_):
    if not vars_:
        return ""
    return "          <localVars>\n" + "\n".join(vars_) + "\n          </localVars>\n"


def _interface(axes, station, prog_id, jog_axis, reset_axes, process_vars):
    """PLC_PRG 接口：七个 localVars 块，与种子分组/顺序逐字一致。"""
    io_addr = station["io_addr"]
    g1 = [_var(n, "BOOL", io_addr.get(n)) for n in _STATION_BOOL_IN]

    fbv = [(a, "fb", "INT") for a in axes] + [(a, "rel_d", "INT") for a in axes
                                              if "rel_d" in a["io"]]
    g2 = [_var(a["io"][role], "INT", io_addr.get(a["io"][role])) for a, role, _ in fbv]

    g3 = [_var(a["io"]["sp"], "INT", io_addr.get(a["io"]["sp"])) for a in axes]

    g4 = [_var(a["io"]["sw"], "WORD", io_addr.get(a["io"]["sw"])) for a in axes]
    g4 += [_var(a["io"]["v"], "INT", io_addr.get(a["io"]["v"])) for a in axes]
    g4 += [_var(a["io"]["err_id"], "INT", io_addr.get(a["io"]["err_id"]))
           for a in axes if "err_id" in a["io"]]
    g4 += [_var(n, "BOOL", io_addr.get(n)) for n in _STATION_BOOL_OUT]
    g4 += [_var("prog_id", "INT", "%QW20", initial=prog_id)]

    g5 = [_var("%s_cw" % a["short"], "WORD") for a in axes]
    g5 += [_var("%s_interp" % a["short"], "INT") for a in axes]
    g5 += [_var("%s_jog_pos" % jog_axis, "INT")]
    g5 += [_var("%s_tgt" % a["short"], "INT") for a in axes]
    for pre in ("go", "hm", "rel"):
        g5 += [_var("%s_%s_exe" % (pre, a["short"]), "BOOL") for a in axes
               if pre != "rel" or "rel_d" in a["io"]]

    g6 = []
    for a in axes:
        d = a["defaults"]
        for suf, field in (("vel", "velocity"), ("acc", "acceleration"),
                           ("dec", "deceleration")):
            g6.append(_var("%s_%s_bus" % (a["short"], suf), "REAL",
                           initial=_num(d.get(field), "defaults.%s（轴 %s）" % (field, a["axis"]))))

    g7 = []
    for pre, fb, cov in _FB_BLOCKS:
        for a in axes:
            if cov == "all" or (cov == "rel" and "rel_d" in a["io"]) \
                    or (cov == "jog" and a["short"] == jog_axis) \
                    or (cov == "reset" and a["short"] in reset_axes):
                g7.append(_var("%s_%s" % (pre, a["short"]), fb, derived=True))

    return (_block(g1) + _block(g2) + _block(g3) + _block(g4) + _block(g5)
            + _block(g6) + _block(g7) + _block(list(process_vars)))


def _body(axes, prog_id, station, jog_axis, jog_velocity, reset_axes, process_body):
    """PLC_PRG 的 ST 体：全部轴派生接线（模板 §2.2），与种子逐字一致 + 工艺层尾接。"""
    L = []
    L.append("(* ---- program identity ---- *)")
    L.append("prog_id := %s;" % _fmt(prog_id))
    L.append("")
    L.append("(* ---- 扫描头：采样驱动器状态 ---- *)")
    for a in axes:
        L.append("%s := dv_%s.sw;" % (a["io"]["sw"], a["short"]))
    L.append("")
    L.append("(* ---- 目标总线默认 = 绝对定位字（MC_MOVERELATIVE 触发时改写为 叠加目标） ---- *)")
    for a in axes:
        L.append("%s_tgt := %s;" % (a["short"], a["io"]["sp"]))
    L.append("")
    L.append("(* ---- 使能 / 复位 / 快停（快停最后落笔 = 优先级最高） ---- *)")
    run, qs = station["cmd"]["run"], station["cmd"]["quickstop"]
    for a in axes:
        s = a["short"]
        L.append("pwr_%s(Enable := %s AND NOT %s, EnablePositive := TRUE, "
                 "EnableNegative := TRUE, cw := %s_cw, sw := %s);"
                 % (s, run, qs, s, a["io"]["sw"]))
    for a in axes:
        if a["short"] in reset_axes:
            s = a["short"]
            L.append("rs_%s(fire := %s, cw := %s_cw, sw := %s);"
                     % (s, station["cmd"]["cmd_reset"], s, a["io"]["sw"]))
    for a in axes:
        s = a["short"]
        L.append("st_%s(fire := %s, v_act := %s, cw := %s_cw, sw := %s);"
                 % (s, qs, a["io"]["v"], s, a["io"]["sw"]))
    L.append("")
    L.append("(* ---- MC 读块（后续命令/状态聚合消费） ---- *)")
    for a in axes:
        L.append("rap_%s(Enable := TRUE, ActualPosition := %s);" % (a["short"], a["io"]["fb"]))
    L.append("")
    L.append("(* ---- MC 指令层 ---- *)")
    for a in axes:
        s, d = a["short"], a["defaults"]
        L.append("go_%s(fire := %s, Position := %s, Velocity := %s, Acceleration := %s, "
                 "Deceleration := %s," % (s, station["cmd"]["cmd_go"], a["io"]["sp"],
                                          _fmt(_num(d.get("velocity"), "velocity")),
                                          _fmt(_num(d.get("acceleration"), "acceleration")),
                                          _fmt(_num(d.get("deceleration"), "deceleration"))))
        L.append("     sw := %s, abort_bus := ix_%s.Aborted," % (a["io"]["sw"], s))
        L.append("     interp_exe := go_%s_exe, dyn_vel := %s_vel_bus, "
                 "dyn_acc := %s_acc_bus, dyn_dec := %s_dec_bus);" % (s, s, s, s))
    for a in axes:
        if "rel_d" not in a["io"]:
            continue
        s, d = a["short"], a["defaults"]
        L.append("rel_%s(fire := %s, Distance := %s, ActualPosition := rap_%s.Position, "
                 "Velocity := %s, Acceleration := %s, Deceleration := %s,"
                 % (s, station["cmd"]["cmd_rel"], a["io"]["rel_d"], s,
                    _fmt(_num(d.get("velocity"), "velocity")),
                    _fmt(_num(d.get("acceleration"), "acceleration")),
                    _fmt(_num(d.get("deceleration"), "deceleration"))))
        L.append("     sw := %s, abort_bus := ix_%s.Aborted," % (a["io"]["sw"], s))
        L.append("     interp_exe := rel_%s_exe, abs_tgt := %s_tgt, "
                 "dyn_vel := %s_vel_bus, dyn_acc := %s_acc_bus, dyn_dec := %s_dec_bus);"
                 % (s, s, s, s, s))
    for a in axes:
        L.append("hm_%s(fire := %s, sw := %s, home_exe := hm_%s_exe);"
                 % (a["short"], station["cmd"]["cmd_home"], a["io"]["sw"], a["short"]))
    L.append("jg_%s(JogForward := %s, JogBackward := %s, Velocity := %s, "
             "cur_pos := %s, sw := %s, jog_pos := %s_jog_pos);"
             % (jog_axis, station["cmd"]["jog_fwd"], station["cmd"]["jog_rev"],
                _fmt(jog_velocity), _next_io(axes, jog_axis)["fb"],
                _next_io(axes, jog_axis)["sw"], jog_axis))
    for a in axes:
        L.append("hlt_%s(fire := %s, ax_busy := ix_%s.Busy);"
                 % (a["short"], station["cmd"]["cmd_halt"], a["short"]))
    L.append("")
    L.append("(* ---- 插补引擎：每扫描输出插补点（回零时目标=当前定位字, 场景约定 sp=0） ---- *)")
    for a in axes:
        s = a["short"]
        L.append("ix_%s(fire := go_%s_exe OR hm_%s_exe OR rel_%s_exe, pos_target := %s_tgt,"
                 % (s, s, s, s, s))
        L.append("     hold_req := hlt_%s.Stopping, vel_req := %s_vel_bus, "
                 "acc_req := %s_acc_bus, dec_req := %s_dec_bus);" % (s, s, s, s))
    L.append("")
    L.append("(* ---- INTERP 结束（完成或中止）后清触发线 ---- *)")
    for a in axes:
        s = a["short"]
        L.append("IF ix_%s.Done OR ix_%s.Aborted THEN go_%s_exe := FALSE; "
                 "hm_%s_exe := FALSE; rel_%s_exe := FALSE; END_IF;" % (s, s, s, s, s))
    L.append("")
    L.append("(* ---- 插补点路由：点动时用 jog_pos，否则用 INTERP 输出 ---- *)")
    L.append("IF jg_%s.Busy THEN" % jog_axis)
    L.append("    %s_interp := %s_jog_pos;" % (jog_axis, jog_axis))
    L.append("ELSE")
    L.append("    %s_interp := ix_%s.Setpoint;" % (jog_axis, jog_axis))
    L.append("END_IF;")
    for a in axes:
        if a["short"] == jog_axis:
            continue
        L.append("%s_interp := ix_%s.Setpoint;" % (a["short"], a["short"]))
    L.append("")
    L.append("(* ---- 驱动器：跟随插补点，闭位置环 ---- *)")
    for a in axes:
        s = a["short"]
        L.append("dv_%s(cw := %s_cw, Setpoint := %s_interp, pos_fb := %s);" % (s, s, s, a["io"]["fb"]))
        L.append("%s := dv_%s.v_cmd;" % (a["io"]["v"], s))
    L.append("")
    L.append("(* ---- 应用状态聚合（经 MC_ReadStatus） ---- *)")
    for a in axes:
        s = a["short"]
        L.append("rds_%s(Enable := TRUE, sw := %s, interp_busy := ix_%s.Busy, v_act := %s);"
                 % (s, a["io"]["sw"], s, a["io"]["v"]))
    L.append("all_oe := %s;" % " AND ".join("pwr_%s.Status" % a["short"] for a in axes))
    L.append("move_done := %s;" % " AND ".join("go_%s.Done" % a["short"] for a in axes))
    L.append("any_moving := %s;" % " OR ".join("rds_%s.Moving" % a["short"] for a in axes))
    L.append("fault_any := %s;" % " OR ".join("rds_%s.ErrorStop" % a["short"] for a in axes))
    L.append("")
    L.append("(* ---- ErrorID 诊断出口（相对命令优先报告） ---- *)")
    for a in axes:
        s = a["short"]
        if "rel_d" in a["io"]:
            L.append("IF rel_%s.Error THEN %s := rel_%s.ErrorID; "
                     "ELSE %s := go_%s.ErrorID; END_IF;"
                     % (s, a["io"]["err_id"], s, a["io"]["err_id"], s))
        else:
            L.append("%s := go_%s.ErrorID;" % (a["io"]["err_id"], s))
    if process_body:
        L.append(process_body.rstrip("\n"))
    return "\n".join(L)


def _next_io(axes, short):
    return next(a["io"] for a in axes if a["short"] == short)


def expand_project(device_model, *, prog_id, jog_axis="x", jog_velocity=50.0,
                   reset_axes=("x",), process_vars=(), process_body=""):
    """axis_objects → 完整 PLCopen XML 文本（确定性）。

    prog_id:        程序身份（%QW20 初值，契约② prog_id 顺延规则）；
    jog_axis:       单轴点动挂靠轴（模板 §2.2 应用配置）；
    jog_velocity:   点动速度（应用层参数，不属于轴对象）；
    reset_axes:     MC_RESET 挂靠轴；
    process_vars:   工艺层变量声明（_var 元组流，追加在第七块后）；
    process_body:   工艺层 ST 尾接（序列器/互锁——LLM 的职责区）。
    """
    axes = _prepare_axes(device_model)
    points = _station_points(device_model)
    io_addr = {p["name"]: p.get("address")
               for p in device_model.get("io_points", []) if p.get("address")}
    station = {"points_raw": device_model.get("io_points", []),
               "io_addr": io_addr,
               "cmd": {n: n for n in _STATION_BOOL_IN}}

    # poswin / 行程：全轴一致才可代入 FB 体（字面常量形态的模板约束）
    poswins = {a["poswin"] for a in axes if a["poswin"] is not None}
    if len(poswins) > 1:
        raise AxisExpandError("多轴 poswin 不一致 %s——FB 体字面常量形态无法逐轴表达"
                              "（lx 参数化后解除）" % sorted(poswins))
    strokes = {tuple(a["stroke"]) for a in axes if a["stroke"] is not None}
    if len(strokes) > 1:
        raise AxisExpandError("多轴行程不一致 %s——MC 越程界限为 FB 体共享常量"
                              "（lx 参数化后解除）" % sorted(strokes))

    template = _TEMPLATE.read_text(encoding="utf-8")
    if poswins:
        template = _subst_fb_constants(template, poswins.pop(),
                                       strokes.pop() if strokes else (0, 100))
    elif strokes:
        template = _subst_fb_constants(template, 2.0, strokes.pop())

    interface = _interface(axes, station, prog_id, jog_axis, reset_axes, process_vars)
    body = _body(axes, prog_id, station, jog_axis, jog_velocity, reset_axes, process_body)
    prg = ('      <pou name="PLC_PRG" pouType="program">\n        <interface>\n'
           + interface
           + '        </interface>\n        <body>\n          <ST>\n'
           + '            <xhtml xmlns="http://www.w3.org/1999/xhtml">'
           + _escape(body)
           + '</xhtml>\n          </ST>\n        </body>\n      </pou>\n')
    return template.replace("@@PLC_PRG@@\n", prg, 1)


def _escape(st_text):
    return st_text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
