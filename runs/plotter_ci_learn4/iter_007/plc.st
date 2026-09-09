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
    SCAN_T : REAL := 0.02;
    pos : REAL;
    tgt : REAL;
    vel : REAL;
    edge_ir : BOOL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: hold position *)
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

PROGRAM PLC_PRG
  VAR
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    ton_run : TON;
    e_draw : BOOL;
    e_home : BOOL;
    e_go : BOOL;
    up_draw : BOOL;
    up_home : BOOL;
    up_go : BOOL;
    x_sww : WORD;
    y_sww : WORD;
    z_sww : WORD;
    fault_src : BOOL;
    abort_latch : BOOL;
    pl_step : INT := 0;
    h_step : INT := 0;
    tx : INT := 50;
    ty : INT := 50;
    tz : INT := 10;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    manual_active : BOOL;
    jog_ok : BOOL;
    z_ready : BOOL;
    xy_ready : BOOL;
    k : INT;
    ang : REAL;
    vx_r : REAL;
    vy_r : REAL;
    vz_r : REAL;
    prev_xsp : INT;
    prev_ysp : INT;
    prev_zsp : INT;
    drive_en : BOOL;
    xs : INT := 1;
    ys : INT := 1;
    zs402 : INT := 1;
    xsw_tmp : WORD;
    ysw_tmp : WORD;
    zsw_tmp : WORD;
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

(* ---------- edge detection ---------- *)
up_draw := cmd_draw AND NOT e_draw;
up_home := cmd_home AND NOT e_home;
up_go := cmd_go AND NOT e_go;
e_draw := cmd_draw;
e_home := cmd_home;
e_go := cmd_go;

