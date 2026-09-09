FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL;
    ACCEL : REAL;
    POSWIN : REAL;
    MAXPOS : INT;
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
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        (* target out of range: do not start, hold position *)
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
    IF vel > 0.0 THEN vel := MAX(0.0, vel - ACCEL * 0.02);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + ACCEL * 0.02);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * ACCEL);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - ACCEL * 0.02);
        ELSE vel := MIN(0.0, vel + ACCEL * 0.02);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(VMAX, vel + ACCEL * 0.02);
        ELSE vel := MAX(-VMAX, vel - ACCEL * 0.02);
        END_IF;
    END_IF;
END_IF;

pos := pos + vel * 0.02;
Setpoint := REAL_TO_INT(pos);
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    ix : INTERP;
    iy : INTERP;
    iz : INTERP;
    x_t : INT := 50;
    y_t : INT := 50;
    z_t : INT := 10;
    go_x : BOOL;
    go_y : BOOL;
    go_z : BOOL;
    go_mode : BOOL;
    pl_step : INT := 0;
    seg : INT := 1;
    draw_prev : BOOL;
    go_prev : BOOL;
    draw_edge : BOOL;
    go_edge : BOOL;
    ready : BOOL;
    hold : BOOL;
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

(* ---------- edge detection ---------- *)
draw_edge := cmd_draw AND NOT draw_prev;
draw_prev := cmd_draw;
go_edge := cmd_go AND NOT go_prev;
go_prev := cmd_go;

