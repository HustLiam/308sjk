FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
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
    edge_ir : BOOL;
    SCAN_T : REAL := 0.02;
  END_VAR
IF INT_TO_REAL(pos_target) > MAXPOS OR pos_target < 0 THEN
    (* target out of range: no start, hold position *)
    Done := TRUE;
ELSE
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;
IF fire AND NOT edge_ir THEN
    edge_noop := FALSE;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    (* continuous tracking: new target starts new trajectory while fire held *)
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
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    sp_prev : INT := 0;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
  END_VAR
(* ============ DRIVE402: servo drive (CSP follow + position loop) ============ *)
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
    ELSE state := 1;
END_CASE;

(* ---- position loop: P control + setpoint differential feedforward ---- *)
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

(* ---- status word ---- *)
sw := 16#0031;
IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
IF state >= 4 THEN sw := sw OR 16#0004; END_IF;
IF (state >= 4) AND (v_out = 0.0) THEN sw := sw OR 16#0400; END_IF;

v_cmd := REAL_TO_INT(v_out);
sp_prev := Setpoint;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    cw : WORD;
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
        ELSE pstep := 0;
    END_CASE;
ELSE
    cw := (cw AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
Status := (sw AND 16#0004) <> 0;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dr_x : DRIVE402;
    dr_y : DRIVE402;
    dr_z : DRIVE402;
    pw_x : MC_POWER;
    pw_y : MC_POWER;
    pw_z : MC_POWER;
    pl_step : INT := 0;
    tx : INT := 0;
    ty : INT := 0;
    tz : INT := 0;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    abort_latch : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    draw_trig : BOOL;
    go_trig : BOOL;
    hold : BOOL;
    enable_ax : BOOL;
    xy_vmax : REAL := 40.0;
  END_VAR
  VAR
    prog_id AT %QW20 : INT := 3;
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
  END_VAR
prog_id := 3;

(* ---- pen state: pure physical threshold (no enable/run composition) ---- *)
pen_down := (z_fb <= 2);

(* ---- hold / abort conditions ---- *)
hold := quickstop OR fault_any OR (NOT run) OR (NOT all_oe);
enable_ax := run AND (NOT quickstop) AND (NOT fault_any);

(* ---- interpolators ---- *)
xy_vmax := 40.0;
IF (pl_step >= 3) AND (pl_step <= 27) THEN xy_vmax := 20.0; END_IF;

ix_x(fire := go_x_exe, pos_target := tx, hold_req := hold, VMAX := xy_vmax, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := go_y_exe, pos_target := ty, hold_req := hold, VMAX := xy_vmax, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := go_z_exe, pos_target := tz, hold_req := hold, VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10.0);

(* ---- drives ---- *)
dr_x(cw := pw_x.cw, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
dr_y(cw := pw_y.cw, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
dr_z(cw := pw_z.cw, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

x_sw := WORD_TO_INT(dr_x.sw);
y_sw := WORD_TO_INT(dr_y.sw);
z_sw := WORD_TO_INT(dr_z.sw);
x_v := dr_x.v_cmd;
y_v := dr_y.v_cmd;
z_v := dr_z.v_cmd;

(* ---- fault detection (C6) ---- *)
fault_any := ((dr_x.sw AND 16#0008) <> 0) OR ((dr_y.sw AND 16#0008) <> 0) OR ((dr_z.sw AND 16#0008) <> 0);

(* ---- power handshake ---- *)
pw_x(Enable := enable_ax, sw := dr_x.sw);
pw_y(Enable := enable_ax, sw := dr_y.sw);
pw_z(Enable := enable_ax, sw := dr_z.sw);
all_oe := pw_x.Status AND pw_y.Status AND pw_z.Status;

(* ---- edges ---- *)
draw_trig := cmd_draw AND (NOT edge_draw);
edge_draw := cmd_draw;
go_trig := cmd_go AND (NOT edge_go);
edge_go := cmd_go;

(* ---- abort handling (C5/C6/C7) ---- *)
IF quickstop OR fault_any OR (NOT run) OR (NOT all_oe) THEN
    abort_latch := TRUE;
    IF pl_step <> 0 THEN
        pl_step := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := FALSE;
    END_IF;
    plot_done := FALSE;
END_IF;
IF cmd_reset AND (NOT quickstop) AND (NOT fault_any) THEN
    abort_latch := FALSE;
END_IF;

(* ---- plot sequence start: requires cmd_draw rising edge + enable + no abort latch (C7/C5) ---- *)
IF (pl_step = 0) AND draw_trig AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any)
   AND (NOT abort_latch) AND (NOT go_x_exe) AND (NOT go_y_exe) AND (NOT go_z_exe) THEN
    tz := 10;
    go_z_exe := TRUE;
    plot_done := FALSE;
    pl_step := 1;
END_IF;

(* ---- step table: 1=z-up 2=XY to start 3=z-down 4..27=24 waypoints 28=z-up 29=return center ---- *)
CASE pl_step OF
    1: tz := 10;
    2: tx := 50; ty := 25; go_x_exe := TRUE; go_y_exe := TRUE;
    3: tz := 0;
    4: tx := 57; ty := 26;
    5: tx := 63; ty := 28;
    6: tx := 68; ty := 32;
    7: tx := 72; ty := 38;
    8: tx := 74; ty := 44;
    9: tx := 75; ty := 50;
    10: tx := 74; ty := 57;
    11: tx := 72; ty := 63;
    12: tx := 68; ty := 68;
    13: tx := 63; ty := 72;
    14: tx := 57; ty := 74;
    15: tx := 50; ty := 75;
    16: tx := 43; ty := 74;
    17: tx := 38; ty := 72;
    18: tx := 32; ty := 68;
    19: tx := 28; ty := 63;
    20: tx := 26; ty := 57;
    21: tx := 25; ty := 50;
    22: tx := 26; ty := 44;
    23: tx := 28; ty := 38;
    24: tx := 32; ty := 32;
    25: tx := 38; ty := 28;
    26: tx := 43; ty := 26;
    27: tx := 50; ty := 25;
    28: tz := 10;
    29: tx := 50; ty := 50;
    ELSE pl_step := pl_step;
END_CASE;

(* ---- step advance: C2/C3/C4 sequencing via interp Done + physical windows ---- *)
IF pl_step = 1 THEN
    IF (NOT ix_z.Busy) AND ix_z.Done AND (ABS(z_fb - 10) <= 1) THEN
        pl_step := 2;
    END_IF;
ELSIF pl_step <> 0 THEN
    IF (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done
       AND (NOT ix_z.Busy) AND ix_z.Done
       AND (ABS(x_fb - tx) <= 2) AND (ABS(y_fb - ty) <= 2) AND (ABS(z_fb - tz) <= 1) THEN
        IF pl_step = 29 THEN
            plot_done := TRUE;
            pl_step := 0;
            go_x_exe := FALSE;
            go_y_exe := FALSE;
            go_z_exe := FALSE;
        ELSE
            pl_step := pl_step + 1;
        END_IF;
    END_IF;
END_IF;

(* ---- manual home: Z reference = pen-up safe position ---- *)
IF (pl_step = 0) AND cmd_home AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any) THEN
    tz := 10;
    go_z_exe := TRUE;
END_IF;

(* ---- manual positioning: rejected while pen down (C1/AC3, only pen-lift requests accepted) ---- *)
IF (pl_step = 0) AND go_trig AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any)
   AND (NOT abort_latch) AND ((z_fb > 2) OR (z_sp >= 9)) THEN
    tx := x_sp;
    ty := y_sp;
    tz := z_sp;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
END_IF;

(* ---- manual jog: X only, pen-up only (C1/AC2) ---- *)
IF (pl_step = 0) AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND (z_fb > 2) THEN
    IF jog_fwd THEN
        IF tx < 100 THEN tx := tx + 1; END_IF;
        go_x_exe := TRUE;
    END_IF;
    IF jog_rev THEN
        IF tx > 0 THEN tx := tx - 1; END_IF;
        go_x_exe := TRUE;
    END_IF;
END_IF;

(* ---- manual completion: release exe lines when all axes settled ---- *)
IF (pl_step = 0) AND (NOT cmd_go) AND (NOT cmd_home) AND (NOT jog_fwd) AND (NOT jog_rev)
   AND (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done
   AND (NOT ix_z.Busy) AND ix_z.Done
   AND (ABS(x_fb - tx) <= 2) AND (ABS(y_fb - ty) <= 2) AND (ABS(z_fb - tz) <= 1) THEN
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
END_IF;

(* ---- indicators ---- *)
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := (pl_step = 0) AND (NOT go_x_exe) AND (NOT go_y_exe) AND (NOT go_z_exe);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
