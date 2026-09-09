FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL;
    ACCEL : REAL;
    POSWIN : REAL;
    MAXPOS : INT;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    pos : REAL;
    tgt : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
    edge_ir : BOOL;
    SCAN_T : REAL := 0.02;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: no start *)
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    (* continuous tracking *)
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
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
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
ELSE
    state := 1;
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

(* status word synthesis *)
sw := 16#0000;
IF state = 1 THEN sw := 16#0040; END_IF;
IF state >= 2 THEN sw := sw OR 16#0031; END_IF;
IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
IF state >= 4 THEN sw := sw OR 16#0004; END_IF;
IF state = 5 THEN sw := sw AND 16#FFDF; END_IF;
IF (state = 6) OR (state = 7) THEN sw := sw OR 16#0008; END_IF;
END_FUNCTION_BLOCK

FUNCTION_BLOCK AX_ENABLE
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
  END_VAR
  VAR_INPUT
    quickstop AT %QX0.5 : BOOL;
  END_VAR
  VAR_OUTPUT
    cw : WORD;
    Status : BOOL;
  END_VAR
  VAR
    pstep : INT := 0;
  END_VAR
IF quickstop THEN
    cw := (cw AND 16#00F0) OR 16#0002;
    pstep := 0;
ELSIF Enable THEN
    CASE pstep OF
        0: cw := (cw AND 16#00F0) OR 16#0006;  pstep := 1;
        1: IF (sw AND 16#0001) <> 0 THEN cw := (cw AND 16#00F0) OR 16#0007; pstep := 2; END_IF;
        2: IF (sw AND 16#0002) <> 0 THEN cw := (cw AND 16#00F0) OR 16#000F; pstep := 3; END_IF;
        3: IF (sw AND 16#0004) <> 0 THEN pstep := 4; END_IF;
        4: cw := (cw AND 16#00F0) OR 16#000F;
    ELSE
        pstep := 0;
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
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    en_x : AX_ENABLE;
    en_y : AX_ENABLE;
    en_z : AX_ENABLE;
    cw_x : WORD;
    cw_y : WORD;
    cw_z : WORD;
    sw_x : WORD;
    sw_y : WORD;
    sw_z : WORD;
    tx : INT;
    ty : INT;
    tz : INT;
    k : INT;
    ang : REAL;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    hold_ax : BOOL;
    draw_exe : BOOL;
    go_exe : BOOL;
    home_exe : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    edge_home : BOOL;
    edge_reset : BOOL;
    pl_step : INT := 0;
    qs_stop : BOOL;
    flt : BOOL;
    md_latch : BOOL;
    xy_ok : BOOL;
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

(* ---- enable / handshake (CiA402) ---- *)
en_x(Enable := run, sw := sw_x, quickstop := quickstop);
en_y(Enable := run, sw := sw_y, quickstop := quickstop);
en_z(Enable := run, sw := sw_z, quickstop := quickstop);
cw_x := en_x.cw;
cw_y := en_y.cw;
cw_z := en_z.cw;

(* ---- pen state: pure physical judgement ---- *)
pen_down := z_fb <= 2;

(* ---- quickstop abort (C4) ---- *)
IF quickstop THEN
    qs_stop := TRUE;
    pl_step := 0;
    draw_exe := FALSE;
    go_exe := FALSE;
    home_exe := FALSE;
    plot_done := FALSE;
    md_latch := FALSE;
END_IF;

(* ---- fault abort (C5) ---- *)
IF ((sw_x AND 16#0008) <> 0) OR ((sw_y AND 16#0008) <> 0) OR ((sw_z AND 16#0008) <> 0) THEN
    flt := TRUE;
    pl_step := 0;
    draw_exe := FALSE;
    go_exe := FALSE;
    home_exe := FALSE;
    plot_done := FALSE;
    md_latch := FALSE;
END_IF;

(* ---- reset (quickstop released + cmd_reset / fault reset) ---- *)
IF (NOT quickstop) AND cmd_reset AND NOT edge_reset THEN
    qs_stop := FALSE;
    flt := FALSE;
END_IF;

(* ---- default targets from NC channels ---- *)
tx := x_sp;
ty := y_sp;
tz := z_sp;

(* ---- manual home (z up) ---- *)
IF cmd_home AND NOT edge_home AND (pl_step = 0) AND (NOT quickstop) AND run AND en_z.Status THEN
    home_exe := TRUE;
END_IF;

(* ---- manual go, interlocked when pen down (C2) ---- *)
IF cmd_go AND NOT edge_go AND (NOT pen_down) AND (pl_step = 0) AND (NOT quickstop) AND run
   AND en_x.Status AND en_y.Status AND (NOT qs_stop) AND (NOT flt) THEN
    go_exe := TRUE;
    md_latch := FALSE;
END_IF;

(* ---- draw start: only from pen-up safe state (C6) ---- *)
IF cmd_draw AND NOT edge_draw AND run AND en_x.Status AND en_y.Status AND en_z.Status
   AND (z_fb >= 8) AND (NOT quickstop) AND (pl_step = 0) AND (NOT qs_stop) AND (NOT flt) THEN
    draw_exe := TRUE;
    plot_done := FALSE;
    pl_step := 1;
END_IF;

(* ---- sequence step table ---- *)
CASE pl_step OF
    0: tx := x_fb; ty := y_fb; tz := z_fb;
    1: tx := x_fb; ty := y_fb; tz := 10;
    2: tx := 50; ty := 25; tz := 10;
    3: tx := 50; ty := 25; tz := 0;
    4..26:
       k := pl_step - 3;
       ang := INT_TO_REAL(k) * 0.2617994;
       tx := 50 + REAL_TO_INT(25.0 * SIN(ang));
       ty := 50 - REAL_TO_INT(25.0 * COS(ang));
       tz := 0;
    27: tx := 50; ty := 25; tz := 0;
    28: tx := 50; ty := 25; tz := 10;
    29: tx := 50; ty := 50; tz := 10;
ELSE
    tx := x_fb; ty := y_fb; tz := 10;
END_CASE;

(* ---- XY physically at target before pen down (C7) ---- *)
xy_ok := (ABS(x_fb - tx) <= 2) AND (ABS(y_fb - ty) <= 2);

(* ---- fire lines: level-held ---- *)
fire_x := go_exe OR draw_exe;
fire_y := go_exe OR draw_exe;
fire_z := go_exe OR home_exe OR draw_exe;
hold_ax := quickstop OR (NOT run);

(* ---- interpolation engines ---- *)
ix_x(fire := fire_x, pos_target := tx, hold_req := hold_ax, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := fire_y, pos_target := ty, hold_req := hold_ax, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := fire_z, pos_target := tz, hold_req := hold_ax, VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10);

(* ---- drives (CSP following) ---- *)
drv_x(cw := cw_x, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
drv_y(cw := cw_y, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
drv_z(cw := cw_z, Setpoint := ix_z.Setpoint, pos_fb := z_fb);
sw_x := drv_x.sw;
sw_y := drv_y.sw;
sw_z := drv_z.sw;

x_v := REAL_TO_INT(drv_x.v_cmd);
y_v := REAL_TO_INT(drv_y.v_cmd);
z_v := REAL_TO_INT(drv_z.v_cmd);

(* ---- manual jog, interlocked when pen down (C1) ---- *)
IF (NOT draw_exe) AND (NOT go_exe) AND (NOT quickstop) AND run AND en_x.Status AND (NOT pen_down) THEN
    IF jog_fwd THEN x_v := 20; END_IF;
    IF jog_rev THEN x_v := -20; END_IF;
END_IF;

(* ---- sequence advance: all axes idle and done ---- *)
IF (pl_step <> 0) AND (NOT quickstop) AND run
   AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF (pl_step = 3) AND (NOT xy_ok) THEN
        pl_step := pl_step;
    ELSIF pl_step = 29 THEN
        plot_done := TRUE;
        pl_step := 0;
        draw_exe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- manual move completion ---- *)
IF go_exe AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    go_exe := FALSE;
    md_latch := TRUE;
END_IF;
IF home_exe AND (NOT ix_z.Busy) AND ix_z.Done THEN
    home_exe := FALSE;
END_IF;

(* ---- outputs (C8) ---- *)
move_done := md_latch AND (NOT draw_exe);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
all_oe := en_x.Status AND en_y.Status AND en_z.Status;
fault_any := ((sw_x AND 16#0008) <> 0) OR ((sw_y AND 16#0008) <> 0) OR ((sw_z AND 16#0008) <> 0);

(* ---- edge memory update ---- *)
edge_draw := cmd_draw;
edge_go := cmd_go;
edge_home := cmd_home;
edge_reset := cmd_reset;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
