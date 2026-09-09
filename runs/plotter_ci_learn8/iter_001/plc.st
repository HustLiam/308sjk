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
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    dist : REAL;
    stop_d : REAL;
    edge_ir : BOOL;
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

FUNCTION_BLOCK DRIVE402
  VAR_INPUT
    cw : WORD;
    Setpoint : INT;
    pos_fb : INT;
  END_VAR
  VAR_OUTPUT
    sw : WORD;
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : WORD;
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
    sp_prev : INT := 0;
  END_VAR
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
ELSE
    state := 1;
END_CASE;

(* ---- position loop: P control + setpoint-diff feedforward ---- *)
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

(* ---- status word from state (CiA402) ---- *)
CASE state OF
    1: sw := 16#0010;
    2: sw := 16#0031;
    3: sw := 16#0032;
    4: sw := 16#0037;
    5: sw := 16#0017;
    6: sw := 16#001F;
    7: sw := 16#0008;
ELSE
    sw := 16#0010;
END_CASE;
IF (state = 4) AND (v_out = 0.0) THEN
    sw := sw OR 16#0400;
END_IF;

v_cmd := REAL_TO_INT(v_out);
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    cw : WORD;
    Status : BOOL;
  END_VAR
  VAR
    pstep : INT := 0;
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
    ELSE
        pstep := 0;
    END_CASE;
