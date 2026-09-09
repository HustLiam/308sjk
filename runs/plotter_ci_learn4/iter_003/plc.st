FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL := 40.0;
    ACCEL : REAL := 80.0;
    POSWIN : REAL := 2.0;
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
    dist : REAL;
    stop_d : REAL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: do not start, stay *)
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
    enable : BOOL;
    fault_reset : BOOL;
  END_VAR
  VAR_INPUT
    quickstop AT %QX0.5 : BOOL;
  END_VAR
  VAR_OUTPUT
    cw : WORD;
    sw : WORD;
    enabled : BOOL;
  END_VAR
  VAR
    state : INT := 1;
  END_VAR
CASE state OF
    1:  (* shutdown done, ready to switch on: keep sending shutdown cmd 0x06, sw = 0x31 *)
        cw := 16#0006;
        sw := 16#0031;
        IF enable AND NOT quickstop THEN
            state := 2;
        END_IF;
    2:  (* switch on *)
        cw := 16#0007;
        sw := 16#0033;
        IF enable AND NOT quickstop THEN
            state := 3;
        END_IF;
    3:  (* operation enabled *)
        cw := 16#000F;
        sw := 16#0037;
        IF NOT enable OR quickstop THEN
            state := 1;
        END_IF;
    4:  (* fault *)
        cw := 16#0000;
        sw := 16#0048;
        IF fault_reset AND NOT quickstop THEN
            state := 1;
        END_IF;
ELSE
    state := 1;
END_CASE;
enabled := (state = 3);
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    dr_x : DRIVE402;
    dr_y : DRIVE402;
    dr_z : DRIVE402;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    draw_prev : BOOL;
    home_prev : BOOL;
    edge_draw : BOOL;
    edge_home : BOOL;
    step : INT := 0;
    go_x : BOOL;
    go_y : BOOL;
    go_z : BOOL;
    tx : INT;
    ty : INT;
    tz : INT;
    man_go : BOOL;
    fault_bit : BOOL;
    fault_latch : BOOL;
    swx : WORD;
    swy : WORD;
    swz : WORD;
    vx : REAL;
    vy : REAL;
    vz : REAL;
    jog_allow : BOOL;
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

(* edge memories *)
edge_draw := cmd_draw AND NOT draw_prev;
draw_prev := cmd_draw;
edge_home := cmd_home AND NOT home_prev;
home_prev := cmd_home;

