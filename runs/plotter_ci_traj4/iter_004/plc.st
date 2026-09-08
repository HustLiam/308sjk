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
    VMAX : REAL := 40.0;
    ACCEL : REAL := 60.0;
    POSWIN : REAL := 2.0;
    SCAN_T : REAL := 0.02;
    edge_ir : BOOL;
    tgt : REAL;
    pos : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > 100 OR pos_target < 0 THEN
        (* target out of range: do not start, stay *)
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
END_IF;
edge_ir := fire;

(* continuous tracking: while fire level held, a new target starts a new trajectory *)
IF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
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
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 60.0;
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
    ELSE state := 1;
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
ELSIF state = 3 THEN
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
    ELSE sw := 16#0040;
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
        ELSE pstep := 0;
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
    sw : WORD;
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
  VAR_OUTPUT
    Busy : BOOL;
  END_VAR
  VAR
    jog_pos : INT := 3;
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
    Done : BOOL;
  END_VAR
  VAR
    home_exe : BOOL;
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
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    jg_x : MC_MOVEJOG;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_sww : WORD;
    y_sww : WORD;
    z_sww : WORD;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    edge_hm : BOOL;
    fault_now : BOOL;
    z_lift : BOOL;
    pl_step : INT := 0;
    pl_k : INT := 1;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    x_t : INT;
    y_t : INT;
    z_t : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT;
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

(* ---- sample drive status words ---- *)
x_sww := dv_x.sw;
y_sww := dv_y.sw;
z_sww := dv_z.sw;
x_sw := WORD_TO_INT(x_sww);
y_sw := WORD_TO_INT(y_sww);
z_sw := WORD_TO_INT(z_sww);

