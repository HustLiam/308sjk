# -*- coding: utf-8 -*-
"""确定性运动栈生成器 v3（纯代码，零模板依赖——2026-09-24 负责人指令重构）。"""
import re

FB_ST = """FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    vel_req : REAL;
    acc_req : REAL;
    dec_req : REAL;
    POSWIN : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
    Aborted : BOOL;
  END_VAR
  VAR
    edge_ir : BOOL;
    pos : REAL;
    vel : REAL;
    tgt : REAL;
    dist : REAL;
    stop_d : REAL;
    SCAN_T : REAL := 0.02;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > 100 OR pos_target < 0 THEN
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
        Aborted := FALSE;
    END_IF;
END_IF;
edge_ir := fire;
IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - dec_req * SCAN_T); END_IF;
    IF vel < 0.0 THEN vel := MIN(0.0, vel + dec_req * SCAN_T); END_IF;
    IF vel = 0.0 AND Busy THEN Busy := FALSE; Done := FALSE; Aborted := TRUE; END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * dec_req);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - dec_req * SCAN_T); END_IF;
        IF dist < 0.0 THEN vel := MIN(0.0, vel + dec_req * SCAN_T); END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt; Busy := FALSE; Done := TRUE; Aborted := FALSE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(vel_req, vel + acc_req * SCAN_T); END_IF;
        IF dist < 0.0 THEN vel := MAX(-vel_req, vel - acc_req * SCAN_T); END_IF;
    END_IF;
END_IF;
pos := pos + vel * SCAN_T;
Setpoint := REAL_TO_INT(pos);
END_FUNCTION_BLOCK

FUNCTION_BLOCK DRIVE402
  VAR_INPUT
    cw : WORD;
    Setpoint : INT;
    pos_fb : INT;
  END_VAR
  VAR_OUTPUT
    sw : WORD;
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    pos_err : REAL;
    v_ff : REAL;
    v_out : REAL;
    sp_prev : INT;
    SCAN_T : REAL := 0.02;
    KP : REAL := 25.0;
    QS_DECEL : REAL := 120.0;
  END_VAR
cmd := cw AND 16#000F;
IF state = 7 AND (cw AND 16#0080) <> 0 THEN state := 1; END_IF;
CASE state OF
    1: IF cmd = 16#06 THEN state := 2; END_IF;
    2: IF cmd = 16#07 THEN state := 3; END_IF;
       IF cmd = 16#0F THEN state := 4; END_IF;
       IF cmd = 16#00 THEN state := 1; END_IF;
    3: IF cmd = 16#0F THEN state := 4; END_IF;
       IF cmd = 16#06 THEN state := 2; END_IF;
       IF cmd = 16#00 THEN state := 1; END_IF;
       IF cmd = 16#02 THEN state := 5; END_IF;
    4: IF cmd = 16#07 THEN state := 3; END_IF;
       IF cmd = 16#06 THEN state := 2; END_IF;
       IF cmd = 16#00 THEN state := 1; END_IF;
       IF cmd = 16#02 THEN state := 5; END_IF;
    5: IF v_out = 0.0 THEN
           IF cmd = 16#0F THEN state := 4; END_IF;
           IF cmd = 16#06 THEN state := 2; END_IF;
           IF cmd = 16#00 THEN state := 1; END_IF;
       END_IF;
    6: IF v_out = 0.0 THEN state := 7; END_IF;
END_CASE;
IF state >= 4 AND state <= 6 THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / SCAN_T;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF v_ff = 0.0 AND ABS(pos_err) <= 1.5 THEN v_out := 0.0; END_IF;
ELSE
    v_out := 0.0;
END_IF;
sp_prev := Setpoint;
v_cmd := REAL_TO_INT(v_out);
sw := 0;
CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0031;
    3: sw := 16#0033;
    4: sw := 16#0037;
    5: sw := 16#0014;
    7: sw := 16#0018;
END_CASE;
IF state = 4 THEN
    IF ABS(pos_err) <= 2.0 AND v_ff = 0.0 THEN sw := sw OR 16#0400; END_IF;
END_IF;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
    EnablePositive : BOOL;
    EnableNegative : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    pstep : INT;
  END_VAR
IF Enable AND (EnablePositive OR EnableNegative) AND pstep = 4 AND (sw AND 16#0004) = 0 AND (sw AND 16#0008) = 0 THEN
    pstep := 0;
END_IF;
IF Enable AND (EnablePositive OR EnableNegative) THEN
    CASE pstep OF
        0: cw := (cw AND 16#00F0) OR 16#0006; pstep := 1;
        1: IF (sw AND 16#0001) <> 0 THEN cw := (cw AND 16#00F0) OR 16#0007; pstep := 2; END_IF;
        2: IF (sw AND 16#0002) <> 0 THEN cw := (cw AND 16#00F0) OR 16#000F; pstep := 3; END_IF;
        3: IF (sw AND 16#0004) <> 0 THEN pstep := 4; END_IF;
    END_CASE;
ELSE
    cw := (cw AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
Status := (sw AND 16#0004) <> 0;
Busy := Enable AND pstep < 4;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_RESET
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_rs : BOOL;
    busy_rs : BOOL;
  END_VAR
IF fire AND NOT edge_rs THEN busy_rs := TRUE; END_IF;
edge_rs := fire;
IF busy_rs THEN
    IF (sw AND 16#0008) <> 0 THEN cw := cw OR 16#0080;
    ELSE cw := cw AND 16#FF7F; busy_rs := FALSE; END_IF;
END_IF;
Busy := busy_rs;
Error := (sw AND 16#0008) <> 0 AND NOT busy_rs;
Done := NOT busy_rs AND (sw AND 16#0008) = 0 AND NOT fire;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_STOP
  VAR_INPUT
    fire : BOOL;
    v_act : INT;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
IF fire THEN
    cw := (cw AND 16#00F0) OR 16#0002;
    Done := FALSE;
ELSE
    Done := TRUE;
END_IF;
Busy := fire AND v_act <> 0;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_HALT
  VAR_INPUT
    fire : BOOL;
    ax_busy : BOOL;
  END_VAR
  VAR_OUTPUT
    Stopping : BOOL;
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_hl : BOOL;
    done_latch : BOOL;
  END_VAR
IF fire AND NOT edge_hl THEN done_latch := FALSE; Stopping := TRUE; END_IF;
edge_hl := fire;
IF Stopping AND NOT ax_busy THEN Stopping := FALSE; done_latch := TRUE; END_IF;
Busy := Stopping;
Done := done_latch AND NOT Stopping;
Error := FALSE;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVEABSOLUTE
  VAR_INPUT
    fire : BOOL;
    Position : INT;
    Velocity : REAL;
    Acceleration : REAL;
    Deceleration : REAL;
    sw : WORD;
    abort_bus : BOOL;
  END_VAR
  VAR_IN_OUT
    interp_exe : BOOL;
    dyn_vel : REAL;
    dyn_acc : REAL;
    dyn_dec : REAL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Active : BOOL;
    CommandAborted : BOOL;
    Error : BOOL;
    ErrorID : INT;
  END_VAR
  VAR
    edge_ma : BOOL;
    edge_ab : BOOL;
  END_VAR
IF fire AND NOT edge_ma THEN
    Error := FALSE; ErrorID := 0; CommandAborted := FALSE;
    IF (sw AND 16#0008) <> 0 THEN Error := TRUE; ErrorID := 2;
    ELSIF (sw AND 16#0004) = 0 THEN Error := TRUE; ErrorID := 3;
    ELSE interp_exe := TRUE; dyn_vel := Velocity; dyn_acc := Acceleration; dyn_dec := Deceleration;
    END_IF;
END_IF;
edge_ma := fire;
IF interp_exe AND abort_bus AND NOT edge_ab THEN
    interp_exe := FALSE; CommandAborted := TRUE;
END_IF;
edge_ab := abort_bus;
Busy := interp_exe;
Active := interp_exe;
Done := NOT interp_exe AND NOT Error;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVERELATIVE
  VAR_INPUT
    fire : BOOL;
    Distance : INT;
    ActualPosition : INT;
    Velocity : REAL;
    Acceleration : REAL;
    Deceleration : REAL;
    sw : WORD;
    abort_bus : BOOL;
  END_VAR
  VAR_IN_OUT
    interp_exe : BOOL;
    abs_tgt : INT;
    dyn_vel : REAL;
    dyn_acc : REAL;
    dyn_dec : REAL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Active : BOOL;
    CommandAborted : BOOL;
    Error : BOOL;
    ErrorID : INT;
  END_VAR
  VAR
    edge_rl : BOOL;
    edge_ab : BOOL;
  END_VAR
IF fire AND NOT edge_rl THEN
    Error := FALSE; ErrorID := 0; CommandAborted := FALSE;
    IF (sw AND 16#0008) <> 0 THEN Error := TRUE; ErrorID := 2;
    ELSIF (sw AND 16#0004) = 0 THEN Error := TRUE; ErrorID := 3;
    ELSE abs_tgt := ActualPosition + Distance;
         interp_exe := TRUE;
         dyn_vel := Velocity; dyn_acc := Acceleration; dyn_dec := Deceleration;
    END_IF;
END_IF;
edge_rl := fire;
IF interp_exe AND abort_bus AND NOT edge_ab THEN
    interp_exe := FALSE; CommandAborted := TRUE;
END_IF;
edge_ab := abort_bus;
Busy := interp_exe;
Active := interp_exe;
Done := NOT interp_exe AND NOT Error;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVEJOG
  VAR_INPUT
    JogForward : BOOL;
    JogBackward : BOOL;
    Velocity : REAL;
    cur_pos : INT;
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    jog_pos : INT;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    jog_step : INT;
    edge_jg : BOOL;
  END_VAR
IF (JogForward OR JogBackward) AND NOT edge_jg AND NOT Busy THEN jog_pos := cur_pos; END_IF;
edge_jg := JogForward OR JogBackward;
jog_step := REAL_TO_INT(Velocity * 0.02);
IF jog_step < 1 THEN jog_step := 1; END_IF;
IF JogForward AND jog_pos < 97 THEN jog_pos := jog_pos + jog_step; END_IF;
IF JogBackward AND jog_pos > 3 THEN jog_pos := jog_pos - jog_step; END_IF;
Busy := JogForward OR JogBackward;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_HOME
  VAR_INPUT
    fire : BOOL;
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    home_exe : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_hm : BOOL;
  END_VAR
IF fire AND NOT edge_hm THEN home_exe := TRUE; END_IF;
edge_hm := fire;
Busy := home_exe;
Done := NOT home_exe;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_READSTATUS
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
    interp_busy : BOOL;
    v_act : INT;
  END_VAR
  VAR_OUTPUT
    Valid : BOOL;
    Moving : BOOL;
    StandStill : BOOL;
    Disabled : BOOL;
    ErrorStop : BOOL;
  END_VAR
Valid := Enable;
Disabled := Enable AND (sw AND 16#0004) = 0;
ErrorStop := Enable AND (sw AND 16#0008) <> 0;
Moving := Enable AND (interp_busy OR v_act <> 0);
StandStill := Enable AND (sw AND 16#0004) <> 0 AND NOT interp_busy AND v_act = 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_READACTUALPOSITION
  VAR_INPUT
    Enable : BOOL;
    ActualPosition : INT;
  END_VAR
  VAR_OUTPUT
    Valid : BOOL;
    Position : INT;
  END_VAR
Valid := Enable;
Position := ActualPosition;
END_FUNCTION_BLOCK"""