(* ---------- fault detection (C5): sw bit3 ---------- *)
fault_any := ((INT_TO_WORD(x_sw) AND 16#0008) <> 16#0000)
          OR ((INT_TO_WORD(y_sw) AND 16#0008) <> 16#0000)
          OR ((INT_TO_WORD(z_sw) AND 16#0008) <> 16#0000);

(* ---------- enable / ready (C2, C4) ---------- *)
ready := run AND (NOT quickstop) AND (NOT fault_any);
all_oe := ready;

(* ---------- level abort: quickstop / fault / run-off (C2, C4, C5) ---------- *)
IF (NOT run) OR quickstop OR fault_any THEN
    pl_step := 0;
    seg := 1;
    plot_done := FALSE;
    go_x := FALSE;
    go_y := FALSE;
    go_z := FALSE;
    go_mode := FALSE;
END_IF;

(* ---------- draw sequence (C3) ---------- *)
CASE pl_step OF
    0: IF draw_edge AND ready THEN        (* C4: no start when not enabled *)
           z_t := 10;                      (* pen up first *)
           go_z := TRUE;
           seg := 1;
           pl_step := 1;
       END_IF;
    1: IF iz.Done AND (NOT iz.Busy) THEN  (* pen up completed -> move to circle start *)
           x_t := 50;
           y_t := 25;
           go_x := TRUE;
           go_y := TRUE;
           pl_step := 2;
       END_IF;
    2: IF ix.Done AND iy.Done AND (NOT ix.Busy) AND (NOT iy.Busy) THEN
           z_t := 0;                       (* pen down, stable before XY sweep (C6) *)
           go_z := TRUE;
           pl_step := 3;
       END_IF;
    3: CASE seg OF                        (* 24-segment circle, targets only (C7 via POSWIN) *)
           1:  x_t := 57;  y_t := 26;
           2:  x_t := 63;  y_t := 28;
           3:  x_t := 68;  y_t := 32;
           4:  x_t := 72;  y_t := 38;
           5:  x_t := 74;  y_t := 44;
           6:  x_t := 75;  y_t := 50;
           7:  x_t := 74;  y_t := 57;
           8:  x_t := 72;  y_t := 63;
           9:  x_t := 68;  y_t := 68;
           10: x_t := 63;  y_t := 72;
           11: x_t := 57;  y_t := 74;
           12: x_t := 50;  y_t := 75;
           13: x_t := 44;  y_t := 74;
           14: x_t := 38;  y_t := 72;
           15: x_t := 32;  y_t := 68;
           16: x_t := 28;  y_t := 63;
           17: x_t := 26;  y_t := 57;
           18: x_t := 25;  y_t := 50;
           19: x_t := 26;  y_t := 44;
           20: x_t := 28;  y_t := 38;
           21: x_t := 32;  y_t := 32;
           22: x_t := 38;  y_t := 28;
           23: x_t := 44;  y_t := 26;
           24: x_t := 50;  y_t := 25;      (* close back to start *)
           ELSE x_t := 50; y_t := 25;
       END_CASE;
    4: IF iz.Done AND (NOT iz.Busy) THEN  (* pen lifted -> return to center *)
           x_t := 50;
           y_t := 50;
           go_x := TRUE;
           go_y := TRUE;
           pl_step := 5;
       END_IF;
    5: IF ix.Done AND iy.Done AND (NOT ix.Busy) AND (NOT iy.Busy) THEN
           plot_done := TRUE;              (* sequence complete *)
           go_x := FALSE;
           go_y := FALSE;
           go_z := FALSE;
           pl_step := 0;
       END_IF;
    ELSE pl_step := 0;
END_CASE;

(* circle segment advance *)
IF (pl_step = 3) AND go_x AND go_y
   AND ix.Done AND iy.Done AND (NOT ix.Busy) AND (NOT iy.Busy) THEN
    IF seg < 24 THEN
        seg := seg + 1;
    ELSE
        z_t := 10;                          (* pen up after closing *)
        go_z := TRUE;
        pl_step := 4;
    END_IF;
END_IF;

(* ---------- manual jog (C1: blocked while pen down) ---------- *)
IF (pl_step = 0) AND ready AND (NOT pen_down) AND (NOT go_mode) THEN
    IF jog_fwd THEN
        x_t := MIN(100, x_t + 1);
        go_x := TRUE;
    END_IF;
    IF jog_rev THEN
        x_t := MAX(0, x_t - 1);
        go_x := TRUE;
    END_IF;
END_IF;

(* ---------- manual go (C1: blocked while pen down / during draw) ---------- *)
IF go_edge AND (pl_step = 0) AND ready AND (NOT pen_down) THEN
    go_mode := TRUE;
    x_t := x_sp;
    y_t := y_sp;
    go_x := TRUE;
    go_y := TRUE;
END_IF;
IF go_mode AND ix.Done AND iy.Done AND (NOT ix.Busy) AND (NOT iy.Busy) THEN
    go_x := FALSE;
    go_y := FALSE;
END_IF;
move_done := go_mode AND ix.Done AND iy.Done AND (NOT ix.Busy) AND (NOT iy.Busy);

(* ---------- axis interpolation ---------- *)
hold := (NOT run) OR quickstop OR fault_any;

ix(fire := go_x AND ready AND ((NOT go_z) OR (iz.Done AND (NOT iz.Busy))),
   pos_target := x_t, hold_req := hold,
   VMAX := 40.0, ACCEL := 100.0, POSWIN := 2.0, MAXPOS := 100);

iy(fire := go_y AND ready AND ((NOT go_z) OR (iz.Done AND (NOT iz.Busy))),
   pos_target := y_t, hold_req := hold,
   VMAX := 40.0, ACCEL := 100.0, POSWIN := 2.0, MAXPOS := 100);

iz(fire := go_z AND ready AND ((NOT go_x) OR (ix.Done AND (NOT ix.Busy))),
   pos_target := z_t, hold_req := hold,
   VMAX := 20.0, ACCEL := 50.0, POSWIN := 1.0, MAXPOS := 10);

(* ---------- velocity outputs + simulated servo feedback ---------- *)
x_v := ix.Setpoint - x_fb;
x_fb := ix.Setpoint;
y_v := iy.Setpoint - y_fb;
y_fb := iy.Setpoint;
z_v := iz.Setpoint - z_fb;
z_fb := iz.Setpoint;

(* C2/C4: force zero velocity commands while quickstop active or drives off *)
IF (NOT run) OR quickstop THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
pen_down := (z_fb <= 2);

(* ---------- drive status words: bit10 = in position, bit3 = fault (never local) ---------- *)
IF all_oe THEN
    x_sw := 16#0400;
    y_sw := 16#0400;
    z_sw := 16#0400;
ELSE
    x_sw := 16#0000;
    y_sw := 16#0000;
    z_sw := 16#0000;
END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