fault_now := (x_sww AND 16#0008) <> 0 OR (y_sww AND 16#0008) <> 0 OR (z_sww AND 16#0008) <> 0;
fault_any := fault_now;

(* ---- abort: quickstop / fault / power-off (C2 C6 C7) ---- *)
IF quickstop OR fault_now OR NOT run THEN
    IF pl_step <> 0 THEN
        pl_step := 0;
        pl_k := 1;
        plot_done := FALSE;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        hm_x_exe := FALSE;
        hm_y_exe := FALSE;
        hm_z_exe := FALSE;
    END_IF;
    IF quickstop THEN
        z_lift := TRUE;   (* lift pen request, level-held until pen up *)
    END_IF;
END_IF;

(* ---- cmd_draw accept: needs all_oe (C3); pen-lift handled as step 1 ---- *)
IF cmd_draw AND NOT edge_draw AND pl_step = 0 AND all_oe
   AND NOT fault_now AND NOT quickstop AND run THEN
    pl_step := 1;
    pl_k := 1;
    plot_done := FALSE;
    pl_tx := x_fb; pl_ty := y_fb; pl_tz := 10;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
END_IF;
edge_draw := cmd_draw;

(* ---- manual positioning: refused while pen down (C1) ---- *)
IF cmd_go AND NOT edge_go AND pl_step = 0 AND NOT pen_down
   AND NOT quickstop AND NOT fault_now AND all_oe THEN
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
END_IF;
edge_go := cmd_go;

(* ---- homing: Z reference = pen-up safe position ---- *)
IF cmd_home AND NOT edge_hm AND pl_step = 0 AND NOT quickstop AND NOT fault_now THEN
    hm_x_exe := TRUE;
    hm_y_exe := TRUE;
    hm_z_exe := TRUE;
END_IF;
edge_hm := cmd_home;

(* ---- sequencer step table ---- *)
CASE pl_step OF
    0: pl_step := pl_step;
    1: pl_tx := x_fb; pl_ty := y_fb; pl_tz := 10;   (* lift pen in place first *)
    2: pl_tx := 50; pl_ty := 25; pl_tz := 10;       (* pen-up move to circle start *)
    3: pl_tx := 50; pl_ty := 25; pl_tz := 0;        (* pen down *)
    4: pl_tz := 0;                                  (* 24-segment circle sweep *)
       CASE pl_k OF
           1: pl_tx := 56; pl_ty := 26;
           2: pl_tx := 63; pl_ty := 28;
           3: pl_tx := 68; pl_ty := 32;
           4: pl_tx := 72; pl_ty := 38;
           5: pl_tx := 74; pl_ty := 44;
           6: pl_tx := 75; pl_ty := 50;
           7: pl_tx := 74; pl_ty := 57;
           8: pl_tx := 72; pl_ty := 63;
           9: pl_tx := 68; pl_ty := 68;
          10: pl_tx := 63; pl_ty := 72;
          11: pl_tx := 56; pl_ty := 74;
          12: pl_tx := 50; pl_ty := 75;
          13: pl_tx := 44; pl_ty := 74;
          14: pl_tx := 38; pl_ty := 72;
          15: pl_tx := 32; pl_ty := 68;
          16: pl_tx := 28; pl_ty := 63;
          17: pl_tx := 26; pl_ty := 57;
          18: pl_tx := 25; pl_ty := 50;
          19: pl_tx := 26; pl_ty := 44;
          20: pl_tx := 28; pl_ty := 38;
          21: pl_tx := 32; pl_ty := 32;
          22: pl_tx := 38; pl_ty := 28;
          23: pl_tx := 44; pl_ty := 26;
          24: pl_tx := 50; pl_ty := 25;              (* close back to start *)
          ELSE pl_tx := 50; pl_ty := 25;
       END_CASE;
    5: pl_tx := 50; pl_ty := 25; pl_tz := 10;        (* pen up *)
    6: pl_tx := 50; pl_ty := 50; pl_tz := 10;        (* return to circle center *)
    ELSE pl_step := 0;
END_CASE;

(* ---- step advance: all axes settled ---- *)
IF pl_step <> 0 AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    CASE pl_step OF
        1: pl_step := 2;
        2: pl_step := 3;
        3: pl_step := 4;
        4: IF pl_k >= 24 THEN pl_step := 5; ELSE pl_k := pl_k + 1; END_IF;
        5: pl_step := 6;
        6: plot_done := TRUE;
           pl_step := 0;
           go_x_exe := FALSE;
           go_y_exe := FALSE;
           go_z_exe := FALSE;
        ELSE pl_step := 0;
    END_CASE;
END_IF;

(* ---- target mux ---- *)
IF hm_x_exe OR hm_y_exe OR hm_z_exe THEN
    x_t := 0; y_t := 0; z_t := 10;
ELSIF pl_step <> 0 THEN
    x_t := pl_tx; y_t := pl_ty; z_t := pl_tz;
ELSE
    x_t := x_sp; y_t := y_sp; z_t := z_sp;
END_IF;

(* ---- quickstop pen-lift override (C2) ---- *)
IF z_lift THEN
    z_t := 10;
    go_z_exe := TRUE;
    IF ix_z.Done AND (z_fb >= 8) THEN
        z_lift := FALSE;
        go_z_exe := FALSE;
    END_IF;
END_IF;

(* ---- enable / reset / quickstop (quickstop last = highest priority) ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_sww);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_sww);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_sww);
rs_x(fire := cmd_reset AND fault_now, cw := x_cw, sw := x_sww);
rs_y(fire := cmd_reset AND fault_now, cw := y_cw, sw := y_sww);
rs_z(fire := cmd_reset AND fault_now, cw := z_cw, sw := z_sww);
st_x(fire := quickstop, cw := x_cw, sw := x_sww);
st_y(fire := quickstop, cw := y_cw, sw := y_sww);
st_z(fire := quickstop, cw := z_cw, sw := z_sww);

(* ---- interpolators: level-held fire, continuous tracking ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_t, hold_req := FALSE);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_t, hold_req := FALSE);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_t, hold_req := FALSE);

(* ---- clear trigger lines only when idle ---- *)
IF pl_step = 0 THEN
    IF ix_x.Done THEN go_x_exe := FALSE; hm_x_exe := FALSE; END_IF;
    IF ix_y.Done THEN go_y_exe := FALSE; hm_y_exe := FALSE; END_IF;
    IF ix_z.Done AND NOT z_lift THEN go_z_exe := FALSE; hm_z_exe := FALSE; END_IF;
END_IF;

(* ---- jog: pen-interlocked, draw-interlocked (C1) ---- *)
IF jog_fwd AND NOT pen_down AND (pl_step = 0) AND NOT quickstop AND NOT fault_now THEN
    jg_x(JogForward := TRUE, JogBackward := FALSE);
ELSIF jog_rev AND NOT pen_down AND (pl_step = 0) AND NOT quickstop AND NOT fault_now THEN
    jg_x(JogForward := FALSE, JogBackward := TRUE);
ELSE
    jg_x(JogForward := FALSE, JogBackward := FALSE);
END_IF;

IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- application outputs ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := (pl_step = 0) AND NOT go_x_exe AND NOT go_y_exe AND NOT go_z_exe
             AND NOT hm_x_exe AND NOT z_lift
             AND ix_x.Done AND ix_y.Done AND ix_z.Done;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
pen_down := z_fb <= 2;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
