FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL;
    ACCEL : REAL;
    MinP : REAL;
    MaxP : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    edge_ir : BOOL;
    tgt : REAL;
    pos : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    POSWIN : REAL := 0.5;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF (INT_TO_REAL(pos_target) > MaxP) OR (INT_TO_REAL(pos_target) < MinP) THEN
        Busy := FALSE;
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
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
        IF (vel = 0.0) AND (ABS(dist) <= POSWIN) THEN
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
    cw : INT;
    Setpoint : INT;
    pos_fb : INT;
  END_VAR
  VAR_OUTPUT
    sw : INT;
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : INT;
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 80.0;
  END_VAR
cmd := cw AND 16#000F;
IF (state = 7) AND ((cw AND 16#0080) <> 0) THEN
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
IF (state >= 4) AND (state <= 6) THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / SCAN_T;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF (v_ff = 0.0) AND (ABS(pos_err) <= 1.5) THEN
        v_out := 0.0;
    END_IF;
ELSE
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - QS_DECEL * SCAN_T);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + QS_DECEL * SCAN_T);
    END_IF;
END_IF;
sp_prev := Setpoint;
v_cmd := REAL_TO_INT(v_out);
sw := 0;
CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0031;
    3: sw := 16#0033;
    4: sw := 16#0037;
    5: sw := 16#0014;
    6: sw := 16#0016;
    7: sw := 16#0018;
END_CASE;
IF state = 4 THEN
    IF (ABS(pos_err) <= 2.0) AND (v_ff = 0.0) THEN
        sw := sw OR 16#0400;
    END_IF;
END_IF;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : INT;
    sw : INT;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
  END_VAR
  VAR
    pstep : INT;
  END_VAR
