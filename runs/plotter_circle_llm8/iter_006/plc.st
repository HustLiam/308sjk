FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL;
    ACCEL : REAL;
    POSWIN : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    tgt : REAL := 0.0;
    pos : REAL := 0.0;
    vel : REAL := 0.0;
    edge_ir : BOOL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > 100 OR pos_target < 0 THEN
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;
edge_ir := fire;
IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - ACCEL * 0.02);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + ACCEL * 0.02);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * ACCEL);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - ACCEL * 0.02);
        ELSE vel := MIN(0.0, vel + ACCEL * 0.02);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(VMAX, vel + ACCEL * 0.02);
        ELSE vel := MAX(-VMAX, vel + ACCEL * 0.02);
        END_IF;
    END_IF;
END_IF;
pos := pos + vel * 0.02;
Setpoint := REAL_TO_INT(pos);
END_FUNCTION_BLOCK

FUNCTION_BLOCK DRIVE402
  VAR_INPUT
    cw : WORD;
    Setpoint : INT;
    pos_fb : INT;
    VLIM : REAL;
  END_VAR
  VAR_OUTPUT
    v_cmd : INT;
    sw : WORD;
  END_VAR
  VAR
    state : INT := 1;
    v_out : REAL := 0.0;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
    cmd : WORD;
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
END_CASE;
IF state >= 4 AND state <= 6 THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / 0.02;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + 8.0 * pos_err;
    IF v_out > VLIM THEN v_out := VLIM; END_IF;
    IF v_out < -VLIM THEN v_out := -VLIM; END_IF;
    IF v_ff = 0.0 AND ABS(pos_err) <= 1.5 THEN
        v_out := 0.0;
    END_IF;
ELSIF state = 5 THEN
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - 100.0 * 0.02);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + 100.0 * 0.02);
    END_IF;
ELSE
    v_out := 0.0;
END_IF;
sp_prev := Setpoint;
v_cmd := REAL_TO_INT(v_out);
sw := 16#0000;
CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0031;
    3: sw := 16#0033;
    4: sw := 16#0037;
    5: sw := 16#0014;
    7: sw := 16#0018;
END_CASE;
IF state = 4 THEN
    IF ABS(pos_err) <= 2.0 AND v_ff = 0.0 THEN
        sw := sw OR 16#0400;
    END_IF;
END_IF;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
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
    cw : WORD;
    sw : WORD;
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
Done := NOT busy AND (sw AND 16#0008) = 0 AND NOT fire;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_STOP
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
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

FUNCTION_BLOCK MC_MOVEJOG
  VAR_INPUT
    JogForward : BOOL;
    JogBackward : BOOL;
  END_VAR
  VAR_IN_OUT
    jog_pos : INT;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
  END_VAR
IF JogForward AND jog_pos < 97 THEN
    jog_pos := jog_pos + 1;
ELSIF JogBackward AND jog_pos > 3 THEN
    jog_pos := jog_pos - 1;
END_IF;
Busy := JogForward OR JogBackward;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    jg_x : MC_MOVEJOG;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_sww : WORD;
    y_sww : WORD;
    z_sww : WORD;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_t : INT;
    y_t : INT;
    z_t : INT;
    x_tg : INT;
    y_tg : INT;
    z_tg : INT;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    arc_exe : BOOL;
    theta : REAL := 0.0;
    arc_x : INT;
    arc_y : INT;
    pl_step : INT;
    plot_fin : BOOL;
    x_jog_pos : INT;
    hold_all : BOOL;
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
(* ---- program identity ---- *)
prog_id := 3;

(* ---- scan head: sample drive status words from drive FB outputs ---- *)
x_sww := dv_x.sw;
y_sww := dv_y.sw;
z_sww := dv_z.sw;

(* ---- fault / pen / enable state ---- *)
fault_any := ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0);
pen_down := (z_fb <= 1);
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
hold_all := quickstop OR fault_any OR (NOT run);

(* ---- power enable / reset / quickstop (quickstop last = highest priority) ---- *)
pwr_x(Enable := run AND (NOT quickstop) AND (NOT fault_any), cw := x_cw, sw := x_sww);
pwr_y(Enable := run AND (NOT quickstop) AND (NOT fault_any), cw := y_cw, sw := y_sww);
pwr_z(Enable := run AND (NOT quickstop) AND (NOT fault_any), cw := z_cw, sw := z_sww);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sww);
st_x(fire := quickstop OR fault_any OR (NOT run), cw := x_cw);
st_y(fire := quickstop OR fault_any OR (NOT run), cw := y_cw);
st_z(fire := quickstop OR fault_any OR (NOT run), cw := z_cw);

(* ---- jog: only allowed when pen up (C2) ---- *)
jg_x(JogForward := jog_fwd AND (NOT pen_down) AND run AND all_oe AND (pl_step = 0),
     JogBackward := jog_rev AND (NOT pen_down) AND run AND all_oe AND (pl_step = 0),
     jog_pos := x_jog_pos);

