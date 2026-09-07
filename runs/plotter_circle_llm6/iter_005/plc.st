FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    Vmax : REAL;
    Accel : REAL;
    Poswin : REAL;
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
    dist : REAL;
    stop_d : REAL;
    SCAN_T : REAL := 0.02;
  END_VAR
IF fire AND (NOT Busy) THEN
    IF (pos_target > 100) OR (pos_target < 0) THEN
        Done := TRUE;
    ELSIF ABS(INT_TO_REAL(pos_target) - pos) > 0.499 THEN
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    ELSE
        Done := TRUE;
    END_IF;
END_IF;

IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - Accel * SCAN_T);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + Accel * SCAN_T);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * Accel);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - Accel * SCAN_T);
        ELSE vel := MIN(0.0, vel + Accel * SCAN_T);
        END_IF;
        IF (vel = 0.0) AND (ABS(dist) <= Poswin) THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(Vmax, vel + Accel * SCAN_T);
        ELSE vel := MAX(-Vmax, vel + Accel * SCAN_T);
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
    Vmax : REAL;
  END_VAR
  VAR_OUTPUT
    v_cmd : INT;
    sw : WORD;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    sp_prev : INT;
    SCAN_T : REAL := 0.02;
    QS_DECEL : REAL := 2000.0;
    KP : REAL := 8.0;
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
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / SCAN_T;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > Vmax THEN v_out := Vmax; END_IF;
    IF v_out < -Vmax THEN v_out := -Vmax; END_IF;
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

sw := 0;
CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0031;
    3: sw := 16#0033;
    4: sw := 16#0037;
    5: sw := 16#0014;
    6: sw := 16#0008;
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
IF fire AND (NOT edge_rs) THEN
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

FUNCTION_BLOCK MC_MOVEABS
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
IF fire AND (NOT edge_ma) THEN
    interp_exe := TRUE;
END_IF;
edge_ma := fire;
Done := (NOT interp_exe);
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
IF fire AND (NOT edge_hm) THEN
    home_exe := TRUE;
END_IF;
edge_hm := fire;
Done := (NOT home_exe);
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_JOG
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
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_swv : WORD;
    y_swv : WORD;
    z_swv : WORD;
    pl_step : INT := 0;
    tx : INT := 50;
    ty : INT := 50;
    tz : INT := 10;
    ang : REAL := -1.5708;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_z_exe : BOOL;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT := 50;
    abort : BOOL;
    home_active : BOOL;
    man_active : BOOL;
    man_done : BOOL;
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    rs_y : MC_RESET;
    rs_z : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    jg_x : MC_JOG;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
  END_VAR
  VAR
    prog_id AT %QW20 : INT := 3;
    run AT %QX0.0 : BOOL;
    cmd_home AT %QX0.1 : BOOL;
    cmd_go AT %QX0.2 : BOOL;
    jog_fwd AT %QX0.3 : BOOL;
    jog_rev AT %QX0.4 : BOOL;
    quickstop AT %QX0.5 : BOOL;
    cmd_reset AT %QX0.7 : BOOL;
    all_oe AT %QX1.0 : BOOL;
    move_done AT %QX1.1 : BOOL;
    any_moving AT %QX1.2 : BOOL;
    fault_any AT %QX1.3 : BOOL;
    cmd_draw AT %QX2.0 : BOOL;
    pen_down AT %QX2.1 : BOOL;
    plot_done AT %QX2.2 : BOOL;
    x_fb AT %QW0 : INT;
    y_fb AT %QW1 : INT;
    z_fb AT %QW2 : INT;
    x_sw AT %QW6 : INT;
    y_sw AT %QW7 : INT;
    z_sw AT %QW8 : INT;
    x_sp AT %QW10 : INT;
    y_sp AT %QW11 : INT;
    z_sp AT %QW12 : INT;
    x_v AT %QW13 : INT;
    y_v AT %QW14 : INT;
    z_v AT %QW15 : INT;
  END_VAR
(* ---- program identity ---- *)
prog_id := 3;

(* ---- pen state: z_fb<=1 means pen on paper ---- *)
pen_down := (z_fb <= 1);

(* ---- sample drive status words ---- *)
x_swv := dv_x.sw;
y_swv := dv_y.sw;
z_swv := dv_z.sw;
x_sw := WORD_TO_INT(x_swv);
y_sw := WORD_TO_INT(y_swv);
z_sw := WORD_TO_INT(z_swv);

(* C4: any drive fault ---- *)
fault_any := ((x_swv AND 16#0008) <> 0) OR ((y_swv AND 16#0008) <> 0) OR ((z_swv AND 16#0008) <> 0);

(* ---- power / reset / quickstop ---- *)
pwr_x(Enable := run AND (NOT quickstop), cw := x_cw, sw := x_swv);
pwr_y(Enable := run AND (NOT quickstop), cw := y_cw, sw := y_swv);
pwr_z(Enable := run AND (NOT quickstop), cw := z_cw, sw := z_swv);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_swv);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_swv);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_swv);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* ---- abort condition: level, evaluated every scan ---- *)
abort := quickstop OR fault_any OR (NOT run);

