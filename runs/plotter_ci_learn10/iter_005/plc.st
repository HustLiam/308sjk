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
    IF (INT_TO_REAL(pos_target) > MAXPOS) OR (pos_target < 0) THEN
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
        ELSE vel := MAX(-VMAX, vel + ACCEL * SCAN_T);
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
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    man_active : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    edge_reset : BOOL;
    draw_trig : BOOL;
    go_trig : BOOL;
    rst_pulse : BOOL;
    flt : BOOL;
    stop_latch : BOOL;
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

(* ---- edge detection ---- *)
rst_pulse := cmd_reset AND NOT edge_reset;
edge_reset := cmd_reset;
draw_trig := cmd_draw AND NOT edge_draw;
edge_draw := cmd_draw;
go_trig := cmd_go AND NOT edge_go;
edge_go := cmd_go;

(* ---- physical pen state (C1/C2 basis, pure physical judgement) ---- *)
pen_down := (z_fb <= 2);

(* ---- fault detection: bit3 of each drive status word (C5) ---- *)
flt := ((x_sw MOD 16) >= 8) OR ((y_sw MOD 16) >= 8) OR ((z_sw MOD 16) >= 8);
fault_any := flt;

(* ---- stop latch: quickstop / fault, cleared by reset (C4/C5) ---- *)
IF quickstop OR flt THEN
    stop_latch := TRUE;
END_IF;
IF rst_pulse AND NOT quickstop AND NOT flt THEN
    stop_latch := FALSE;
END_IF;

(* ---- abort sequence on quickstop / fault / run-off (C3/C4/C5) ---- *)
IF (quickstop OR flt OR NOT run) AND (pl_step <> 0) THEN
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

(* ---- start draw sequence (C6): only from safe state ---- *)
IF (pl_step = 0) AND draw_trig AND run AND NOT quickstop AND NOT flt
   AND NOT stop_latch AND NOT pen_down THEN
    plot_done := FALSE;
    man_active := FALSE;
    pl_step := 1;
END_IF;

(* ---- manual positioning (C2): rejected while pen down ---- *)
IF (pl_step = 0) AND go_trig AND run AND NOT quickstop AND NOT flt
   AND NOT stop_latch AND NOT pen_down THEN
    man_active := TRUE;
    move_done := FALSE;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
END_IF;
IF man_active AND NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done THEN
    move_done := TRUE;
END_IF;
IF pl_step <> 0 THEN
    move_done := FALSE;   (* C8: move_done off during drawing *)
END_IF;

(* ---- manual jog (C1): interlocked while pen down ---- *)
IF run AND NOT quickstop AND NOT flt AND NOT pen_down
   AND (pl_step = 0) AND NOT man_active THEN
    IF jog_fwd AND (x_sp < 100) THEN
        x_sp := x_sp + 1;
        go_x_exe := TRUE;
        move_done := FALSE;
    END_IF;
    IF jog_rev AND (x_sp > 0) THEN
        x_sp := x_sp - 1;
        go_x_exe := TRUE;
        move_done := FALSE;
    END_IF;
END_IF;

(* ---- draw sequence step table (C6/C7): 24-segment circle, center (50,50) r=25 ---- *)
CASE pl_step OF
    0: pl_step := pl_step;                     (* idle *)
    1: z_sp := 10;  go_z_exe := TRUE;          (* pen up *)
    2: x_sp := 50;  y_sp := 25;  go_x_exe := TRUE;  go_y_exe := TRUE;  (* circle start *)
    3: z_sp := 0;                              (* pen down, XY already in position (C7) *)
    4: x_sp := 57;  y_sp := 26;                (* k=01 *)
    5: x_sp := 63;  y_sp := 28;                (* k=02 *)
    6: x_sp := 68;  y_sp := 32;                (* k=03 *)
    7: x_sp := 72;  y_sp := 38;                (* k=04 *)
    8: x_sp := 74;  y_sp := 44;                (* k=05 *)
    9: x_sp := 75;  y_sp := 50;                (* k=06 *)
    10: x_sp := 74; y_sp := 57;                (* k=07 *)
    11: x_sp := 72; y_sp := 63;                (* k=08 *)
    12: x_sp := 68; y_sp := 68;                (* k=09 *)
    13: x_sp := 63; y_sp := 72;                (* k=10 *)
    14: x_sp := 57; y_sp := 74;                (* k=11 *)
    15: x_sp := 50; y_sp := 75;                (* k=12 *)
    16: x_sp := 43; y_sp := 74;                (* k=13 *)
    17: x_sp := 38; y_sp := 72;                (* k=14 *)
    18: x_sp := 32; y_sp := 68;                (* k=15 *)
    19: x_sp := 28; y_sp := 63;                (* k=16 *)
    20: x_sp := 26; y_sp := 57;                (* k=17 *)
    21: x_sp := 25; y_sp := 50;                (* k=18 *)
    22: x_sp := 26; y_sp := 44;                (* k=19 *)
    23: x_sp := 28; y_sp := 38;                (* k=20 *)
    24: x_sp := 32; y_sp := 32;                (* k=21 *)
    25: x_sp := 38; y_sp := 28;                (* k=22 *)
    26: x_sp := 44; y_sp := 26;                (* k=23 *)
    27: x_sp := 50; y_sp := 25;                (* closure back to start *)
    28: z_sp := 10;                            (* pen up *)
    29: x_sp := 50; y_sp := 50;                (* return to circle center *)
ELSE
    pl_step := 0;
END_CASE;

(* ---- sequence advance: all axes at target and idle ---- *)
IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
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

(* ---- interpolation engines (XY: VMAX 40, Z: VMAX 20) ---- *)
ix_x(fire := go_x_exe, pos_target := x_sp, hold_req := FALSE, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := go_y_exe, pos_target := y_sp, hold_req := FALSE, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := go_z_exe, pos_target := z_sp, hold_req := FALSE, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0, MAXPOS := 10.0);

(* ---- drive power handshake (CiA402: shutdown while disabled) ---- *)
mcp_x(Enable := run AND NOT quickstop AND NOT flt, sw := swx);
mcp_y(Enable := run AND NOT quickstop AND NOT flt, sw := swy);
mcp_z(Enable := run AND NOT quickstop AND NOT flt, sw := swz);

cw_x := mcp_x.cw;
cw_y := mcp_y.cw;
cw_z := mcp_z.cw;
IF quickstop OR flt THEN
    cw_x := (cw_x AND 16#00F0) OR 16#0002;   (* quickstop / fault stop *)
    cw_y := (cw_y AND 16#00F0) OR 16#0002;
    cw_z := (cw_z AND 16#00F0) OR 16#0002;
END_IF;
IF rst_pulse THEN
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

(* ---- velocity commands (C3: zero when run off) ---- *)
IF run THEN
    x_v := REAL_TO_INT(drv_x.v_cmd);
    y_v := REAL_TO_INT(drv_y.v_cmd);
    z_v := REAL_TO_INT(drv_z.v_cmd);
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
