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
    Done : BOOL := TRUE;
  END_VAR
  VAR
    SCAN_T : REAL := 0.02;
    pos : REAL;
    tgt : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
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
(* DRIVE402: CSP following + position loop; state 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA *)
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
    state := state;
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

(* status word: bit0 rdy-on, bit1 enabled, bit2 oe, bit3 fault, bit10 target-reached *)
sw := 16#0000;
IF state >= 2 THEN sw := sw OR 16#0001; END_IF;
IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
IF state >= 4 THEN sw := sw OR 16#0004; END_IF;
IF state = 7 THEN sw := sw OR 16#0008; END_IF;
IF v_out = 0.0 AND pos_err = 0.0 THEN sw := sw OR 16#0400; END_IF;

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
    pstep : INT;
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
        pstep := pstep;
    END_CASE;
ELSE
    cw := (cw AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
Status := (sw AND 16#0004) <> 0;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    pw_x : MC_POWER;
    pw_y : MC_POWER;
    pw_z : MC_POWER;
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    tg_x : INT := 50;
    tg_y : INT := 50;
    tg_z : INT := 10;
    pl_step : INT;
    k : INT;
    ang : REAL;
    edge_draw : BOOL;
    edge_go : BOOL;
    edge_reset : BOOL;
    go_exe : BOOL;
    mv_dn : BOOL;
    pen_block : BOOL;
    hold_xy : BOOL;
    hold_z : BOOL;
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

(* ---- pen state: pure physical judgement (C1) ---- *)
pen_down := (z_fb <= 2);
pen_block := pen_down OR (z_fb < 5);

(* ---- fault from drive status words (C4) ---- *)
fault_any := ((drv_x.sw AND 16#0008) <> 0) OR ((drv_y.sw AND 16#0008) <> 0) OR ((drv_z.sw AND 16#0008) <> 0);

(* ---- cmd_draw accept: C5, rising edge, needs all_oe, no fault, pen up ---- *)
IF (cmd_draw AND NOT edge_draw) AND all_oe AND (NOT fault_any) AND (z_fb >= 8) AND (pl_step = 0) AND (NOT quickstop) THEN
    pl_step := 1;
END_IF;
edge_draw := cmd_draw;

(* ---- abort on quickstop / fault (C3/C4): level abort, no extra latch ---- *)
IF quickstop OR fault_any THEN
    pl_step := 0;
    plot_done := FALSE;
    go_exe := FALSE;
END_IF;

(* quickstop: Z priority lift to safe position *)
IF quickstop THEN
    tg_z := 10;
END_IF;

(* ---- manual modes, only in idle & pen up (C1) ---- *)
IF (pl_step = 0) AND (NOT quickstop) THEN
    IF all_oe AND (NOT pen_block) THEN
        IF jog_fwd AND (tg_x < 100) THEN tg_x := tg_x + 1; END_IF;
        IF jog_rev AND (tg_x > 0) THEN tg_x := tg_x - 1; END_IF;
        IF (cmd_go AND NOT edge_go) AND (NOT go_exe) THEN
            tg_x := x_sp;
            tg_y := y_sp;
            go_exe := TRUE;
            mv_dn := FALSE;
        END_IF;
    END_IF;
    (* manual Z setpoint, not pen-interlocked; cmd_home = Z reference (up) *)
    IF cmd_home THEN
        tg_z := 10;
    ELSE
        tg_z := z_sp;
    END_IF;
END_IF;
edge_go := cmd_go;
edge_reset := cmd_reset;

(* ---- drawing sequence step table (C2/C6) ---- *)
CASE pl_step OF
    1:  tg_x := 50; tg_y := 25; tg_z := 10;   (* pen-up locate circle start *)
    2:  tg_z := 0;                              (* pen down at start *)
    3..26:                                       (* 24-segment circle, ccw, z=0 constant *)
        k := (pl_step - 3) MOD 24;
        ang := INT_TO_REAL(k) * 0.2617994;
        tg_x := REAL_TO_INT(50.0 + 25.0 * SIN(ang) + 0.5);
        tg_y := REAL_TO_INT(50.0 - 25.0 * COS(ang) + 0.5);
    27: tg_z := 10;                             (* pen up after closure *)
    28: tg_x := 50; tg_y := 50; tg_z := 10;     (* return to center, safe Z *)
ELSE
    pl_step := pl_step;
END_CASE;

(* ---- hold requests (C3/C4/C7) ---- *)
hold_xy := quickstop OR fault_any OR (NOT all_oe);
hold_z := fault_any OR (NOT all_oe);

(* ---- FB calls, once per scan ---- *)
pw_x(Enable := run, sw := drv_x.sw);
pw_y(Enable := run, sw := drv_y.sw);
pw_z(Enable := run, sw := drv_z.sw);

ix_x(fire := all_oe, pos_target := tg_x, hold_req := hold_xy, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := all_oe, pos_target := tg_y, hold_req := hold_xy, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := all_oe, pos_target := tg_z, hold_req := hold_z, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0, MAXPOS := 10.0);

drv_x(cw := pw_x.cw, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
drv_y(cw := pw_y.cw, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
drv_z(cw := pw_z.cw, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

(* ---- step advance: all axes positioned & idle (C6) ---- *)
IF (pl_step <> 0) AND ix_x.Done AND (NOT ix_x.Busy) AND ix_y.Done AND (NOT ix_y.Busy) AND ix_z.Done AND (NOT ix_z.Busy) THEN
    IF pl_step >= 28 THEN
        plot_done := TRUE;
        pl_step := 0;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- manual go completion ---- *)
IF go_exe AND ix_x.Done AND (NOT ix_x.Busy) AND ix_y.Done AND (NOT ix_y.Busy) THEN
    go_exe := FALSE;
    mv_dn := TRUE;
END_IF;

(* ---- outputs ---- *)
all_oe := pw_x.Status AND pw_y.Status AND pw_z.Status;
x_sw := WORD_TO_INT(drv_x.sw);
y_sw := WORD_TO_INT(drv_y.sw);
z_sw := WORD_TO_INT(drv_z.sw);
x_v := REAL_TO_INT(drv_x.v_cmd);
y_v := REAL_TO_INT(drv_y.v_cmd);
z_v := REAL_TO_INT(drv_z.v_cmd);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := mv_dn AND (pl_step = 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