PLACEHOLDER = "(* PROCESS LAYER *)"

_TYPE_MAP = {
    "VAR_INPUT": "inputVars", "VAR_OUTPUT": "outputVars",
    "VAR_IN_OUT": "inOutVars", "VAR": "localVars",
}


def _fb_defs_to_xml_pous():
    """FB 定义 ST → 各 <pou> 元素。每行一个变量（FB_ST 已按此格式写死）。"""
    from xml.sax.saxutils import escape
    pous = []
    for m in re.finditer(r"FUNCTION_BLOCK (\w+)(.*?)END_FUNCTION_BLOCK", FB_ST, re.DOTALL):
        name, raw = m.group(1), m.group(2)
        iface_parts = []
        body = raw
        for var_type, xml_tag in _TYPE_MAP.items():
            pat = re.search(r"%s\s*\n(.*?)\n\s*END_%s" % (var_type, var_type), raw, re.DOTALL)
            if not pat:
                continue
            decls = pat.group(1)
            body = body.replace(pat.group(0), "")
            var_elems = []
            for seg_line in decls.splitlines():
                for seg in seg_line.split(";"):
                    seg = seg.strip()
                    if not seg:
                        continue
                    dm = re.match(r"^(\w+)\s*:\s*([\w.]+)", seg)
                    if not dm:
                        continue
                    vn, typ = dm.group(1), dm.group(2)
                    if typ in ("BOOL", "INT", "WORD", "REAL"):
                        type_xml = "<%s />" % typ
                    else:
                        type_xml = '<derived name="%s" />' % typ
                    init_m = re.search(r":=\s*([\d.-]+)", seg)
                    init_xml = ""
                    if init_m:
                        init_xml = '<initialValue><simpleValue value="%s" /></initialValue>' % init_m.group(1)
                    var_elems.append(
                        '            <variable name="%s">\n'
                        '              <type>%s</type>%s\n'
                        '            </variable>' % (vn, type_xml, "\n              " + init_xml if init_xml else ""))
            if var_elems:
                iface_parts.append(
                    "          <%s>\n%s\n          </%s>" % (xml_tag, "\n".join(var_elems), xml_tag))
        iface = "\n".join(iface_parts)
        pous.append(
            '      <pou name="%s" pouType="functionBlock">\n'
            '        <interface>\n%s\n        </interface>\n'
            '        <body>\n          <ST>\n'
            '            <xhtml xmlns="http://www.w3.org/1999/xhtml">%s</xhtml>\n'
            '          </ST>\n        </body>\n      </pou>'
            % (name, iface, escape(body.strip())))
    return pous


