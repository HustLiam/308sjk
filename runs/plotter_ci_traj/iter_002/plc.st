FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL;
    ACCEL : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    edge_ir : BOOL;
    tgt : REAL := 0.0;
    pos : REAL := 0.0;
    vel : REAL := 0.0;
    stop_d : REAL := 0.0;
    dist : REAL := 0.0;
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
        IF (vel = 0.0) AND (ABS(dist) <= 2.0) THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(VMAX, vel + ACCEL * 0.02);
        ELSE vel := MAX(-VMAX, vel - ACCEL * 0.02);
        END_IF;
    END_IF;
ELSIF fire AND (INT_TO_REAL(pos_target) <> tgt) AND (pos_target <= 100) AND (pos_target >= 0) THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;

pos := pos + vel * 0.02;
Setpoint := REAL_TO_INT(pos);
END_FUNCTION_BLOCK

FUNCTION_BLOCK DRIVE402
  VAR_INPUT
    cw : WORD;
    Setpoint : INT;
    pos_fb : INT;
    QS_DECEL : REAL;
  END_VAR
  VAR_OUTPUT
    sw : WORD;
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    sp_prev : INT := 0;
  END_VAR
(* state: 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA *)
cmd := cw AND 16#000F;

IF (state = 7) AND ((cw AND 16#0080) <> 0) THEN
    state := 1;
END_IF;

CASE state OF
    1: IF cmd = 16#0006 THEN state := 2; END_IF;
    2: IF cmd = 16#0007 THEN state := 3;
       ELSIF cmd = 16#000F THEN state := 4;
       ELSIF cmd = 16#0000 THEN state := 1;
       END_IF;
    3: IF cmd = 16#000F THEN state := 4;
       ELSIF cmd = 16#0006 THEN state := 2;
       ELSIF cmd = 16#0000 THEN state := 1;
       ELSIF cmd = 16#0002 THEN state := 5;
       END_IF;
    4: IF cmd = 16#0007 THEN state := 3;
       ELSIF cmd = 16#0006 THEN state := 2;
       ELSIF cmd = 16#0000 THEN state := 1;
       ELSIF cmd = 16#0002 THEN state := 5;
       END_IF;
    5: IF v_out = 0.0 THEN
           IF cmd = 16#000F THEN state := 4;
           ELSIF cmd = 16#0006 THEN state := 2;
           ELSIF cmd = 16#0000 THEN state := 1;
           END_IF;
       END_IF;
    6: IF v_out = 0.0 THEN state := 7; END_IF;
ELSE
    state := 1;
END_CASE;

IF state = 5 THEN
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - QS_DECEL * 0.02);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + QS_DECEL * 0.02);
    END_IF;
ELSIF (state = 4) OR (state = 6) THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / 0.02;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + 8.0 * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF (v_ff = 0.0) AND (ABS(pos_err) <= 1.5) THEN
        v_out := 0.0;
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
    6: sw := 16#0012;
    7: sw := 16#0018;
ELSE
    sw := 16#0040;
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
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
  END_VAR
  VAR
    pstep : INT := 0;
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
    ELSE
        pstep := 0;
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

PROGRAM PLC_PRG
  VAR
    draw_prev : BOOL;
    go_prev : BOOL;
    draw_edge : BOOL;
    go_edge : BOOL;
    qs_latch : BOOL;
    pl_step : INT := 0;
    idx : INT := 0;
    pl_tx : INT := 0;
    pl_ty : INT := 0;
    pl_tz : INT := 0;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    x_t : INT;
    y_t : INT;
    z_t : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT := 0;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    swx : WORD;
    swy : WORD;
    swz : WORD;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
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

(* ---- scan driver status words ---- *)
swx := dv_x.sw;
swy := dv_y.sw;
swz := dv_z.sw;

