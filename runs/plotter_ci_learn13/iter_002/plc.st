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
    SCAN_T : REAL := 0.02;
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    edge_ir : BOOL;
    dist : REAL := 0.0;
    stop_d : REAL := 0.0;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: no start, hold position *)
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
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
    v_cmd : REAL;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    v_out : REAL := 0.0;
    sp_prev : INT := 0;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
  END_VAR
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

(* ---- position loop: P control + setpoint diff feedforward ---- *)
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

(* ---- status word CiA402 ---- *)
sw := 16#0040;
CASE state OF
    2: sw := 16#0031;
    3: sw := 16#0033;
    4: sw := 16#0037;
    5: sw := 16#0017;
    6: sw := 16#0007;
    7: sw := 16#000F;
    ELSE sw := 16#0040;
END_CASE;
IF v_out = 0.0 THEN sw := sw OR 16#0400; END_IF;

v_cmd := v_out;
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
IF enable AND pstep = 4 AND (sw AND 16#0004) = 0 AND (sw AND 16#0008) = 0 THEN
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

FUNCTION_BLOCK MC_MOVEABSOLUTE
  VAR_INPUT
    fire : BOOL;
    interp_done : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
  END_VAR
  VAR
    interp_exe : BOOL;
    edge_ma : BOOL;
  END_VAR
IF fire AND NOT edge_ma THEN
    interp_exe := TRUE;
END_IF;
edge_ma := fire;
IF interp_exe AND interp_done THEN
    interp_exe := FALSE;
END_IF;
Done := NOT interp_exe;
Busy := interp_exe;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    pw_x : MC_POWER;
    pw_y : MC_POWER;
    pw_z : MC_POWER;
    ma_x : MC_MOVEABSOLUTE;
    ma_y : MC_MOVEABSOLUTE;
    ma_z : MC_MOVEABSOLUTE;
    en : BOOL;
    fault_now : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    go_exe : BOOL;
    manual_done : BOOL;
    idle3 : BOOL;
    xy_idle : BOOL;
    pl_step : INT := 0;
    seg : INT := 0;
    ang : REAL := 0.0;
    tx : INT := 0;
    ty : INT := 0;
    tz : INT := 10;
    vmxy : REAL := 40.0;
    swx : WORD;
    swy : WORD;
    swz : WORD;
    cwx : WORD;
    cwy : WORD;
    cwz : WORD;
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
    prog_id AT %QW20 : INT;
  END_VAR
prog_id := 3;

(* ---- physical pen state (pure judgement, independent of sequence) ---- *)
pen_down := (z_fb <= 2);

(* ---- fault monitoring (C4) ---- *)
fault_now := ((swx AND 16#0008) <> 0) OR ((swy AND 16#0008) <> 0) OR ((swz AND 16#0008) <> 0);
fault_any := fault_now;

(* ---- enable level (C3/C6) ---- *)
en := run AND (NOT quickstop) AND (NOT fault_now);

(* ---- velocity limit: circle segment 20 percent/s, otherwise 40 percent/s (C5) ---- *)
vmxy := 40.0;
IF pl_step = 3 THEN vmxy := 20.0; END_IF;

(* ---- draw sequence start (C1) ---- *)
IF cmd_draw AND (NOT edge_draw) AND en AND (pl_step = 0) THEN
    tx := 50; ty := 25; tz := 10;      (* pen up, locate circle start (50,25) *)
    pl_step := 1;
    plot_done := FALSE;
    manual_done := FALSE;
END_IF;
edge_draw := cmd_draw;

(* ---- manual positioning cmd_go, interlocked when pen down (C2) ---- *)
IF cmd_go AND (NOT edge_go) AND en AND (pl_step = 0) AND (NOT pen_down) THEN
    tx := x_sp; ty := y_sp;
    go_exe := TRUE;
    manual_done := FALSE;
END_IF;
edge_go := cmd_go;
IF go_exe AND (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done THEN
    go_exe := FALSE;
    manual_done := TRUE;
END_IF;

(* ---- jog X, interlocked when pen down (C2) ---- *)
IF jog_fwd AND en AND (pl_step = 0) AND (NOT go_exe) AND (NOT pen_down) THEN
    IF tx < 100 THEN tx := tx + 1; END_IF;
END_IF;
IF jog_rev AND en AND (pl_step = 0) AND (NOT go_exe) AND (NOT pen_down) THEN
    IF tx > 0 THEN tx := tx - 1; END_IF;
END_IF;

(* ---- manual Z setpoint / home (not pen-interlocked per spec) ---- *)
IF (pl_step = 0) AND en THEN
    tz := z_sp;
    IF cmd_home THEN tz := 10; END_IF;
END_IF;

(* ---- abort on quickstop / fault (C3/C4): level abort, no extra latch ---- *)
IF quickstop OR fault_now THEN
    pl_step := 0;
    go_exe := FALSE;
    manual_done := FALSE;
    plot_done := FALSE;
END_IF;

idle3 := (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done
         AND (NOT ix_z.Busy) AND ix_z.Done;
xy_idle := (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done;

(* ---- sequencer: ELSIF chain prevents same-scan double advance ---- *)
IF pl_step = 1 AND idle3 THEN
    tz := 0;                            (* pen down at circle start *)
    pl_step := 2;
ELSIF pl_step = 2 AND idle3 THEN
    seg := 1;
    ang := 0.2617994;                   (* 2*pi/24 *)
    tx := REAL_TO_INT(50.0 + 25.0 * SIN(ang));
    ty := REAL_TO_INT(50.0 - 25.0 * COS(ang));
    pl_step := 3;
ELSIF pl_step = 3 AND xy_idle THEN
    IF seg >= 24 THEN
        tz := 10;                       (* pen up at closed start point *)
        pl_step := 4;
    ELSE
        seg := seg + 1;
        ang := INT_TO_REAL(seg MOD 24) * 0.2617994;
        tx := REAL_TO_INT(50.0 + 25.0 * SIN(ang));
        ty := REAL_TO_INT(50.0 - 25.0 * COS(ang));
    END_IF;
ELSIF pl_step = 4 AND idle3 THEN
    tx := 50; ty := 50;                 (* return to circle center *)
    pl_step := 5;
ELSIF pl_step = 5 AND idle3 THEN
    plot_done := TRUE;
    pl_step := 0;
END_IF;

(* ---- interpolation engines (fire level held, continuous tracking) ---- *)
ix_x(fire := en, pos_target := tx, hold_req := quickstop, VMAX := vmxy, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := en, pos_target := ty, hold_req := quickstop, VMAX := vmxy, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := en, pos_target := tz, hold_req := quickstop, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0, MAXPOS := 10.0);

(* ---- MC layer ---- *)
ma_x(fire := en, interp_done := ix_x.Done);
ma_y(fire := en, interp_done := ix_y.Done);
ma_z(fire := en, interp_done := ix_z.Done);
pw_x(Enable := en, sw := swx);
pw_y(Enable := en, sw := swy);
pw_z(Enable := en, sw := swz);
cwx := pw_x.cw;
cwy := pw_y.cw;
cwz := pw_z.cw;

(* ---- drives ---- *)
drv_x(cw := cwx, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
drv_y(cw := cwy, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
drv_z(cw := cwz, Setpoint := ix_z.Setpoint, pos_fb := z_fb);
swx := drv_x.sw;
swy := drv_y.sw;
swz := drv_z.sw;
x_sw := WORD_TO_INT(swx);
y_sw := WORD_TO_INT(swy);
z_sw := WORD_TO_INT(swz);
x_v := REAL_TO_INT(drv_x.v_cmd);
y_v := REAL_TO_INT(drv_y.v_cmd);
z_v := REAL_TO_INT(drv_z.v_cmd);

(* ---- status outputs ---- *)
all_oe := pw_x.Status AND pw_y.Status AND pw_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := manual_done AND (pl_step = 0);

(* ---- fault reset (C4: reset allowed after quickstop released) ---- *)
IF cmd_reset AND (NOT quickstop) THEN
    manual_done := FALSE;
END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
