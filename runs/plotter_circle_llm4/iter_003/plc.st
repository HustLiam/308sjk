FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    cur_pos : INT;
    vmax : REAL;
    accel : REAL;
    poswin : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    edge_i : BOOL;
    tgt : REAL;
    pos : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
IF fire AND NOT edge_i THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;
edge_i := fire;

(* continuous tracking: while fire held, a new target starts a new move *)
IF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;

IF NOT Busy THEN
    pos := INT_TO_REAL(cur_pos);
    vel := 0.0;
    Done := TRUE;
END_IF;

IF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * accel);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN
            vel := MAX(0.0, vel - accel * 0.02);
        ELSE
            vel := MIN(0.0, vel + accel * 0.02);
        END_IF;
        IF (vel = 0.0) AND (ABS(dist) <= poswin) THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN
            vel := MIN(vmax, vel + accel * 0.02);
        ELSE
            vel := MAX(-vmax, vel - accel * 0.02);
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
    vlim : REAL;
  END_VAR
  VAR_OUTPUT
    sw : WORD;
    v_cmd : INT;
  END_VAR
  VAR
    cmd : WORD;
    state : INT;
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
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
END_CASE;

(* position loop: P control + setpoint-diff feedforward *)
IF state >= 4 AND state <= 6 THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / 0.02;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + 8.0 * pos_err;
    IF v_out > vlim THEN v_out := vlim; END_IF;
    IF v_out < -vlim THEN v_out := -vlim; END_IF;
    IF v_ff = 0.0 AND ABS(pos_err) <= 1.5 THEN
        v_out := 0.0;
    END_IF;
ELSIF state = 5 THEN
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - 100.0 * 0.02);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + 100.0 * 0.02);
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

PROGRAM PLC_PRG
  VAR
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    rs_y : MC_RESET;
    rs_z : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    run_ok : BOOL;
    fault_now : BOOL;
    fault_latch : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    edge_hm : BOOL;
    jog_ok : BOOL;
    jog_act : BOOL;
    jog_pos : INT;
    pl_step : INT;
    seg : INT;
    th : REAL;
    x_t : INT;
    y_t : INT;
    z_t : INT;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_z_exe : BOOL;
    manual_exe : BOOL;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
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
    prog_id AT %QW20 : INT := 2;
  END_VAR
(* ---- program identity ---- *)
prog_id := 2;

pen_down := (z_fb <= 1);

(* ---- fault detect / latch / reset (C4) ---- *)
fault_now := ((dv_x.sw AND 16#0008) <> 0) OR ((dv_y.sw AND 16#0008) <> 0) OR ((dv_z.sw AND 16#0008) <> 0);
IF fault_now THEN
    fault_latch := TRUE;
END_IF;
IF cmd_reset AND NOT fault_now THEN
    fault_latch := FALSE;
END_IF;
fault_any := fault_now;

(* ---- enable interlock (C3) ---- *)
run_ok := run AND NOT quickstop AND NOT fault_latch;

(* ---- power / reset / quickstop (quickstop written last = highest prio, C1) ---- *)
pwr_x(Enable := run_ok, sw := dv_x.sw, cw := x_cw);
pwr_y(Enable := run_ok, sw := dv_y.sw, cw := y_cw);
pwr_z(Enable := run_ok, sw := dv_z.sw, cw := z_cw);
rs_x(fire := cmd_reset, sw := dv_x.sw, cw := x_cw);
rs_y(fire := cmd_reset, sw := dv_y.sw, cw := y_cw);
rs_z(fire := cmd_reset, sw := dv_z.sw, cw := z_cw);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;

(* ---- abort: quickstop / fault / run-off stops sequencer, plot_done off (C1/C4) ---- *)
IF quickstop OR fault_now OR NOT run THEN
    pl_step := 0;
    seg := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_z_exe := FALSE;
    manual_exe := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- cmd_draw start: requires run, enabled, idle, pen up (C5) ---- *)
IF cmd_draw AND NOT edge_draw AND run_ok AND all_oe AND (pl_step = 0) AND NOT manual_exe AND (z_fb = 10) THEN
    x_t := 50;
    y_t := 25;
    z_t := 10;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    pl_step := 1;
END_IF;
edge_draw := cmd_draw;

(* ---- manual cmd_go: X/Y blocked while pen down (C2), Z free ---- *)
IF cmd_go AND NOT edge_go AND run_ok AND all_oe AND (pl_step = 0) AND NOT manual_exe THEN
    IF NOT pen_down THEN
        x_t := x_sp;
        go_x_exe := TRUE;
    END_IF;
    IF NOT pen_down THEN
        y_t := y_sp;
        go_y_exe := TRUE;
    END_IF;
    z_t := z_sp;
    go_z_exe := TRUE;
    manual_exe := TRUE;
END_IF;
edge_go := cmd_go;

(* ---- cmd_home: Z reference = pen-up safe position ---- *)
IF cmd_home AND NOT edge_hm AND run_ok AND all_oe AND (pl_step = 0) AND NOT manual_exe THEN
    z_t := 10;
    go_z_exe := TRUE;
    hm_z_exe := TRUE;
END_IF;
edge_hm := cmd_home;

(* ---- circle draw sequencer (C5): targets only, level-held exe lines ---- *)
CASE pl_step OF
    1:  (* XY position to circle start (50,25) *)
        x_t := 50;
        y_t := 25;
        IF (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 25) <= 2) THEN
            z_t := 0;
            go_z_exe := TRUE;
            pl_step := 2;
        END_IF;
    2:  (* pen down to 0 *)
        z_t := 0;
        IF (NOT ix_z.Busy) AND (ABS(z_fb - 0) <= 1) THEN
            seg := 0;
            pl_step := 3;
        END_IF;
    3:  (* circle interpolation: center (50,50) r=25, 12 chords of 30 deg *)
        th := INT_TO_REAL(seg) * 0.5236;
        x_t := REAL_TO_INT(50.0 + 25.0 * COS(th));
        y_t := REAL_TO_INT(50.0 + 25.0 * SIN(th));
        IF (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (ABS(x_fb - x_t) <= 3) AND (ABS(y_fb - y_t) <= 3) THEN
            IF seg >= 11 THEN
                z_t := 10;
                go_z_exe := TRUE;
                pl_step := 4;
            ELSE
                seg := seg + 1;
            END_IF;
        END_IF;
    4:  (* pen up to 10 *)
        z_t := 10;
        IF (NOT ix_z.Busy) AND (ABS(z_fb - 10) <= 1) THEN
            x_t := 50;
            y_t := 50;
            go_x_exe := TRUE;
            go_y_exe := TRUE;
            pl_step := 5;
        END_IF;
    5:  (* return to center (50,50), finish *)
        x_t := 50;
        y_t := 50;
        z_t := 10;
        IF (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 50) <= 2) THEN
            plot_done := TRUE;
            pl_step := 0;
            go_x_exe := FALSE;
            go_y_exe := FALSE;
            go_z_exe := FALSE;
        END_IF;
