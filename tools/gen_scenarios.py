#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""混合运动控制场景: 单个 INTERP FB + 环形回绕内联到 PLC_PRG"""
import pathlib

INTERP_ST = """tgt := INT_TO_REAL(pos_target);
IF NOT Busy AND ABS(tgt - pos) &gt; POSWIN THEN
    Busy := TRUE;
    Done := FALSE;
END_IF;
IF hold_req THEN
    IF vel &gt; 0.0 THEN vel := MAX(0.0, vel - ACCEL * SCAN_T);
    ELSIF vel &lt; 0.0 THEN vel := MIN(0.0, vel + ACCEL * SCAN_T);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * ACCEL);
    IF ABS(dist) &lt;= stop_d THEN
        IF dist &gt;= 0.0 THEN vel := MAX(0.0, vel - ACCEL * SCAN_T);
        ELSE vel := MIN(0.0, vel + ACCEL * SCAN_T);
        END_IF;
        IF vel = 0.0 AND ABS(dist) &lt;= POSWIN THEN
            pos := tgt; Busy := FALSE; Done := TRUE;
        END_IF;
    ELSE
        IF dist &gt; 0.0 THEN vel := MIN(VMAX, vel + ACCEL * SCAN_T);
        ELSE vel := MAX(-VMAX, vel - ACCEL * SCAN_T);
        END_IF;
    END_IF;
END_IF;
pos := pos + vel * SCAN_T;
Setpoint := REAL_TO_INT(pos);"""

def vd(n, a, t, init=None):
    i = f'<initialValue><simpleValue value="{init}" /></initialValue>' if init else ""
    return f'<variable name="{n}" address="{a}"><type><{t} /></type>{i}</variable>'

# PLC_PRG: X 和肘用 INTERP 实例, 转盘的环形插补内联(独立的轨迹状态变量)
prg = """prog_id := 5;
IF cmd_home THEN
    x_tgt := 0; tt_tgt := 0; el_tgt := 0;
END_IF;
el_hold := quickstop OR (el_tgt &lt; 0) OR (el_tgt &gt; 270);

(* X 直线轴 + 肘有限旋转轴: 共用 INTERP FB *)
ix_x(pos_target := x_tgt, hold_req := quickstop);
x_cmd := ix_x.Setpoint;
ix_el(pos_target := el_tgt, hold_req := el_hold);
el_cmd := ix_el.Setpoint;

(* 转盘环形轴: 插补逻辑内联(环形回绕 + 最短路径) *)
ttv_tgt := INT_TO_REAL(tt_tgt);
IF NOT ttv_busy AND ABS(ttv_tgt - ttv_pos) &gt; 3.0 THEN
    ttv_busy := TRUE;
    ttv_done := FALSE;
END_IF;
ttv_dist := ttv_tgt - ttv_pos;
IF ttv_dist &gt; 180.0 THEN ttv_dist := ttv_dist - 360.0; END_IF;
IF ttv_dist &lt; -180.0 THEN ttv_dist := ttv_dist + 360.0; END_IF;
IF quickstop THEN
    IF ttv_vel &gt; 0.0 THEN ttv_vel := MAX(0.0, ttv_vel - 90.0 * 0.02);
    ELSIF ttv_vel &lt; 0.0 THEN ttv_vel := MIN(0.0, ttv_vel + 90.0 * 0.02);
    END_IF;
ELSIF ttv_busy THEN
    ttv_stop := (ttv_vel * ttv_vel) / 360.0;
    IF ABS(ttv_dist) &lt;= ttv_stop THEN
        IF ttv_dist &gt;= 0.0 THEN ttv_vel := MAX(0.0, ttv_vel - 3.6);
        ELSE ttv_vel := MIN(0.0, ttv_vel + 3.6);
        END_IF;
        IF ttv_vel = 0.0 AND ABS(ttv_dist) &lt;= 3.0 THEN
            ttv_pos := ttv_tgt; ttv_busy := FALSE; ttv_done := TRUE;
        END_IF;
    ELSE
        IF ttv_dist &gt; 0.0 THEN ttv_vel := MIN(90.0, ttv_vel + 3.6);
        ELSE ttv_vel := MAX(-90.0, ttv_vel - 3.6);
        END_IF;
    END_IF;
END_IF;
ttv_pos := ttv_pos + ttv_vel * 0.02;
IF ttv_pos &gt;= 360.0 THEN ttv_pos := ttv_pos - 360.0; END_IF;
IF ttv_pos &lt; 0.0 THEN ttv_pos := ttv_pos + 360.0; END_IF;
tt_cmd := REAL_TO_INT(ttv_pos);
tt_done := ttv_done;
tt_busy := ttv_busy;

(* 状态 *)
move_done := ix_x.Done AND ttv_done AND ix_el.Done;
any_moving := ix_x.Busy OR ttv_busy OR ix_el.Busy;"""

