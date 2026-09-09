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
    SCAN_T : REAL;
    pos : REAL;
    tgt : REAL;
    vel : REAL;
    edge_ir : BOOL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF (INT_TO_REAL(pos_target) > MAXPOS) OR (INT_TO_REAL(pos_target) < 0.0) THEN
        (* target out of range: no trajectory, hold position *)
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
    Enable : BOOL;
    cw : WORD;
  END_VAR
  VAR_OUTPUT
    sw : WORD;
  END_VAR
  VAR
    state : INT := 1;
  END_VAR
(* CiA402 state machine: 1=SOD(sw=0x40) 2=RTSO(sw=0x31,bit0=1) 3=OP(sw=0x37) *)
(* idle convention: while not enabled the PLC keeps sending shutdown cw=0x06 *)
CASE state OF
    1: IF (cw AND 16#000F) = 16#0006 THEN state := 2; END_IF;
    2: IF Enable AND ((cw AND 16#000F) = 16#000F) THEN state := 3; END_IF;
    3: IF NOT Enable THEN state := 2; END_IF;
ELSE
    state := 1;
END_CASE;

CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0031;
    3: sw := 16#0037;
ELSE
    sw := 16#0040;
END_CASE;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    cw_word : WORD;
    fault_latch : BOOL;
    abort : BOOL;
    hold : BOOL;
    x_sw_w : WORD;
    y_sw_w : WORD;
    z_sw_w : WORD;
    draw_started : BOOL;
    go_started : BOOL;
    tx : INT;
    ty : INT;
    tz : INT;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    man_x : BOOL;
    man_y : BOOL;
    pl_step : INT;
    prev_x : INT;
    prev_y : INT;
    prev_z : INT;
    xv_r : REAL;
    yv_r : REAL;
    zv_r : REAL;
    cx : INT;
    cy : INT;
    cz : INT;
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
    x_sw AT %QW6 : INT;
    y_sw AT %QW7 : INT;
    z_sw AT %QW8 : INT;
    x_sp AT %QW10 : INT;
    y_sp AT %QW11 : INT;
    z_sp AT %QW12 : INT;
    x_v AT %QW13 : INT;
    y_v AT %QW14 : INT;
    z_v AT %QW15 : INT;
    prog_id AT %QW20 : INT;
  END_VAR
prog_id := 3;

(* C4 fault detect: mirror INT status words into WORD for bit tests *)
x_sw_w := INT_TO_WORD(x_sw);
y_sw_w := INT_TO_WORD(y_sw);
z_sw_w := INT_TO_WORD(z_sw);
IF (x_sw_w AND 16#0008) <> 0 OR (y_sw_w AND 16#0008) <> 0 OR (z_sw_w AND 16#0008) <> 0 THEN
    fault_latch := TRUE;
END_IF;
IF cmd_reset AND NOT quickstop THEN
    fault_latch := FALSE;
END_IF;
fault_any := fault_latch;

abort := quickstop OR fault_latch;
hold := abort OR NOT run;

pen_down := (z_fb <= 2);

(* CiA402 command word: shutdown while disabled (RTSO idle convention),
   switch-on+operation when enabled and fault free *)
IF run AND NOT fault_latch THEN
    cw_word := 16#000F;
ELSE
    cw_word := 16#0006;
END_IF;

drv_x(Enable := run AND NOT fault_latch, cw := cw_word);
drv_y(Enable := run AND NOT fault_latch, cw := cw_word);
drv_z(Enable := run AND NOT fault_latch, cw := cw_word);
x_sw := WORD_TO_INT(drv_x.sw);
y_sw := WORD_TO_INT(drv_y.sw);
z_sw := WORD_TO_INT(drv_z.sw);

IF abort THEN
    pl_step := 0;
    draw_started := FALSE;
    go_started := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    man_x := FALSE;
    man_y := FALSE;
    plot_done := FALSE;
    move_done := FALSE;
    tx := ix_x.Setpoint;
    ty := ix_y.Setpoint;
    tz := ix_z.Setpoint;
END_IF;

(* level-gated home: pen up to Z reference 10mm *)
IF cmd_home AND run AND NOT abort AND (pl_step = 0) AND NOT man_x AND NOT go_z_exe THEN
    tz := 10;
    go_z_exe := TRUE;
END_IF;

(* C1: manual positioning rejected while pen down; level-gated *)
IF cmd_go AND run AND NOT abort AND (pl_step = 0) AND NOT pen_down AND NOT man_x AND NOT go_started THEN
    tx := x_sp;
    ty := y_sp;
    man_x := TRUE;
    man_y := TRUE;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_started := TRUE;
    move_done := FALSE;
END_IF;

IF man_x AND NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done THEN
    move_done := TRUE;
    man_x := FALSE;
    man_y := FALSE;
    go_started := FALSE;
END_IF;
IF NOT cmd_go AND NOT man_x THEN
    go_started := FALSE;
END_IF;

(* C6: draw start level-gated; step 1 lifts pen if down *)
IF cmd_draw AND run AND NOT abort AND (pl_step = 0) AND NOT plot_done AND NOT draw_started THEN
    plot_done := FALSE;
    move_done := FALSE;
    tx := x_fb;
    ty := y_fb;
    tz := 10;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
    draw_started := TRUE;
    pl_step := 1;
END_IF;
IF (pl_step = 0) AND NOT cmd_draw THEN
    draw_started := FALSE;
END_IF;

(* C1: jog only pen up, enabled, idle; level increments repeated targets *)
IF (pl_step = 0) AND run AND NOT abort AND NOT pen_down AND NOT man_x THEN
    IF jog_fwd AND (tx < 100) THEN
        tx := tx + 1;
        go_x_exe := TRUE;
    END_IF;
    IF jog_rev AND (tx > 0) THEN
        tx := tx - 1;
        go_x_exe := TRUE;
    END_IF;
END_IF;

(* sequence step table: 1 lift, 2 start point, 3 pen down,
   4..27 = 24 circle segments (k=1..23 then closure k=0),
   28 lift, 29 back to center *)
CASE pl_step OF
    1: tz := 10;
    2: tx := 50; ty := 25;
    3: tz := 0;
    4: tx := 57; ty := 26;
    5: tx := 63; ty := 28;
    6: tx := 68; ty := 32;
    7: tx := 72; ty := 38;
    8: tx := 74; ty := 44;
    9: tx := 75; ty := 50;
    10: tx := 74; ty := 57;
    11: tx := 72; ty := 63;
    12: tx := 68; ty := 68;
    13: tx := 63; ty := 72;
    14: tx := 57; ty := 74;
    15: tx := 50; ty := 75;
    16: tx := 44; ty := 74;
    17: tx := 38; ty := 72;
    18: tx := 32; ty := 68;
    19: tx := 28; ty := 63;
    20: tx := 26; ty := 57;
    21: tx := 25; ty := 50;
    22: tx := 26; ty := 44;
    23: tx := 28; ty := 38;
    24: tx := 32; ty := 32;
    25: tx := 38; ty := 28;
    26: tx := 44; ty := 26;
    27: tx := 50; ty := 25;
    28: tz := 10;
    29: tx := 50; ty := 50;
ELSE
    pl_step := pl_step;
END_CASE;

(* C2: advance only when all axes at target, interp idle *)
IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step >= 29 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

ix_x(fire := go_x_exe, pos_target := tx, hold_req := hold, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := go_y_exe, pos_target := ty, hold_req := hold, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := go_z_exe, pos_target := tz, hold_req := hold, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0, MAXPOS := 10.0);

cx := ix_x.Setpoint;
cy := ix_y.Setpoint;
cz := ix_z.Setpoint;

IF pl_step <> 0 THEN
    x_sp := cx;
    y_sp := cy;
    z_sp := cz;
END_IF;

xv_r := 0.0;
yv_r := 0.0;
zv_r := 0.0;
IF ix_x.Busy THEN xv_r := INT_TO_REAL(cx - prev_x) / 0.02; END_IF;
IF ix_y.Busy THEN yv_r := INT_TO_REAL(cy - prev_y) / 0.02; END_IF;
IF ix_z.Busy THEN zv_r := INT_TO_REAL(cz - prev_z) / 0.02; END_IF;
IF xv_r > 40.0 THEN xv_r := 40.0; ELSIF xv_r < -40.0 THEN xv_r := -40.0; END_IF;
IF yv_r > 40.0 THEN yv_r := 40.0; ELSIF yv_r < -40.0 THEN yv_r := -40.0; END_IF;
IF zv_r > 20.0 THEN zv_r := 20.0; ELSIF zv_r < -20.0 THEN zv_r := -20.0; END_IF;
x_v := REAL_TO_INT(xv_r);
y_v := REAL_TO_INT(yv_r);
z_v := REAL_TO_INT(zv_r);
prev_x := cx;
prev_y := cy;
prev_z := cz;

any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
all_oe := run AND NOT fault_latch;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
