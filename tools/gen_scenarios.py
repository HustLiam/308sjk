#!/usr/bin/env python3
"""生成三个连续跟踪版运动控制场景 XML"""
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


def make_interp(vmax, accel, poswin):
    return f"""      <pou name="INTERP" pouType="functionBlock">
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
            <variable name="VMAX"><type><REAL /></type><initialValue><simpleValue value="{vmax}" /></initialValue></variable>
            <variable name="ACCEL"><type><REAL /></type><initialValue><simpleValue value="{accel}" /></initialValue></variable>
            <variable name="POSWIN"><type><REAL /></type><initialValue><simpleValue value="{poswin}" /></initialValue></variable>
          </localVars>
        </interface>
        <body>
          <ST>
            <xhtml xmlns="http://www.w3.org/1999/xhtml">{INTERP_ST}</xhtml>
          </ST>
        </body>
      </pou>"""


def make_xml(name, desc, interp, prg_body, prg_vars):
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://www.plcopen.org/xml/tc6_0201">
  <fileHeader companyName="" productName="Agent Pipeline" productVersion="0.9.0"
              creationDateTime="2026-09-03T14:00:00"
              description="{desc}" />
  <contentHeader name="{name}.project" modificationDateTime="2026-09-03T14:00:00">
    <coordinateInfo>
      <fbd><scaling x="1" y="1" /></fbd>
      <ld><scaling x="1" y="1" /></ld>
      <sfc><scaling x="1" y="1" /></sfc>
    </coordinateInfo>
  </contentHeader>
  <types>
    <dataTypes />
    <pous>
{interp}
      <pou name="PLC_PRG" pouType="program">
        <interface>
{prg_vars}
        </interface>
        <body>
          <ST>
            <xhtml xmlns="http://www.w3.org/1999/xhtml">{prg_body}</xhtml>
          </ST>
        </body>
      </pou>
    </pous>
  </types>
  <instances>
    <configurations />
  </instances>
</project>
"""

# ======== 1. axis_osc ========
axis_osc_prg = """prog_id := 2;
IF cmd_home THEN target := 0; END_IF;
ix_x(pos_target := target, hold_req := quickstop);
x_cmd := ix_x.Setpoint;
move_done := ix_x.Done;
any_moving := ix_x.Busy;"""

axis_osc_vars = """          <localVars>
            <variable name="quickstop" address="%QX0.0"><type><BOOL /></type></variable>
            <variable name="cmd_home" address="%QX0.1"><type><BOOL /></type></variable>
            <variable name="target" address="%QW10"><type><INT /></type></variable>
          </localVars>
          <localVars>
            <variable name="x_fb" address="%QW0"><type><INT /></type></variable>
            <variable name="x_cmd" address="%QW12"><type><INT /></type></variable>
          </localVars>
          <localVars>
            <variable name="move_done" address="%QX1.0"><type><BOOL /></type></variable>
            <variable name="any_moving" address="%QX1.1"><type><BOOL /></type></variable>
            <variable name="prog_id" address="%QW20"><type><INT /></type><initialValue><simpleValue value="2" /></initialValue></variable>
          </localVars>
          <localVars>
            <variable name="ix_x"><type><derived name="INTERP" /></type></variable>
          </localVars>"""

# ======== 2. xy_pick ========
xy_prg = """prog_id := 3;
IF cmd_home THEN x_target := 0; y_target := 0; END_IF;
interlock_active := (x_fb &lt; 20) AND (y_target &gt; 50);
ix_x(pos_target := x_target, hold_req := quickstop);
x_cmd := ix_x.Setpoint;
ix_y(pos_target := y_target, hold_req := quickstop OR interlock_active);
y_cmd := ix_y.Setpoint;
move_done := ix_x.Done AND ix_y.Done;
any_moving := ix_x.Busy OR ix_y.Busy;"""

xy_vars = """          <localVars>
            <variable name="quickstop" address="%QX0.0"><type><BOOL /></type></variable>
            <variable name="cmd_home" address="%QX0.1"><type><BOOL /></type></variable>
            <variable name="x_target" address="%QW10"><type><INT /></type></variable>
            <variable name="y_target" address="%QW11"><type><INT /></type></variable>
          </localVars>
          <localVars>
            <variable name="x_fb" address="%QW0"><type><INT /></type></variable>
            <variable name="y_fb" address="%QW1"><type><INT /></type></variable>
            <variable name="x_cmd" address="%QW13"><type><INT /></type></variable>
            <variable name="y_cmd" address="%QW14"><type><INT /></type></variable>
          </localVars>
          <localVars>
            <variable name="move_done" address="%QX1.0"><type><BOOL /></type></variable>
            <variable name="any_moving" address="%QX1.1"><type><BOOL /></type></variable>
            <variable name="interlock_active" address="%QX1.2"><type><BOOL /></type></variable>
            <variable name="prog_id" address="%QW20"><type><INT /></type><initialValue><simpleValue value="3" /></initialValue></variable>
          </localVars>
          <localVars>
            <variable name="ix_x"><type><derived name="INTERP" /></type></variable>
            <variable name="ix_y"><type><derived name="INTERP" /></type></variable>
          </localVars>"""

# ======== 3. z_lift ========
z_prg = """prog_id := 4;
at_upper := ls_upper;
at_lower := ls_lower;
z_hold := quickstop;
IF cmd_up AND part_present THEN
    z_target := 80;
