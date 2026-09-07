FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    hold_req : BOOL;
    pos_target : INT;
    vmax_in : REAL;
    acc_in : REAL;
    poswin_in : REAL;
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
  END_VAR
(* trigger: start new move when idle *)
IF fire AND NOT edge_ir AND NOT Busy THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;
(* continuous tracking: while fire level held, target change starts new move *)
IF Busy AND fire AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Done := FALSE;
END_IF;
edge_ir := fire;

IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - acc_in * SCAN_T);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + acc_in * SCAN_T);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * acc_in);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - acc_in * SCAN_T);
        ELSE vel := MIN(0.0, vel + acc_in * SCAN_T);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= poswin_in THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(vmax_in, vel + acc_in * SCAN_T);
        ELSE vel := MAX(-vmax_in, vel + acc_in * SCAN_T);
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
    state : INT;
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
    cmd : WORD;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 4.0;
    QS_DECEL : REAL := 160.0;
  END_VAR
(* ============ DRIVE402: drive (CSP follow + position loop) ============ *)
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

(* ---- position loop: P control + setpoint diff feedforward ---- *)
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
    x_swi : WORD;
    y_swi : WORD;
    z_swi : WORD;
    x_jog_pos : INT := 50;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    pl_step : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    tgt_x : INT;
    tgt_y : INT;
    tgt_z : INT;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    draw_prev : BOOL;
    draw_active : BOOL;
    pen_up : BOOL;
    go_ok : BOOL;
    hm_ok : BOOL;
    jg_ok : BOOL;
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
    x_fb AT %QW1 : INT;
    y_fb AT %QW2 : INT;
    z_fb AT %QW3 : INT;
    x_sp AT %QW4 : INT;
    y_sp AT %QW5 : INT;
    z_sp AT %QW6 : INT;
    x_sw AT %QW7 : INT;
    y_sw AT %QW8 : INT;
    z_sw AT %QW9 : INT;
    x_v AT %QW10 : INT;
    y_v AT %QW11 : INT;
    z_v AT %QW12 : INT;
    prog_id AT %QW20 : INT := 2;
  END_VAR
(* ================== program identity ================== *)
prog_id := 2;

(* ================== scan head: sample drive status words ================== *)
x_swi := INT_TO_WORD(x_sw);
y_swi := INT_TO_WORD(y_sw);
z_swi := INT_TO_WORD(z_sw);

pen_down := z_fb <= 1;              (* C2: pen on paper, entry tolerance *)
pen_up := z_fb >= 9;
fault_any := ((x_swi AND 16#0008) <> 0) OR ((y_swi AND 16#0008) <> 0) OR ((z_swi AND 16#0008) <> 0);  (* C4 *)

(* ================== enable / reset / quickstop (quickstop last = highest prio) ================== *)
pwr_x(Enable := run AND NOT quickstop AND NOT fault_any, cw := x_cw, sw := x_swi);
pwr_y(Enable := run AND NOT quickstop AND NOT fault_any, cw := y_cw, sw := y_swi);
pwr_z(Enable := run AND NOT quickstop AND NOT fault_any, cw := z_cw, sw := z_swi);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_swi);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_swi);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_swi);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* ================== MC command layer: manual go / home / jog (C2 pen interlock) ================== *)
go_ok := cmd_go AND pen_up AND run AND NOT quickstop AND NOT fault_any AND (pl_step = 0);
hm_ok := cmd_home AND run AND NOT quickstop AND NOT fault_any AND (pl_step = 0);
jg_ok := pen_up AND run AND NOT quickstop AND NOT fault_any AND (pl_step = 0);

go_x(fire := go_ok, Position := x_sp, interp_exe => go_x_exe);
go_y(fire := go_ok, Position := y_sp, interp_exe => go_y_exe);
go_z(fire := go_ok, Position := z_sp, interp_exe => go_z_exe);
hm_x(fire := hm_ok, home_exe => hm_x_exe);
hm_y(fire := hm_ok, home_exe => hm_y_exe);
hm_z(fire := hm_ok, home_exe => hm_z_exe);
jg_x(JogForward := jog_fwd AND jg_ok, JogBackward := jog_rev AND jg_ok, jog_pos := x_jog_pos);