END_CASE;

(* ---- interpolation engines (continuous tracking while exe held) ---- *)
ix_x(fire := go_x_exe, pos_target := x_t, cur_pos := x_fb, vmax := 40.0, accel := 80.0, poswin := 2.0);
ix_y(fire := go_y_exe, pos_target := y_t, cur_pos := y_fb, vmax := 40.0, accel := 80.0, poswin := 2.0);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_t, cur_pos := z_fb, vmax := 20.0, accel := 40.0, poswin := 1.0);

(* ---- manual jog: level active, pen-down interlock (C2) ---- *)
jog_ok := run_ok AND all_oe AND (pl_step = 0) AND NOT manual_exe AND NOT pen_down;
IF jog_ok AND (jog_fwd OR jog_rev) AND NOT jog_act THEN
    jog_pos := x_fb;
END_IF;
jog_act := jog_ok AND (jog_fwd OR jog_rev);
IF jog_fwd AND jog_act AND (jog_pos < 97) THEN
    jog_pos := jog_pos + 1;
END_IF;
IF jog_rev AND jog_act AND (jog_pos > 3) THEN
    jog_pos := jog_pos - 1;
END_IF;

(* ---- setpoint routing: jog overrides X interpolation ---- *)
IF jog_act THEN
    x_interp := jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives follow interpolation setpoints ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb, vlim := 120.0);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb, vlim := 120.0);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb, vlim := 20.0);
z_v := dv_z.v_cmd;

(* ---- status words out ---- *)
x_sw := WORD_TO_INT(dv_x.sw);
y_sw := WORD_TO_INT(dv_y.sw);
z_sw := WORD_TO_INT(dv_z.sw);

(* ---- velocity hard gate: zero during quickstop / fault / run off (C1/C3/C4) ---- *)
IF quickstop OR fault_latch OR NOT run THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

(* ---- exe line clearing when in position ---- *)
IF go_x_exe AND ix_x.Done AND NOT ix_x.Busy AND (ABS(x_fb - x_t) <= 2) AND (pl_step = 0) THEN
    go_x_exe := FALSE;
END_IF;
IF go_y_exe AND ix_y.Done AND NOT ix_y.Busy AND (ABS(y_fb - y_t) <= 2) AND (pl_step = 0) THEN
    go_y_exe := FALSE;
END_IF;
IF go_z_exe AND ix_z.Done AND NOT ix_z.Busy AND (ABS(z_fb - z_t) <= 1) AND (pl_step = 0) THEN
    go_z_exe := FALSE;
END_IF;
IF hm_z_exe AND ix_z.Done AND NOT ix_z.Busy THEN
    hm_z_exe := FALSE;
END_IF;

(* ---- manual move done summary (off during draw, C6) ---- *)
move_done := manual_exe AND NOT go_x_exe AND NOT go_y_exe AND NOT go_z_exe AND NOT hm_z_exe;
IF move_done THEN
    manual_exe := FALSE;
    move_done := TRUE;
END_IF;

any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
