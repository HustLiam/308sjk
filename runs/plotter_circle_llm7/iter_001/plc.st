FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    hold_req : BOOL;
    pos_target : INT;
    v_max : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    edge_ir : BOOL;
    pos : REAL;
    tgt : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    ACCEL : REAL := 80.0;
    POSWIN : REAL := 0.5;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF (pos_target > 100) OR (pos_target < 0) THEN
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
END_IF;
edge_ir := fire;

(* continuous tracking: new target while fire held -> new trajectory *)
IF fire AND (NOT Busy) AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;

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
        IF dist > 0.0 THEN vel := MIN(v_max, vel + ACCEL * SCAN_T);
        ELSE vel := MAX(-v_max, vel + ACCEL * SCAN_T);
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
    v_cmd : INT;
    sw : WORD;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_ff : REAL;
    pos_err : REAL;
    v_out : REAL;
    sp_prev : INT;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 120.0;
  END_VAR
(* state: 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA *)
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

(* position loop: P control + setpoint differential feedforward *)
IF (state >= 4) AND (state <= 6) THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / SCAN_T;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF (v_ff = 0.0) AND (ABS(pos_err) <= 1.5) THEN
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
        0: cw := (cw AND 16#00F0) OR 16#0006; pstep := 1;
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
    x_jog_pos : INT;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    go_cmd : BOOL;
    hm_cmd : BOOL;
    jog_ok : BOOL;
    draw_edge : BOOL;
    draw_prev : BOOL;
    hold : BOOL;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    pl_step : INT;
    pl_seg : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    theta : REAL;
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
x_sww := INT_TO_WORD(x_sw);
y_sww := INT_TO_WORD(y_sw);
z_sww := INT_TO_WORD(z_sw);

pen_down := z_fb <= 1;
fault_any := ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0);
hold := quickstop OR fault_any OR (NOT run);

(* ---- enable / reset / quickstop (quickstop written last = highest priority) ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_sww);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_sww);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_sww);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sww);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_sww);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_sww);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* ---- command gating: C2 pen interlock for manual moves, C3 run interlock ---- *)
go_cmd := cmd_go AND run AND (NOT pen_down) AND (NOT quickstop) AND (NOT fault_any);
hm_cmd := cmd_home AND run AND (NOT quickstop) AND (NOT fault_any);
jog_ok := (jog_fwd OR jog_rev) AND run AND (NOT pen_down) AND (NOT quickstop) AND (NOT fault_any);

go_x(fire := go_cmd, Position := x_sp, interp_exe := go_x_exe);
go_y(fire := go_cmd, Position := y_sp, interp_exe := go_y_exe);
go_z(fire := go_cmd, Position := z_sp, interp_exe := go_z_exe);
hm_x(fire := hm_cmd, home_exe := hm_x_exe);
hm_y(fire := hm_cmd, home_exe := hm_y_exe);
hm_z(fire := hm_cmd, home_exe := hm_z_exe);
jg_x(JogForward := jog_ok AND jog_fwd, JogBackward := jog_ok AND jog_rev, jog_pos := x_jog_pos);

(* ---- draw start edge ---- *)
draw_edge := cmd_draw AND NOT draw_prev;
draw_prev := cmd_draw;

