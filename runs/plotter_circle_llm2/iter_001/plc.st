PROGRAM PLC_PRG
  VAR
    edge_draw : BOOL;
    edge_home : BOOL;
    edge_go : BOOL;
    edge_reset : BOOL;
    prev_draw : BOOL;
    prev_home : BOOL;
    prev_go : BOOL;
    prev_reset : BOOL;
    gate : BOOL;
    aborted : BOOL;
    home_busy : BOOL;
    go_busy : BOOL;
    jogging : BOOL;
    step : INT := 0;
    gx : INT := 50;
    gy : INT := 50;
    theta : REAL := -1.5707963;
    x_tgt : REAL;
    y_tgt : REAL;
    z_tgt : REAL;
    ex : REAL;
    ey : REAL;
    ez : REAL;
    vx : REAL;
    vy : REAL;
    vz : REAL;
    xff : REAL;
    yff : REAL;
    w_x_sw : WORD;
    w_y_sw : WORD;
    w_z_sw : WORD;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.01;
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
prog_id := 2;

(* ---- edge detection ---- *)
edge_draw := cmd_draw AND NOT prev_draw;
edge_home := cmd_home AND NOT prev_home;
edge_go := cmd_go AND NOT prev_go;
edge_reset := cmd_reset AND NOT prev_reset;
prev_draw := cmd_draw;
prev_home := cmd_home;
prev_go := cmd_go;
prev_reset := cmd_reset;

(* ---- drive status words: bit3=fault bit10=in-position ---- *)
IF run THEN
    w_x_sw := 16#0037;
    w_y_sw := 16#0037;
    w_z_sw := 16#0037;
ELSE
    w_x_sw := 16#0040;
    w_y_sw := 16#0040;
    w_z_sw := 16#0040;
END_IF;

(* ---- fault and gate (C3/C4) ---- *)
fault_any := (w_x_sw AND 16#0008) <> 0 OR (w_y_sw AND 16#0008) <> 0 OR (w_z_sw AND 16#0008) <> 0;
gate := run AND NOT quickstop AND NOT fault_any;

(* ---- abort handling (C1/C4): estop or fault kills sequence, plot_done off ---- *)
IF quickstop OR fault_any THEN
    plot_done := FALSE;
    IF step >= 1 AND step <= 7 THEN
        step := 0;
        aborted := TRUE;
    END_IF;
END_IF;

(* ---- reset clears abort latch ---- *)
IF edge_reset THEN
    aborted := FALSE;
END_IF;

(* ---- sequence start: draw requires run, no estop, no fault, not aborted (C3) ---- *)
IF edge_draw AND gate AND NOT aborted AND step = 0 THEN
    step := 1;
    plot_done := FALSE;
END_IF;

IF edge_home AND gate AND step = 0 THEN
    home_busy := TRUE;
END_IF;

(* ---- manual go rejected while pen down (C2) ---- *)
IF edge_go AND gate AND step = 0 AND NOT pen_down THEN
    go_busy := TRUE;
    gx := x_sp;
    gy := y_sp;
END_IF;

(* ---- jog only while pen up (C2) ---- *)
jogging := (jog_fwd OR jog_rev) AND gate AND step = 0 AND z_fb > 1;

(* ---- sequencer (C5): pen up -> start point (50,25) -> pen down -> full circle -> pen up -> center ---- *)
xff := 0.0;
yff := 0.0;
CASE step OF
    0: IF home_busy THEN
           z_tgt := 10.0;
           x_tgt := INT_TO_REAL(x_fb);
           y_tgt := INT_TO_REAL(y_fb);
           IF z_fb >= 9 THEN home_busy := FALSE; END_IF;
       ELSIF go_busy THEN
           x_tgt := INT_TO_REAL(gx);
           y_tgt := INT_TO_REAL(gy);
           z_tgt := INT_TO_REAL(z_sp);
           IF pen_down THEN go_busy := FALSE; END_IF;
           IF (ABS(x_fb - gx) <= 2) AND (ABS(y_fb - gy) <= 2) THEN go_busy := FALSE; END_IF;
       ELSIF jogging THEN
           IF jog_fwd THEN x_tgt := MIN(100.0, INT_TO_REAL(x_fb) + 3.0);
           ELSE x_tgt := MAX(0.0, INT_TO_REAL(x_fb) - 3.0); END_IF;
           y_tgt := INT_TO_REAL(y_fb);
           z_tgt := INT_TO_REAL(z_fb);
       ELSE
           x_tgt := INT_TO_REAL(x_fb);
           y_tgt := INT_TO_REAL(y_fb);
           z_tgt := INT_TO_REAL(z_sp);
       END_IF;
    1: z_tgt := 10.0;
       x_tgt := INT_TO_REAL(x_fb);
       y_tgt := INT_TO_REAL(y_fb);
       IF z_fb >= 9 THEN step := 2; END_IF;
    2: x_tgt := 50.0;
       y_tgt := 25.0;
       z_tgt := 10.0;
       IF (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 25) <= 2) THEN step := 3; END_IF;
    3: x_tgt := 50.0;
       y_tgt := 25.0;
       z_tgt := 0.0;
       IF z_fb <= 1 THEN
           theta := -1.5707963;
           step := 4;
       END_IF;
    4: theta := theta + 1.6 * SCAN_T;
       x_tgt := 50.0 + 25.0 * COS(theta);
       y_tgt := 50.0 + 25.0 * SIN(theta);
       z_tgt := 0.0;
       xff := -25.0 * SIN(theta) * 1.6;
       yff := 25.0 * COS(theta) * 1.6;
       IF theta >= 4.7123890 THEN step := 5; END_IF;
    5: x_tgt := 50.0;
       y_tgt := 25.0;
       z_tgt := 10.0;
       IF z_fb >= 9 THEN step := 6; END_IF;
    6: x_tgt := 50.0;
       y_tgt := 50.0;
       z_tgt := 10.0;
       IF (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 50) <= 2) THEN
           step := 7;
           plot_done := TRUE;
       END_IF;
    7: x_tgt := 50.0;
       y_tgt := 50.0;
       z_tgt := 10.0;
       step := step;
