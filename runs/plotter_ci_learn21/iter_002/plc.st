FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    hold_req : BOOL;
    pos_target : INT;
    VMAX : REAL;
    ACCEL : REAL;
    POSWIN : REAL;
    MAXPOS : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    dist : REAL := 0.0;
    stop_d : REAL := 0.0;
    SCAN_T : REAL := 0.02;
    edge_ir : BOOL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: no motion *)
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;
edge_ir := fire;

IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - ACCEL * SCAN_T);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + ACCEL * SCAN_T);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * ACCEL);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - ACCEL * SCAN_T);
        ELSE vel := MIN(0.0, vel + ACCEL * SCAN_T);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(VMAX, vel + ACCEL * SCAN_T);
        ELSE vel := MAX(-VMAX, vel - ACCEL * SCAN_T);
        END_IF;
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
    v_cmd : REAL;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    v_out : REAL := 0.0;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
    sp_prev : INT;
  END_VAR
(* state: 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA *)

cmd := cw AND 16#000F;

IF state = 7 AND (cw AND 16#0080) <> 0 THEN
    state := 1;
END_IF;

CASE state OF
    1: IF cmd = 16#06 THEN state := 2; END_IF;
    2: IF cmd = 16#07 THEN state := 3;
       ELSIF cmd = 16#0F THEN state := 4;
       ELSIF cmd = 16#00 THEN state := 1;
       END_IF;
    3: IF cmd = 16#0F THEN state := 4;
       ELSIF cmd = 16#06 THEN state := 2;
       ELSIF cmd = 16#00 THEN state := 1;
       ELSIF cmd = 16#02 THEN state := 5;
       END_IF;
    4: IF cmd = 16#07 THEN state := 3;
       ELSIF cmd = 16#06 THEN state := 2;
       ELSIF cmd = 16#00 THEN state := 1;
       ELSIF cmd = 16#02 THEN state := 5;
       END_IF;
    5: IF v_out = 0.0 THEN
           IF cmd = 16#0F THEN state := 4;
           ELSIF cmd = 16#06 THEN state := 2;
           ELSIF cmd = 16#00 THEN state := 1;
           END_IF;
       END_IF;
    6: IF v_out = 0.0 THEN state := 7; END_IF;
END_CASE;

IF state >= 4 AND state <= 6 THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / SCAN_T;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF v_ff = 0.0 AND ABS(pos_err) <= 1.5 THEN
        v_out := 0.0;
    END_IF;
ELSIF state = 5 THEN
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - QS_DECEL * SCAN_T);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + QS_DECEL * SCAN_T);
    END_IF;
ELSE
    v_out := 0.0;
END_IF;

sp_prev := Setpoint;
v_cmd := v_out;

(* status word *)
sw := 16#0010;
CASE state OF
    1: sw := sw OR 16#0040;
    2: sw := sw OR 16#0021;
    3: sw := sw OR 16#0023;
    4: sw := sw OR 16#0037;
    5: sw := sw OR 16#0011;
    6: sw := sw OR 16#0018;
    7: sw := sw OR 16#0018;
END_CASE;
IF state = 4 AND v_out = 0.0 AND ABS(pos_err) <= 1.5 THEN
    sw := sw OR 16#0400;
END_IF;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
  END_VAR
  VAR
    pstep : INT := 0;
  END_VAR
