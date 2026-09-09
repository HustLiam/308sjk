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
    inited : BOOL;
  END_VAR
IF NOT inited THEN
    Done := TRUE;
    inited := TRUE;
END_IF;

IF fire AND NOT edge_ir THEN
    IF (INT_TO_REAL(pos_target) > MAXPOS) OR (pos_target < 0) THEN
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
    v_ff : REAL;
    pos_err : REAL;
    v_out : REAL;
    sp_prev : INT;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
  END_VAR
(* ============ DRIVE402: axis drive (CSP following + position loop) ============ *)
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

(* ---- position loop: P control + setpoint differential feedforward ---- *)
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

(* ---- status word (bit0=ready,bit1=on,bit2=oe,bit3=fault,bit10=in-position) ---- *)
CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0031;
    3: sw := 16#0033;
    4: sw := 16#0037;
    5: sw := 16#0017;
    6: sw := 16#0013;
    7: sw := 16#0008;
END_CASE;
IF (state = 4) AND (v_out = 0.0) THEN
    sw := sw OR 16#0400;
END_IF;
v_cmd := REAL_TO_INT(v_out);
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    pstep_ax : INT := 0;
    cw_x : WORD;
    cw_y : WORD;
    cw_z : WORD;
    swx : WORD;
    swy : WORD;
    swz : WORD;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    jog_exe : BOOL;
    fire_x : BOOL;
    fire_y : BOOL;
    tx : INT;
    ty : INT;
    tz : INT;
    pl_step : INT := 0;
    abt : BOOL;
    vxy : REAL := 40.0;
    ang : REAL;
    hold : BOOL;
    pen_up_now : BOOL;
    oe_ok : BOOL;
    faultd : BOOL;
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

