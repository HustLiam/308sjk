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
    SCAN_T : REAL := 0.02;
    pos : REAL;
    tgt : REAL;
    vel : REAL;
    edge_ir : BOOL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
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
        ELSE vel := MAX(-VMAX, vel - ACCEL * SCAN_T);
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
    ton_seg : TON;
    run_ok : BOOL;
    qs_stop : BOOL;
    dr_mem : BOOL;
    go_mem : BOOL;
    dr_trig : BOOL;
    go_trig : BOOL;
    pl_step : INT := 0;
    seg : INT := 0;
    kk : INT;
    ang : REAL;
    tx : INT := 50;
    ty : INT := 50;
    tz : INT := 10;
    go_x : BOOL;
    go_y : BOOL;
    go_z : BOOL;
    man_go : BOOL;
    fault_l : BOOL;
    x_prev : INT;
    y_prev : INT;
    z_prev : INT;
    x_dlt : INT;
    y_dlt : INT;
    z_dlt : INT;
    sw_x_w : WORD;
    sw_y_w : WORD;
    sw_z_w : WORD;
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

(* C2/C3/C4: enable chain *)
run_ok := run AND NOT quickstop AND NOT fault_l;
qs_stop := quickstop OR fault_l OR NOT run;

(* edge detection *)
dr_trig := cmd_draw AND NOT dr_mem;
go_trig := cmd_go AND NOT go_mem;
dr_mem := cmd_draw;
go_mem := cmd_go;

(* fault reset (C2) *)
IF cmd_reset AND NOT quickstop THEN
    fault_l := FALSE;
END_IF;

(* abort: quickstop / fault / run off (C2/C3/C4) *)
IF quickstop OR fault_l OR NOT run THEN
    pl_step := 0;
    seg := 0;
    go_x := FALSE;
    go_y := FALSE;
    go_z := FALSE;
    man_go := FALSE;
    plot_done := FALSE;
END_IF;

(* ================= sequence step targets ================= *)
CASE pl_step OF
    0:  IF dr_trig AND run_ok THEN
            plot_done := FALSE;
            man_go := FALSE;
            seg := 0;
            tz := 10;              (* lift pen first (AC2) *)
            tx := 50;
            ty := 25;
            go_x := TRUE;
            go_y := TRUE;
            go_z := TRUE;
            pl_step := 1;
        END_IF;
        IF run_ok THEN
            (* manual z: not pen-interlocked *)
            tz := z_sp;
            go_z := TRUE;
            IF cmd_home THEN
                tz := 10;          (* Z reference = pen-up safe position *)
            END_IF;
            (* C1: X/Y manual motion only with pen up *)
            IF (jog_fwd OR jog_rev) AND (z_fb >= 9) THEN
                go_x := TRUE;
                IF jog_fwd THEN tx := MIN(100, tx + 1); END_IF;
                IF jog_rev THEN tx := MAX(0, tx - 1); END_IF;
            END_IF;
            IF go_trig THEN
                tz := z_sp;
                go_z := TRUE;
                IF z_fb >= 9 THEN
                    tx := x_sp;
                    ty := y_sp;
                    go_x := TRUE;
                    go_y := TRUE;
                    man_go := TRUE;
                END_IF;
            END_IF;
        END_IF;
    1:  tz := 10;                  (* lift pen, hold XY target (50,25) *)
    2:  tx := 50;                  (* XY fast position to circle start *)
        ty := 25;
    3:  tz := 0;                   (* pen down: XY already in position (C8) *)
    4:  kk := seg MOD 24;          (* 24-chord circle sweep, 15 deg steps *)
        IF seg > 0 THEN
            ang := INT_TO_REAL(kk) * 0.2617994;
            tx := REAL_TO_INT(50.0 + 25.0 * SIN(ang) + 0.5);
            ty := REAL_TO_INT(50.0 - 25.0 * COS(ang) + 0.5);
        END_IF;
    5:  tz := 10;                  (* lift pen at closure point *)
    6:  tx := 50;                  (* return to center *)
        ty := 50;
END_CASE;

(* ================= sequence transitions ================= *)
IF pl_step = 1 THEN
    IF (NOT ix_z.Busy) AND ix_z.Done AND (z_fb >= 9) THEN
        pl_step := 2;
    END_IF;