(* ---------- enable stage ---------- *)
ton_run(IN := run AND (NOT quickstop), PT := T#1S);
drive_en := ton_run.Q AND run AND (NOT quickstop);

(* ---------- CiA402-like drive state machines (1=SOD, 2=RTSO, 3=OE) ---------- *)
(* idle: continuous shutdown (cw=0x06) keeps drive in RTSO (sw bit0=1) *)
IF quickstop THEN
    drive_en := FALSE;
END_IF;
CASE xs OF
    1: IF drive_en THEN xs := 3; ELSE xs := 2; END_IF;
    2: IF drive_en THEN xs := 3; END_IF;
    3: IF NOT drive_en THEN xs := 2; END_IF;
ELSE
    xs := 1;
END_CASE;
CASE ys OF
    1: IF drive_en THEN ys := 3; ELSE ys := 2; END_IF;
    2: IF drive_en THEN ys := 3; END_IF;
    3: IF NOT drive_en THEN ys := 2; END_IF;
ELSE
    ys := 1;
END_CASE;
CASE zs402 OF
    1: IF drive_en THEN zs402 := 3; ELSE zs402 := 2; END_IF;
    2: IF drive_en THEN zs402 := 3; END_IF;
    3: IF NOT drive_en THEN zs402 := 2; END_IF;
ELSE
    zs402 := 1;
END_CASE;

all_oe := (xs = 3) AND (ys = 3) AND (zs402 = 3);

(* ---------- status words: RTSO=0x31 / OE=0x37, bit10=target reached ---------- *)
IF xs = 3 THEN xsw_tmp := 16#0037; ELSE xsw_tmp := 16#0031; END_IF;
IF (NOT ix_x.Busy) AND ix_x.Done THEN xsw_tmp := xsw_tmp OR 16#0400; END_IF;
x_sw := WORD_TO_INT(xsw_tmp);
IF ys = 3 THEN ysw_tmp := 16#0037; ELSE ysw_tmp := 16#0031; END_IF;
IF (NOT ix_y.Busy) AND ix_y.Done THEN ysw_tmp := ysw_tmp OR 16#0400; END_IF;
y_sw := WORD_TO_INT(ysw_tmp);
IF zs402 = 3 THEN zsw_tmp := 16#0037; ELSE zsw_tmp := 16#0031; END_IF;
IF (NOT ix_z.Busy) AND ix_z.Done THEN zsw_tmp := zsw_tmp OR 16#0400; END_IF;
z_sw := WORD_TO_INT(zsw_tmp);

(* ---------- drive diagnostics: bit3 = fault (C5) ---------- *)
x_sww := INT_TO_WORD(x_sw);
y_sww := INT_TO_WORD(y_sw);
z_sww := INT_TO_WORD(z_sw);
fault_src := ((x_sww AND 16#0008) <> 16#0000) OR ((y_sww AND 16#0008) <> 16#0000) OR ((z_sww AND 16#0008) <> 16#0000);
fault_any := fault_src;

(* ---------- pen state: pure physical judgement ---------- *)
pen_down := (z_fb <= 2);

z_ready := (NOT ix_z.Busy) AND ix_z.Done;
xy_ready := (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND ix_x.Done AND ix_y.Done;

(* manual X/Y allowed only pen up, idle, enabled (C3/C4) *)
jog_ok := all_oe AND (NOT pen_down) AND (pl_step = 0) AND (h_step = 0) AND (NOT manual_active) AND (NOT quickstop) AND (NOT fault_src);

(* ---------- abort handling (C5): estop / fault kills sequences ---------- *)
IF quickstop OR fault_src THEN
    abort_latch := TRUE;
    pl_step := 0;
    h_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    manual_active := FALSE;
    plot_done := FALSE;
END_IF;
IF (NOT quickstop) AND (NOT fault_src) AND cmd_reset THEN
    abort_latch := FALSE;
END_IF;

(* ---------- homing sequencer: Z up first (safety), then X/Y to (50,50) ---------- *)
CASE h_step OF
    0:
        IF up_home AND all_oe AND (pl_step = 0) AND (NOT manual_active) AND (NOT abort_latch) AND (NOT quickstop) THEN
            tz := 10;
            go_z_exe := TRUE;
            move_done := FALSE;
            h_step := 1;
        END_IF;
    1:
        tz := 10;
        IF z_ready THEN
            tx := 50;
            ty := 50;
            go_x_exe := TRUE;
            go_y_exe := TRUE;
            h_step := 2;
        END_IF;
    2:
        tx := 50;
        ty := 50;
        tz := 10;
        IF xy_ready THEN
            h_step := 0;
            go_x_exe := FALSE;
            go_y_exe := FALSE;
            go_z_exe := FALSE;
            move_done := TRUE;
        END_IF;
ELSE
    h_step := 0;
END_CASE;

(* ---------- circle waypoint targets for steps 4..27 (C2, k=1..24) ---------- *)
IF (pl_step >= 4) AND (pl_step <= 27) THEN
    k := pl_step - 3;
    ang := INT_TO_REAL(k) * 0.2617994;
    tx := REAL_TO_INT(50.0 + 25.0 * SIN(ang) + 0.5);
    ty := REAL_TO_INT(50.0 - 25.0 * COS(ang) + 0.5);
    tz := 0;
END_IF;

(* ---------- draw sequencer (C1/C2/C6); pen-lift handled as step 1 ---------- *)
CASE pl_step OF
    0:
        IF up_draw AND all_oe AND (h_step = 0) AND (NOT manual_active) AND (NOT abort_latch) AND (NOT quickstop) THEN
            tx := x_fb;
            ty := y_fb;
            tz := 10;
            go_x_exe := TRUE;
            go_y_exe := TRUE;
            go_z_exe := TRUE;
            plot_done := FALSE;
            move_done := FALSE;
            pl_step := 1;
        END_IF;
    1:
        tx := x_fb;
        ty := y_fb;
        tz := 10;
    2:
        tx := 50;
        ty := 25;
        tz := 10;
    3:
        tx := 50;
        ty := 25;
        tz := 0;
    28:
        tx := 50;
        ty := 25;
        tz := 10;
    29:
        tx := 50;
        ty := 50;
        tz := 10;
ELSE
    pl_step := pl_step;
END_CASE;

(* ---------- draw step advance ---------- *)
IF (pl_step = 1) AND z_ready AND (z_fb >= 10) THEN
    pl_step := 2;
END_IF;
IF (pl_step = 2) AND xy_ready AND z_ready AND (z_fb >= 10) THEN
    pl_step := 3;
END_IF;
IF (pl_step = 3) AND z_ready AND (z_fb <= 2) THEN
    pl_step := 4;
END_IF;
IF (pl_step >= 4) AND (pl_step <= 27) AND xy_ready THEN
    pl_step := pl_step + 1;
END_IF;
IF (pl_step = 28) AND z_ready AND (z_fb >= 10) THEN
    pl_step := 29;
END_IF;
IF (pl_step = 29) AND xy_ready AND z_ready AND (z_fb >= 10) THEN
    plot_done := TRUE;
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
END_IF;

(* ---------- manual positioning via NC setpoints (C3/C4: pen up only) ---------- *)
IF up_go AND jog_ok THEN
    tx := x_sp;
    ty := y_sp;
    tz := z_sp;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
    manual_active := TRUE;
    move_done := FALSE;
END_IF;
IF manual_active AND xy_ready AND z_ready AND (pl_step = 0) AND (h_step = 0) THEN
    manual_active := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    move_done := TRUE;
END_IF;

(* ---------- interpolator calls (once per scan) ---------- *)
ix_x(fire := go_x_exe, pos_target := tx, hold_req := quickstop OR abort_latch, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := go_y_exe, pos_target := ty, hold_req := quickstop OR abort_latch, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := go_z_exe, pos_target := tz, hold_req := quickstop OR abort_latch, VMAX := 20.0, ACCEL := 80.0, POSWIN := 1.0, MAXPOS := 10);

(* ---------- velocity commands from setpoint deltas ---------- *)
IF quickstop OR fault_src OR (xs <> 3) OR (ys <> 3) OR (zs402 <> 3) THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    vx_r := INT_TO_REAL(ix_x.Setpoint - prev_xsp) / 0.02;
    vy_r := INT_TO_REAL(ix_y.Setpoint - prev_ysp) / 0.02;
    vz_r := INT_TO_REAL(ix_z.Setpoint - prev_zsp) / 0.02;
    IF vx_r > 120.0 THEN
        vx_r := 120.0;
    END_IF;
    IF vx_r < -120.0 THEN
        vx_r := -120.0;
    END_IF;
    IF vy_r > 120.0 THEN
        vy_r := 120.0;
    END_IF;
    IF vy_r < -120.0 THEN
        vy_r := -120.0;
    END_IF;
    IF vz_r > 120.0 THEN
        vz_r := 120.0;
    END_IF;
    IF vz_r < -120.0 THEN
        vz_r := -120.0;
    END_IF;
    x_v := REAL_TO_INT(vx_r);
    y_v := REAL_TO_INT(vy_r);
    z_v := REAL_TO_INT(vz_r);
    IF jog_ok THEN
        IF jog_fwd THEN
            x_v := 30;
        ELSIF jog_rev THEN
            x_v := -30;
        END_IF;
    END_IF;
END_IF;
prev_xsp := ix_x.Setpoint;
prev_ysp := ix_y.Setpoint;
prev_zsp := ix_z.Setpoint;

any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