(* ---- pen state & fault summary (C1/C4) ---- *)
pen_down := (z_fb <= 2);
fault_any := ((swx AND 16#0008) <> 0) OR ((swy AND 16#0008) <> 0) OR ((swz AND 16#0008) <> 0);

(* ---- command edges ---- *)
draw_edge := cmd_draw AND NOT draw_prev;
go_edge := cmd_go AND NOT go_prev;
draw_prev := cmd_draw;
go_prev := cmd_go;

(* ---- C3/C4: abort sequence on quickstop or fault (level-held, every scan) ---- *)
IF quickstop OR fault_any THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;
qs_latch := qs_latch OR quickstop;
IF (NOT quickstop) AND cmd_reset AND (NOT fault_any) THEN
    qs_latch := FALSE;
END_IF;

(* ---- enable / reset / quickstop (quickstop last = highest priority) ---- *)
pwr_x(Enable := run AND (NOT quickstop), cw := x_cw, sw := swx);
pwr_y(Enable := run AND (NOT quickstop), cw := y_cw, sw := swy);
pwr_z(Enable := run AND (NOT quickstop), cw := z_cw, sw := swz);
rs_x(fire := cmd_reset, cw := x_cw, sw := swx);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* ---- idle flag: sequence not running (0=idle, 7=finished) ---- *)

(* ---- MC command layer: manual moves gated by pen interlock (C1) & readiness (C4/C5) ---- *)
go_x(fire := go_edge AND all_oe AND (NOT pen_down) AND (NOT fault_any) AND (NOT quickstop) AND ((pl_step = 0) OR (pl_step = 7)), interp_exe := go_x_exe);
go_y(fire := go_edge AND all_oe AND (NOT pen_down) AND (NOT fault_any) AND (NOT quickstop) AND ((pl_step = 0) OR (pl_step = 7)), interp_exe := go_y_exe);
go_z(fire := go_edge AND all_oe AND (NOT fault_any) AND (NOT quickstop) AND ((pl_step = 0) OR (pl_step = 7)), interp_exe := go_z_exe);
hm_x(fire := cmd_home AND all_oe AND (NOT fault_any) AND (NOT quickstop), home_exe := hm_x_exe);
hm_y(fire := cmd_home AND all_oe AND (NOT fault_any) AND (NOT quickstop), home_exe := hm_y_exe);
hm_z(fire := cmd_home AND all_oe AND (NOT fault_any) AND (NOT quickstop), home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd AND (NOT pen_down) AND (NOT fault_any) AND (NOT quickstop) AND ((pl_step = 0) OR (pl_step = 7)),
     JogBackward := jog_rev AND (NOT pen_down) AND (NOT fault_any) AND (NOT quickstop) AND ((pl_step = 0) OR (pl_step = 7)),
     jog_pos := x_jog_pos);

(* ---- draw sequence trigger (C5: only when all_oe and no fault; C3: after reset; re-run allowed from idle/finished) ---- *)
IF draw_edge AND all_oe AND (NOT fault_any) AND (NOT quickstop) AND (NOT qs_latch) AND ((pl_step = 0) OR (pl_step = 7)) THEN
    pl_step := 1;
    idx := 0;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
    pl_tx := x_fb;
    pl_ty := y_fb;
    pl_tz := 10;
END_IF;

(* ---- sequencer: 1=raise pen 2=XY to start 3=pen down 4=24-seg sweep 5=pen up 6=back to center 7=finished ---- *)
CASE pl_step OF
    1: (* raise pen to safe position first (C2) *)
       pl_tx := x_fb;
       pl_ty := y_fb;
       pl_tz := 10;
    2: (* XY positioning only allowed once pen confirmed up (C2) *)
       IF z_fb >= 9 THEN
           pl_tx := 50;
           pl_ty := 25;
           pl_tz := 10;
       ELSE
           pl_tz := 10;
       END_IF;
    3: (* pen down only after XY in position (C2) *)
       pl_tx := 50;
       pl_ty := 25;
       pl_tz := 0;
    4: (* 24-segment polyline sweep, waypoints per step table (C6) *)
       pl_tz := 0;
       CASE idx OF
           1:  pl_tx := 57; pl_ty := 26;
           2:  pl_tx := 63; pl_ty := 28;
           3:  pl_tx := 68; pl_ty := 32;
           4:  pl_tx := 72; pl_ty := 38;
           5:  pl_tx := 74; pl_ty := 44;
           6:  pl_tx := 75; pl_ty := 50;
           7:  pl_tx := 74; pl_ty := 57;
           8:  pl_tx := 72; pl_ty := 63;
           9:  pl_tx := 68; pl_ty := 68;
           10: pl_tx := 63; pl_ty := 72;
           11: pl_tx := 57; pl_ty := 74;
           12: pl_tx := 50; pl_ty := 75;
           13: pl_tx := 43; pl_ty := 74;
           14: pl_tx := 38; pl_ty := 72;
           15: pl_tx := 32; pl_ty := 68;
           16: pl_tx := 28; pl_ty := 63;
           17: pl_tx := 26; pl_ty := 57;
           18: pl_tx := 25; pl_ty := 50;
           19: pl_tx := 26; pl_ty := 44;
           20: pl_tx := 28; pl_ty := 38;
           21: pl_tx := 32; pl_ty := 32;
           22: pl_tx := 38; pl_ty := 28;
           23: pl_tx := 43; pl_ty := 26;
           24: pl_tx := 50; pl_ty := 25;
       ELSE
           pl_tx := 50;
           pl_ty := 25;
       END_CASE;
    5: (* pen up *)
       pl_tz := 10;
    6: (* back to circle center, pen up *)
       pl_tx := 50;
       pl_ty := 50;
       pl_tz := 10;
    7: (* finished: hold state, release trigger lines (level, every scan) *)
       go_x_exe := FALSE;
       go_y_exe := FALSE;
       go_z_exe := FALSE;
ELSE
    pl_step := pl_step;
END_CASE;

(* ---- step advance: only for running steps 1..6; all three interpolators idle and done ---- *)
IF (pl_step >= 1) AND (pl_step <= 6) AND (NOT quickstop) AND (NOT fault_any)
   AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    CASE pl_step OF
        1: IF z_fb >= 9 THEN pl_step := 2; END_IF;
        2: IF (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 25) <= 2) THEN pl_step := 3; END_IF;
        3: IF z_fb <= 2 THEN pl_step := 4; idx := 1; END_IF;
        4: IF idx >= 24 THEN
               pl_step := 5;
           ELSE
               idx := idx + 1;
           END_IF;
        5: IF z_fb >= 9 THEN pl_step := 6; END_IF;
        6: IF (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 50) <= 2) THEN
               pl_step := 7;
           END_IF;
    ELSE
        pl_step := 0;
    END_CASE;