(* ---- C1/C4/C3 abort: quickstop / fault / run drop kills sequence and plot_done ---- *)
IF quickstop OR fault_any OR (NOT run) THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- C5 plot sequence ---- *)
CASE pl_step OF
    0: IF draw_edge AND run AND all_oe AND (NOT fault_any) THEN
           IF z_fb < 10 THEN
               pl_tz := 10;
               go_z_exe := TRUE;
               pl_step := 1;
           ELSE
               pl_tx := 50;
               pl_ty := 25;
               go_x_exe := TRUE;
               go_y_exe := TRUE;
               pl_step := 2;
           END_IF;
       END_IF;
    1: IF ix_z.Done AND (NOT ix_z.Busy) THEN
           pl_tx := 50;
           pl_ty := 25;
           go_x_exe := TRUE;
           go_y_exe := TRUE;
           pl_step := 2;
       END_IF;
    2: IF ix_x.Done AND ix_y.Done AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) THEN
           pl_tz := 0;
           go_z_exe := TRUE;
           pl_step := 3;
       END_IF;
    3: IF ix_z.Done AND (NOT ix_z.Busy) THEN
           pl_seg := 0;
           theta := -90.0;
           pl_step := 4;
       END_IF;
    4: IF ix_x.Done AND ix_y.Done AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) THEN
           IF pl_seg >= 36 THEN
               pl_tz := 10;
               go_z_exe := TRUE;
               pl_step := 5;
           ELSE
               theta := -90.0 + INT_TO_REAL(pl_seg + 1) * 10.0;
               pl_tx := REAL_TO_INT(50.0 + 25.0 * COS(theta * 0.0174533));
               pl_ty := REAL_TO_INT(50.0 + 25.0 * SIN(theta * 0.0174533));
               pl_seg := pl_seg + 1;
           END_IF;
       END_IF;
    5: IF ix_z.Done AND (NOT ix_z.Busy) THEN
           pl_tx := 50;
           pl_ty := 50;
           go_x_exe := TRUE;
           go_y_exe := TRUE;
           pl_step := 6;
       END_IF;
    6: IF ix_x.Done AND ix_y.Done AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) THEN
           plot_done := TRUE;
           pl_step := 0;
           go_x_exe := FALSE;
           go_y_exe := FALSE;
       END_IF;
END_CASE;

(* ---- target routing: home -> sequencer -> NC setpoint channel ---- *)
IF hm_x_exe THEN
    x_tgt := 0;
ELSIF pl_step <> 0 THEN
    x_tgt := pl_tx;
ELSE
    x_tgt := x_sp;
END_IF;
IF hm_y_exe THEN
    y_tgt := 0;
ELSIF pl_step <> 0 THEN
    y_tgt := pl_ty;
ELSE
    y_tgt := y_sp;
END_IF;
IF hm_z_exe THEN
    z_tgt := 10;
ELSIF pl_step <> 0 THEN
    z_tgt := pl_tz;
ELSE
    z_tgt := z_sp;
END_IF;

(* ---- interpolation engines (trigger lines held at level during sequence) ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, hold_req := hold, pos_target := x_tgt, v_max := 38.0);
ix_y(fire := go_y_exe OR hm_y_exe, hold_req := hold, pos_target := y_tgt, v_max := 38.0);
ix_z(fire := go_z_exe OR hm_z_exe, hold_req := hold, pos_target := z_tgt, v_max := 20.0);

(* ---- clear trigger lines only outside the draw sequence ---- *)
IF ix_x.Done AND (pl_step = 0) THEN
    go_x_exe := FALSE;
    hm_x_exe := FALSE;
END_IF;
IF ix_y.Done AND (pl_step = 0) THEN
    go_y_exe := FALSE;
    hm_y_exe := FALSE;
END_IF;
IF ix_z.Done AND (pl_step = 0) THEN
    go_z_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

(* ---- setpoint routing: jog override on X ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives follow interpolated setpoints ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);

x_sww := dv_x.sw;
y_sww := dv_y.sw;
z_sww := dv_z.sw;
x_sw := WORD_TO_INT(x_sww);
y_sw := WORD_TO_INT(y_sww);
z_sw := WORD_TO_INT(z_sww);

(* ---- C1: quickstop/fault/run-drop forces velocity outputs to zero ---- *)
IF quickstop OR fault_any OR (NOT run) THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    x_v := dv_x.v_cmd;
    y_v := dv_y.v_cmd;
    z_v := dv_z.v_cmd;
END_IF;

(* ---- application status ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := (pl_step = 0) AND go_x.Done AND go_y.Done AND go_z.Done;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
