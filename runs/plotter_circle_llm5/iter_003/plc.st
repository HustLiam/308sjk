FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    vmax : REAL;
    acc : REAL;
    poswin : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL := true;
  END_VAR
  VAR
    pos : REAL := 50.0;
    tgt : REAL := 50.0;
    vel : REAL;
    stop_d : REAL;
    dist : REAL;
    edge_ir : BOOL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > 100 OR pos_target < 0 THEN
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
END_IF;
edge_ir := fire;

IF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;

IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - acc * 0.02);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + acc * 0.02);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * acc);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - acc * 0.02);
        ELSE vel := MIN(0.0, vel + acc * 0.02);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= poswin THEN
            pos := tgt;
            vel := 0.0;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(vmax, vel + acc * 0.02);
        ELSE vel := MAX(-vmax, vel - acc * 0.02);
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
  END_VAR
  VAR_OUTPUT
    sw : WORD;
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
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
    7: IF cmd = 16#06 THEN state := 2; END_IF;
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

FUNCTION_BLOCK MC_MOVEABSOLUTE
  VAR_INPUT
    fire : BOOL;
    Position : INT;
  END_VAR
  VAR_OUTPUT
    interp_exe : BOOL;
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
IF JogForward AND jog_pos < 97 THEN
    jog_pos := jog_pos + 1;
ELSIF JogBackward AND jog_pos > 3 THEN
    jog_pos := jog_pos - 1;
END_IF;
Busy := JogForward OR JogBackward;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_HOME
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_OUTPUT
    home_exe : BOOL;
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
    hm_z : MC_HOME;
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
    x_jog_pos : INT := 50;
    pl_x_exe : BOOL;
    pl_y_exe : BOOL;
    pl_z_exe : BOOL;
    pl_step : INT;
    arc_i : INT;
    arc_ang : REAL;
    tx_r : REAL;
    ty_r : REAL;
    x_t : INT;
    y_t : INT;
    z_t : INT;
    edge_draw : BOOL;
    abort_latch : BOOL;
    x_tgt_use : INT;
    y_tgt_use : INT;
    z_tgt_use : INT;
    x_fire : BOOL;
    y_fire : BOOL;
    z_fire : BOOL;
    hold_all : BOOL;
  END_VAR
  VAR
    prog_id AT %QW20 : INT := 2;
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
    x_fb AT %QW0 : INT;
    y_fb AT %QW1 : INT;
    z_fb AT %QW2 : INT;
    x_sp AT %QW3 : INT;
    y_sp AT %QW4 : INT;
    z_sp AT %QW5 : INT;
    x_sw AT %QW6 : INT;
    y_sw AT %QW7 : INT;
    z_sw AT %QW8 : INT;
    x_v AT %QW9 : INT;
    y_v AT %QW10 : INT;
    z_v AT %QW11 : INT;
  END_VAR
(* ---- program identity ---- *)
prog_id := 2;

(* ---- scan head: sample drive status words ---- *)
x_sww := dv_x.sw;
y_sww := dv_y.sw;
z_sww := dv_z.sw;
x_sw := WORD_TO_INT(x_sww);
y_sw := WORD_TO_INT(y_sww);
z_sw := WORD_TO_INT(z_sww);

(* ---- application status ---- *)
pen_down := z_fb <= 1;
fault_any := ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0);

(* ---- abort handling: C1 / C3 / C4 ---- *)
IF quickstop OR fault_any OR NOT run THEN
    pl_step := 0;
    arc_i := 0;
    pl_x_exe := FALSE;
    pl_y_exe := FALSE;
    pl_z_exe := FALSE;
    plot_done := FALSE;
    abort_latch := TRUE;
END_IF;
IF cmd_reset AND NOT quickstop AND NOT fault_any AND run THEN
    abort_latch := FALSE;
END_IF;

(* ---- draw start: rising edge of cmd_draw, gated by run / no fault / not aborted ---- *)
IF cmd_draw AND NOT edge_draw AND run AND NOT quickstop AND NOT fault_any AND NOT abort_latch AND (pl_step = 0) THEN
    pl_step := 1;
    arc_i := 0;
    plot_done := FALSE;
END_IF;
edge_draw := cmd_draw;

(* ---- manual commands (C2: pen interlock on X/Y) ---- *)
go_x(fire := cmd_go AND run AND NOT pen_down AND NOT quickstop AND NOT fault_any AND (pl_step = 0), Position := x_sp);
go_y(fire := cmd_go AND run AND NOT pen_down AND NOT quickstop AND NOT fault_any AND (pl_step = 0), Position := y_sp);
go_z(fire := cmd_go AND run AND NOT quickstop AND NOT fault_any AND (pl_step = 0), Position := z_sp);
hm_z(fire := cmd_home AND run AND NOT quickstop AND NOT fault_any);

