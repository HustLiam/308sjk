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
        (* target out of range: no start *)
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

PROGRAM PLC_PRG
  VAR
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    draw_pv : BOOL;
    go_pv : BOOL;
    home_pv : BOOL;
    go_exe : BOOL;
    home_exe : BOOL;
    man_done : BOOL;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    hold_all : BOOL;
    pl_tx : INT := 50;
    pl_ty : INT := 50;
    pl_tz : INT := 10;
    pl_step : INT := 0;
    seg : INT := 0;
    kk : INT;
    tx_use : INT;
    ty_use : INT;
    tz_use : INT;
    ang : REAL;
    verr : REAL;
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
  END_VAR
prog_id := 3;

(* physical pen state: pure position judgement *)
pen_down := (z_fb <= 2);

(* fault source: none wired in this plant; diagnostic word bit3 mirror *)
fault_any := FALSE;

(* enable chain: C2/AC1/AC6 *)
all_oe := run AND NOT quickstop;

(* edge memories *)
IF cmd_draw AND NOT draw_pv AND all_oe AND NOT quickstop AND (pl_step = 0) AND (z_fb >= 9) THEN
    plot_done := FALSE;
    man_done := FALSE;
    go_exe := FALSE;
    seg := 0;
    pl_step := 1;
END_IF;
draw_pv := cmd_draw;

IF cmd_go AND NOT go_pv AND (z_fb >= 9) AND (pl_step = 0) AND all_oe AND NOT quickstop THEN
    go_exe := TRUE;
    man_done := FALSE;
END_IF;
go_pv := cmd_go;

IF cmd_home AND NOT home_pv AND all_oe AND NOT quickstop THEN
    home_exe := TRUE;
END_IF;
home_pv := cmd_home;

(* abort on quickstop or fault: C3/C6 level abort, auto release *)
IF quickstop OR fault_any THEN
    pl_step := 0;
    go_exe := FALSE;
    home_exe := FALSE;
    plot_done := FALSE;
    man_done := FALSE;
END_IF;

(* sequence step targets *)
CASE pl_step OF
    0: pl_tx := pl_tx;  (* idle: keep last *)
    1: pl_tx := 50; pl_ty := 25; pl_tz := 10;  (* pen up, position at circle start *)
    2: pl_tz := 0;                              (* pen down, XY unchanged *)
    3: IF seg <= 23 THEN kk := seg; ELSE kk := 0; END_IF;
       ang := INT_TO_REAL(kk) * 0.2617994;      (* 2*pi/24 *)
       pl_tx := REAL_TO_INT(50.0 + 25.0 * SIN(ang));
       pl_ty := REAL_TO_INT(50.0 - 25.0 * COS(ang));
       pl_tz := 0;
    4: pl_tz := 10;                             (* pen up *)
    5: pl_tx := 50; pl_ty := 50; pl_tz := 10;   (* return to center *)
END_CASE;

(* target selection *)
IF pl_step <> 0 THEN
    tx_use := pl_tx;
    ty_use := pl_ty;
    tz_use := pl_tz;
ELSIF home_exe THEN
    tz_use := 10;
    tx_use := x_sp;
    ty_use := y_sp;
ELSE
    tx_use := x_sp;
    ty_use := y_sp;
    tz_use := z_sp;
END_IF;

(* fire flags: level held during sequence / manual moves; XY fire gated by pen-up (C1) *)
fire_x := all_oe AND NOT quickstop AND ((pl_step <> 0) OR go_exe) AND (pl_step <> 0 OR z_fb >= 9);
fire_y := fire_x;
fire_z := all_oe AND NOT quickstop;
hold_all := (NOT all_oe) OR quickstop;

ix_x(fire := fire_x, pos_target := tx_use, hold_req := hold_all, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := fire_y, pos_target := ty_use, hold_req := hold_all, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := fire_z, pos_target := tz_use, hold_req := hold_all, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0, MAXPOS := 10);

(* sequence advance: all axes idle and done *)
IF (pl_step <> 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    CASE pl_step OF
        1: pl_step := 2;
        2: pl_step := 3; seg := 1;
        3: IF seg >= 24 THEN pl_step := 4; ELSE seg := seg + 1; END_IF;
        4: pl_step := 5;
        5: plot_done := TRUE; pl_step := 0;
    END_CASE;
END_IF;

(* manual move completion *)
IF go_exe AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND ix_x.Done AND ix_y.Done THEN
    go_exe := FALSE;
    man_done := TRUE;
END_IF;

IF home_exe AND (NOT ix_z.Busy) AND ix_z.Done AND (z_fb >= 9) THEN
    home_exe := FALSE;
END_IF;

(* velocity commands: servo toward interp setpoint, clamped to axis limits (C5/AC8) *)
IF all_oe AND NOT quickstop AND ix_x.Busy THEN
    verr := INT_TO_REAL(ix_x.Setpoint - x_fb) * 10.0;
    IF verr > 40.0 THEN verr := 40.0; END_IF;
    IF verr < -40.0 THEN verr := -40.0; END_IF;
    x_v := REAL_TO_INT(verr);
ELSE
    x_v := 0;
END_IF;

IF all_oe AND NOT quickstop AND ix_y.Busy THEN
    verr := INT_TO_REAL(ix_y.Setpoint - y_fb) * 10.0;
    IF verr > 40.0 THEN verr := 40.0; END_IF;
    IF verr < -40.0 THEN verr := -40.0; END_IF;
    y_v := REAL_TO_INT(verr);
ELSE
    y_v := 0;
END_IF;

IF all_oe AND NOT quickstop AND ix_z.Busy THEN
    verr := INT_TO_REAL(ix_z.Setpoint - z_fb) * 10.0;
    IF verr > 20.0 THEN verr := 20.0; END_IF;
    IF verr < -20.0 THEN verr := -20.0; END_IF;
    z_v := REAL_TO_INT(verr);
ELSE
    z_v := 0;
END_IF;

(* diagnostic status words: bit10 = in position *)
x_sw := 0;
IF ix_x.Done AND (NOT ix_x.Busy) THEN x_sw := 1024; END_IF;
y_sw := 0;
IF ix_y.Done AND (NOT ix_y.Busy) THEN y_sw := 1024; END_IF;
z_sw := 0;
IF ix_z.Done AND (NOT ix_z.Busy) THEN z_sw := 1024; END_IF;

(* indicators *)
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := man_done;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
