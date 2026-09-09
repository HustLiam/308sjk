FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    hold_req : BOOL;
    pos_target : INT;
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
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    edge_ir : BOOL;
    dist : REAL;
    stop_d : REAL;
    SCAN_T : REAL := 0.02;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF INT_TO_REAL(pos_target) > MAXPOS OR pos_target < 0 THEN
        (* target out of range: no start, hold position *)
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
        ELSE vel := MAX(-VMAX, vel + ACCEL * SCAN_T);
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
    v_cmd : REAL;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_ff : REAL;
    pos_err : REAL;
    v_out : REAL := 0.0;
    sp_prev : INT;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
  END_VAR
(* DRIVE402: CSP following + position loop; state 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA *)
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

(* status word *)
sw := 16#0000;
IF state >= 2 THEN sw := sw OR 16#0001; END_IF;
IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
IF state >= 4 THEN sw := sw OR 16#0004; END_IF;
IF state = 7 THEN sw := sw OR 16#0008; END_IF;
IF (v_out = 0.0) AND (state = 4) THEN sw := sw OR 16#0400; END_IF;
v_cmd := v_out;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dx : DRIVE402;
    dy : DRIVE402;
    dz : DRIVE402;
    seg_timer : TON;
    jog_timer : TON;
    cw : WORD;
    xsw : WORD;
    ysw : WORD;
    zsw : WORD;
    pstep : INT := 0;
    pl_step : INT := 0;
    seg : INT := 0;
    tx : INT := 0;
    ty : INT := 0;
    tz : INT := 0;
    go_x : BOOL;
    go_y : BOOL;
    go_z : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    edge_home : BOOL;
    edge_jq : BOOL;
    manual_exe : BOOL;
    fault_latch : BOOL;
    seg_to : BOOL;
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
prog_id := 3;

(* ---- status word snapshots (WORD for bit ops) ---- *)
xsw := INT_TO_WORD(x_sw);
ysw := INT_TO_WORD(y_sw);
zsw := INT_TO_WORD(z_sw);

(* ---- fault handling (C6/C7): drive fault bit3 + segment timeout latch ---- *)
IF cmd_reset THEN
    fault_latch := FALSE;
    seg_to := FALSE;
