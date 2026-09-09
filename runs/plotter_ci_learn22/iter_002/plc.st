FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    hold_req : BOOL;
    pos_target : INT;
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
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    stop_d : REAL := 0.0;
    dist : REAL := 0.0;
    SCAN_T : REAL := 0.02;
    edge_ir : BOOL;
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
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
    sp_prev : INT := 0;
  END_VAR
(* ============ DRIVE402: axis drive (CSP follow + position loop) ============ *)
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

(* ---- position loop: P control + setpoint-difference feedforward ---- *)
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

(* ---- status word ---- *)
sw := 16#0000;
IF state >= 2 THEN sw := sw OR 16#0001; END_IF;
IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
IF (state >= 4) AND (state <= 6) THEN sw := sw OR 16#0004; END_IF;
IF state = 7 THEN sw := sw OR 16#0008; END_IF;
sw := sw OR 16#0010;
IF state <> 5 THEN sw := sw OR 16#0020; END_IF;
IF (v_out = 0.0) AND (state >= 3) AND (state <= 4) THEN sw := sw OR 16#0400; END_IF;

v_cmd := REAL_TO_INT(v_out);
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
    cw : WORD;
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
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    pw_x : MC_POWER;
    pw_y : MC_POWER;
    pw_z : MC_POWER;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    tx : INT;
    ty : INT;
    tz : INT;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    pl_step : INT := 0;
    seg : INT := 0;
    go_exe : BOOL;
    home_exe : BOOL;
    gtx : INT;
    gty : INT;
    gtz : INT;
    edge_draw : BOOL;
    edge_go : BOOL;
    edge_home : BOOL;
    draw_cmd : BOOL;
    go_cmd : BOOL;
    home_cmd : BOOL;
    flt_latch : BOOL;
    abort : BOOL;
    enable : BOOL;
    rad : REAL;
    xw : WORD;
    yw : WORD;
    zw : WORD;
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

