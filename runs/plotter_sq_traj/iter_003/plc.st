FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    vmax : REAL;
    acc : REAL;
    win : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    tgt : REAL;
    pos : REAL;
    vel : REAL;
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
END_IF;
(* continuous tracking: target change while fire held *)
IF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > win) THEN
    IF pos_target <= 100 AND pos_target >= 0 THEN
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
END_IF;
edge_ir := fire;

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
        IF vel = 0.0 AND ABS(dist) <= win THEN
            pos := tgt;
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
    v_cmd : INT;
    sw : WORD;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
  END_VAR
cmd := cw AND 16#000F;

IF state = 7 AND (cw AND 16#0080) <> 0 THEN
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

IF state >= 4 AND state <= 6 THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / 0.02;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + 40.0 * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF v_ff = 0.0 AND ABS(pos_err) <= 1.5 THEN
        v_out := 0.0;
    END_IF;
ELSIF state = 5 THEN
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - 200.0 * 0.02);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + 200.0 * 0.02);
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
ELSE
    sw := 16#0000;
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
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_sww : WORD;
    y_sww : WORD;
    z_sww : WORD;
    edge_draw : BOOL;
    prev_draw : BOOL;
    edge_reset : BOOL;
    prev_reset : BOOL;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    x_jog_pos : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    pl_step : INT := 0;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    txu : INT;
    tyu : INT;
    tzu : INT;
    mgate : BOOL;
    hold_all : BOOL;
    gate_go : BOOL;
    gate_goz : BOOL;
    gate_hme : BOOL;
    prev_go : BOOL;
    prev_goz : BOOL;
    prev_hme : BOOL;
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
    prog_id AT %QW20 : INT := 2;
  END_VAR
(* ---- program identity ---- *)
prog_id := 2;

(* ---- edge detection ---- *)
edge_draw := cmd_draw AND NOT prev_draw;
prev_draw := cmd_draw;
edge_reset := cmd_reset AND NOT prev_reset;
prev_reset := cmd_reset;

(* ---- status scan ---- *)
x_sww := INT_TO_WORD(x_sw);
y_sww := INT_TO_WORD(y_sw);
z_sww := INT_TO_WORD(z_sw);

fault_any := ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0);
pen_down := (z_fb <= 2);

(* C1: pen-down interlock gate for manual XY motion *)
mgate := NOT pen_down;
hold_all := quickstop OR (NOT run);

(* ---- enable / reset / quickstop (quickstop written last = highest priority) ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_sww);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_sww);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_sww);
rs_x(fire := edge_reset, cw := x_cw, sw := x_sww);
rs_y(fire := edge_reset, cw := y_cw, sw := y_sww);
rs_z(fire := edge_reset, cw := z_cw, sw := z_sww);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* ---- MC command layer (manual) ---- *)
jg_x(JogForward := jog_fwd AND mgate AND run AND NOT quickstop,
     JogBackward := jog_rev AND mgate AND run AND NOT quickstop,
     jog_pos := x_jog_pos);

gate_go := cmd_go AND mgate AND run AND (NOT quickstop) AND (pl_step = 0) AND (NOT fault_any);
gate_goz := cmd_go AND run AND (NOT quickstop) AND (pl_step = 0) AND (NOT fault_any);
gate_hme := cmd_home AND run AND (NOT quickstop) AND (pl_step = 0);

go_x(fire := gate_go, Position := x_sp);
go_y(fire := gate_go, Position := y_sp);
go_z(fire := gate_goz, Position := z_sp);
hm_x(fire := gate_hme);
hm_y(fire := gate_hme);
hm_z(fire := gate_hme);

(* ---- local trigger-edge handling (interp_exe/home_exe are FB outputs) ---- *)
IF gate_go AND NOT prev_go THEN
    go_x_exe := TRUE;
    go_y_exe := TRUE;
END_IF;
prev_go := gate_go;
IF gate_goz AND NOT prev_goz THEN
    go_z_exe := TRUE;
END_IF;
prev_goz := gate_goz;
IF gate_hme AND NOT prev_hme THEN
    hm_x_exe := TRUE;
    hm_y_exe := TRUE;
    hm_z_exe := TRUE;