ELSE
    cw := (cw AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
Status := (sw AND 16#0004) <> 0;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    mcp_x : MC_POWER;
    mcp_y : MC_POWER;
    mcp_z : MC_POWER;
    dr_x : DRIVE402;
    dr_y : DRIVE402;
    dr_z : DRIVE402;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    prev_draw : BOOL;
    prev_go : BOOL;
    prev_rs : BOOL;
    plot_exe : BOOL;
    go_exe : BOOL;
    pl_step : INT := 0;
    kk : INT;
    tx : INT;
    ty : INT;
    tz : INT;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    cw_x : WORD;
    cw_y : WORD;
    cw_z : WORD;
    in_plot : BOOL;
    flt : BOOL;
    vmxy : REAL;
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

(* ---- C1: cmd_draw rising edge, only when all_oe and pen up ---- *)
IF cmd_draw AND NOT prev_draw AND all_oe AND (z_fb = 10) AND NOT quickstop AND NOT flt AND (pl_step = 0) THEN
    plot_exe := TRUE;
    plot_done := FALSE;
    pl_step := 1;
END_IF;
prev_draw := cmd_draw;

(* manual go (C3: X/Y rejected while pen down; Z free) ---- *)
IF cmd_go AND NOT prev_go AND (pl_step = 0) AND NOT quickstop AND NOT flt THEN
    go_exe := TRUE;
END_IF;
prev_go := cmd_go;

(* ---- C6: fault detection / reset ---- *)
IF ((dr_x.sw AND 16#0008) <> 16#0000) OR ((dr_y.sw AND 16#0008) <> 16#0000) OR ((dr_z.sw AND 16#0008) <> 16#0000) THEN
    flt := TRUE;
END_IF;
fault_any := flt;

IF cmd_reset AND NOT prev_rs AND NOT (((dr_x.sw AND 16#0008) <> 16#0000) OR ((dr_y.sw AND 16#0008) <> 16#0000) OR ((dr_z.sw AND 16#0008) <> 16#0000)) THEN
    flt := FALSE;
END_IF;
prev_rs := cmd_reset;

(* ---- C5/C6: abort sequence on quickstop / fault ---- *)
IF quickstop OR flt THEN
    pl_step := 0;
    plot_exe := FALSE;
    plot_done := FALSE;
    go_exe := FALSE;
END_IF;

in_plot := pl_step <> 0;

(* ---- sequence step table (authoritative plan) ---- *)
CASE pl_step OF
    0: tx := tx;
    1: tx := 50; ty := 25; tz := 10;   (* pen-up move to circle start *)
    2: tz := 0;                         (* pen down *)
    3..25: kk := pl_step - 2;           (* circle sweep k=1..23 *)
        tx := REAL_TO_INT(50.0 + 25.0 * SIN(INT_TO_REAL(kk) * 0.2617994));
        ty := REAL_TO_INT(50.0 - 25.0 * COS(INT_TO_REAL(kk) * 0.2617994));
        tz := 0;
    26: tx := 50; ty := 25; tz := 0;    (* closure back to start *)
    27: tz := 10;                       (* pen up *)
    28: tx := 50; ty := 50; tz := 10;   (* return to center *)
ELSE
    pl_step := 0;
END_CASE;

(* ---- mode mux: plot / jog / home / manual go ---- *)
IF in_plot THEN
    fire_x := plot_exe;
    fire_y := plot_exe;
    fire_z := plot_exe;
ELSIF (jog_fwd OR jog_rev) AND NOT pen_down AND all_oe AND NOT quickstop AND NOT flt THEN
    fire_x := TRUE;
    IF jog_fwd THEN tx := 100; ELSE tx := 0; END_IF;
    fire_y := FALSE;
    fire_z := FALSE;
ELSIF cmd_home AND NOT quickstop AND NOT flt AND NOT in_plot THEN
    fire_x := FALSE;
    fire_y := FALSE;
    fire_z := TRUE;
    tz := 10;
ELSIF go_exe THEN
    IF NOT pen_down THEN
        fire_x := TRUE; tx := x_sp;
        fire_y := TRUE; ty := y_sp;
    ELSE
        fire_x := FALSE;
        fire_y := FALSE;
    END_IF;
    fire_z := TRUE;
    tz := z_sp;
ELSE
    fire_x := FALSE;
    fire_y := FALSE;
    fire_z := FALSE;
END_IF;

(* ---- stepper: advance when all axes idle and done ---- *)
IF in_plot AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step >= 28 THEN
        plot_done := TRUE;
        pl_step := 0;
        plot_exe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

IF go_exe AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    go_exe := FALSE;
END_IF;

(* ---- C4/AC3/AC4: reduce XY speed while pen is down ---- *)
vmxy := 40.0;
IF pen_down THEN vmxy := 25.0; END_IF;

(* ---- interpolation engines ---- *)
ix_x(fire := fire_x, pos_target := tx, hold_req := quickstop OR flt, VMAX := vmxy, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := fire_y, pos_target := ty, hold_req := quickstop OR flt, VMAX := vmxy, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := fire_z, pos_target := tz, hold_req := quickstop OR flt, VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10.0);

(* ---- power / drives ---- *)
mcp_x(Enable := run AND NOT quickstop AND NOT flt, sw := dr_x.sw);
mcp_y(Enable := run AND NOT quickstop AND NOT flt, sw := dr_y.sw);
mcp_z(Enable := run AND NOT quickstop AND NOT flt, sw := dr_z.sw);

cw_x := mcp_x.cw;
cw_y := mcp_y.cw;
cw_z := mcp_z.cw;
IF flt AND cmd_reset THEN
    cw_x := cw_x OR 16#0080;
    cw_y := cw_y OR 16#0080;
    cw_z := cw_z OR 16#0080;
END_IF;

dr_x(cw := cw_x, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
dr_y(cw := cw_y, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
dr_z(cw := cw_z, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

(* ---- outputs ---- *)
x_sw := WORD_TO_INT(dr_x.sw);
y_sw := WORD_TO_INT(dr_y.sw);
z_sw := WORD_TO_INT(dr_z.sw);
x_v := dr_x.v_cmd;
y_v := dr_y.v_cmd;
z_v := dr_z.v_cmd;

all_oe := mcp_x.Status AND mcp_y.Status AND mcp_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
pen_down := z_fb <= 1;
move_done := NOT in_plot AND NOT go_exe AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