(* ---- sequencer: continuous-tracking mode; targets assigned EVERY scan inside each step ---- *)
CASE pl_step OF
    0: theta := 0.0;
       IF cmd_draw AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND (NOT jg_x.Busy) THEN
           plot_fin := FALSE;
           IF z_fb >= 9 THEN
               pl_step := 1;
           ELSE
               pl_step := 8;
           END_IF;
       ELSIF cmd_go AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND (NOT pen_down) AND (NOT jg_x.Busy) THEN
           pl_step := 20;
       ELSIF cmd_home AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any) THEN
           pl_step := 30;
       END_IF;
    8: z_t := 10;
       IF z_fb >= 9 THEN pl_step := 1; END_IF;
    1: x_t := 50; y_t := 25;
       IF (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 25) <= 2) THEN pl_step := 2; END_IF;
    2: z_t := 0;
       IF z_fb <= 1 THEN pl_step := 3; END_IF;
    3: IF theta < 6.2832 THEN
           theta := theta + 0.032;
           IF theta > 6.2832 THEN theta := 6.2832; END_IF;
       END_IF;
       IF (theta >= 6.2832) AND (ABS(x_fb - 50) <= 3) AND (ABS(y_fb - 25) <= 3) THEN pl_step := 4; END_IF;
    4: z_t := 10;
       IF z_fb >= 9 THEN pl_step := 5; END_IF;
    5: x_t := 50; y_t := 50;
       IF (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 50) <= 2) THEN pl_step := 7; END_IF;
    7: x_t := 50; y_t := 50; z_t := 10;
       IF NOT cmd_draw THEN pl_step := 0; END_IF;
    20: x_t := x_sp; y_t := y_sp; z_t := z_sp;
        IF (ABS(x_fb - x_sp) <= 2) AND (ABS(y_fb - y_sp) <= 2) AND (ABS(z_fb - z_sp) <= 1) THEN pl_step := 21; END_IF;
    21: x_t := x_sp; y_t := y_sp; z_t := z_sp;
        IF NOT cmd_go THEN pl_step := 0; END_IF;
    30: x_t := 0; y_t := 0; z_t := 10;
        IF (ABS(x_fb) <= 3) AND (ABS(y_fb) <= 3) AND (z_fb >= 9) THEN pl_step := 31; END_IF;
    31: x_t := 0; y_t := 0; z_t := 10;
        IF NOT cmd_home THEN pl_step := 0; END_IF;
END_CASE;

(* ---- abort: quickstop / fault / run lost (C1, C3, C4) ---- *)
IF quickstop OR fault_any OR (NOT run) THEN
    pl_step := 0;
    plot_fin := FALSE;
END_IF;

(* ---- plot_done latch (set on reaching done state, cleared on abort / new draw) ---- *)
plot_fin := plot_fin OR (pl_step = 7);
plot_done := plot_fin;

(* ---- level-held trigger lines, derived every scan from sequencer state ---- *)
go_x_exe := (pl_step = 1) OR (pl_step = 5) OR (pl_step = 20) OR (pl_step = 21);
go_y_exe := (pl_step = 1) OR (pl_step = 5) OR (pl_step = 20) OR (pl_step = 21);
go_z_exe := (pl_step = 2) OR (pl_step = 4) OR (pl_step = 8) OR (pl_step = 20) OR (pl_step = 21) OR (pl_step = 30) OR (pl_step = 31);
arc_exe := (pl_step = 3);

(* ---- arc setpoints: center (50,50), radius 25, omega = 1.6 rad/s (tangential 40 %/s) ---- *)
arc_x := REAL_TO_INT(50.0 + 25.0 * COS(theta));
arc_y := REAL_TO_INT(50.0 + 25.0 * SIN(theta));

(* ---- interp targets ---- *)
x_tg := x_t;
y_tg := y_t;
z_tg := z_t;

ix_x(fire := go_x_exe, pos_target := x_tg, hold_req := hold_all,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0);
ix_y(fire := go_y_exe, pos_target := y_tg, hold_req := hold_all,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0);
ix_z(fire := go_z_exe, pos_target := z_tg, hold_req := hold_all,
     VMAX := 20.0, ACCEL := 50.0, POSWIN := 1.0);

(* ---- setpoint routing: jog / arc / interp ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSIF arc_exe THEN
    x_interp := arc_x;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
IF arc_exe THEN
    y_interp := arc_y;
ELSE
    y_interp := ix_y.Setpoint;
END_IF;
z_interp := ix_z.Setpoint;

(* ---- drives ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb, VLIM := 120.0);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb, VLIM := 120.0);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb, VLIM := 20.0);
z_v := dv_z.v_cmd;

(* ---- status word diagnostics outputs ---- *)
x_sw := WORD_TO_INT(dv_x.sw);
y_sw := WORD_TO_INT(dv_y.sw);
z_sw := WORD_TO_INT(dv_z.sw);

(* ---- velocity interlock: outputs forced to zero during quickstop / fault / no run ---- *)
IF quickstop OR fault_any OR (NOT run) THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

(* ---- status outputs ---- *)
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := (pl_step = 21) OR (pl_step = 31);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