def build_axis_stack(spec):
    """spec → (变量声明行, 轴栈逻辑 ST)。CSP 形态（PLC 出位置指令）。"""
    axes = spec.get("axes") or []
    io_by_name = {p.get("name"): p for p in spec.get("io_list", [])}
    shorts = [a["axis"].replace("_axis", "") for a in axes]
    D = lambda a, k: "%g" % ((a.get("defaults") or {}).get(k))
    P = lambda a: "%g" % (a.get("poswin") or 2)
    v, L = [], []

    def var(name, typ, addr=None):
        v.append("    %s AT %s : %s;" % (name, addr, typ) if addr
                 else "    %s : %s;" % (name, typ))

    for name, p in io_by_name.items():
        if p.get("address"):
            typ = "WORD" if "_sw" in name else ("BOOL" if p.get("type") == "BOOL" else "INT")
            var(name, typ, p["address"])
    var("prog_id", "INT", "%QW20")
    var("pl_step", "INT")
    for s in shorts:
        for nm, tp in [("%s_tgt" % s, "INT"), ("%s_interp" % s, "INT"), ("%s_cw" % s, "WORD"),
                       ("%s_vel_bus" % s, "REAL"), ("%s_acc_bus" % s, "REAL"), ("%s_dec_bus" % s, "REAL"),
                       ("%s_jog_pos" % s, "INT"), ("go_%s_exe" % s, "BOOL"), ("hm_%s_exe" % s, "BOOL"),
                       ("rel_%s_exe" % s, "BOOL"), ("pwr_%s" % s, "MC_POWER"), ("rs_%s" % s, "MC_RESET"),
                       ("st_%s" % s, "MC_STOP"), ("go_%s" % s, "MC_MOVEABSOLUTE"), ("rel_%s" % s, "MC_MOVERELATIVE"),
                       ("jg_%s" % s, "MC_MOVEJOG"), ("hm_%s" % s, "MC_HOME"), ("hlt_%s" % s, "MC_HALT"),
                       ("rap_%s" % s, "MC_READACTUALPOSITION"), ("rds_%s" % s, "MC_READSTATUS"),
                       ("ix_%s" % s, "INTERP"), ("dv_%s" % s, "DRIVE402")]:
            var(nm, tp)

    L.append("prog_id := %s;" % spec.get("prog_id", 1))
    L.append("")
    for s in shorts:
        L.append("%s_sw := dv_%s.sw;" % (s, s))
    L.append("")
    for s, a in zip(shorts, axes):
        stroke = a.get("stroke") or [0, 100]
        ref = int(stroke[1]) if s == "z" and shorts[-1] == s else int(stroke[0])
        L.append("IF hm_%s_exe THEN %s_tgt := %d; ELSE %s_tgt := %s_sp; END_IF;" % (s, s, ref, s, s))
    L.append("")
    for s in shorts:
        L.append("pwr_%s(Enable := run AND NOT quickstop, EnablePositive := TRUE, "
                 "EnableNegative := TRUE, cw := %s_cw, sw := %s_sw);" % (s, s, s))
    for s in shorts:
        L.append("rs_%s(fire := cmd_reset, cw := %s_cw, sw := %s_sw);" % (s, s, s))
        L.append("st_%s(fire := quickstop, v_act := %s_v, cw := %s_cw, sw := %s_sw);" % (s, s, s, s))
        L.append("rap_%s(Enable := TRUE, ActualPosition := %s_fb);" % (s, s))
    L.append("")
    for s, a in zip(shorts, axes):
        L.append("go_%s(fire := go_%s_exe, Position := %s_sp, Velocity := %s, "
                 "Acceleration := %s, Deceleration := %s, sw := %s_sw, abort_bus := ix_%s.Aborted,"
                 " interp_exe := go_%s_exe, dyn_vel := %s_vel_bus, dyn_acc := %s_acc_bus, dyn_dec := %s_dec_bus);"
                 % (s, s, s, D(a, "velocity"), D(a, "acceleration"), D(a, "deceleration"), s, s, s, s, s, s))
        L.append("hm_%s(fire := cmd_home, sw := %s_sw, home_exe := hm_%s_exe);" % (s, s, s))
        L.append("IF hm_%s_exe AND %s_vel_bus = 0.0 THEN "
                 "%s_vel_bus := %s; %s_acc_bus := %s; %s_dec_bus := %s; END_IF;"
                 % (s, s, s, D(a, "velocity"), s, D(a, "acceleration"), s, D(a, "deceleration")))
        L.append("jg_%s(JogForward := jog_fwd, JogBackward := jog_rev, Velocity := %s, "
                 "cur_pos := %s_fb, sw := %s_sw, jog_pos => %s_jog_pos);"
                 % (s, D(a, "velocity"), s, s, s))
        L.append("hlt_%s(fire := cmd_halt, ax_busy := ix_%s.Busy);" % (s, s))
    L.append("")
    for s, a in zip(shorts, axes):
        L.append("ix_%s(fire := go_%s_exe OR hm_%s_exe OR rel_%s_exe, pos_target := %s_tgt,"
                 " hold_req := hlt_%s.Stopping, vel_req := %s_vel_bus, acc_req := %s_acc_bus,"
                 " dec_req := %s_dec_bus, POSWIN := %s);" % (s, s, s, s, s, s, s, s, s, P(a)))
    L.append("")
    for s in shorts:
        L.append("IF ix_%s.Done OR ix_%s.Aborted THEN go_%s_exe := FALSE; "
                 "hm_%s_exe := FALSE; rel_%s_exe := FALSE; END_IF;" % (s, s, s, s, s))
    L.append("")
    for i, s in enumerate(shorts):
        if i == 0:
            L.append("IF jg_%s.Busy THEN" % s)
            L.append("    %s_interp := %s_jog_pos;" % (s, s))
            L.append("ELSE")
            L.append("    %s_interp := ix_%s.Setpoint;" % (s, s))
            L.append("END_IF;")
        else:
            L.append("%s_interp := ix_%s.Setpoint;" % (s, s))
    L.append("")
    for s in shorts:
        L.append("dv_%s(cw := %s_cw, Setpoint := %s_interp, pos_fb := %s_fb);" % (s, s, s, s))
        L.append("%s_cmd := %s_interp;" % (s, s))
    L.append("")
    for s in shorts:
        L.append("rds_%s(Enable := TRUE, sw := %s_sw, interp_busy := ix_%s.Busy, v_act := %s_v);"
                 % (s, s, s, s))
    oe = " AND ".join("pwr_%s.Status" % s for s in shorts)
    mv = " OR ".join("rds_%s.Moving" % s for s in shorts)
    fs = " OR ".join("rds_%s.ErrorStop" % s for s in shorts)
    L.append("all_oe := %s;" % oe)
    L.append("any_moving := %s;" % mv)
    L.append("fault_any := %s;" % fs)
    L.append("")
    L.append(PLACEHOLDER)
    return v, "\n".join(L)


