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
    Done : BOOL := true;
  END_VAR
  VAR
    SCAN_T : REAL := 0.02;
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    dist : REAL := 0.0;
    stop_d : REAL := 0.0;
    edge_ir : BOOL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: do not start, hold position *)
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
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    sp_prev : INT := 0;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
  END_VAR
(* ============ DRIVE402: drive (CSP follow + position loop) ============ *)
(* state: 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA                    *)
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

(* ---- status word synthesis ---- *)
sw := 16#0000;
IF state >= 2 THEN sw := sw OR 16#0001; END_IF;
IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
IF state >= 4 THEN sw := sw OR 16#0004; END_IF;
IF state = 7 THEN sw := sw OR 16#0008; END_IF;
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
IF Enable AND pstep = 4 AND (sw AND 16#0004) = 0 AND (sw AND 16#0008) = 0 THEN
    pstep := 0;
END_IF;
IF Enable THEN
    CASE pstep OF
        0: cw := (cw AND 16#00F0) OR 16#0006;  pstep := 1;
        1: IF (sw AND 16#0001) <> 0 THEN cw := (cw AND 16#00F0) OR 16#0007; pstep := 2; END_IF;
        2: IF (sw AND 16#0002) <> 0 THEN cw := (cw AND 16#00F0) OR 16#000F; pstep := 3; END_IF;
        3: IF (sw AND 16#0004) <> 0 THEN pstep := 4; END_IF;
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
    mcp_x : MC_POWER;
    mcp_y : MC_POWER;
    mcp_z : MC_POWER;
    cw_x : WORD;
    cw_y : WORD;
    cw_z : WORD;
    swx : WORD;
    swy : WORD;
    swz : WORD;
    pl_step : INT := 0;
    pl_tx : INT := 0;
    pl_ty : INT := 0;
    pl_tz : INT := 0;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    man_active : BOOL;
    flt : BOOL;
    stop_latch : BOOL;
    axes_idle : BOOL;
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

(* ---- physical pen state (P21: pure physical judgement, no enable terms) ---- *)
pen_down := (z_fb <= 2);

(* ---- fault detection: bit3 of each drive status word (C5) ---- *)
flt := ((swx AND 16#0008) <> 0) OR ((swy AND 16#0008) <> 0) OR ((swz AND 16#0008) <> 0);
fault_any := flt;

(* ---- stop latch: set by quickstop/fault; cleared when clear + reset (C4/C5, level) ---- *)
IF quickstop OR flt THEN
    stop_latch := TRUE;
END_IF;
IF (NOT quickstop) AND (NOT flt) AND cmd_reset THEN
    stop_latch := FALSE;
END_IF;

(* ---- abort sequence on quickstop / fault / run-off (C3/C4/C5) ---- *)
IF (quickstop OR flt OR (NOT run)) AND (pl_step <> 0) THEN
    pl_step := 0;
    plot_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    man_active := FALSE;
END_IF;
IF NOT run THEN
    plot_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    man_active := FALSE;
END_IF;

(* ---- axes idle: level summary of interpolation state (recomputed every scan) ---- *)
axes_idle := (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
             AND ix_x.Done AND ix_y.Done AND ix_z.Done;

(* ---- start draw sequence (C6): level-gated; pen-lift handled as step 1 ---- *)
IF (pl_step = 0) AND cmd_draw AND run AND all_oe AND (NOT quickstop) AND (NOT flt)
   AND (NOT stop_latch) AND (NOT plot_done) AND axes_idle THEN
    plot_done := FALSE;
    man_active := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    pl_tx := x_fb;
    pl_ty := y_fb;
    pl_tz := 10;
    pl_step := 1;
END_IF;

(* ---- manual positioning (C2): rejected while pen down ---- *)
IF (pl_step = 0) AND cmd_go AND run AND (NOT quickstop) AND (NOT flt)
   AND (NOT stop_latch) AND (NOT pen_down) AND (NOT man_active) AND axes_idle THEN
    man_active := TRUE;
    pl_tx := x_sp;
    pl_ty := y_sp;
    move_done := FALSE;
END_IF;
IF man_active THEN
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    IF (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND ix_x.Done AND ix_y.Done
       AND (ABS(x_fb - pl_tx) <= 2) AND (ABS(y_fb - pl_ty) <= 2) THEN
        move_done := TRUE;
    END_IF;
END_IF;
IF (NOT cmd_go) OR (pl_step <> 0) OR (NOT run) OR quickstop OR flt THEN
    man_active := FALSE;
END_IF;
IF pl_step <> 0 THEN
    move_done := FALSE;   (* C8: move_done off during drawing *)
END_IF;

(* ---- manual jog (C1): interlocked while pen down, continuous tracking ---- *)
IF run AND (NOT quickstop) AND (NOT flt) AND (NOT pen_down)
   AND (pl_step = 0) AND (NOT man_active) AND (NOT cmd_draw) THEN
    IF jog_fwd AND (x_sp < 100) THEN
        x_sp := x_sp + 1;
        pl_tx := x_sp;
        go_x_exe := TRUE;
        move_done := FALSE;
    END_IF;
    IF jog_rev AND (x_sp > 0) THEN
        x_sp := x_sp - 1;
        pl_tx := x_sp;
        go_x_exe := TRUE;
        move_done := FALSE;
    END_IF;
END_IF;

(* ---- draw sequence step table (C6/C7): 24-segment circle, center (50,50) r=25 ---- *)
CASE pl_step OF
    0: pl_tx := x_sp; pl_ty := y_sp; pl_tz := z_sp;   (* idle: track NC/manual targets *)
    1: pl_tz := 10;  go_z_exe := TRUE;                (* pen up (first step of sequence) *)
    2: pl_tx := 50;  pl_ty := 25;  go_x_exe := TRUE;  go_y_exe := TRUE;  (* circle start *)
    3: pl_tz := 0;                                     (* pen down, XY already in position (C7) *)
    4: pl_tx := 57;  pl_ty := 26;                      (* k=01 *)
    5: pl_tx := 63;  pl_ty := 28;                      (* k=02 *)
    6: pl_tx := 68;  pl_ty := 32;                      (* k=03 *)
    7: pl_tx := 72;  pl_ty := 38;                      (* k=04 *)
    8: pl_tx := 74;  pl_ty := 44;                      (* k=05 *)
    9: pl_tx := 75;  pl_ty := 50;                      (* k=06 *)
    10: pl_tx := 74; pl_ty := 57;                      (* k=07 *)
    11: pl_tx := 72; pl_ty := 63;                      (* k=08 *)
    12: pl_tx := 68; pl_ty := 68;                      (* k=09 *)
    13: pl_tx := 63; pl_ty := 72;                      (* k=10 *)
    14: pl_tx := 57; pl_ty := 74;                      (* k=11 *)
    15: pl_tx := 50; pl_ty := 75;                      (* k=12 *)
    16: pl_tx := 43; pl_ty := 74;                      (* k=13 *)
    17: pl_tx := 38; pl_ty := 72;                      (* k=14 *)
    18: pl_tx := 32; pl_ty := 68;                      (* k=15 *)
    19: pl_tx := 28; pl_ty := 63;                      (* k=16 *)
    20: pl_tx := 26; pl_ty := 57;                      (* k=17 *)
    21: pl_tx := 25; pl_ty := 50;                      (* k=18 *)
    22: pl_tx := 26; pl_ty := 44;                      (* k=19 *)
    23: pl_tx := 28; pl_ty := 38;                      (* k=20 *)
    24: pl_tx := 32; pl_ty := 32;                      (* k=21 *)
    25: pl_tx := 38; pl_ty := 28;                      (* k=22 *)
    26: pl_tx := 44; pl_ty := 26;                      (* k=23 *)
    27: pl_tx := 50; pl_ty := 25;                      (* closure back to start *)
    28: pl_tz := 10;                                   (* pen up *)
    29: pl_tx := 50; pl_ty := 50;                      (* return to circle center *)
ELSE
    pl_step := 0;
END_CASE;

(* ---- sequence advance: all interp idle + Done, per-axis physical window only for commanded axes (C7) ---- *)
IF (pl_step <> 0) AND axes_idle
   AND ((NOT go_x_exe) OR (ABS(x_fb - pl_tx) <= 2))
   AND ((NOT go_y_exe) OR (ABS(y_fb - pl_ty) <= 2))
   AND ((NOT go_z_exe) OR (ABS(z_fb - pl_tz) <= 1)) THEN
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

(* ---- interpolation engines, internal sequence targets (XY: VMAX 40, Z: VMAX 20) ---- *)
ix_x(fire := go_x_exe, pos_target := pl_tx, hold_req := FALSE, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := go_y_exe, pos_target := pl_ty, hold_req := FALSE, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := go_z_exe, pos_target := pl_tz, hold_req := FALSE, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0, MAXPOS := 10.0);

(* ---- drive power handshake (CiA402: shutdown while disabled keeps RTSO) ---- *)
mcp_x(Enable := run AND (NOT quickstop) AND (NOT flt), sw := swx);
mcp_y(Enable := run AND (NOT quickstop) AND (NOT flt), sw := swy);
mcp_z(Enable := run AND (NOT quickstop) AND (NOT flt), sw := swz);

cw_x := mcp_x.cw;
cw_y := mcp_y.cw;
cw_z := mcp_z.cw;
IF quickstop OR flt THEN
    cw_x := (cw_x AND 16#00F0) OR 16#0002;   (* quickstop / fault stop *)
    cw_y := (cw_y AND 16#00F0) OR 16#0002;
    cw_z := (cw_z AND 16#00F0) OR 16#0002;
END_IF;
IF cmd_reset AND (NOT quickstop) AND (NOT flt) THEN
    cw_x := cw_x OR 16#0080;                 (* fault reset *)
    cw_y := cw_y OR 16#0080;
    cw_z := cw_z OR 16#0080;
END_IF;

(* ---- drives: CSP follow + position loop ---- *)
drv_x(cw := cw_x, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
drv_y(cw := cw_y, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
drv_z(cw := cw_z, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

swx := drv_x.sw;
swy := drv_y.sw;
swz := drv_z.sw;
x_sw := WORD_TO_INT(swx);
y_sw := WORD_TO_INT(swy);
z_sw := WORD_TO_INT(swz);

(* ---- velocity commands (C3: zero when run off; axis limits XY 40 / Z 20) ---- *)
IF run THEN
    x_v := REAL_TO_INT(MIN(40.0, MAX(-40.0, drv_x.v_cmd)));
    y_v := REAL_TO_INT(MIN(40.0, MAX(-40.0, drv_y.v_cmd)));
    z_v := REAL_TO_INT(MIN(20.0, MAX(-20.0, drv_z.v_cmd)));
ELSE
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

(* ---- status outputs ---- *)
all_oe := mcp_x.Status AND mcp_y.Status AND mcp_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