END_IF;
fault_latch := fault_latch OR ((xsw AND 16#0008) <> 0) OR ((ysw AND 16#0008) <> 0) OR ((zsw AND 16#0008) <> 0) OR seg_to;
fault_any := fault_latch;

(* ---- power handshake: idle keeps shutdown cmd 0x06 (RTSO), run ramps to OE ---- *)
IF quickstop THEN
    cw := 16#0002;
    pstep := 0;
ELSIF run AND NOT fault_latch THEN
    CASE pstep OF
        0: cw := 16#0006; pstep := 1;
        1: IF ((xsw AND 16#0001) <> 0) AND ((ysw AND 16#0001) <> 0) AND ((zsw AND 16#0001) <> 0) THEN
               cw := 16#0007; pstep := 2;
           END_IF;
        2: IF ((xsw AND 16#0002) <> 0) AND ((ysw AND 16#0002) <> 0) AND ((zsw AND 16#0002) <> 0) THEN
               cw := 16#000F; pstep := 3;
           END_IF;
        3: cw := 16#000F; pstep := 4;
        4: cw := 16#000F;
        ELSE pstep := 0;
    END_CASE;
ELSE
    cw := 16#0006;
    pstep := 0;
END_IF;
all_oe := (pstep = 4) AND ((xsw AND 16#0004) <> 0) AND ((ysw AND 16#0004) <> 0) AND ((zsw AND 16#0004) <> 0);

(* ---- abort: quickstop / fault / run off (C6/C8) ---- *)
IF (NOT run) OR quickstop OR fault_latch THEN
    pl_step := 0;
    go_x := FALSE;
    go_y := FALSE;
    go_z := FALSE;
    manual_exe := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- start on cmd_draw rising: pen up first (C4/AC2) ---- *)
IF cmd_draw AND NOT edge_draw AND (pl_step = 0) AND all_oe AND NOT quickstop AND NOT fault_latch THEN
    plot_done := FALSE;
    tz := 10;
    go_z := TRUE;
    pl_step := 1;
END_IF;

(* ---- sequence: level-held trigger lines, only targets change ---- *)
CASE pl_step OF
    1: IF ix_z.Done AND NOT ix_z.Busy AND (z_fb >= 8) THEN
           tx := 50; ty := 25;
           go_x := TRUE; go_y := TRUE;
           pl_step := 2;
       END_IF;
    2: IF ix_x.Done AND ix_y.Done AND NOT ix_x.Busy AND NOT ix_y.Busy THEN
           tz := 0;
           go_z := TRUE;
           seg := 0;
           pl_step := 3;
       END_IF;
    3: IF ix_z.Done AND NOT ix_z.Busy AND (z_fb <= 2) THEN
           pl_step := 4;
           seg := 1;
       END_IF;
    4: CASE seg OF
           1: tx := 57; ty := 26;
           2: tx := 63; ty := 28;
           3: tx := 68; ty := 32;
           4: tx := 72; ty := 38;
           5: tx := 74; ty := 44;
           6: tx := 75; ty := 50;
           7: tx := 74; ty := 57;
           8: tx := 72; ty := 63;
           9: tx := 68; ty := 68;
           10: tx := 63; ty := 72;
           11: tx := 57; ty := 74;
           12: tx := 50; ty := 75;
           13: tx := 43; ty := 74;
           14: tx := 37; ty := 72;
           15: tx := 32; ty := 68;
           16: tx := 28; ty := 63;
           17: tx := 26; ty := 57;
           18: tx := 25; ty := 50;
           19: tx := 26; ty := 44;
           20: tx := 28; ty := 38;
           21: tx := 32; ty := 32;
           22: tx := 37; ty := 28;
           23: tx := 44; ty := 26;
           24: tx := 50; ty := 25;
           ELSE tx := 50; ty := 25;
       END_CASE;
    5: IF ix_z.Done AND NOT ix_z.Busy AND (z_fb >= 8) THEN
           tx := 50; ty := 50;
           go_x := TRUE; go_y := TRUE;
           pl_step := 6;
       END_IF;
    6: IF ix_x.Done AND ix_y.Done AND NOT ix_x.Busy AND NOT ix_y.Busy THEN
           plot_done := TRUE;
           go_x := FALSE; go_y := FALSE;
           pl_step := 0;
       END_IF;
    ELSE pl_step := 0;
END_CASE;

(* ---- segment advance: in-position confirm (POSWIN) then next target (C5) ---- *)
IF (pl_step = 4) AND ix_x.Done AND ix_y.Done AND NOT ix_x.Busy AND NOT ix_y.Busy THEN
    IF seg >= 24 THEN
        tz := 10;
        go_z := TRUE;
        pl_step := 5;
    ELSE
        seg := seg + 1;
    END_IF;
END_IF;

(* ---- segment timeout monitor (C7): 2.0 s per arc segment ---- *)
seg_timer(IN := (pl_step = 4) AND (ix_x.Busy OR ix_y.Busy), PT := T#2S);
IF seg_timer.Q THEN
    seg_to := TRUE;
END_IF;

(* ---- manual positioning cmd_go: rejected when pen down (C1/AC3) ---- *)
IF cmd_go AND NOT edge_go AND (z_fb >= 8) AND (pl_step = 0) AND all_oe AND NOT quickstop AND NOT fault_latch THEN
    tx := x_sp;
    ty := y_sp;
    go_x := TRUE;
    go_y := TRUE;
    manual_exe := TRUE;
END_IF;
move_done := manual_exe AND ix_x.Done AND ix_y.Done AND NOT ix_x.Busy AND NOT ix_y.Busy;
IF move_done THEN
    manual_exe := FALSE;
    go_x := FALSE;
    go_y := FALSE;
END_IF;

(* ---- jog: pen-up only, pulsed target increments (C2/AC4/AC5) ---- *)
IF (jog_fwd <> jog_rev) AND (z_fb >= 8) AND (pl_step = 0) AND all_oe AND NOT quickstop AND NOT fault_latch THEN
    jog_timer(IN := TRUE, PT := T#100MS);
ELSE
    jog_timer(IN := FALSE, PT := T#100MS);
END_IF;
IF jog_timer.Q AND NOT edge_jq THEN
    IF jog_fwd THEN tx := tx + 1;
    ELSE tx := tx - 1;
    END_IF;
    IF tx > 100 THEN tx := 100; END_IF;
    IF tx < 0 THEN tx := 0; END_IF;
    go_x := TRUE;
END_IF;
edge_jq := jog_timer.Q;

(* ---- home button: pen-up safe position ---- *)
IF cmd_home AND NOT edge_home AND (pl_step = 0) AND NOT quickstop AND NOT fault_latch THEN
    tz := 10;
    go_z := TRUE;
END_IF;

(* ---- manual z setpoint (not pen-interlocked) ---- *)
IF (z_sp <> tz) AND (pl_step = 0) AND NOT go_z AND NOT quickstop AND NOT fault_latch THEN
    tz := z_sp;
    go_z := TRUE;
END_IF;

(* ---- axis chains: interp -> drive ---- *)
ix_x(fire := go_x, pos_target := tx, hold_req := quickstop, VMAX := 40.0, ACCEL := 100.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := go_y, pos_target := ty, hold_req := quickstop, VMAX := 40.0, ACCEL := 100.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := go_z, pos_target := tz, hold_req := quickstop, VMAX := 20.0, ACCEL := 100.0, POSWIN := 1.0, MAXPOS := 10.0);

dx(cw := cw, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
dy(cw := cw, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
dz(cw := cw, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

x_sw := WORD_TO_INT(dx.sw);
y_sw := WORD_TO_INT(dy.sw);
z_sw := WORD_TO_INT(dz.sw);
x_v := REAL_TO_INT(dx.v_cmd);
y_v := REAL_TO_INT(dy.v_cmd);
z_v := REAL_TO_INT(dz.v_cmd);

(* ---- pure physical outputs ---- *)
pen_down := (z_fb <= 2);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);

(* ---- edge memories ---- *)
edge_draw := cmd_draw;
edge_go := cmd_go;
edge_home := cmd_home;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
