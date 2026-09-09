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
    dist : REAL;
    stop_d : REAL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF INT_TO_REAL(pos_target) > MAXPOS OR pos_target < 0 THEN
        (* target out of range: no interp, hold *)
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
    v_ff : REAL;
    pos_err : REAL;
    v_out : REAL;
    sp_prev : INT;
    SCAN_T : REAL := 0.02;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
  END_VAR
(* DRIVE402: CSP following + position loop *)
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

(* ---- position loop: P control + setpoint feedforward ---- *)
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
IF state >= 2 THEN sw := sw OR 16#0010; END_IF;
IF state >= 2 THEN sw := sw OR 16#0001; END_IF;
IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
IF state >= 4 THEN sw := sw OR 16#0004; END_IF;
IF state = 5 THEN sw := sw OR 16#0020; END_IF;
IF (state = 4) AND (v_out = 0.0) THEN sw := sw OR 16#0400; END_IF;

v_cmd := REAL_TO_INT(v_out);
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
    mcpx : MC_POWER;
    mcpy : MC_POWER;
    mcpz : MC_POWER;
    ixx : INTERP;
    ixy : INTERP;
    ixz : INTERP;
    drvx : DRIVE402;
    drvy : DRIVE402;
    drvz : DRIVE402;
    swx : WORD;
    swy : WORD;
    swz : WORD;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    tgt_x : INT;
    tgt_y : INT;
    tgt_z : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_step : INT := 0;
    seg : INT := 0;
    edge_home : BOOL;
    z_prev : INT;
    homed : BOOL;
    fault_latch : BOOL;
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