END_IF;
prev_hme := gate_hme;

(* ---- draw sequencer: step table (targets re-written every scan) ---- *)
CASE pl_step OF
    0: IF edge_draw AND all_oe AND (NOT fault_any) AND (NOT quickstop) AND run THEN
           pl_step := 1;
           plot_done := FALSE;
           go_x_exe := TRUE;
           go_y_exe := TRUE;
           go_z_exe := TRUE;
           hm_x_exe := FALSE;
           hm_y_exe := FALSE;
           hm_z_exe := FALSE;
       END_IF;
    1: pl_tx := 20; pl_ty := 20; pl_tz := 10;   (* pen up, position start *)
    2: pl_tx := 20; pl_ty := 20; pl_tz := 0;    (* pen down *)
    3: pl_tx := 80; pl_ty := 20; pl_tz := 0;    (* edge +X *)
    4: pl_tx := 80; pl_ty := 80; pl_tz := 0;    (* edge +Y *)
    5: pl_tx := 20; pl_ty := 80; pl_tz := 0;    (* edge -X *)
    6: pl_tx := 20; pl_ty := 20; pl_tz := 0;    (* edge -Y, close *)
    7: pl_tx := 20; pl_ty := 20; pl_tz := 10;   (* pen up *)
    8: pl_tx := 50; pl_ty := 50; pl_tz := 10;   (* park center *)
ELSE
    pl_step := 0;
END_CASE;

(* ---- abort: quickstop / run off / any drive fault (C3/C4) ---- *)
IF quickstop OR (NOT run) OR fault_any THEN
    pl_step := 0;
    plot_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

(* ---- target routing to interpolation engines ---- *)
IF pl_step <> 0 THEN
    txu := pl_tx;
    tyu := pl_ty;
    tzu := pl_tz;
ELSE
    txu := x_sp;
    tyu := y_sp;
    IF hm_z_exe THEN
        tzu := 10;   (* Z reference = pen-up safe position *)
    ELSE
        tzu := z_sp;
    END_IF;
END_IF;
IF hm_x_exe THEN txu := 0; END_IF;
IF hm_y_exe THEN tyu := 0; END_IF;

(* ---- interpolation engines (level-held triggers, continuous tracking) ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := txu, hold_req := hold_all,
     vmax := 40.0, acc := 80.0, win := 2.0);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := tyu, hold_req := hold_all,
     vmax := 40.0, acc := 80.0, win := 2.0);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := tzu, hold_req := hold_all,
     vmax := 20.0, acc := 40.0, win := 1.0);

(* ---- clear manual trigger lines once idle ---- *)
IF (pl_step = 0) AND NOT quickstop THEN
    IF ix_x.Done THEN
        go_x_exe := FALSE;
        hm_x_exe := FALSE;
    END_IF;
    IF ix_y.Done THEN
        go_y_exe := FALSE;
        hm_y_exe := FALSE;
    END_IF;
    IF ix_z.Done THEN
        go_z_exe := FALSE;
        hm_z_exe := FALSE;
    END_IF;
END_IF;

(* ---- sequencer advance: all axes settled at target (C5/C2) ---- *)
IF (pl_step <> 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 8 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- interpolation point routing (jog overrides X) ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives: CSP follow + position loop ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_sww := dv_x.sw;
x_v := dv_x.v_cmd;
IF x_v > 40 THEN x_v := 40; END_IF;
IF x_v < -40 THEN x_v := -40; END_IF;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_sww := dv_y.sw;
y_v := dv_y.v_cmd;
IF y_v > 40 THEN y_v := 40; END_IF;
IF y_v < -40 THEN y_v := -40; END_IF;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_sww := dv_z.sw;
z_v := dv_z.v_cmd;
IF z_v > 20 THEN z_v := 20; END_IF;
IF z_v < -20 THEN z_v := -20; END_IF;

(* ---- status outputs ---- *)
x_sw := WORD_TO_INT(x_sww);
y_sw := WORD_TO_INT(y_sww);
z_sw := WORD_TO_INT(z_sww);
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := (pl_step = 0) AND go_x.Done AND go_y.Done AND go_z.Done AND (NOT quickstop) AND all_oe;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
