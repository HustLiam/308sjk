FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL := 40.0;
    ACCEL : REAL := 80.0;
    POSWIN : INT := 2;
    MAXPOS : INT := 100;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    SCAN_T : REAL := 0.02;
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    edge_ir : BOOL;
    dist : REAL := 0.0;
    stop_d : REAL := 0.0;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: hold position *)
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
    v_cmd : REAL;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    v_out : REAL := 0.0;
    sp_prev : INT := 0;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
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

(* status word from state *)
sw := 16#0000;
IF state >= 2 THEN sw := sw OR 16#0001; END_IF;
IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
IF state >= 4 THEN sw := sw OR 16#0004; END_IF;
IF state = 7 THEN sw := sw OR 16#0008; END_IF;
IF (v_ff = 0.0) AND (ABS(pos_err) <= 1.5) AND (state = 4) THEN sw := sw OR 16#0400; END_IF;
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
    pl_step : INT := 0;
    tx : INT := 50;
    ty : INT := 50;
    tz : INT := 10;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    jog_x_exe : BOOL;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    fire_lift : BOOL;
    hold_all : BOOL;
    pen_ok : BOOL;
    edge_draw : BOOL;
    edge_go : BOOL;
    fault_latch : BOOL;
    manual_done : BOOL;
    cw_reg : WORD := 16#0006;
    sw_all : WORD;
    pstep : INT := 0;
    enable_pwr : BOOL;
    vm_xy : REAL := 40.0;
    pd_timer : TON;
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