(* drive status words and fault detection (bit3) *)
swx := dr_x.sw;
swy := dr_y.sw;
swz := dr_z.sw;
fault_bit := ((swx AND 16#0008) <> 0) OR ((swy AND 16#0008) <> 0) OR ((swz AND 16#0008) <> 0);
IF fault_bit THEN
    fault_latch := TRUE;
END_IF;
IF cmd_reset AND NOT fault_bit THEN
    fault_latch := FALSE;
END_IF;

pen_down := (z_fb <= 2);

(* sequence state machine: 0=idle, 1..28=draw, 100/101=homing *)
CASE step OF
    0:  IF edge_home AND all_oe AND NOT quickstop AND NOT fault_latch AND NOT pen_down THEN
            tx := 0;
            ty := 0;
            go_x := TRUE;
            go_y := TRUE;
            step := 100;
        ELSIF edge_draw AND all_oe AND (z_fb = 10) AND NOT quickstop AND NOT fault_latch AND NOT pen_down THEN
            tx := 50;
            ty := 25;
            go_x := TRUE;
            go_y := TRUE;
            plot_done := FALSE;
            step := 1;
        ELSIF cmd_go AND NOT pen_down AND (z_fb = 10) AND all_oe AND NOT quickstop AND NOT fault_latch THEN
            tx := x_sp;
            ty := y_sp;
            go_x := TRUE;
            go_y := TRUE;
            man_go := TRUE;
        ELSE
            IF NOT man_go THEN
                go_x := FALSE;
                go_y := FALSE;
            END_IF;
        END_IF;
        (* manual pen up/down via z_sp, not interlocked by pen state *)
        IF (z_sp >= 0) AND (z_sp <= 10) AND NOT quickstop AND NOT fault_latch THEN
            tz := z_sp;
            go_z := TRUE;
        END_IF;
    1:  (* approach circle start point, pen up *)
        tx := 50;
        ty := 25;
    2:  (* pen down after XY confirmed in position *)
        tz := 0;
        go_z := TRUE;
    3:  tx := 57; ty := 26;
    4:  tx := 63; ty := 28;
    5:  tx := 68; ty := 32;
    6:  tx := 72; ty := 38;
    7:  tx := 74; ty := 44;
    8:  tx := 75; ty := 50;
    9:  tx := 74; ty := 57;
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
    26: (* close circle back to start *)
        tx := 50;
        ty := 25;
    27: (* pen up *)
        tz := 10;
        go_z := TRUE;
    28: (* return to center *)
        tx := 50;
        ty := 50;
    100: (* homing XY *)
        tx := 0;
        ty := 0;
    101: (* homing Z to pen-up safe position *)
        tz := 10;
        go_z := TRUE;
ELSE
    step := 0;
END_CASE;

(* emergency stop / fault: level abort, clear sequence *)
IF quickstop OR fault_latch THEN
    step := 0;
    go_x := FALSE;
    go_y := FALSE;
    go_z := FALSE;
    man_go := FALSE;
    plot_done := FALSE;
END_IF;

(* drives *)
dr_x(enable := run AND NOT fault_latch, quickstop := quickstop, fault_reset := cmd_reset);
dr_y(enable := run AND NOT fault_latch, quickstop := quickstop, fault_reset := cmd_reset);
dr_z(enable := run AND NOT fault_latch, quickstop := quickstop, fault_reset := cmd_reset);

(* interpolators: fire held over whole sequence, continuous tracking on target change *)
ix_x(fire := go_x, pos_target := tx, hold_req := quickstop OR fault_latch, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := go_y, pos_target := ty, hold_req := quickstop OR fault_latch, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := go_z, pos_target := tz, hold_req := quickstop OR fault_latch, VMAX := 20.0, ACCEL := 80.0, POSWIN := 1.0, MAXPOS := 10);

(* sequence advance: all axes idle and done *)
IF (step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF step = 28 THEN
        plot_done := TRUE;
        step := 0;
        go_x := FALSE;
        go_y := FALSE;
        go_z := FALSE;
    ELSIF step = 101 THEN
        step := 0;
        go_x := FALSE;
        go_y := FALSE;
        go_z := FALSE;
    ELSE
        step := step + 1;
    END_IF;
END_IF;

(* manual positioning completion *)
IF man_go AND NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done THEN
    go_x := FALSE;
    go_y := FALSE;
    man_go := FALSE;
END_IF;

(* velocity commands: P-tracking of interpolator setpoints *)
IF quickstop OR fault_latch OR NOT run THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    vx := INT_TO_REAL(ix_x.Setpoint - x_fb) * 20.0;
    IF vx > 40.0 THEN vx := 40.0; END_IF;
    IF vx < -40.0 THEN vx := -40.0; END_IF;
    x_v := REAL_TO_INT(vx);
    vy := INT_TO_REAL(ix_y.Setpoint - y_fb) * 20.0;
    IF vy > 40.0 THEN vy := 40.0; END_IF;
    IF vy < -40.0 THEN vy := -40.0; END_IF;
    y_v := REAL_TO_INT(vy);
    vz := INT_TO_REAL(ix_z.Setpoint - z_fb) * 20.0;
    IF vz > 20.0 THEN vz := 20.0; END_IF;
    IF vz < -20.0 THEN vz := -20.0; END_IF;
    z_v := REAL_TO_INT(vz);
    (* manual jog on X: only pen up, idle, enabled *)
    jog_allow := (step = 0) AND NOT pen_down AND all_oe;
    IF jog_allow THEN
        IF jog_fwd THEN
            x_v := 20;
        ELSIF jog_rev THEN
            x_v := -20;
        END_IF;
    END_IF;
END_IF;

(* outputs *)
all_oe := dr_x.enabled AND dr_y.enabled AND dr_z.enabled;
move_done := man_go AND NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
fault_any := fault_latch;
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