IF Enable AND pstep = 4 AND (sw AND 16#0004) = 0 AND (sw AND 16#0008) = 0 THEN
    pstep := 0;
END_IF;
IF Enable THEN
    CASE pstep OF
        0: cw := (cw AND 16#00F0) OR 16#0006;  pstep := 1;
        1: IF (sw AND 16#0001) <> 0 THEN cw := (cw AND 16#00F0) OR 16#0007; pstep := 2; END_IF;
        2: IF (sw AND 16#0002) <> 0 THEN cw := (cw AND 16#00F0) OR 16#000F; pstep := 3; END_IF;
        3: IF (sw AND 16#0004) <> 0 THEN pstep := 4; END_IF;
        4: pstep := 4;
    END_CASE;
ELSE
    cw := (cw AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
Status := (sw AND 16#0004) <> 0;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    pow_x : MC_POWER;
    pow_y : MC_POWER;
    pow_z : MC_POWER;
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    sw_x : WORD;
    sw_y : WORD;
    sw_z : WORD;
    cw_x : WORD;
    cw_y : WORD;
    cw_z : WORD;
    oe_x : BOOL;
    oe_y : BOOL;
    oe_z : BOOL;
    vx_r : REAL := 0.0;
    vy_r : REAL := 0.0;
    vz_r : REAL := 0.0;
    x_set : INT;
    y_set : INT;
    z_set : INT;
    tx : INT := 50;
    ty : INT := 50;
    tz : INT := 10;
    bx : BOOL;
    by : BOOL;
    bz : BOOL;
    dx : BOOL;
    dy : BOOL;
    dz : BOOL;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    mgo_x_exe : BOOL;
    mgo_y_exe : BOOL;
    mgo_z_exe : BOOL;
    pl_step : INT := 0;
    wp : INT := 0;
    ed_draw : BOOL;
    ed_go : BOOL;
    ed_home : BOOL;
    ed_reset : BOOL;
    fault_latch : BOOL;
    fault_live : BOOL;
  END_VAR
  VAR
    run AT %QX0.0 : BOOL;
    cmd_home AT %QX0.1 : BOOL;
    cmd_go AT %QX0.2 : BOOL;
    jog_fwd AT %QX0.3 : BOOL;
    jog_rev AT %QX0.4 : BOOL;
    quickstop AT %QX0.5 : BOOL;
    cmd_reset AT %QX0.7 : BOOL;
    cmd_draw AT %QX2.0 : BOOL;
    all_oe AT %QX1.0 : BOOL;
    move_done AT %QX1.1 : BOOL;
    any_moving AT %QX1.2 : BOOL;
    fault_any AT %QX1.3 : BOOL;
    pen_down AT %QX2.1 : BOOL;
    plot_done AT %QX2.2 : BOOL;
    x_fb AT %QW0 : INT;
    y_fb AT %QW1 : INT;
    z_fb AT %QW2 : INT;
    x_sp AT %QW10 : INT;
    y_sp AT %QW11 : INT;
    z_sp AT %QW12 : INT;
    x_sw AT %QW6 : INT;
    y_sw AT %QW7 : INT;
    z_sw AT %QW8 : INT;
    x_v AT %QW13 : INT;
    y_v AT %QW14 : INT;
    z_v AT %QW15 : INT;
    prog_id AT %QW20 : INT := 3;
  END_VAR
prog_id := 3;

(* ---- physical pen state ---- *)
pen_down := (z_fb <= 2);

(* ---- fault handling (C7) ---- *)
fault_live := ((sw_x AND 16#0008) <> 0) OR ((sw_y AND 16#0008) <> 0) OR ((sw_z AND 16#0008) <> 0);
IF fault_live THEN
    fault_latch := TRUE;
END_IF;
IF cmd_reset AND NOT ed_reset AND NOT fault_live THEN
    fault_latch := FALSE;
END_IF;
ed_reset := cmd_reset;
fault_any := fault_latch OR fault_live;

(* ---- power handshake (C8) ---- *)
pow_x(Enable := run AND NOT quickstop AND NOT fault_latch, sw := sw_x, cw := cw_x, Status => oe_x);
pow_y(Enable := run AND NOT quickstop AND NOT fault_latch, sw := sw_y, cw := cw_y, Status => oe_y);
pow_z(Enable := run AND NOT quickstop AND NOT fault_latch, sw := sw_z, cw := cw_z, Status => oe_z);
all_oe := oe_x AND oe_y AND oe_z;

(* ---- manual positioning (C1: rejected while pen down) ---- *)
IF cmd_go AND NOT ed_go THEN
    mgo_x_exe := FALSE;
    mgo_y_exe := FALSE;
    mgo_z_exe := FALSE;
    IF (NOT pen_down) AND all_oe AND (NOT quickstop) AND (NOT fault_latch) AND (pl_step = 0) THEN
        tx := x_sp;
        ty := y_sp;
        tz := z_sp;
        mgo_x_exe := TRUE;
        mgo_y_exe := TRUE;
        mgo_z_exe := TRUE;
    END_IF;
END_IF;
ed_go := cmd_go;
move_done := (mgo_x_exe OR mgo_y_exe OR mgo_z_exe) AND dx AND dy AND dz AND (NOT bx) AND (NOT by) AND (NOT bz);

(* ---- manual jog (C1: blocked while pen down) ---- *)
IF jog_fwd AND (NOT pen_down) AND all_oe AND (NOT quickstop) AND (pl_step = 0) AND (NOT fault_latch) THEN
    IF tx < 95 THEN tx := tx + 5; END_IF;
    mgo_x_exe := TRUE;
END_IF;
IF jog_rev AND (NOT pen_down) AND all_oe AND (NOT quickstop) AND (pl_step = 0) AND (NOT fault_latch) THEN
    IF tx > 5 THEN tx := tx - 5; END_IF;
    mgo_x_exe := TRUE;
END_IF;

(* ---- home: pen up ---- *)
IF cmd_home AND NOT ed_home AND all_oe AND (NOT quickstop) THEN
    tz := 10;
    mgo_z_exe := TRUE;
END_IF;
ed_home := cmd_home;

(* ---- draw start (C8: only when all_oe) ---- *)
IF cmd_draw AND NOT ed_draw AND (pl_step = 0) AND all_oe AND (NOT quickstop) AND (NOT fault_latch) THEN
    plot_done := FALSE;
    wp := 0;
    mgo_x_exe := FALSE;
    mgo_y_exe := FALSE;
    mgo_z_exe := FALSE;
    tz := 10;
    go_z_exe := TRUE;
    pl_step := 10;
END_IF;
ed_draw := cmd_draw;

(* ---- abort on quickstop / fault (C6/C7: level abort, no latch) ---- *)
IF quickstop OR fault_latch THEN
    pl_step := 0;
    wp := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    mgo_x_exe := FALSE;
    mgo_y_exe := FALSE;
    mgo_z_exe := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- sequence: 10=pen up 11=XY to start 12=pen down 13=sweep 14=pen up 15=to center (C2..C5) ---- *)
CASE pl_step OF
    0: pl_step := 0;
    10: IF dz AND (NOT bz) THEN
            tx := 50;
            ty := 25;
            go_x_exe := TRUE;
            go_y_exe := TRUE;
            pl_step := 11;
        END_IF;
    11: IF dx AND dy AND (NOT bx) AND (NOT by) THEN
            go_x_exe := FALSE;
            go_y_exe := FALSE;
            tz := 0;
            wp := 1;
            pl_step := 12;
        END_IF;
    12: IF dz AND (NOT bz) THEN
            go_x_exe := TRUE;
            go_y_exe := TRUE;
            pl_step := 13;
        END_IF;
    13: CASE wp OF
            1: tx := 57; ty := 26;
            2: tx := 63; ty := 28;
            3: tx := 68; ty := 32;
            4: tx := 72; ty := 38;
            5: tx := 74; ty := 44;
            6: tx := 75; ty := 50;
            7: tx := 74; ty := 57;
            8: tx := 72; ty := 63;
            9: tx := 68; ty := 68;
            10: tx := 63; ty := 72;
            11: tx := 57; ty := 74;
            12: tx := 50; ty := 75;
            13: tx := 44; ty := 74;
            14: tx := 38; ty := 72;
            15: tx := 32; ty := 68;
            16: tx := 28; ty := 63;
            17: tx := 26; ty := 57;
            18: tx := 25; ty := 50;
            19: tx := 26; ty := 44;
            20: tx := 28; ty := 38;
            21: tx := 32; ty := 32;
            22: tx := 38; ty := 28;
            23: tx := 44; ty := 26;
            24: tx := 50; ty := 25;
        END_CASE;
        IF dx AND dy AND (NOT bx) AND (NOT by) THEN
            wp := wp + 1;
            IF wp > 24 THEN
                go_x_exe := FALSE;
                go_y_exe := FALSE;
                tz := 10;
                pl_step := 14;
            END_IF;
        END_IF;
    14: IF dz AND (NOT bz) THEN
            tx := 50;
            ty := 50;
            go_x_exe := TRUE;
            go_y_exe := TRUE;
            pl_step := 15;
        END_IF;
    15: IF dx AND dy AND (NOT bx) AND (NOT by) THEN
            go_x_exe := FALSE;
            go_y_exe := FALSE;
            go_z_exe := FALSE;
            plot_done := TRUE;
            pl_step := 0;
        END_IF;
ELSE
    pl_step := 0;
END_CASE;

(* ---- interpolators ---- *)
ix_x(fire := go_x_exe OR mgo_x_exe, pos_target := tx, hold_req := quickstop, VMAX := 40.0, ACCEL := 60.0, POSWIN := 0.6, MAXPOS := 100.0, Setpoint => x_set, Busy => bx, Done => dx);
ix_y(fire := go_y_exe OR mgo_y_exe, pos_target := ty, hold_req := quickstop, VMAX := 40.0, ACCEL := 60.0, POSWIN := 0.6, MAXPOS := 100.0, Setpoint => y_set, Busy => by, Done => dy);
ix_z(fire := go_z_exe OR mgo_z_exe, pos_target := tz, hold_req := quickstop, VMAX := 20.0, ACCEL := 40.0, POSWIN := 0.6, MAXPOS := 10.0, Setpoint => z_set, Busy => bz, Done => dz);

(* ---- drives ---- *)
drv_x(cw := cw_x, Setpoint := x_set, pos_fb := x_fb, sw => sw_x, v_cmd => vx_r);
drv_y(cw := cw_y, Setpoint := y_set, pos_fb := y_fb, sw => sw_y, v_cmd => vy_r);
drv_z(cw := cw_z, Setpoint := z_set, pos_fb := z_fb, sw => sw_z, v_cmd => vz_r);

x_v := REAL_TO_INT(vx_r);
y_v := REAL_TO_INT(vy_r);
z_v := REAL_TO_INT(LIMIT(-20.0, vz_r, 20.0));

(* quickstop: zero velocity commands immediately (C6) *)
IF quickstop THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);

x_sw := WORD_TO_INT(sw_x);
y_sw := WORD_TO_INT(sw_y);
z_sw := WORD_TO_INT(sw_z);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