(* ---- C4: fault detection and latch ---- *)
fault_any := ((dx.sw AND 16#0008) <> 0) OR ((dy.sw AND 16#0008) <> 0) OR ((dz.sw AND 16#0008) <> 0);
IF fault_any THEN fault_latch := TRUE; END_IF;
IF cmd_reset AND NOT fault_any AND NOT quickstop THEN fault_latch := FALSE; END_IF;

(* ---- C6: power handshake, controlled power-down lifts pen first ---- *)
sw_all := dx.sw OR dy.sw OR dz.sw;
pd_timer(IN := NOT run, PT := T#2S);
enable_pwr := (run OR NOT pd_timer.Q) AND NOT fault_latch;

IF enable_pwr AND pstep = 4 AND (sw_all AND 16#0004) = 0 AND (sw_all AND 16#0008) = 0 THEN
    pstep := 0;
END_IF;
IF enable_pwr THEN
    CASE pstep OF
        0: cw_reg := (cw_reg AND 16#00F0) OR 16#0006;  pstep := 1;
        1: IF (sw_all AND 16#0001) <> 0 THEN cw_reg := (cw_reg AND 16#00F0) OR 16#0007; pstep := 2; END_IF;
        2: IF (sw_all AND 16#0002) <> 0 THEN cw_reg := (cw_reg AND 16#00F0) OR 16#000F; pstep := 3; END_IF;
        3: IF (sw_all AND 16#0004) <> 0 THEN pstep := 4; END_IF;
        ELSE pstep := 0;
    END_CASE;
ELSE
    cw_reg := (cw_reg AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
all_oe := (sw_all AND 16#0004) <> 0;

hold_all := quickstop OR fault_latch;

(* ---- C3/C4: abort sequence ---- *)
IF quickstop OR fault_latch THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    jog_x_exe := FALSE;
    plot_done := FALSE;
    manual_done := FALSE;
END_IF;

(* pen state output: pure physical *)
pen_down := z_fb <= 2;
pen_ok := z_fb >= 2;

(* ---- C2: manual go with pen-down interlock ---- *)
IF cmd_go AND NOT edge_go THEN
    IF pen_ok AND NOT quickstop AND NOT fault_latch AND pl_step = 0 AND NOT jog_x_exe THEN
        tx := x_sp;
        ty := y_sp;
        go_x_exe := TRUE;
        go_y_exe := TRUE;
        manual_done := FALSE;
    END_IF;
END_IF;
edge_go := cmd_go;

(* ---- C2: jog with pen-down interlock ---- *)
IF (jog_fwd OR jog_rev) AND pen_ok AND NOT quickstop AND NOT fault_latch AND pl_step = 0 AND NOT go_x_exe THEN
    jog_x_exe := TRUE;
    IF jog_fwd THEN tx := ix_x.Setpoint + 1;
    ELSE tx := ix_x.Setpoint - 1;
    END_IF;
ELSIF NOT jog_fwd AND NOT jog_rev THEN
    jog_x_exe := FALSE;
END_IF;

(* ---- C1: draw sequence start ---- *)
IF cmd_draw AND NOT edge_draw THEN
    IF NOT quickstop AND NOT fault_latch AND pen_ok AND NOT go_x_exe AND NOT jog_x_exe THEN
        pl_step := 1;
        plot_done := FALSE;
        manual_done := FALSE;
    END_IF;
END_IF;
edge_draw := cmd_draw;

(* ---- C6: lift pen before power-down ---- *)
IF NOT run AND NOT quickstop AND NOT fault_latch AND z_fb < 9 THEN
    tz := 10;
    fire_lift := TRUE;
ELSE
    fire_lift := FALSE;
END_IF;

(* ---- sequencer step table (waypoints verbatim, rounded to INT) ---- *)
CASE pl_step OF
    0: pl_step := pl_step;
    1: tx := 50; ty := 25; tz := 10;
    2: tx := 50; ty := 25; tz := 0;
    3: tx := 57; ty := 26;
    4: tx := 63; ty := 28;
    5: tx := 68; ty := 32;
    6: tx := 72; ty := 38;
    7: tx := 74; ty := 44;
    8: tx := 75; ty := 50;
    9: tx := 74; ty := 57;
    10: tx := 72; ty := 63;
    11: tx := 68; ty := 68;
    12: tx := 63; ty := 72;
    13: tx := 57; ty := 74;
    14: tx := 50; ty := 75;
    15: tx := 44; ty := 74;
    16: tx := 38; ty := 72;
    17: tx := 32; ty := 68;
    18: tx := 28; ty := 63;
    19: tx := 26; ty := 57;
    20: tx := 25; ty := 50;
    21: tx := 26; ty := 44;
    22: tx := 28; ty := 38;
    23: tx := 32; ty := 32;
    24: tx := 38; ty := 28;
    25: tx := 44; ty := 26;
    26: tx := 50; ty := 25;
    27: tx := 50; ty := 25; tz := 10;
    28: tx := 50; ty := 50; tz := 10;
    ELSE pl_step := 0;
END_CASE;

(* step advance: all axes idle and done *)
IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 28 THEN
        plot_done := TRUE;
        pl_step := 0;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* manual move completion *)
IF go_x_exe AND NOT ix_x.Busy AND ix_x.Done AND go_y_exe AND NOT ix_y.Busy AND ix_y.Done THEN
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    manual_done := TRUE;
END_IF;

(* fire lines: level-held *)
fire_x := go_x_exe OR jog_x_exe OR (pl_step <> 0);
fire_y := go_y_exe OR (pl_step <> 0);
fire_z := fire_lift OR (pl_step <> 0);

(* ---- C5: speed limits, slower while pen down (circle sweep steps 2..26) ---- *)
IF pl_step >= 2 AND pl_step <= 26 THEN
    vm_xy := 20.0;
ELSE
    vm_xy := 40.0;
END_IF;

ix_x(fire := fire_x, pos_target := tx, hold_req := hold_all, VMAX := vm_xy, ACCEL := 80.0, POSWIN := 2, MAXPOS := 100);
ix_y(fire := fire_y, pos_target := ty, hold_req := hold_all, VMAX := vm_xy, ACCEL := 80.0, POSWIN := 2, MAXPOS := 100);
ix_z(fire := fire_z, pos_target := tz, hold_req := hold_all, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1, MAXPOS := 10);

dx(cw := cw_reg, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
dy(cw := cw_reg, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
dz(cw := cw_reg, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

x_sw := dx.sw;
y_sw := dy.sw;
z_sw := dz.sw;

(* C3: quickstop forces zero velocity commands *)
IF quickstop OR fault_latch THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    x_v := REAL_TO_INT(dx.v_cmd);
    y_v := REAL_TO_INT(dy.v_cmd);
    z_v := REAL_TO_INT(dz.v_cmd);
END_IF;

any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := manual_done;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