(* C1/C3/C4: abort clears sequence state and plot_done (level) ---- *)
IF abort THEN
    pl_step := 0;
    ang := -1.5708;
    plot_done := FALSE;
END_IF;

(* ---- homing: level-held (C2: only when safe) ---- *)
home_active := cmd_home AND run AND (pl_step = 0) AND (NOT abort);
IF home_active THEN
    tz := 10;
END_IF;
hm_z_exe := home_active;

(* ---- manual positioning: level-held, C2 pen interlock on XY ---- *)
man_active := cmd_go AND run AND (pl_step = 0) AND (NOT abort) AND (NOT pen_down);
IF man_active THEN
    tx := x_sp;
    ty := y_sp;
    tz := z_sp;
END_IF;

(* ---- circle sequencer: pure continuous tracking, targets only ---- *)
(* 0=idle 1=pen up 2=XY to start(50,25) 3=pen down 4=arc full turn 5=pen up 6=back to center *)
IF (NOT abort) THEN
    CASE pl_step OF
        0: IF cmd_draw AND all_oe THEN
               ang := -1.5708;
               pl_step := 1;
           END_IF;
        1: tz := 10;
        2: tx := 50;
           ty := 25;
        3: tz := 0;
        4: ang := ang + 0.024;
           IF ang >= 4.7124 THEN
               ang := 4.7124;
           END_IF;
           tx := 50 + REAL_TO_INT(25.0 * COS(ang));
           ty := 50 + REAL_TO_INT(25.0 * SIN(ang));
        5: tz := 10;
        6: tx := 50;
           ty := 50;
    END_CASE;
END_IF;

(* ---- exe lines: level-held, no single-scan pulses ---- *)
go_x_exe := man_active OR (pl_step = 2) OR (pl_step = 4) OR (pl_step = 6);
go_y_exe := man_active OR (pl_step = 2) OR (pl_step = 4) OR (pl_step = 6);
go_z_exe := man_active OR home_active OR (pl_step = 1) OR (pl_step = 3) OR (pl_step = 5);

(* ---- step advance: level conditions, checked every scan ---- *)
IF pl_step = 1 THEN
    IF (z_fb >= 9) AND ix_z.Done AND (NOT ix_z.Busy) THEN
        pl_step := 2;
    END_IF;
END_IF;
IF pl_step = 2 THEN
    IF (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 25) <= 2) AND ix_x.Done AND ix_y.Done AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) THEN
        pl_step := 3;
    END_IF;
END_IF;
IF pl_step = 3 THEN
    IF (z_fb <= 1) AND ix_z.Done AND (NOT ix_z.Busy) THEN
        ang := -1.5708;
        pl_step := 4;
    END_IF;
END_IF;
IF pl_step = 4 THEN
    IF (ang >= 4.7124) AND ix_x.Done AND ix_y.Done AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) THEN
        pl_step := 5;
    END_IF;
END_IF;
IF pl_step = 5 THEN
    IF (z_fb >= 9) AND ix_z.Done AND (NOT ix_z.Busy) THEN
        pl_step := 6;
    END_IF;
END_IF;
IF pl_step = 6 THEN
    IF (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 50) <= 2) AND ix_x.Done AND ix_y.Done AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) THEN
        plot_done := TRUE;
        pl_step := 0;
    END_IF;
END_IF;

(* ---- jog: level-held, C2 pen interlock ---- *)
jg_x(JogForward := jog_fwd AND (NOT pen_down) AND run AND (NOT abort), JogBackward := jog_rev AND (NOT pen_down) AND run AND (NOT abort), jog_pos := x_jog_pos);

(* ---- setpoint routing: jog overrides X, else sequencer targets ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := tx;
END_IF;
y_interp := ty;
z_interp := tz;

(* ---- interp engines: continuous tracking, level-held fire ---- *)
ix_x(fire := go_x_exe OR (pl_step = 4), pos_target := x_interp, hold_req := abort, Vmax := 60.0, Accel := 80.0, Poswin := 0.5);
ix_y(fire := go_y_exe OR (pl_step = 4), pos_target := y_interp, hold_req := abort, Vmax := 60.0, Accel := 80.0, Poswin := 0.5);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_interp, hold_req := abort, Vmax := 20.0, Accel := 40.0, Poswin := 0.4);

(* ---- drives follow setpoint, closed position loop ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb, Vmax := 120.0);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb, Vmax := 120.0);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb, Vmax := 20.0);
z_v := dv_z.v_cmd;

(* ---- application status (level) ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
man_done := man_active AND (ABS(x_fb - tx) <= 2) AND (ABS(y_fb - ty) <= 2) AND (ABS(z_fb - tz) <= 1) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy);
move_done := man_done;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
