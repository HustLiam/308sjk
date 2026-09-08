FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    pos_fb : INT;
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
    stop_d : REAL := 0.0;
    dist : REAL := 0.0;
    edge_ir : BOOL;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF ABS(INT_TO_REAL(pos_target) - pos) > 0.499 THEN
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    ELSE
        Done := TRUE;
    END_IF;
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
        ELSE vel := MAX(-VMAX, vel + ACCEL * SCAN_T);
        END_IF;
    END_IF;
ELSE
    (* idle with fire level held: continuous tracking -- new target starts a new move *)
    IF fire AND ABS(INT_TO_REAL(pos_target) - pos) > 0.499 THEN
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    ELSE
        (* glue internal position to real feedback while idle, no divergence *)
        IF ABS(INT_TO_REAL(pos_fb) - pos) <= 2.0 THEN
            pos := INT_TO_REAL(pos_fb);
        END_IF;
        vel := 0.0;
        Done := TRUE;
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
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    sp_prev : INT := 0;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 20.0;
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
ELSE
    state := 1;
END_CASE;

IF state = 4 THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / SCAN_T;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF v_ff = 0.0 AND ABS(pos_err) <= 1.5 THEN
        v_out := 0.0;
    END_IF;
ELSIF state = 5 THEN
    (* quickstop active: controlled decel, position hold *)
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
ELSE
    sw := 16#0040;
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
    x_swm : WORD;
    y_swm : WORD;
    z_swm : WORD;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT := 0;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    pl_step : INT := 0;
    seg : INT := 1;
    x_t : INT := 0;
    y_t : INT := 0;
    z_t : INT := 0;
    x_tgt : INT := 0;
    y_tgt : INT := 0;
    z_tgt : INT := 0;
    go_fire : BOOL;
    home_fire : BOOL;
    jog_fwd_eff : BOOL;
    jog_rev_eff : BOOL;
    cmd_go_eff : BOOL;
    plot_active : BOOL;
    vx_lim : INT := 0;
    vy_lim : INT := 0;
    vz_lim : INT := 0;
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

(* ---- scan head: sample drive status words ---- *)
x_swm := dv_x.sw;
y_swm := dv_y.sw;
z_swm := dv_z.sw;
x_sw := WORD_TO_INT(x_swm);
y_sw := WORD_TO_INT(y_swm);
z_sw := WORD_TO_INT(z_swm);

(* ---- physical pen state: pure physical judgement, no compound conditions ---- *)
pen_down := z_fb <= 2;

(* ---- enable / reset / quickstop (quickstop last = highest priority) ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_swm);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_swm);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_swm);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_swm);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_swm);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_swm);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* ---- C1: manual XY motion forbidden while pen down (z_fb<1) ---- *)
cmd_go_eff := cmd_go AND (z_fb >= 1);
jog_fwd_eff := jog_fwd AND (z_fb >= 1);
jog_rev_eff := jog_rev AND (z_fb >= 1);

plot_active := pl_step <> 0;

(* ---- plot sequencer target selection (level-held, continuous tracking) ---- *)
IF plot_active THEN
    x_tgt := x_t; y_tgt := y_t; z_tgt := z_t;
ELSE
    x_tgt := x_sp; y_tgt := y_sp; z_tgt := z_sp;
END_IF;

(* ---- MC command layer ---- *)
go_fire := cmd_go_eff OR plot_active;
home_fire := cmd_home AND NOT quickstop;
go_x(fire := go_fire, Position := x_tgt, interp_exe := go_x_exe);
go_y(fire := go_fire, Position := y_tgt, interp_exe := go_y_exe);
go_z(fire := go_fire, Position := z_tgt, interp_exe := go_z_exe);
hm_x(fire := home_fire, home_exe := hm_x_exe);
hm_y(fire := home_fire, home_exe := hm_y_exe);
hm_z(fire := home_fire, home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd_eff, JogBackward := jog_rev_eff, jog_pos := x_jog_pos);

(* ---- interpolators: XY vmax 40, Z vmax 20; synced to real feedback ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_tgt, pos_fb := x_fb, hold_req := quickstop,
     VMAX := 40.0, ACCEL := 120.0, POSWIN := 2.0);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_tgt, pos_fb := y_fb, hold_req := quickstop,
     VMAX := 40.0, ACCEL := 120.0, POSWIN := 2.0);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_tgt, pos_fb := z_fb, hold_req := quickstop,
     VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0);

(* ---- clear trigger lines when interp done (manual mode only; during plot the
      exe lines stay level-held and are cleared at completion/abort) ---- *)
IF NOT plot_active THEN
    IF ix_x.Done THEN go_x_exe := FALSE; hm_x_exe := FALSE; END_IF;
    IF ix_y.Done THEN go_y_exe := FALSE; hm_y_exe := FALSE; END_IF;
    IF ix_z.Done THEN go_z_exe := FALSE; hm_z_exe := FALSE; END_IF;