(* ---- C5: fault latch / reset ---- *)
IF ((swx AND 16#0008) <> 0) OR ((swy AND 16#0008) <> 0) OR ((swz AND 16#0008) <> 0) THEN
    fault_latch := TRUE;
END_IF;
IF fault_latch AND cmd_reset AND NOT quickstop THEN
    fault_latch := FALSE;
END_IF;
fault_any := fault_latch;

(* ---- C6: homing support ---- *)
IF cmd_home AND NOT edge_home THEN
    homed := TRUE;
END_IF;
edge_home := cmd_home;
IF NOT run THEN
    homed := FALSE;
END_IF;

(* ---- C4/C5: abort sequence on quickstop or fault ---- *)
IF quickstop OR fault_latch THEN
    pl_step := 0;
    plot_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
END_IF;

(* ---- manual jog / manual go (C1: blocked while pen down) ---- *)
IF pl_step = 0 AND NOT (z_fb <= 2) THEN
    IF jog_fwd THEN
        tgt_x := x_fb + 5;
        go_x_exe := TRUE;
    END_IF;
    IF jog_rev THEN
        tgt_x := x_fb - 5;
        go_x_exe := TRUE;
    END_IF;
    IF cmd_go THEN
        tgt_x := x_sp;
        tgt_y := y_sp;
        go_x_exe := TRUE;
        go_y_exe := TRUE;
    END_IF;
END_IF;

(* ---- manual z (not interlocked) ---- *)
IF pl_step = 0 AND (z_sp <> z_prev) THEN
    tgt_z := z_sp;
    go_z_exe := TRUE;
    z_prev := z_sp;
END_IF;

(* ---- draw sequence trigger: level-held ---- *)
IF (pl_step = 0) AND cmd_draw AND run
   AND NOT quickstop AND NOT fault_latch
   AND mcpx.Status AND mcpy.Status AND mcpz.Status THEN
    plot_done := FALSE;
    pl_step := 1;
    seg := 0;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
END_IF;

(* ---- sequence state machine (continuous tracking: each step only changes targets;
        advance = NOT Busy AND Done AND physical position window) ---- *)
CASE pl_step OF
    0: pl_step := pl_step;  (* idle *)
    1: (* pen up, move to circle start (50,25) *)
       tgt_z := 10;
       tgt_x := 50;
       tgt_y := 25;
       IF (NOT ixz.Busy AND ixz.Done AND (ABS(z_fb - 10) <= 1))
          AND (NOT ixx.Busy AND ixx.Done AND (ABS(x_fb - 50) <= 2))
          AND (NOT ixy.Busy AND ixy.Done AND (ABS(y_fb - 25) <= 2)) THEN
           pl_step := 2;
       END_IF;
    2: (* pen down at (50,25) *)
       tgt_z := 0;
       tgt_x := 50;
       tgt_y := 25;
       IF (z_fb <= 2) AND (NOT ixz.Busy AND ixz.Done AND (ABS(z_fb - 0) <= 2)) THEN
           pl_step := 3;
           seg := 1;
       END_IF;
    3: (* 24-segment polygonal circle sweep, waypoints per table *)
       CASE seg OF
           1: pl_tx := 57; pl_ty := 26;
           2: pl_tx := 63; pl_ty := 28;
           3: pl_tx := 68; pl_ty := 32;
           4: pl_tx := 72; pl_ty := 38;
           5: pl_tx := 74; pl_ty := 44;
           6: pl_tx := 75; pl_ty := 50;
           7: pl_tx := 74; pl_ty := 57;
           8: pl_tx := 72; pl_ty := 63;
           9: pl_tx := 68; pl_ty := 68;
           10: pl_tx := 63; pl_ty := 72;
           11: pl_tx := 57; pl_ty := 74;
           12: pl_tx := 50; pl_ty := 75;
           13: pl_tx := 44; pl_ty := 74;
           14: pl_tx := 38; pl_ty := 72;
           15: pl_tx := 32; pl_ty := 68;
           16: pl_tx := 28; pl_ty := 63;
           17: pl_tx := 26; pl_ty := 57;
           18: pl_tx := 25; pl_ty := 50;
           19: pl_tx := 26; pl_ty := 44;
           20: pl_tx := 28; pl_ty := 38;
           21: pl_tx := 32; pl_ty := 32;
           22: pl_tx := 38; pl_ty := 28;
           23: pl_tx := 44; pl_ty := 26;
           24: pl_tx := 50; pl_ty := 25;
           ELSE pl_tx := 50; pl_ty := 25;
       END_CASE;
       tgt_x := pl_tx;
       tgt_y := pl_ty;
       tgt_z := 0;
       IF (NOT ixx.Busy AND ixx.Done AND (ABS(x_fb - pl_tx) <= 2))
          AND (NOT ixy.Busy AND ixy.Done AND (ABS(y_fb - pl_ty) <= 2)) THEN
           IF seg >= 24 THEN
               pl_step := 4;
           ELSE
               seg := seg + 1;
           END_IF;
       END_IF;
    4: (* pen up *)
       tgt_z := 10;
       IF (NOT ixz.Busy AND ixz.Done AND (ABS(z_fb - 10) <= 1)) THEN
           pl_step := 5;
       END_IF;
    5: (* return to circle center *)
       tgt_x := 50;
       tgt_y := 50;
       tgt_z := 10;
       IF (NOT ixz.Busy AND ixz.Done AND (ABS(z_fb - 10) <= 1))
          AND (NOT ixx.Busy AND ixx.Done AND (ABS(x_fb - 50) <= 2))
          AND (NOT ixy.Busy AND ixy.Done AND (ABS(y_fb - 50) <= 2)) THEN
           plot_done := TRUE;
           pl_step := 0;
           go_x_exe := FALSE;
           go_y_exe := FALSE;
           go_z_exe := FALSE;
       END_IF;
    ELSE pl_step := 0;
END_CASE;

(* ---- fire lines (level-held, dropped on quickstop/fault) ---- *)
fire_x := go_x_exe AND NOT quickstop AND NOT fault_latch;
fire_y := go_y_exe AND NOT quickstop AND NOT fault_latch;
fire_z := go_z_exe AND NOT quickstop AND NOT fault_latch;

(* ---- FB calls ---- *)
mcpx(Enable := run AND NOT fault_latch, sw := swx);
mcpy(Enable := run AND NOT fault_latch, sw := swy);
mcpz(Enable := run AND NOT fault_latch, sw := swz);

ixx(fire := fire_x, pos_target := tgt_x, hold_req := quickstop OR fault_latch,
    VMAX := 38.0, ACCEL := 120.0, POSWIN := 1.0, MAXPOS := 100.0);
ixy(fire := fire_y, pos_target := tgt_y, hold_req := quickstop OR fault_latch,
    VMAX := 38.0, ACCEL := 120.0, POSWIN := 1.0, MAXPOS := 100.0);
ixz(fire := fire_z, pos_target := tgt_z, hold_req := quickstop OR fault_latch,
    VMAX := 20.0, ACCEL := 80.0, POSWIN := 1.0, MAXPOS := 10.0);

drvx(cw := mcpx.cw, Setpoint := ixx.Setpoint, pos_fb := x_fb);
drvy(cw := mcpy.cw, Setpoint := ixy.Setpoint, pos_fb := y_fb);
drvz(cw := mcpz.cw, Setpoint := ixz.Setpoint, pos_fb := z_fb);
swx := drvx.sw;
swy := drvy.sw;
swz := drvz.sw;

x_sw := WORD_TO_INT(swx);
y_sw := WORD_TO_INT(swy);
z_sw := WORD_TO_INT(swz);

(* ---- C6/C4/C5: command zeroing when not running / quickstop / fault ---- *)
IF quickstop OR fault_latch OR (NOT run) THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    x_v := drvx.v_cmd;
    y_v := drvy.v_cmd;
    z_v := drvz.v_cmd;
END_IF;

(* ---- outputs ---- *)
all_oe := mcpx.Status AND mcpy.Status AND mcpz.Status;
move_done := (pl_step = 0) AND ixx.Done AND ixy.Done AND ixz.Done AND NOT quickstop;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
pen_down := z_fb <= 2;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