(* ================== draw sequencer (multi-segment, level-held triggers) ================== *)
CASE pl_step OF
    0:  IF cmd_draw AND NOT draw_prev AND run AND all_oe AND NOT quickstop AND NOT fault_any THEN
            (* start: pen up first (C5), hold XY *)
            pl_tx := x_fb; pl_ty := y_fb; pl_tz := 10;
            go_x_exe := TRUE; go_y_exe := TRUE; go_z_exe := TRUE;
            plot_done := FALSE;
            pl_step := 1;
        END_IF;
    1:  pl_tx := 50; pl_ty := 25; pl_tz := 10;   (* XY to circle start, pen up *)
    2:  pl_tx := 50; pl_ty := 25; pl_tz := 0;    (* pen down at start *)
    3:  pl_tx := 60; pl_ty := 27; pl_tz := 0;    (* arc waypoints, ~22.5 deg steps *)
    4:  pl_tx := 68; pl_ty := 32; pl_tz := 0;
    5:  pl_tx := 73; pl_ty := 41; pl_tz := 0;
    6:  pl_tx := 75; pl_ty := 50; pl_tz := 0;
    7:  pl_tx := 73; pl_ty := 59; pl_tz := 0;
    8:  pl_tx := 68; pl_ty := 68; pl_tz := 0;
    9:  pl_tx := 60; pl_ty := 73; pl_tz := 0;
    10: pl_tx := 50; pl_ty := 75; pl_tz := 0;
    11: pl_tx := 41; pl_ty := 73; pl_tz := 0;
    12: pl_tx := 32; pl_ty := 68; pl_tz := 0;
    13: pl_tx := 27; pl_ty := 59; pl_tz := 0;
    14: pl_tx := 25; pl_ty := 50; pl_tz := 0;
    15: pl_tx := 27; pl_ty := 41; pl_tz := 0;
    16: pl_tx := 32; pl_ty := 32; pl_tz := 0;
    17: pl_tx := 41; pl_ty := 27; pl_tz := 0;
    18: pl_tx := 50; pl_ty := 25; pl_tz := 0;    (* full circle back to start *)
    19: pl_tx := 50; pl_ty := 25; pl_tz := 10;   (* pen up *)
    20: pl_tx := 50; pl_ty := 50; pl_tz := 10;   (* return to circle center *)
END_CASE;

(* ================== target routing ================== *)
IF pl_step <> 0 THEN
    tgt_x := pl_tx; tgt_y := pl_ty; tgt_z := pl_tz;
ELSE
    tgt_x := x_sp; tgt_y := y_sp; tgt_z := z_sp;
END_IF;
IF hm_x_exe THEN tgt_x := 0; END_IF;
IF hm_y_exe THEN tgt_y := 0; END_IF;
IF hm_z_exe THEN tgt_z := 10; END_IF;

(* ================== interpolation engines (continuous tracking) ================== *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := tgt_x, vmax_in := 35.0, acc_in := 80.0, poswin_in := 2.0, hold_req := FALSE);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := tgt_y, vmax_in := 35.0, acc_in := 80.0, poswin_in := 2.0, hold_req := FALSE);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := tgt_z, vmax_in := 20.0, acc_in := 80.0, poswin_in := 1.0, hold_req := FALSE);

(* ================== setpoint routing (jog overrides interp on X) ================== *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ================== drives: CSP follow + position loop ================== *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ================== clear trigger lines when idle (manual moves only) ================== *)
IF (pl_step = 0) AND ix_x.Done THEN go_x_exe := FALSE; hm_x_exe := FALSE; END_IF;
IF (pl_step = 0) AND ix_y.Done THEN go_y_exe := FALSE; hm_y_exe := FALSE; END_IF;
IF (pl_step = 0) AND ix_z.Done THEN go_z_exe := FALSE; hm_z_exe := FALSE; END_IF;

(* ================== sequencer advance: all axes idle & done ================== *)
IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 20 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE; go_y_exe := FALSE; go_z_exe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ================== abort: quickstop / fault / run off (C1/C3/C4) ================== *)
IF quickstop OR fault_any OR NOT run THEN
    IF pl_step <> 0 THEN
        pl_step := 0;
        plot_done := FALSE;
        go_x_exe := FALSE; go_y_exe := FALSE; go_z_exe := FALSE;
    END_IF;
    hm_x_exe := FALSE; hm_y_exe := FALSE; hm_z_exe := FALSE;
END_IF;

(* ================== application status outputs ================== *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
draw_active := pl_step <> 0;
move_done := go_x.Done AND go_y.Done AND go_z.Done AND NOT draw_active;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);

x_sw := WORD_TO_INT(dv_x.sw);
y_sw := WORD_TO_INT(dv_y.sw);
z_sw := WORD_TO_INT(dv_z.sw);

draw_prev := cmd_draw;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
