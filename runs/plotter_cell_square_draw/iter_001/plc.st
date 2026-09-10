FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL;
    ACCEL : REAL;
    POSWIN : REAL;
    MAXPOS : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    SCAN_T : REAL := 0.02;
    pos : REAL;
    tgt : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
    edge_ir : BOOL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: no move, stay *)
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    (* continuous tracking: new target starts new trajectory while fire held *)
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
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
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT := 0;
    cmd : WORD;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
  END_VAR
(* DRIVE402: CSP follow + position loop. state: 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA *)
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

(* position loop: P control + setpoint differential feedforward *)
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
    tx : INT;
    ty : INT;
    tz : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT := 3;
    pl_step : INT := 0;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    edge_draw : BOOL;
    fault_latch : BOOL;
    z_up_seq : BOOL;
    man_ok : BOOL;
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

(* ---- scan head: sample drive status words ---- *)
x_sww := dv_x.sw;
y_sww := dv_y.sw;
z_sww := dv_z.sw;

(* ---- pen state: pure physical detection ---- *)
pen_down := z_fb <= 2;

(* ---- fault latch (C6), reset via cmd_reset ---- *)
IF (x_sww AND 16#0008) <> 0 OR (y_sww AND 16#0008) <> 0 OR (z_sww AND 16#0008) <> 0 THEN
    fault_latch := TRUE;
END_IF;
IF cmd_reset AND NOT quickstop THEN
    fault_latch := FALSE;
END_IF;

(* ---- abort conditions: quickstop / run off / fault (C4,C5,C6) ---- *)
IF quickstop OR NOT run OR fault_latch THEN
    pl_step := 0;
    z_up_seq := FALSE;
    go_x_exe := FALSE; go_y_exe := FALSE; go_z_exe := FALSE;
    hm_x_exe := FALSE; hm_y_exe := FALSE; hm_z_exe := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- start plot sequence on cmd_draw rising edge (C4: only when all axes enabled) ---- *)
IF cmd_draw AND NOT edge_draw THEN
    IF all_oe AND NOT quickstop AND NOT fault_latch AND pl_step = 0 THEN
        plot_done := FALSE;
        z_up_seq := FALSE;
        pl_step := 1;
    END_IF;
END_IF;
edge_draw := cmd_draw;

(* ---- C3: in step1 XY long move only after pen up (z_fb >= 9) ---- *)
IF pl_step = 1 AND z_fb >= 9 AND NOT ix_z.Busy AND ix_z.Done THEN
    z_up_seq := TRUE;
END_IF;

(* ---- sequence step table (deterministic plan) ---- *)
CASE pl_step OF
    1: pl_tx := 20; pl_ty := 20; pl_tz := 10;   (* pen_up_move *)
    2: pl_tx := 20; pl_ty := 20; pl_tz := 0;    (* pen_down_move: C8 XY already still and positioned *)
    3: pl_tx := 80; pl_ty := 20; pl_tz := 0;    (* edge +X *)
    4: pl_tx := 80; pl_ty := 80; pl_tz := 0;    (* edge +Y *)
    5: pl_tx := 20; pl_ty := 80; pl_tz := 0;    (* edge -X *)
    6: pl_tx := 20; pl_ty := 20; pl_tz := 0;    (* edge -Y close *)
    7: pl_tx := 20; pl_ty := 20; pl_tz := 10;   (* pen up *)
    8: pl_tx := 50; pl_ty := 50; pl_tz := 10;   (* return center *)
    ELSE pl_tx := pl_tx;
END_CASE;

(* ---- step advance: all interpolators idle and Done (POSWIN XY=2.0 Z=1.0, C7) ---- *)
IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step >= 8 THEN
        plot_done := TRUE;
        pl_step := 0;
        z_up_seq := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

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

(* ---- manual command gates (C1: no XY go while pen down; C2: no jog while pen down) ---- *)
man_ok := (pl_step = 0) AND NOT quickstop AND NOT fault_latch AND all_oe AND NOT jg_x.Busy;

go_x(fire := cmd_go AND man_ok AND NOT pen_down, Position := x_sp, interp_exe := go_x_exe);
go_y(fire := cmd_go AND man_ok AND NOT pen_down, Position := y_sp, interp_exe := go_y_exe);
go_z(fire := cmd_go AND man_ok, Position := z_sp, interp_exe := go_z_exe);
hm_x(fire := cmd_home AND (pl_step = 0) AND NOT quickstop AND NOT fault_latch, home_exe := hm_x_exe);
hm_y(fire := cmd_home AND (pl_step = 0) AND NOT quickstop AND NOT fault_latch, home_exe := hm_y_exe);
hm_z(fire := cmd_home AND (pl_step = 0) AND NOT quickstop AND NOT fault_latch, home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd AND man_ok AND NOT pen_down,
     JogBackward := jog_rev AND man_ok AND NOT pen_down,
     jog_pos := x_jog_pos);

(* ---- target mux: sequence / home / manual ---- *)
IF pl_step <> 0 THEN
    tx := pl_tx; ty := pl_ty; tz := pl_tz;
ELSIF hm_x_exe OR hm_y_exe OR hm_z_exe THEN
    tx := 0; ty := 0; tz := 10;
ELSE
    tx := x_sp; ty := y_sp; tz := z_sp;
END_IF;
IF jg_x.Busy THEN
    tx := x_jog_pos;
END_IF;

(* ---- interpolator fire lines: sequence holds level (step1 XY gated by pen-up) ---- *)
fire_x := (pl_step <> 0 AND (pl_step <> 1 OR z_up_seq)) OR go_x_exe OR hm_x_exe;
fire_y := (pl_step <> 0 AND (pl_step <> 1 OR z_up_seq)) OR go_y_exe OR hm_y_exe;
fire_z := (pl_step <> 0) OR go_z_exe OR hm_z_exe;

ix_x(fire := fire_x, pos_target := tx, hold_req := FALSE, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := fire_y, pos_target := ty, hold_req := FALSE, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := fire_z, pos_target := tz, hold_req := FALSE, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0, MAXPOS := 10.0);

(* ---- clear manual trigger lines after completion ---- *)
IF ix_x.Done AND pl_step = 0 AND NOT hm_x_exe THEN go_x_exe := FALSE; END_IF;
IF ix_y.Done AND pl_step = 0 AND NOT hm_y_exe THEN go_y_exe := FALSE; END_IF;
IF ix_z.Done AND pl_step = 0 AND NOT hm_z_exe THEN go_z_exe := FALSE; END_IF;
IF ix_x.Done AND pl_step = 0 THEN hm_x_exe := FALSE; END_IF;
IF ix_y.Done AND pl_step = 0 THEN hm_y_exe := FALSE; END_IF;
IF ix_z.Done AND pl_step = 0 THEN hm_z_exe := FALSE; END_IF;

(* ---- interpolation point routing ---- *)
x_interp := ix_x.Setpoint;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
END_IF;

(* ---- drives: CSP follow + position loop ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- application status outputs ---- *)
x_sw := WORD_TO_INT(x_sww);
y_sw := WORD_TO_INT(y_sww);
z_sw := WORD_TO_INT(z_sww);
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
fault_any := ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := (pl_step = 0) AND NOT pen_down AND NOT quickstop AND NOT fault_latch
             AND NOT jg_x.Busy AND (x_v = 0) AND (y_v = 0) AND (z_v = 0)
             AND go_x.Done AND go_y.Done AND go_z.Done;
IF quickstop THEN
    plot_done := FALSE;
END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