IF Enable AND (pstep = 4) AND ((sw AND 16#0004) = 0) AND ((sw AND 16#0008) = 0) THEN
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

FUNCTION_BLOCK MC_RESET
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : INT;
    sw : INT;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
  END_VAR
  VAR
    busy : BOOL;
    edge_rs : BOOL;
  END_VAR
IF fire AND NOT edge_rs THEN
    busy := TRUE;
END_IF;
edge_rs := fire;
IF busy THEN
    IF (sw AND 16#0008) <> 0 THEN
        cw := cw OR 16#0080;
    ELSE
        cw := cw AND 16#FF7F;
        busy := FALSE;
    END_IF;
END_IF;
Done := (NOT busy) AND ((sw AND 16#0008) = 0) AND (NOT fire);
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_STOP
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : INT;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
  END_VAR
IF fire THEN
    cw := (cw AND 16#00F0) OR 16#0002;
    Done := FALSE;
ELSE
    Done := TRUE;
END_IF;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    rs_y : MC_RESET;
    rs_z : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    x_cw : INT;
    y_cw : INT;
    z_cw : INT;
    x_set : INT;
    y_set : INT;
    z_set : INT;
    homed : BOOL;
    pen_up : BOOL;
    pen_was_down : BOOL;
    home_st : INT;
    step : INT;
    edge_home : BOOL;
    edge_go : BOOL;
    edge_draw : BOOL;
    edge_jog : BOOL;
    exe_x : BOOL;
    exe_y : BOOL;
    exe_z : BOOL;
    man_active : BOOL;
    jog_active : BOOL;
    x_cmd : INT;
    y_cmd : INT;
    z_cmd : INT;
    z_man_last : INT;
    jog_pos : INT;
  END_VAR
  VAR
    run AT %QX0.0 : BOOL;
    cmd_home AT %QX0.1 : BOOL;
    cmd_go AT %QX0.2 : BOOL;
    jog_fwd AT %QX0.3 : BOOL;
    jog_rev AT %QX0.4 : BOOL;
    quickstop AT %QX0.5 : BOOL;
    cmd_reset AT %QX0.6 : BOOL;
    cmd_draw AT %QX0.7 : BOOL;
    all_oe AT %QX0.8 : BOOL;
    move_done AT %QX0.9 : BOOL;
    any_moving AT %QX1.0 : BOOL;
    fault_any AT %QX1.1 : BOOL;
    pen_down AT %QX1.2 : BOOL;
    plot_done AT %QX1.3 : BOOL;
    x_fb AT %QW1 : INT;
    y_fb AT %QW2 : INT;
    z_fb AT %QW3 : INT;
    x_sp AT %QW4 : INT;
    y_sp AT %QW5 : INT;
    z_sp AT %QW6 : INT;
    x_sw AT %QW10 : INT;
    y_sw AT %QW11 : INT;
    z_sw AT %QW12 : INT;
    x_v AT %QW13 : INT;
    y_v AT %QW14 : INT;
    z_v AT %QW15 : INT;
    prog_id AT %QW20 : INT := 2;
  END_VAR
prog_id := 2;
(* sample drive status / faults (C4/C7) *)
x_sw := dv_x.sw;
y_sw := dv_y.sw;
z_sw := dv_z.sw;
fault_any := ((x_sw AND 16#0008) <> 0) OR ((y_sw AND 16#0008) <> 0) OR ((z_sw AND 16#0008) <> 0);

(* pen state (C7) *)
pen_down := (z_fb <= 2);
pen_up := (z_fb >= 8);

(* enable / reset / quickstop, quickstop last = highest priority (C2) *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_sw);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_sw);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_sw);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sw);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_sw);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_sw);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* abort on quickstop / fault (C2/AC5/AC6) *)
IF quickstop OR fault_any THEN
    step := 0;
    home_st := 0;
    man_active := FALSE;
    plot_done := FALSE;
END_IF;

(* homing: Z ref = pen-up safe position (C5) *)
IF cmd_home AND NOT edge_home AND run AND NOT quickstop AND NOT fault_any THEN
    home_st := 1;
    pen_was_down := NOT pen_up;
    exe_z := TRUE;
    z_cmd := 10;
    z_man_last := 10;
END_IF;
edge_home := cmd_home;

(* manual cmd_go: homed, pen up, targets in travel (C1/C3/C5) *)
IF cmd_go AND NOT edge_go AND homed AND all_oe AND NOT quickstop AND NOT fault_any
   AND (step = 0) AND (home_st = 0) AND pen_up
   AND NOT exe_x AND NOT exe_y AND NOT jog_active THEN
    IF (x_sp >= 0) AND (x_sp <= 100) AND (y_sp >= 0) AND (y_sp <= 100) THEN
        exe_x := TRUE;
        x_cmd := x_sp;
        exe_y := TRUE;
        y_cmd := y_sp;
        man_active := TRUE;
    END_IF;
END_IF;
edge_go := cmd_go;

(* manual Z via z_sp, not pen-interlocked (C1) *)
IF (step = 0) AND (home_st = 0) AND NOT exe_z AND NOT quickstop AND NOT fault_any THEN
    IF (z_sp >= 0) AND (z_sp <= 10) AND (z_sp <> z_man_last) THEN
        exe_z := TRUE;
        z_cmd := z_sp;
        z_man_last := z_sp;
    END_IF;
END_IF;

(* jog X: pen up only (C1), gated by quickstop/fault (C2/C4) *)
IF (jog_fwd OR jog_rev) AND NOT edge_jog AND homed AND NOT quickstop AND NOT fault_any
   AND (step = 0) AND (home_st = 0) AND pen_up AND NOT man_active AND NOT exe_x THEN
    jog_pos := x_fb;
END_IF;
edge_jog := jog_fwd OR jog_rev;
jog_active := (jog_fwd OR jog_rev) AND homed AND NOT quickstop AND NOT fault_any
              AND (step = 0) AND (home_st = 0) AND pen_up;
IF jog_active THEN
    IF jog_fwd AND (jog_pos < 97) THEN
        jog_pos := jog_pos + 1;
    ELSIF jog_rev AND (jog_pos > 3) THEN
        jog_pos := jog_pos - 1;
    END_IF;
END_IF;

(* draw trigger: ready + pen up (C6) *)
IF cmd_draw AND NOT edge_draw AND homed AND all_oe AND NOT quickstop AND NOT fault_any
   AND (step = 0) AND (home_st = 0) AND pen_up
   AND NOT man_active AND NOT jog_active THEN
    step := 1;
    plot_done := FALSE;
    exe_z := TRUE;
    z_cmd := 10;
    z_man_last := 10;
END_IF;
edge_draw := cmd_draw;

(* interpolators: X/Y 40%/s acc 80, Z 20mm/s acc 60 (C6) *)
ix_x(fire := exe_x, pos_target := x_cmd, hold_req := quickstop, VMAX := 40.0, ACCEL := 80.0, MinP := 0.0, MaxP := 100.0);
ix_y(fire := exe_y, pos_target := y_cmd, hold_req := quickstop, VMAX := 40.0, ACCEL := 80.0, MinP := 0.0, MaxP := 100.0);
ix_z(fire := exe_z, pos_target := z_cmd, hold_req := quickstop, VMAX := 20.0, ACCEL := 60.0, MinP := 0.0, MaxP := 10.0);

IF ix_x.Done THEN exe_x := FALSE; END_IF;
IF ix_y.Done THEN exe_y := FALSE; END_IF;
IF ix_z.Done THEN exe_z := FALSE; END_IF;

(* homing sequence: pen down -> Z up only (C5) *)
IF home_st = 1 THEN
    IF NOT exe_z THEN
        IF pen_was_down THEN
            homed := TRUE;
            home_st := 0;
        ELSE
            home_st := 2;
            exe_x := TRUE;
            x_cmd := 0;
            exe_y := TRUE;
            y_cmd := 0;
        END_IF;
    END_IF;
ELSIF home_st = 2 THEN
    IF NOT exe_x AND NOT exe_y THEN
        homed := TRUE;
        home_st := 0;
    END_IF;
END_IF;

(* draw sequence (C6) *)
CASE step OF
    1:  IF NOT exe_z THEN step := 2; exe_x := TRUE; x_cmd := 20; exe_y := TRUE; y_cmd := 20; END_IF;
    2:  IF NOT exe_x AND NOT exe_y THEN step := 3; exe_z := TRUE; z_cmd := 0; z_man_last := 0; END_IF;
    3:  IF NOT exe_z THEN step := 4; exe_x := TRUE; x_cmd := 80; END_IF;
    4:  IF NOT exe_x THEN step := 5; exe_y := TRUE; y_cmd := 80; END_IF;
    5:  IF NOT exe_y THEN step := 6; exe_x := TRUE; x_cmd := 20; END_IF;
    6:  IF NOT exe_x THEN step := 7; exe_y := TRUE; y_cmd := 20; END_IF;
    7:  IF NOT exe_y THEN step := 8; exe_z := TRUE; z_cmd := 10; z_man_last := 10; END_IF;
    8:  IF NOT exe_z THEN step := 9; exe_x := TRUE; x_cmd := 50; exe_y := TRUE; y_cmd := 50; END_IF;
    9:  IF NOT exe_x AND NOT exe_y THEN step := 0; plot_done := TRUE; END_IF;
END_CASE;

(* setpoint routing: jog overrides interpolator on X *)
IF jog_active THEN
    x_set := jog_pos;
ELSE
    x_set := ix_x.Setpoint;
END_IF;
y_set := ix_y.Setpoint;
z_set := ix_z.Setpoint;

(* drives follow interpolated setpoints *)
dv_x(cw := x_cw, Setpoint := x_set, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_set, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_set, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* status lamps (C7) *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND man_active
             AND (NOT exe_x) AND (NOT exe_y) AND (step = 0) AND (home_st = 0)
             AND NOT quickstop AND NOT fault_any;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
IF ix_x.Done AND ix_y.Done AND (NOT exe_x) AND (NOT exe_y) AND (step = 0) AND (home_st = 0) THEN
    man_active := FALSE;
END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