def guard_process(process_st):
    lines = []
    for ln in process_st.split_st if hasattr(process_st, "split_st") else process_st.splitlines():
        if re.match(r"^\s*\w+_(?:sw|v|cmd)\s*:=", ln):
            continue
        if re.match(r"^\s*(?:pwr|rs|st|go|rel|hm|jg|hlt|rap|ix|dv|rds)_\w+\(", ln):
            continue
        lines.append(ln)
    return "\n".join(lines)


def _prg_pou_xml(vars_list, body_st):
    from xml.sax.saxutils import escape
    var_elems = []
    for ln in vars_list:
        s = ln.strip()
        m = re.match(r"^(\w+)(?:\s+AT\s+(%Q[\w.]+))?\s*:\s*([\w.]+)\s*;$", s)
        if not m:
            continue
        vn, addr, typ = m.group(1), m.group(2), m.group(3)
        if typ in ("BOOL", "INT", "WORD", "REAL"):
            type_xml = "<%s />" % typ
        else:
            type_xml = '<derived name="%s" />' % typ
        var_elems.append(
            '            <variable name="%s"%s>\n'
            '              <type>%s</type>\n'
            '            </variable>' % (vn, ' address="%s"' % addr if addr else "", type_xml))
    iface = '          <localVars>\n' + "\n".join(var_elems) + '\n          </localVars>'
    return (
        '      <pou name="PLC_PRG" pouType="program">\n'
        '        <interface>\n%s\n        </interface>\n'
        '        <body>\n          <ST>\n'
        '            <xhtml xmlns="http://www.w3.org/1999/xhtml">%s</xhtml>\n'
        '          </ST>\n        </body>\n      </pou>' % (iface, escape(body_st)))