swx := drv_x.sw;
swy := drv_y.sw;
swz := drv_z.sw;
faultd := ((swx AND 16#0008) <> 0) OR ((swy AND 16#0008) <> 0) OR ((swz AND 16#0008) <> 0);
fault_any := faultd;
oe_ok := (pstep_ax = 4);
all_oe := oe_ok;
pen_up_now := (z_fb > 1);
hold := quickstop OR (NOT run) OR faultd;

(* ---- C5/C6/C7 abort: quickstop / fault / run off ---- *)
IF quickstop OR (NOT run) OR faultd THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    jog_exe := FALSE;
    plot_done := FALSE;
    IF quickstop OR faultd THEN
        abt := TRUE;
    END_IF;
END_IF;
IF cmd_reset AND (NOT quickstop) AND (NOT faultd) THEN
    abt := FALSE;
END_IF;

(* ---- drive enable handshake (CiA402: 0x06 -> 0x07 -> 0x0F) ---- *)
IF NOT run THEN
    cw_x := 16#0006;
    cw_y := 16#0006;
    cw_z := 16#0006;
    pstep_ax := 0;
ELSIF faultd THEN
    cw_x := 16#0000;
    cw_y := 16#0000;
    cw_z := 16#0000;
    pstep_ax := 0;
ELSE
    CASE pstep_ax OF
        0:  cw_x := 16#0006;
            cw_y := 16#0006;
            cw_z := 16#0006;
            IF ((swx AND 16#0001) <> 0) AND ((swy AND 16#0001) <> 0) AND ((swz AND 16#0001) <> 0) THEN
                pstep_ax := 1;
            END_IF;
        1:  cw_x := 16#0007;
            cw_y := 16#0007;
            cw_z := 16#0007;
            IF ((swx AND 16#0002) <> 0) AND ((swy AND 16#0002) <> 0) AND ((swz AND 16#0002) <> 0) THEN
                pstep_ax := 2;
            END_IF;
        2:  cw_x := 16#000F;
            cw_y := 16#000F;
            cw_z := 16#000F;
            IF ((swx AND 16#0004) <> 0) AND ((swy AND 16#0004) <> 0) AND ((swz AND 16#0004) <> 0) THEN
                pstep_ax := 3;
            END_IF;
        3:  cw_x := 16#000F;
            cw_y := 16#000F;
            cw_z := 16#000F;
            pstep_ax := 4;
        4:  cw_x := 16#000F;
            cw_y := 16#000F;
            cw_z := 16#000F;
            IF quickstop THEN
                cw_x := 16#0002;
                cw_y := 16#0002;
                cw_z := 16#0002;
                pstep_ax := 0;
            END_IF;
    END_CASE;
END_IF;

(* ---- targets: plot sequencer (level-held fire, continuous tracking) ---- *)
IF pl_step = 0 THEN
    IF (NOT oe_ok) OR abt OR (NOT run) THEN
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        jog_exe := FALSE;
    END_IF;
    (* C1/AC3: manual positioning rejected while pen down *)
    IF cmd_go AND pen_up_now AND oe_ok AND (NOT abt) AND run THEN
        tx := x_sp;
        ty := y_sp;
        go_x_exe := TRUE;
        go_y_exe := TRUE;
    END_IF;
    (* C1/AC2: jog only while pen up *)
    IF jog_fwd AND (NOT jog_rev) AND pen_up_now AND oe_ok AND (NOT abt) AND run THEN
        IF x_fb < 98 THEN
            tx := x_fb + 2;
        END_IF;
        jog_exe := TRUE;
    ELSIF jog_rev AND (NOT jog_fwd) AND pen_up_now AND oe_ok AND (NOT abt) AND run THEN
        IF x_fb > 2 THEN
            tx := x_fb - 2;
        END_IF;
        jog_exe := TRUE;
    ELSE
        jog_exe := FALSE;
    END_IF;
    IF (NOT go_x_exe) AND (NOT go_y_exe) AND (NOT jog_exe) THEN
        IF cmd_home THEN
            tz := 10;
        ELSE
            tz := z_sp;
        END_IF;
        go_z_exe := TRUE;
    END_IF;
ELSIF pl_step = 1 THEN
    tx := 50;
    ty := 25;
    tz := 10;
ELSIF pl_step = 2 THEN
    tz := 0;
ELSIF (pl_step >= 3) AND (pl_step <= 26) THEN
    ang := INT_TO_REAL(pl_step - 2) * 0.2617994;
    tx := REAL_TO_INT(50.0 + 25.0 * SIN(ang) + 0.5);
    ty := REAL_TO_INT(50.0 - 25.0 * COS(ang) + 0.5);
    tz := 0;
ELSIF pl_step = 27 THEN
    tx := 50;
    ty := 25;
    tz := 0;
ELSIF pl_step = 28 THEN
    tz := 10;
ELSIF pl_step = 29 THEN
    tx := 50;
    ty := 50;
    tz := 10;
END_IF;

(* ---- C7: draw start gate ---- *)
IF (pl_step = 0) AND cmd_draw AND run AND oe_ok AND (NOT abt) AND (NOT quickstop) AND (NOT faultd) THEN
    plot_done := FALSE;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
    pl_step := 1;
END_IF;

fire_x := go_x_exe OR jog_exe;
fire_y := go_y_exe;

(* C3: pen-down segments limited to half speed (20) *)
IF (pl_step >= 3) AND (pl_step <= 27) THEN
    vxy := 20.0;
ELSE
    vxy := 40.0;
END_IF;

ix_x(fire := fire_x, pos_target := tx, hold_req := hold, VMAX := vxy, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := fire_y, pos_target := ty, hold_req := hold, VMAX := vxy, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := go_z_exe, pos_target := tz, hold_req := hold, VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10.0);

drv_x(cw := cw_x, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
drv_y(cw := cw_y, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
drv_z(cw := cw_z, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

x_v := drv_x.v_cmd;
y_v := drv_y.v_cmd;
z_v := drv_z.v_cmd;
x_sw := WORD_TO_INT(drv_x.sw);
y_sw := WORD_TO_INT(drv_y.sw);
z_sw := WORD_TO_INT(drv_z.sw);

(* ---- sequencer advance: all axes idle and done ---- *)
IF (pl_step <> 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 29 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := TRUE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

pen_down := (z_fb <= 2);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := (pl_step = 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND ix_x.Done AND ix_y.Done AND (NOT go_x_exe) AND (NOT jog_exe);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