vars_decl = (
    '          <localVars>\n'
    f'            {vd("quickstop","%QX0.0","BOOL")} {vd("cmd_home","%QX0.1","BOOL")}\n'
    '          </localVars>\n'
    '          <localVars>\n'
    f'            {vd("x_fb","%QW0","INT")} {vd("tt_fb","%QW1","INT")} {vd("el_fb","%QW2","INT")}\n'
    '          </localVars>\n'
    '          <localVars>\n'
    f'            {vd("x_tgt","%QW10","INT")} {vd("tt_tgt","%QW11","INT")} {vd("el_tgt","%QW12","INT")}\n'
    '          </localVars>\n'
    '          <localVars>\n'
    f'            {vd("x_cmd","%QW13","INT")} {vd("tt_cmd","%QW14","INT")} {vd("el_cmd","%QW15","INT")}\n'
    f'            {vd("tt_done","%QX1.2","BOOL")} {vd("tt_busy","%QX1.3","BOOL")}\n'
    '          </localVars>\n'
    '          <localVars>\n'
    f'            {vd("move_done","%QX1.0","BOOL")} {vd("any_moving","%QX1.1","BOOL")} {vd("prog_id","%QW20","INT","5")}\n'
    '          </localVars>\n'
    '          <localVars>\n'
    '            <variable name="ix_x"><type><derived name="INTERP" /></type></variable>\n'
    '            <variable name="ix_el"><type><derived name="INTERP" /></type></variable>\n'
    '            <variable name="el_hold"><type><BOOL /></type></variable>\n'
    '          </localVars>\n'
    '          <localVars>\n'
    '            <variable name="ttv_pos"><type><REAL /></type></variable>\n'
    '            <variable name="ttv_vel"><type><REAL /></type></variable>\n'
    '            <variable name="ttv_tgt"><type><REAL /></type></variable>\n'
    '            <variable name="ttv_dist"><type><REAL /></type></variable>\n'
    '            <variable name="ttv_stop"><type><REAL /></type></variable>\n'
    '            <variable name="ttv_busy"><type><BOOL /></type></variable>\n'
    '            <variable name="ttv_done"><type><BOOL /></type></variable>\n'
    '          </localVars>')

interp_fb = f"""      <pou name="INTERP" pouType="functionBlock">
        <interface>
          <inputVars>
            <variable name="pos_target"><type><INT /></type></variable>
            <variable name="hold_req"><type><BOOL /></type></variable>
          </inputVars>
          <outputVars>
            <variable name="Setpoint"><type><INT /></type></variable>
            <variable name="Busy"><type><BOOL /></type></variable>
            <variable name="Done"><type><BOOL /></type></variable>
          </outputVars>
          <localVars>
            <variable name="pos"><type><REAL /></type></variable>
            <variable name="vel"><type><REAL /></type></variable>
            <variable name="tgt"><type><REAL /></type></variable>
            <variable name="dist"><type><REAL /></type></variable>
            <variable name="stop_d"><type><REAL /></type></variable>
            <variable name="SCAN_T"><type><REAL /></type><initialValue><simpleValue value="0.02" /></initialValue></variable>
            <variable name="VMAX"><type><REAL /></type><initialValue><simpleValue value="50.0" /></initialValue></variable>
            <variable name="ACCEL"><type><REAL /></type><initialValue><simpleValue value="100.0" /></initialValue></variable>
            <variable name="POSWIN"><type><REAL /></type><initialValue><simpleValue value="2.0" /></initialValue></variable>
          </localVars>
        </interface>
        <body>
          <ST>
            <xhtml xmlns="http://www.w3.org/1999/xhtml">{INTERP_ST}</xhtml>
          </ST>
        </body>
      </pou>"""

xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0201">
  <fileHeader companyName="" productName="Agent Pipeline" productVersion="1.3.0"
              creationDateTime="2026-09-03T20:00:00"
              description="Mixed linear + rotary: single INTERP FB + inline turntable wraparound" />
  <contentHeader name="mixed_lin_rot" modificationDateTime="2026-09-03T20:00:00">
    <coordinateInfo>
      <fbd><scaling x="1" y="1" /></fbd>
      <ld><scaling x="1" y="1" /></ld>
      <sfc><scaling x="1" y="1" /></sfc>
    </coordinateInfo>
  </contentHeader>
  <types><dataTypes /><pous>
{interp_fb}
      <pou name="PLC_PRG" pouType="program">
        <interface>
{vars_decl}
        </interface>
        <body><ST><xhtml xmlns="http://www.w3.org/1999/xhtml">{prg}</xhtml></ST></body>
      </pou>
    </pous></types>
  <instances><configurations /></instances>
</project>
"""

pathlib.Path("src/plc/mixed_lin_rot.xml").write_text(xml, encoding="utf-8")
print(f"mixed_lin_rot.xml: {len(xml)} bytes (single INTERP + inline turntable)")
