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
    edge_ir : BOOL;
    dist : REAL;
    stop_d : REAL;
    SCAN_T : REAL := 0.02;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF (INT_TO_REAL(pos_target) > MAXPOS) OR (INT_TO_REAL(pos_target) < 0.0) THEN
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

(* position loop: P control + setpoint feed-forward *)
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

(* status word *)
CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0021;
    3: sw := 16#0023;
    4: sw := 16#0037;
    5: sw := 16#0027;
    6: sw := 16#0007;
    7: sw := 16#0008;
END_CASE;
IF state = 4 AND v_out = 0.0 AND ABS(pos_err) <= 1.5 THEN
    sw := sw OR 16#0400;
END_IF;
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
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    pw_x : MC_POWER;
    pw_y : MC_POWER;
    pw_z : MC_POWER;
    pl_step : INT := 0;
    pl_tx : INT := 0;
    pl_ty : INT := 0;
    pl_tz : INT := 10;
    edge_draw : BOOL;
    edge_go : BOOL;
    draw_trig : BOOL;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    hold : BOOL;
    cw_x : WORD;
    cw_y : WORD;
    cw_z : WORD;
    sw_x : WORD;
    sw_y : WORD;
    sw_z : WORD;
    fault_latch : BOOL;
    zvr : REAL;
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