END_IF;

(* ---- plot sequencer (continuous-tracking mode, level-held triggers) ---- *)
(* steps: 1=pen_up_move to (50,25,10); 2=pen_down to (50,25,0);
   3=circle sweep seg 1..24; 4=pen_up to (50,25,10); 5=to center (50,50,10) *)
CASE pl_step OF
    0: IF cmd_draw AND run AND all_oe AND NOT quickstop AND NOT fault_any THEN
           x_t := 50; y_t := 25; z_t := 10;
           x_tgt := 50; y_tgt := 25; z_tgt := 10;
           go_x_exe := TRUE; go_y_exe := TRUE; go_z_exe := TRUE;
           pl_step := 1;
           plot_done := FALSE;
       END_IF;
    1: x_t := 50; y_t := 25; z_t := 10;
    2: x_t := 50; y_t := 25; z_t := 0;
    3: CASE seg OF
           1:  x_t := 56; y_t := 26;
           2:  x_t := 63; y_t := 28;
           3:  x_t := 68; y_t := 32;
           4:  x_t := 72; y_t := 38;
           5:  x_t := 74; y_t := 44;
           6:  x_t := 75; y_t := 50;
           7:  x_t := 74; y_t := 57;
           8:  x_t := 72; y_t := 63;
           9:  x_t := 68; y_t := 68;
           10: x_t := 63; y_t := 72;
           11: x_t := 57; y_t := 74;
           12: x_t := 50; y_t := 75;
           13: x_t := 43; y_t := 74;
           14: x_t := 38; y_t := 72;
           15: x_t := 32; y_t := 68;
           16: x_t := 28; y_t := 63;
           17: x_t := 26; y_t := 57;
           18: x_t := 25; y_t := 50;
           19: x_t := 26; y_t := 44;
           20: x_t := 28; y_t := 38;
           21: x_t := 32; y_t := 32;
           22: x_t := 38; y_t := 28;
           23: x_t := 43; y_t := 26;
           24: x_t := 50; y_t := 25;
       ELSE
           x_t := 50; y_t := 25;
       END_CASE;
       z_t := 0;
    4: x_t := 50; y_t := 25; z_t := 10;
    5: x_t := 50; y_t := 50; z_t := 10;
ELSE
    pl_step := 0;
END_CASE;

(* keep routed targets glued to sequencer output while active *)
IF plot_active THEN
    x_tgt := x_t; y_tgt := y_t; z_tgt := z_t;
END_IF;

(* ---- step advance: all axes idle and done (interp is feedback-glued) ---- *)
IF plot_active AND NOT quickstop AND NOT fault_any
   AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 5 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE; go_y_exe := FALSE; go_z_exe := FALSE;
    ELSIF pl_step = 2 THEN
        pl_step := 3; seg := 1;
    ELSIF pl_step = 3 THEN
        IF seg >= 24 THEN
            pl_step := 4;
        ELSE
            seg := seg + 1;
        END_IF;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- C3/C5: quickstop or fault aborts sequence, plot_done off ---- *)
IF quickstop OR fault_any THEN
    pl_step := 0;
    seg := 1;
    plot_done := FALSE;
    go_x_exe := FALSE; go_y_exe := FALSE; go_z_exe := FALSE;
    hm_x_exe := FALSE; hm_y_exe := FALSE; hm_z_exe := FALSE;
END_IF;

(* ---- interpolation point routing: jog overrides X when jogging ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives: follow interpolation point, position loop ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);

(* ---- C6: velocity clamps: XY 40 %/s, Z 20 mm/s ---- *)
vx_lim := dv_x.v_cmd;
IF vx_lim > 40 THEN vx_lim := 40; END_IF;
IF vx_lim < -40 THEN vx_lim := -40; END_IF;
vy_lim := dv_y.v_cmd;
IF vy_lim > 40 THEN vy_lim := 40; END_IF;
IF vy_lim < -40 THEN vy_lim := -40; END_IF;
vz_lim := dv_z.v_cmd;
IF vz_lim > 20 THEN vz_lim := 20; END_IF;
IF vz_lim < -20 THEN vz_lim := -20; END_IF;

(* C3/C5: quickstop or fault forces velocity to zero *)
IF quickstop OR fault_any OR NOT run THEN
    x_v := 0; y_v := 0; z_v := 0;
ELSE
    x_v := vx_lim; y_v := vy_lim; z_v := vz_lim;
END_IF;

(* ---- application status ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := go_x.Done AND go_y.Done AND go_z.Done AND NOT plot_active;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
fault_any := ((x_swm AND 16#0008) <> 0) OR ((y_swm AND 16#0008) <> 0) OR ((z_swm AND 16#0008) <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