END_CASE;

(* ---- position loop with circle feedforward, gated output (C1/C3/C4) ---- *)
ex := x_tgt - INT_TO_REAL(x_fb);
ey := y_tgt - INT_TO_REAL(y_fb);
ez := z_tgt - INT_TO_REAL(z_fb);
IF gate THEN
    vx := 8.0 * ex + 0.8 * xff;
    vy := 8.0 * ey + 0.8 * yff;
    vz := 6.0 * ez;
    IF vx > 120.0 THEN vx := 120.0; END_IF;
    IF vx < -120.0 THEN vx := -120.0; END_IF;
    IF vy > 120.0 THEN vy := 120.0; END_IF;
    IF vy < -120.0 THEN vy := -120.0; END_IF;
    IF vz > 20.0 THEN vz := 20.0; END_IF;
    IF vz < -20.0 THEN vz := -20.0; END_IF;
    IF (ABS(ex) <= 0.5) AND (xff = 0.0) THEN vx := 0.0; END_IF;
    IF (ABS(ey) <= 0.5) AND (yff = 0.0) THEN vy := 0.0; END_IF;
    IF ABS(ez) <= 0.3 THEN vz := 0.0; END_IF;
ELSE
    vx := 0.0;
    vy := 0.0;
    vz := 0.0;
END_IF;
x_v := REAL_TO_INT(vx);
y_v := REAL_TO_INT(vy);
z_v := REAL_TO_INT(vz);

(* ---- in-position bits ---- *)
IF run AND (ABS(ex) <= 2.0) AND (vx = 0.0) THEN w_x_sw := w_x_sw OR 16#0400; END_IF;
IF run AND (ABS(ey) <= 2.0) AND (vy = 0.0) THEN w_y_sw := w_y_sw OR 16#0400; END_IF;
IF run AND (ABS(ez) <= 1.0) AND (vz = 0.0) THEN w_z_sw := w_z_sw OR 16#0400; END_IF;
x_sw := WORD_TO_INT(w_x_sw);
y_sw := WORD_TO_INT(w_y_sw);
z_sw := WORD_TO_INT(w_z_sw);

(* ---- application outputs (C6) ---- *)
all_oe := run AND NOT fault_any;
pen_down := z_fb <= 1;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := (step = 0) AND NOT go_busy AND NOT home_busy AND NOT jogging
             AND (ABS(ex) <= 2.0) AND (ABS(ey) <= 2.0) AND (ABS(ez) <= 1.0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