END_IF;

(* ---- target routing: sequence owns targets while plotting, else manual setpoints ---- *)
IF (pl_step >= 1) AND (pl_step <= 6) THEN
    x_t := pl_tx;
    y_t := pl_ty;
    z_t := pl_tz;
ELSE
    x_t := x_sp;
    y_t := y_sp;
    z_t := z_sp;
END_IF;

(* ---- interpolators: hold_req gives controlled decel on quickstop (C3: 80/80/60) ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_t, hold_req := quickstop, VMAX := 40.0, ACCEL := 80.0);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_t, hold_req := quickstop, VMAX := 40.0, ACCEL := 80.0);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_t, hold_req := quickstop, VMAX := 20.0, ACCEL := 60.0);

(* ---- clear manual trigger lines once interpolators finished (idle/finished only) ---- *)
IF ix_x.Done AND (NOT ix_x.Busy) AND ((pl_step = 0) OR (pl_step = 7)) THEN
    go_x_exe := FALSE;
    hm_x_exe := FALSE;
END_IF;
IF ix_y.Done AND (NOT ix_y.Busy) AND ((pl_step = 0) OR (pl_step = 7)) THEN
    go_y_exe := FALSE;
    hm_y_exe := FALSE;
END_IF;
IF ix_z.Done AND (NOT ix_z.Busy) AND ((pl_step = 0) OR (pl_step = 7)) THEN
    go_z_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

(* ---- interpolator point routing: jog overrides X while jogging ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives: follow interpolator points, position loop ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb, QS_DECEL := 80.0);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb, QS_DECEL := 80.0);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb, QS_DECEL := 60.0);
z_v := dv_z.v_cmd;

(* ---- application status outputs ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
(* plot_done as level expression: TRUE only in finished state, extinguished by quickstop/fault (C3/C4) *)
plot_done := (pl_step = 7) AND (NOT quickstop) AND (NOT fault_any);
move_done := go_x.Done AND go_y.Done AND go_z.Done AND ((pl_step = 0) OR (pl_step = 7)) AND (NOT quickstop);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
x_sw := WORD_TO_INT(swx);
y_sw := WORD_TO_INT(swy);
z_sw := WORD_TO_INT(swz);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
