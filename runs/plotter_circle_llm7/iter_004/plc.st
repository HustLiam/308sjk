FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
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
  END_VAR
IF fire AND (ABS(INT_TO_REAL(pos_target) - pos) > 0.499) THEN
    IF (pos_target > 100) OR (pos_target < 0) THEN
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
END_IF;

IF hold_req OR (NOT fire) THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - 1.6); END_IF;
    IF vel < 0.0 THEN vel := MIN(0.0, vel + 1.6); END_IF;
    IF (vel = 0.0) AND (ABS(tgt - pos) <= 0.75) THEN
        pos := tgt;
        Busy := FALSE;
        Done := TRUE;
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / 160.0;
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - 1.6); ELSE vel := MIN(0.0, vel + 1.6); END_IF;
        IF (vel = 0.0) AND (ABS(dist) <= 0.75) THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(60.0, vel + 1.6); ELSE vel := MAX(-60.0, vel - 1.6); END_IF;
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
  END_VAR
  VAR_OUTPUT
    sw : WORD;
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL := 0.0;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
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
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / 0.02;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + 8.0 * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF (v_ff = 0.0) AND (ABS(pos_err) <= 1.5) THEN
        v_out := 0.0;
    END_IF;
ELSIF state = 5 THEN
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - 1.6); END_IF;
    IF v_out < 0.0 THEN v_out := MIN(0.0, v_out + 1.6); END_IF;
ELSE
    v_out := 0.0;
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
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
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
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
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

FUNCTION_BLOCK MC_MOVEABSOLUTE
  VAR_INPUT
    fire : BOOL;
    Position : INT;
  END_VAR
  VAR_IN_OUT
    interp_exe : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
  END_VAR
  VAR
    edge_ma : BOOL;
  END_VAR
IF fire AND NOT edge_ma THEN
    interp_exe := TRUE;
END_IF;
edge_ma := fire;
Done := NOT interp_exe;
Busy := interp_exe;
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
IF JogForward AND (jog_pos < 97) THEN
    jog_pos := jog_pos + 1;
ELSIF JogBackward AND (jog_pos > 3) THEN
    jog_pos := jog_pos - 1;
END_IF;
Busy := JogForward OR JogBackward;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_HOME
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_IN_OUT
    home_exe : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
  END_VAR
  VAR
    edge_hm : BOOL;
  END_VAR
IF fire AND NOT edge_hm THEN
    home_exe := TRUE;
END_IF;
edge_hm := fire;
Done := NOT home_exe;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    rs_y : MC_RESET;
    rs_z : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    go_x : MC_MOVEABSOLUTE;
    go_y : MC_MOVEABSOLUTE;
    go_z : MC_MOVEABSOLUTE;
    hm_x : MC_HOME;
    hm_y : MC_HOME;
    hm_z : MC_HOME;
    jg_x : MC_MOVEJOG;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_sww : WORD;
    y_sww : WORD;
    z_sww : WORD;
    fault_now : BOOL;
    abort_latch : BOOL;
    run_ok : BOOL;
    draw_prev : BOOL;
    pl_step : INT;
    theta_r : REAL := 0.0;
    x_circ : REAL;
    y_circ : REAL;
    circ_done : BOOL;
    tx : INT;
    ty : INT;
    tz : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT := 50;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
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
    all_oe AT %QX1.0 : BOOL;
    move_done AT %QX1.1 : BOOL;
    any_moving AT %QX1.2 : BOOL;
    fault_any AT %QX1.3 : BOOL;
    pen_down AT %QX1.4 : BOOL;
    plot_done AT %QX1.5 : BOOL;
    x_fb AT %QW2 : INT;
    y_fb AT %QW3 : INT;
    z_fb AT %QW4 : INT;
    x_sp AT %QW5 : INT;
    y_sp AT %QW6 : INT;
    z_sp AT %QW7 : INT;
    x_sw AT %QW8 : INT;
    y_sw AT %QW9 : INT;
    z_sw AT %QW10 : INT;
    x_v AT %QW11 : INT;
    y_v AT %QW12 : INT;
    z_v AT %QW13 : INT;
    prog_id AT %QW20 : INT := 2;
  END_VAR
prog_id := 2;

(* ---- sample drive status ---- *)
x_sww := dv_x.sw;
y_sww := dv_y.sw;
z_sww := dv_z.sw;
x_sw := WORD_TO_INT(x_sww);
y_sw := WORD_TO_INT(y_sww);
z_sw := WORD_TO_INT(z_sww);

pen_down := z_fb <= 1;
fault_now := ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0);
fault_any := fault_now;

(* ---- abort / reset latch ---- *)
IF quickstop OR fault_now THEN
    abort_latch := TRUE;
    plot_done := FALSE;
    pl_step := 0;
    theta_r := 0.0;
    circ_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;
IF cmd_reset AND (NOT fault_now) AND (NOT quickstop) THEN
    abort_latch := FALSE;
END_IF;
run_ok := run AND (NOT abort_latch);