(* ---- plot sequence: set targets per step (C5) ---- *)
CASE pl_step OF
    0: pl_x_exe := FALSE;
       pl_y_exe := FALSE;
       pl_z_exe := FALSE;
    1: z_t := 10;
       pl_z_exe := TRUE;
    2: x_t := 50;
       y_t := 25;
       pl_x_exe := TRUE;
       pl_y_exe := TRUE;
    3: z_t := 0;
       pl_z_exe := TRUE;
    4: arc_ang := 4.712389 + INT_TO_REAL(arc_i) * 0.2513274;
       tx_r := 50.0 + 25.0 * COS(arc_ang);
       ty_r := 50.0 + 25.0 * SIN(arc_ang);
       x_t := REAL_TO_INT(tx_r);
       y_t := REAL_TO_INT(ty_r);
       pl_x_exe := TRUE;
       pl_y_exe := TRUE;
    5: z_t := 10;
       pl_z_exe := TRUE;
    6: x_t := 50;
       y_t := 50;
       pl_x_exe := TRUE;
       pl_y_exe := TRUE;
END_CASE;

(* ---- fire / target selection ---- *)
x_fire := go_x.interp_exe OR pl_x_exe;
y_fire := go_y.interp_exe OR pl_y_exe;
z_fire := go_z.interp_exe OR pl_z_exe OR hm_z.home_exe;
x_tgt_use := x_sp;
IF pl_x_exe THEN x_tgt_use := x_t; END_IF;
y_tgt_use := y_sp;
IF pl_y_exe THEN y_tgt_use := y_t; END_IF;
z_tgt_use := z_sp;
IF pl_z_exe THEN z_tgt_use := z_t; END_IF;
IF hm_z.home_exe THEN z_tgt_use := 10; END_IF;
hold_all := quickstop OR fault_any OR NOT run;

(* ---- enable / reset / quickstop ---- *)
pwr_x(Enable := run AND NOT quickstop AND NOT fault_any, cw := x_cw, sw := x_sww);
pwr_y(Enable := run AND NOT quickstop AND NOT fault_any, cw := y_cw, sw := y_sww);
pwr_z(Enable := run AND NOT quickstop AND NOT fault_any, cw := z_cw, sw := z_sww);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sww);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_sww);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_sww);
st_x(fire := quickstop OR fault_any OR NOT run, cw := x_cw);
st_y(fire := quickstop OR fault_any OR NOT run, cw := y_cw);
st_z(fire := quickstop OR fault_any OR NOT run, cw := z_cw);

(* ---- interpolation engines (continuous tracking) ---- *)
ix_x(fire := x_fire, pos_target := x_tgt_use, hold_req := hold_all, vmax := 40.0, acc := 80.0, poswin := 2.0);
ix_y(fire := y_fire, pos_target := y_tgt_use, hold_req := hold_all, vmax := 40.0, acc := 80.0, poswin := 2.0);
ix_z(fire := z_fire, pos_target := z_tgt_use, hold_req := hold_all, vmax := 20.0, acc := 80.0, poswin := 1.0);

(* ---- clear trigger lines when axis finished ---- *)
IF ix_x.Done AND NOT ix_x.Busy THEN
    go_x.interp_exe := FALSE;
    pl_x_exe := FALSE;
END_IF;
IF ix_y.Done AND NOT ix_y.Busy THEN
    go_y.interp_exe := FALSE;
    pl_y_exe := FALSE;
END_IF;
IF ix_z.Done AND NOT ix_z.Busy THEN
    go_z.interp_exe := FALSE;
    pl_z_exe := FALSE;
    hm_z.home_exe := FALSE;
END_IF;

(* ---- sequence step advance: all axes idle and done ---- *)
IF (pl_step <> 0) AND ix_x.Done AND ix_y.Done AND ix_z.Done AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy THEN
    CASE pl_step OF
        1: pl_step := 2;
        2: pl_step := 3;
        3: arc_i := 1;
           pl_step := 4;
        4: IF arc_i < 25 THEN arc_i := arc_i + 1;
           ELSE pl_step := 5;
           END_IF;
        5: pl_step := 6;
        6: plot_done := TRUE;
           pl_step := 0;
    END_CASE;
END_IF;

(* ---- jog (C2: pen interlock) ---- *)
jg_x(JogForward := jog_fwd AND run AND NOT pen_down AND NOT quickstop AND NOT fault_any,
     JogBackward := jog_rev AND run AND NOT pen_down AND NOT quickstop AND NOT fault_any,
     jog_pos := x_jog_pos);

(* ---- setpoint routing ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives: CSP follow + position loop ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);

(* ---- velocity outputs with safety gate (C1: quickstop forces zero) ---- *)
IF quickstop OR fault_any OR NOT run THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    x_v := dv_x.v_cmd;
    y_v := dv_y.v_cmd;
    z_v := dv_z.v_cmd;
END_IF;

(* ---- status outputs (C3 / C6) ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := (pl_step = 0) AND go_x.Done AND go_y.Done AND go_z.Done AND NOT go_x.interp_exe AND NOT go_y.interp_exe AND NOT go_z.interp_exe;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