(* ---- fault detection and reset latch (C5) ---- *)
xw := INT_TO_WORD(x_sw);
yw := INT_TO_WORD(y_sw);
zw := INT_TO_WORD(z_sw);
fault_any := ((xw AND 16#0008) <> 0) OR ((yw AND 16#0008) <> 0) OR ((zw AND 16#0008) <> 0);
IF fault_any THEN
    flt_latch := TRUE;
END_IF;
IF cmd_reset AND NOT fault_any AND NOT quickstop THEN
    flt_latch := FALSE;
END_IF;

(* ---- abort: quickstop / fault / run off (C5, C6) ---- *)
abort := quickstop OR flt_latch OR NOT run;
IF abort THEN
    pl_step := 0;
    seg := 0;
    plot_done := FALSE;
    go_exe := FALSE;
    home_exe := FALSE;
END_IF;

enable := run AND NOT quickstop AND NOT flt_latch;

(* ---- drive power handshake ---- *)
pw_x(Enable := enable, sw := drv_x.sw);
pw_y(Enable := enable, sw := drv_y.sw);
pw_z(Enable := enable, sw := drv_z.sw);
all_oe := pw_x.Status AND pw_y.Status AND pw_z.Status;

(* ---- command edges ---- *)
draw_cmd := cmd_draw AND NOT edge_draw;
go_cmd := cmd_go AND NOT edge_go;
home_cmd := cmd_home AND NOT edge_home;
edge_draw := cmd_draw;
edge_go := cmd_go;
edge_home := cmd_home;

(* ---- draw start gate: enabled, no fault, pen up (C7) ---- *)
IF (pl_step = 0) AND draw_cmd AND enable AND all_oe AND NOT flt_latch AND (z_fb >= 9) THEN
    pl_step := 1;
    seg := 0;
    plot_done := FALSE;
    move_done := FALSE;
END_IF;

(* ---- manual go: rejected when pen down (C1) ---- *)
IF go_cmd AND enable AND all_oe AND (pl_step = 0) AND (z_fb >= 9) AND NOT home_exe THEN
    go_exe := TRUE;
    gtx := x_sp;
    gty := y_sp;
    gtz := z_sp;
    move_done := FALSE;
END_IF;

(* ---- home: Z reference = pen up ---- *)
IF home_cmd AND enable AND (pl_step = 0) AND NOT go_exe THEN
    home_exe := TRUE;
    move_done := FALSE;
END_IF;

(* ---- mode selection and target generation ---- *)
fire_x := FALSE;
fire_y := FALSE;
fire_z := FALSE;
IF pl_step <> 0 THEN
    CASE pl_step OF
        1: tx := 50; ty := 25; tz := 10;
        2: tx := 50; ty := 25; tz := 0;
        3: rad := seg * 0.2617994;
           tx := REAL_TO_INT(50.0 + 25.0 * SIN(rad));
           ty := REAL_TO_INT(50.0 - 25.0 * COS(rad));
           tz := 0;
        4: tx := 50; ty := 25; tz := 10;
        5: tx := 50; ty := 50; tz := 10;
    ELSE
        pl_step := 0;
    END_CASE;
    IF pl_step <> 0 THEN
        fire_x := TRUE;
        fire_y := TRUE;
        fire_z := TRUE;
    END_IF;
ELSIF home_exe THEN
    tz := 10;
    fire_z := TRUE;
ELSIF go_exe THEN
    tx := gtx;
    ty := gty;
    tz := gtz;
    fire_x := TRUE;
    fire_y := TRUE;
    fire_z := TRUE;
ELSE
    (* jog: pen up only (C1), level gated continuous tracking *)
    IF enable AND (z_fb >= 9) AND jog_fwd AND NOT ix_x.Busy THEN
        tx := MIN(100, x_fb + 2);
        fire_x := TRUE;
        move_done := FALSE;
    END_IF;
    IF enable AND (z_fb >= 9) AND jog_rev AND NOT ix_x.Busy THEN
        tx := MAX(0, x_fb - 2);
        fire_x := TRUE;
        move_done := FALSE;
    END_IF;
END_IF;

(* ---- interpolators (C4: VMAX XY=40, Z=20) ---- *)
ix_x(fire := fire_x, hold_req := abort, pos_target := tx, VMAX := 40.0, ACCEL := 60.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := fire_y, hold_req := abort, pos_target := ty, VMAX := 40.0, ACCEL := 60.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := fire_z, hold_req := abort, pos_target := tz, VMAX := 20.0, ACCEL := 20.0, POSWIN := 1.0, MAXPOS := 10);

(* ---- drives ---- *)
drv_x(cw := pw_x.cw, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
drv_y(cw := pw_y.cw, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
drv_z(cw := pw_z.cw, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

x_sw := WORD_TO_INT(drv_x.sw);
y_sw := WORD_TO_INT(drv_y.sw);
z_sw := WORD_TO_INT(drv_z.sw);
x_v := drv_x.v_cmd;
y_v := drv_y.v_cmd;
z_v := drv_z.v_cmd;

(* ---- manual completions ---- *)
IF go_exe AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    go_exe := FALSE;
    move_done := TRUE;
END_IF;
IF home_exe AND NOT ix_z.Busy AND ix_z.Done THEN
    home_exe := FALSE;
    move_done := TRUE;
END_IF;

(* ---- sequencer stepping: all axes idle and done (C2, C3) ---- *)
IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    CASE pl_step OF
        1: pl_step := 2;
        2: pl_step := 3; seg := 1;
        3: IF seg < 24 THEN seg := seg + 1; ELSE pl_step := 4; END_IF;
        4: pl_step := 5;
        5: pl_step := 0; plot_done := TRUE;
    ELSE
        pl_step := 0;
    END_CASE;
END_IF;

(* ---- outputs ---- *)
pen_down := (z_fb <= 2);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