ELSIF pl_step = 2 THEN
    IF (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done THEN
        pl_step := 3;
    END_IF;
ELSIF pl_step = 3 THEN
    IF (NOT ix_z.Busy) AND ix_z.Done AND (z_fb <= 1) THEN
        seg := 0;
        pl_step := 4;
    END_IF;
ELSIF pl_step = 4 THEN
    IF (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done
       AND (NOT ix_z.Busy) AND ix_z.Done THEN
        IF seg = 0 THEN
            seg := 1;
        ELSIF seg < 24 THEN
            seg := seg + 1;
        ELSE
            pl_step := 5;          (* circle closed back at (50,25) *)
        END_IF;
    END_IF;
ELSIF pl_step = 5 THEN
    IF (NOT ix_z.Busy) AND ix_z.Done AND (z_fb >= 9) THEN
        pl_step := 6;
    END_IF;
ELSIF pl_step = 6 THEN
    IF (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x := FALSE;
        go_y := FALSE;
        go_z := FALSE;
    END_IF;
END_IF;

(* C7: segment following-error timeout *)
ton_seg(IN := (pl_step = 4) AND (seg > 0) AND (ix_x.Busy OR ix_y.Busy), PT := T#3S);
IF ton_seg.Q THEN
    fault_l := TRUE;
END_IF;

(* ================= interpolator instances ================= *)
ix_x(fire := go_x AND run_ok, pos_target := tx, hold_req := qs_stop,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := go_y AND run_ok, pos_target := ty, hold_req := qs_stop,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := go_z AND run_ok, pos_target := tz, hold_req := qs_stop,
     VMAX := 20.0, ACCEL := 80.0, POSWIN := 1.0, MAXPOS := 10.0);

(* ================= velocity commands (setpoint derivative, scan 20ms) ================= *)
x_dlt := ix_x.Setpoint - x_prev;
y_dlt := ix_y.Setpoint - y_prev;
z_dlt := ix_z.Setpoint - z_prev;
x_prev := ix_x.Setpoint;
y_prev := ix_y.Setpoint;
z_prev := ix_z.Setpoint;

IF run_ok THEN
    x_v := x_dlt * 50;
    y_v := y_dlt * 50;
    z_v := z_dlt * 50;
ELSE
    x_v := 0;                     (* C2/C3/C4: commands zeroed *)
    y_v := 0;
    z_v := 0;
END_IF;

(* ================= status words (bit0=ready, bit3=fault, bit10=in-position) ================= *)
sw_x_w := 16#0000;
sw_y_w := 16#0000;
sw_z_w := 16#0000;
IF run_ok THEN
    sw_x_w := sw_x_w OR 16#0002;
    sw_y_w := sw_y_w OR 16#0002;
    sw_z_w := sw_z_w OR 16#0002;
END_IF;
IF fault_l THEN
    sw_x_w := sw_x_w OR 16#0008;
    sw_y_w := sw_y_w OR 16#0008;
    sw_z_w := sw_z_w OR 16#0008;
END_IF;
IF ABS(x_fb - tx) <= 2 THEN sw_x_w := sw_x_w OR 16#0400; END_IF;
IF ABS(y_fb - ty) <= 2 THEN sw_y_w := sw_y_w OR 16#0400; END_IF;
IF ABS(z_fb - tz) <= 1 THEN sw_z_w := sw_z_w OR 16#0400; END_IF;
x_sw := WORD_TO_INT(sw_x_w);
y_sw := WORD_TO_INT(sw_y_w);
z_sw := WORD_TO_INT(sw_z_w);

(* ================= outputs ================= *)
all_oe := run AND NOT quickstop AND NOT fault_l;
fault_any := fault_l;
pen_down := (z_fb <= 2);          (* pure physical pen state (AC5) *)
any_moving := run_ok AND (ix_x.Busy OR ix_y.Busy OR ix_z.Busy);
move_done := man_go AND (pl_step = 0)
             AND (NOT ix_x.Busy) AND ix_x.Done
             AND (NOT ix_y.Busy) AND ix_y.Done
             AND (NOT ix_z.Busy) AND ix_z.Done;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