ELSIF cmd_down THEN
    z_target := 5;
END_IF;
ix_z(pos_target := z_target, hold_req := z_hold);
z_cmd := ix_z.Setpoint;
clamped := at_lower AND part_present;
any_moving := ix_z.Busy;"""

z_vars = """          <localVars>
            <variable name="quickstop" address="%QX0.0"><type><BOOL /></type></variable>
            <variable name="cmd_up" address="%QX0.1"><type><BOOL /></type></variable>
            <variable name="cmd_down" address="%QX0.2"><type><BOOL /></type></variable>
            <variable name="part_present" address="%QX0.3"><type><BOOL /></type></variable>
            <variable name="ls_upper" address="%QX0.4"><type><BOOL /></type></variable>
            <variable name="ls_lower" address="%QX0.5"><type><BOOL /></type></variable>
          </localVars>
          <localVars>
            <variable name="z_fb" address="%QW0"><type><INT /></type></variable>
            <variable name="z_target" address="%QW10"><type><INT /></type></variable>
            <variable name="z_cmd" address="%QW12"><type><INT /></type></variable>
          </localVars>
          <localVars>
            <variable name="at_upper" address="%QX1.0"><type><BOOL /></type></variable>
            <variable name="at_lower" address="%QX1.1"><type><BOOL /></type></variable>
            <variable name="clamped" address="%QX1.2"><type><BOOL /></type></variable>
            <variable name="any_moving" address="%QX1.3"><type><BOOL /></type></variable>
            <variable name="prog_id" address="%QW20"><type><INT /></type><initialValue><simpleValue value="4" /></initialValue></variable>
          </localVars>
          <localVars>
            <variable name="ix_z"><type><derived name="INTERP" /></type></variable>
            <variable name="z_hold"><type><BOOL /></type></variable>
          </localVars>"""

scenarios = [
    ("axis_osc", "Single-axis continuous tracking", make_interp(50.0, 100.0, 2.0), axis_osc_prg, axis_osc_vars),
    ("xy_pick", "XY positioning with interlock", make_interp(40.0, 80.0, 2.0), xy_prg, xy_vars),
    ("z_lift", "Z-axis lift with safety", make_interp(25.0, 60.0, 1.0), z_prg, z_vars),
]

for name, desc, interp, prg, varsd in scenarios:
    xml = make_xml(name, desc, interp, prg, varsd)
    pathlib.Path(f"src/plc/{name}.xml").write_text(xml, encoding="utf-8")
    print(f"{name}.xml: {len(xml)} bytes")
