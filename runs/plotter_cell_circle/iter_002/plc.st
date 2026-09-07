FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL;
    ACCEL : REAL;
    POSWIN : REAL;
    MAXPOS : INT;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
    Done : BOOL;
    Setpoint : INT;
  END_VAR
  VAR
    edge_ir : BOOL;
    tgt : REAL;
    pos : REAL;
    vel : REAL;
    stop_d : REAL;
    dist : REAL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
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
        ELSE vel := MAX(-VMAX, vel - ACCEL * 0.02);
        END_IF;
    END_IF;
END_IF;

pos := pos + vel * 0.02;
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
    dcmd : INT;
    v_ff : REAL;
    pos_err : REAL;
    v_out : REAL;
    sp_prev : INT;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
  END_VAR
dcmd := cw AND 16#000F;

IF state = 7 AND (cw AND 16#0080) <> 0 THEN
    state := 1;
END_IF;

CASE state OF
    1: IF dcmd = 16#06 THEN state := 2; END_IF;
    2: IF dcmd = 16#07 THEN state := 3;
       ELSIF dcmd = 16#0F THEN state := 4;
       ELSIF dcmd = 16#00 THEN state := 1;
       END_IF;
    3: IF dcmd = 16#0F THEN state := 4;
       ELSIF dcmd = 16#06 THEN state := 2;
       ELSIF dcmd = 16#00 THEN state := 1;
       ELSIF dcmd = 16#02 THEN state := 5;
       END_IF;
    4: IF dcmd = 16#07 THEN state := 3;
       ELSIF dcmd = 16#06 THEN state := 2;
       ELSIF dcmd = 16#00 THEN state := 1;
       ELSIF dcmd = 16#02 THEN state := 5;
       END_IF;
    5: IF v_out = 0.0 THEN
           IF dcmd = 16#0F THEN state := 4;
           ELSIF dcmd = 16#06 THEN state := 2;
           ELSIF dcmd = 16#00 THEN state := 1;
           END_IF;
       END_IF;
    6: IF v_out = 0.0 THEN state := 7; END_IF;
END_CASE;

IF state >= 4 AND state <= 6 THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / 0.02;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF v_ff = 0.0 AND ABS(pos_err) <= 1.5 THEN
        v_out := 0.0;
    END_IF;
ELSIF state = 5 THEN
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - QS_DECEL * 0.02);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + QS_DECEL * 0.02);
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
    cw : INT;
    sw : INT;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
  END_VAR
  VAR
    pstep : INT;
  END_VAR