def generate_xml(spec, process_st):
    vars_list, stack_st = build_axis_stack(spec)
    body_st = stack_st.replace(PLACEHOLDER, guard_process(process_st))
    fb_pous = _fb_defs_to_xml_pous()
    prg_pou = _prg_pou_xml(vars_list, body_st)
    all_pous = "\n".join(fb_pous) + "\n" + prg_pou
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<project xmlns="http://www.plcopen.org/xml/tc6_0201">\n'
        '  <fileHeader companyName="" productName="Agent Stack Gen v3" productVersion="3.0"\n'
        '              creationDateTime="2026-09-24T00:00:00"\n'
        '              description="Deterministic FB + spec-driven stack + agent process" />\n'
        '  <contentHeader name="agent.project" modificationDateTime="2026-09-24T00:00:00">\n'
        '    <coordinateInfo>\n'
        '      <fbd><scaling x="1" y="1" /></fbd>\n'
        '      <ld><scaling x="1" y="1" /></ld>\n'
        '      <sfc><scaling x="1" y="1" /></sfc>\n'
        '    </coordinateInfo>\n'
        '  </contentHeader>\n'
        '  <types>\n'
        '    <dataTypes />\n'
        '    <pous>\n' + all_pous + '\n    </pous>\n'
        '  </types>\n'
        '  <instances>\n'
        '    <configurations />\n'
        '  </instances>\n'
        '</project>\n')