(* ---- drive status / fault (C6) ---- *)
sw_x := drv_x.sw;  sw_y := drv_y.sw;  sw_z := drv_z.sw;
x_sw := WORD_TO_INT(sw_x);
y_sw := WORD_TO_INT(sw_y);
z_sw := WORD_TO_INT(sw_z);
fault_any := ((sw_x AND 16#0008) <> 0) OR ((sw_y AND 16#0008) <> 0) OR ((sw_z AND 16#0008) <> 0);
IF fault_any THEN fault_latch := TRUE; END_IF;
IF cmd_reset AND NOT fault_any THEN fault_latch := FALSE; END_IF;

(* ---- power stage (C3, quickstop override C2) ---- *)
pw_x(Enable := run AND NOT fault_latch, sw := sw_x);
pw_y(Enable := run AND NOT fault_latch, sw := sw_y);
pw_z(Enable := run AND NOT fault_latch, sw := sw_z);
IF quickstop THEN
    cw_x := (pw_x.cw AND 16#00F0) OR 16#0002;
    cw_y := (pw_y.cw AND 16#00F0) OR 16#0002;
    cw_z := (pw_z.cw AND 16#00F0) OR 16#0002;
ELSE
    cw_x := pw_x.cw;
    cw_y := pw_y.cw;
    cw_z := pw_z.cw;
END_IF;
all_oe := pw_x.Status AND pw_y.Status AND pw_z.Status;

(* ---- interpolation enable / hold ---- *)
fire_x := run AND NOT quickstop AND NOT fault_latch;
fire_y := fire_x;
fire_z := fire_x;
hold := quickstop OR fault_latch OR NOT run;

(* ---- manual channel (C1, C7): pen down rejects XY, range check ---- *)
(* gated by NOT plot_done so the safe pen-up end state is held after completion *)
IF pl_step = 0 AND NOT plot_done AND NOT fault_latch AND NOT quickstop THEN
    pl_tz := z_sp;
    IF cmd_home THEN pl_tz := 10; END_IF;
    IF cmd_go AND NOT edge_go THEN
        IF z_fb <= 2 THEN
            (* rejected: pen down *)
            pl_tx := pl_tx;
        ELSIF (x_sp >= 0) AND (x_sp <= 100) AND (y_sp >= 0) AND (y_sp <= 100) THEN
            pl_tx := x_sp;
            pl_ty := y_sp;
        END_IF;
    END_IF;
    IF z_fb > 2 THEN
        IF jog_fwd AND (pl_tx < 100) THEN pl_tx := pl_tx + 1; END_IF;
        IF jog_rev AND (pl_tx > 0) THEN pl_tx := pl_tx - 1; END_IF;
    END_IF;
END_IF;
edge_go := cmd_go;

(* ---- draw sequence trigger (C4): pen-lift handled as step 1 ---- *)
draw_trig := cmd_draw AND NOT edge_draw;
edge_draw := cmd_draw;
IF pl_step = 0 AND draw_trig AND all_oe AND NOT fault_latch AND NOT quickstop THEN
    plot_done := FALSE;
    pl_tx := x_fb;  pl_ty := y_fb;  pl_tz := 10;
    pl_step := 1;
END_IF;

(* ---- abort: quickstop / fault / master off (C2, C6) ---- *)
IF quickstop OR fault_latch OR NOT run THEN
    pl_step := 0;
    plot_done := FALSE;
END_IF;

(* ---- z target clamp ---- *)
IF pl_tz < 0 THEN pl_tz := 0; END_IF;
IF pl_tz > 10 THEN pl_tz := 10; END_IF;

(* ---- sequence step table: circle waypoints (authoritative) ---- *)
CASE pl_step OF
    0: pl_step := pl_step;                       (* idle *)
    1: pl_tx := 50;  pl_ty := 25;  pl_tz := 10;  (* pen-up position start point *)
    2: pl_tz := 0;                               (* pen down *)
    3: pl_tx := 57;  pl_ty := 26;                (* k=01 *)
    4: pl_tx := 63;  pl_ty := 28;                (* k=02 *)
    5: pl_tx := 68;  pl_ty := 32;                (* k=03 *)
    6: pl_tx := 72;  pl_ty := 38;                (* k=04 *)
    7: pl_tx := 74;  pl_ty := 44;                (* k=05 *)
    8: pl_tx := 75;  pl_ty := 50;                (* k=06 *)
    9: pl_tx := 74;  pl_ty := 57;                (* k=07 *)
    10: pl_tx := 72; pl_ty := 63;                (* k=08 *)
    11: pl_tx := 68; pl_ty := 68;                (* k=09 *)
    12: pl_tx := 63; pl_ty := 72;                (* k=10 *)
    13: pl_tx := 57; pl_ty := 74;                (* k=11 *)
    14: pl_tx := 50; pl_ty := 75;                (* k=12 *)
    15: pl_tx := 44; pl_ty := 74;                (* k=13 *)
    16: pl_tx := 38; pl_ty := 72;                (* k=14 *)
    17: pl_tx := 32; pl_ty := 68;                (* k=15 *)
    18: pl_tx := 28; pl_ty := 63;                (* k=16 *)
    19: pl_tx := 26; pl_ty := 57;                (* k=17 *)
    20: pl_tx := 25; pl_ty := 50;                (* k=18 *)
    21: pl_tx := 26; pl_ty := 44;                (* k=19 *)
    22: pl_tx := 28; pl_ty := 38;                (* k=20 *)
    23: pl_tx := 32; pl_ty := 32;                (* k=21 *)
    24: pl_tx := 38; pl_ty := 28;                (* k=22 *)
    25: pl_tx := 44; pl_ty := 26;                (* k=23 *)
    26: pl_tx := 50; pl_ty := 25;                (* close back to start *)
    27: pl_tz := 10;                             (* pen up *)
    28: pl_tx := 50; pl_ty := 50; pl_tz := 10;   (* return center, done *)
END_CASE;

(* ---- step advance: all axes idle and Done (C4) ---- *)
IF (pl_step <> 0) AND all_oe AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 28 THEN
        plot_done := TRUE;
        pl_step := 0;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- interpolators (C5: XY VMAX=40, Z VMAX=20) ---- *)
ix_x(fire := fire_x, pos_target := pl_tx, hold_req := hold, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := fire_y, pos_target := pl_ty, hold_req := hold, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := fire_z, pos_target := pl_tz, hold_req := hold, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0, MAXPOS := 10.0);

(* ---- drives ---- *)
drv_x(cw := cw_x, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
drv_y(cw := cw_y, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
drv_z(cw := cw_z, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

(* ---- velocity command outputs with gating (C2, C3, C5, C6) ---- *)
IF run AND NOT quickstop AND NOT fault_latch THEN
    x_v := REAL_TO_INT(drv_x.v_cmd);
    y_v := REAL_TO_INT(drv_y.v_cmd);
    zvr := drv_z.v_cmd;
    IF zvr > 20.0 THEN zvr := 20.0; END_IF;
    IF zvr < -20.0 THEN zvr := -20.0; END_IF;
    z_v := REAL_TO_INT(zvr);
ELSE
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

(* ---- indicators ---- *)
pen_down := (z_fb <= 2);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := all_oe AND (pl_step = 0) AND NOT quickstop
             AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
             AND ix_x.Done AND ix_y.Done AND ix_z.Done;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