IF Enable AND (pstep = 4) AND (sw AND 16#0004) = 0 AND (sw AND 16#0008) = 0 THEN
    pstep := 0;
END_IF;
IF Enable THEN
    CASE pstep OF
        0: cw := (cw AND 16#00F0) OR 16#0006; pstep := 1;
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
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    edge_home : BOOL;
    pl_step : INT;
    seg_idx : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    ang : REAL;
    seq_act : BOOL;
    pen_up : BOOL;
    jg_active : BOOL;
    x_cw : INT;
    y_cw : INT;
    z_cw : INT;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT;
  END_VAR
  VAR
    prog_id AT %QW20 : INT := 3;
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
    x_fb AT %QW10 : INT;
    y_fb AT %QW11 : INT;
    z_fb AT %QW12 : INT;
    x_sp AT %QW13 : INT;
    y_sp AT %QW14 : INT;
    z_sp AT %QW15 : INT;
    x_sw AT %QW16 : INT;
    y_sw AT %QW17 : INT;
    z_sw AT %QW18 : INT;
    x_v AT %QW19 : INT;
    y_v AT %QW21 : INT;
    z_v AT %QW22 : INT;
  END_VAR
(* ---- program identity ---- *)
prog_id := 3;

(* ---- scan head: sample drive status words ---- *)
x_sw := dv_x.sw;
y_sw := dv_y.sw;
z_sw := dv_z.sw;

(* ---- fault flag (C4) ---- *)
fault_any := ((x_sw AND 16#0008) <> 0) OR ((y_sw AND 16#0008) <> 0) OR ((z_sw AND 16#0008) <> 0);

(* ---- pen state (C2) ---- *)
pen_up := z_fb >= 8;
pen_down := z_fb <= 2;

(* ---- enable / reset / quickstop (quickstop last = highest priority) ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_sw);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_sw);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_sw);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sw);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;

(* ---- abort plot on quickstop / fault (C1, C4) ---- *)
IF (quickstop OR fault_any OR (NOT run)) AND (pl_step <> 0) THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    plot_done := FALSE;
    seq_act := FALSE;
END_IF;

(* ---- draw start: rising edge of cmd_draw, pen must be up (C5) ---- *)
IF cmd_draw AND NOT edge_draw THEN
    IF run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND pen_up AND (pl_step = 0) THEN
        plot_done := FALSE;
        pl_tx := 50;
        pl_ty := 25;
        pl_tz := 10;
        go_x_exe := TRUE;
        go_y_exe := TRUE;
        go_z_exe := TRUE;
        seq_act := TRUE;
        pl_step := 2;
    END_IF;
END_IF;
edge_draw := cmd_draw;

(* ---- plot sequencer targets ---- *)
(* step 2: pen down to 0 ; step 3: circle arc (36 segments, center 50,50 r=25) *)
(* step 4: pen up to 10 ; step 5: XY back to center 50,50 *)
CASE pl_step OF
    2: pl_tz := 0;
    3: ang := INT_TO_REAL(seg_idx) * 0.1745329;
       pl_tx := REAL_TO_INT(50.0 + 25.0 * COS(ang));
       pl_ty := REAL_TO_INT(50.0 + 25.0 * SIN(ang));
    4: pl_tz := 10;
    5: pl_tx := 50;
       pl_ty := 50;
END_CASE;

(* ---- segment / step advance when all axes at target ---- *)
IF (pl_step <> 0) AND seq_act AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    CASE pl_step OF
        2: pl_step := 3;
           seg_idx := 1;
           seq_act := FALSE;
        3: IF seg_idx >= 36 THEN
               pl_step := 4;
           ELSE
               seg_idx := seg_idx + 1;
           END_IF;
           seq_act := FALSE;
        4: pl_step := 5;
           seq_act := FALSE;
        5: plot_done := TRUE;
           pl_step := 0;
           go_x_exe := FALSE;
           go_y_exe := FALSE;
           go_z_exe := FALSE;
           seq_act := FALSE;
    END_CASE;
END_IF;

(* ---- motion-in-progress monitor for step sequencing ---- *)
IF (pl_step <> 0) AND (ix_x.Busy OR ix_y.Busy OR ix_z.Busy) THEN
    seq_act := TRUE;
END_IF;

(* ---- manual positioning: gated by pen_up interlock (C2) ---- *)
IF cmd_go AND NOT edge_go THEN
    IF run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND pen_up AND (pl_step = 0) THEN
        go_x_exe := TRUE;
        go_y_exe := TRUE;
        go_z_exe := TRUE;
    END_IF;
END_IF;
edge_go := cmd_go;

(* ---- homing: Z reference = pen-up safe position 10 ---- *)
IF cmd_home AND NOT edge_home THEN
    IF run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND (pl_step = 0) THEN
        hm_x_exe := TRUE;
        hm_y_exe := TRUE;
        hm_z_exe := TRUE;
    END_IF;
END_IF;
edge_home := cmd_home;

(* ---- X jog: level active, only with pen up (C2) ---- *)
jg_active := (jog_fwd OR jog_rev) AND pen_up AND (pl_step = 0) AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any);
IF jg_active AND jog_fwd AND (x_jog_pos < 97) THEN
    x_jog_pos := x_jog_pos + 1;
END_IF;
IF jg_active AND jog_rev AND (x_jog_pos > 3) THEN
    x_jog_pos := x_jog_pos - 1;
END_IF;

(* ---- target routing: plot > home > manual ---- *)
IF pl_step <> 0 THEN
    x_tgt := pl_tx;
    y_tgt := pl_ty;
    z_tgt := pl_tz;
ELSIF hm_x_exe OR hm_y_exe OR hm_z_exe THEN
    x_tgt := 0;
    y_tgt := 0;
    z_tgt := 10;
ELSE
    x_tgt := x_sp;
    y_tgt := y_sp;
    z_tgt := z_sp;
END_IF;

(* ---- interpolators: XY 40%/s 80%/s^2, Z 20mm/s (C5) ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_tgt, hold_req := FALSE,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_tgt, hold_req := FALSE,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_tgt, hold_req := FALSE,
     VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10);

(* ---- clear manual / home exe lines when finished ---- *)
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

(* ---- setpoint routing: jog overrides interpolated X ---- *)
IF jg_active THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives follow interpolated setpoints ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- status lamps (C6) ---- *)
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := (pl_step = 0) AND (NOT go_x_exe) AND (NOT hm_x_exe)
             AND ix_x.Done AND ix_y.Done AND ix_z.Done AND all_oe;

(* ---- hard velocity gate: quickstop / fault / no-run force zero (C1, C3, C4) ---- *)
IF quickstop OR fault_any OR (NOT run) THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
    any_moving := FALSE;
END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