(* ---- draw trigger (rising edge) ---- *)
IF cmd_draw AND (NOT draw_prev) AND run_ok AND all_oe AND (pl_step = 0) THEN
    theta_r := 0.0;
    circ_done := FALSE;
    plot_done := FALSE;
    pl_step := 1;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
END_IF;
draw_prev := cmd_draw;

(* ---- target selection ---- *)
IF pl_step = 0 THEN
    tx := x_sp;
    ty := y_sp;
    tz := z_sp;
ELSE
    CASE pl_step OF
        1: tx := x_fb; ty := y_fb; tz := 10;
        2: tx := 50; ty := 25; tz := 10;
        3: tx := 50; ty := 25; tz := 0;
        4: tz := 0;
           theta_r := theta_r + 0.032;
           IF theta_r >= 6.2832 THEN
               theta_r := 6.2832;
               circ_done := TRUE;
           END_IF;
           x_circ := 50.0 + 25.0 * COS(theta_r - 1.5708);
           y_circ := 50.0 + 25.0 * SIN(theta_r - 1.5708);
           tx := REAL_TO_INT(x_circ);
           ty := REAL_TO_INT(y_circ);
        5: tx := 50; ty := 25; tz := 10;
        6: tx := 50; ty := 50; tz := 10;
    END_CASE;
END_IF;

(* ---- power / reset / quickstop (quickstop last = highest priority) ---- *)
pwr_x(Enable := run AND (NOT quickstop), cw := x_cw, sw := x_sww);
pwr_y(Enable := run AND (NOT quickstop), cw := y_cw, sw := y_sww);
pwr_z(Enable := run AND (NOT quickstop), cw := z_cw, sw := z_sww);
rs_x(fire := cmd_reset AND (NOT fault_now), cw := x_cw, sw := x_sww);
rs_y(fire := cmd_reset AND (NOT fault_now), cw := y_cw, sw := y_sww);
rs_z(fire := cmd_reset AND (NOT fault_now), cw := z_cw, sw := z_sww);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* ---- MC command layer (C2: pen interlock on jog and cmd_go) ---- *)
go_x(fire := run_ok AND cmd_go AND (NOT pen_down) AND (pl_step = 0), Position := x_sp, interp_exe := go_x_exe);
go_y(fire := run_ok AND cmd_go AND (NOT pen_down) AND (pl_step = 0), Position := y_sp, interp_exe := go_y_exe);
go_z(fire := run_ok AND cmd_go AND (pl_step = 0), Position := z_sp, interp_exe := go_z_exe);
hm_x(fire := cmd_home AND run_ok AND (pl_step = 0), home_exe := hm_x_exe);
hm_y(fire := cmd_home AND run_ok AND (pl_step = 0), home_exe := hm_y_exe);
hm_z(fire := cmd_home AND run_ok AND (pl_step = 0), home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd AND run_ok AND (NOT pen_down) AND (pl_step = 0), JogBackward := jog_rev AND run_ok AND (NOT pen_down) AND (pl_step = 0), jog_pos := x_jog_pos);

(* ---- interpolation engines ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := tx, hold_req := quickstop OR (NOT run));
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := ty, hold_req := quickstop OR (NOT run));
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := tz, hold_req := quickstop OR (NOT run));

(* ---- clear trigger lines when settled and idle ---- *)
IF (pl_step = 0) AND (NOT cmd_go) AND (NOT cmd_home) AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

(* ---- setpoint routing ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
x_v := dv_x.v_cmd;
y_v := dv_y.v_cmd;
z_v := dv_z.v_cmd;

(* ---- C1/C3/C4: force zero velocity on quickstop / run-off / fault ---- *)
IF quickstop OR (NOT run) OR fault_now THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

(* ---- step advance ---- *)
IF (pl_step <> 0) AND (NOT quickstop) AND (NOT fault_now) THEN
    CASE pl_step OF
        1: IF ix_z.Done AND (z_fb >= 9) THEN pl_step := 2; END_IF;
        2: IF ix_x.Done AND ix_y.Done AND (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 25) <= 2) THEN pl_step := 3; END_IF;
        3: IF ix_z.Done AND (z_fb <= 1) THEN pl_step := 4; END_IF;
        4: IF circ_done AND ix_x.Done AND ix_y.Done THEN pl_step := 5; END_IF;
        5: IF ix_z.Done AND (z_fb >= 9) THEN pl_step := 6; END_IF;
        6: IF ix_x.Done AND ix_y.Done AND (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 50) <= 2) THEN
              plot_done := TRUE;
              pl_step := 0;
              go_x_exe := FALSE;
              go_y_exe := FALSE;
              go_z_exe := FALSE;
           END_IF;
    END_CASE;
END_IF;

(* ---- status outputs ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := (pl_step = 0) AND go_x.Done AND go_y.Done AND go_z.Done AND (NOT jg_x.Busy) AND (NOT any_moving) AND (NOT abort_latch);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
