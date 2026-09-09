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
    Done : BOOL := true;
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
        (* target out of range: no interpolation, hold position *)
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
    v_out : REAL := 0.0;
    sp_prev : INT := 0;
    v_ff : REAL;
    pos_err : REAL;
    cmd : WORD;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 200.0;
    SCAN_T : REAL := 0.02;
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
    4: IF cmd = 16#0F THEN state := 4;
       ELSIF cmd = 16#07 THEN state := 3;
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

IF state = 1 THEN
    sw := 16#0040;
ELSE
    sw := 16#0000;
    IF state >= 2 THEN sw := sw OR 16#0001; END_IF;
    IF state >= 3 THEN sw := sw OR 16#0002; END_IF;
    IF state >= 4 AND state <= 6 THEN sw := sw OR 16#0004; END_IF;
    IF state = 7 THEN sw := sw OR 16#0008; END_IF;
    IF state >= 4 AND state <= 6 AND v_out = 0.0 THEN sw := sw OR 16#0400; END_IF;
END_IF;
v_cmd := v_out;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
    cw : WORD;
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
        ELSE pstep := 0;
    END_CASE;
ELSE
    cw := (cw AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
Status := (sw AND 16#0004) <> 0;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    pw_x : MC_POWER;
    pw_y : MC_POWER;
    pw_z : MC_POWER;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    drv_x : DRIVE402;
    drv_y : DRIVE402;
    drv_z : DRIVE402;
    swx : WORD;
    swy : WORD;
    swz : WORD;
    cwx : WORD;
    cwy : WORD;
    cwz : WORD;
    fire_x : BOOL;
    fire_y : BOOL;
    fire_z : BOOL;
    tgt_x : INT;
    tgt_y : INT;
    tgt_z : INT := 10;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    pl_step : INT := 0;
    k : INT := 0;
    manual_active : BOOL;
    md_latch : BOOL;
    home_active : BOOL;
    jog_act : BOOL;
    fault_latch : BOOL;
    pwr_ok : BOOL;
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
    prog_id AT %QW20 : INT;
  END_VAR
prog_id := 3;

pwr_ok := run AND NOT fault_latch;

pw_x(Enable := pwr_ok, sw := swx);
pw_y(Enable := pwr_ok, sw := swy);
pw_z(Enable := pwr_ok, sw := swz);

cwx := pw_x.cw;
cwy := pw_y.cw;
cwz := pw_z.cw;

IF (swx AND 16#0008) <> 0 OR (swy AND 16#0008) <> 0 OR (swz AND 16#0008) <> 0 THEN
    fault_latch := TRUE;
END_IF;
IF cmd_reset THEN
    fault_latch := FALSE;
    cwx := cwx OR 16#0080;
    cwy := cwy OR 16#0080;
    cwz := cwz OR 16#0080;
END_IF;

IF quickstop THEN
    cwx := (cwx AND 16#00F0) OR 16#0002;
    cwy := (cwy AND 16#00F0) OR 16#0002;
    cwz := (cwz AND 16#00F0) OR 16#0002;
END_IF;

fault_any := fault_latch;
all_oe := pw_x.Status AND pw_y.Status AND pw_z.Status;

pen_down := z_fb <= 2;

IF (quickstop OR fault_latch) AND pl_step <> 0 THEN
    pl_step := 0;
    k := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    manual_active := FALSE;
    home_active := FALSE;
    jog_act := FALSE;
END_IF;
IF quickstop OR fault_latch THEN
    plot_done := FALSE;
    md_latch := FALSE;
END_IF;

IF cmd_go AND NOT pen_down AND run AND NOT fault_latch AND NOT quickstop
   AND pl_step = 0 AND NOT jog_act AND NOT manual_active THEN
    tgt_x := x_sp;
    tgt_y := y_sp;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    manual_active := TRUE;
    md_latch := FALSE;
END_IF;
IF manual_active AND NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done THEN
    manual_active := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    md_latch := TRUE;
END_IF;

IF cmd_home AND run AND NOT fault_latch AND NOT quickstop AND pl_step = 0
   AND NOT home_active THEN
    tgt_z := 10;
    go_z_exe := TRUE;
    home_active := TRUE;
END_IF;
IF home_active AND NOT ix_z.Busy AND ix_z.Done THEN
    home_active := FALSE;
    go_z_exe := FALSE;
END_IF;

IF run AND NOT fault_latch AND NOT quickstop AND NOT pen_down
   AND pl_step = 0 AND NOT manual_active AND NOT jog_act THEN
    IF jog_fwd AND NOT jog_rev AND tgt_x < 99 THEN
        tgt_x := tgt_x + 1;
        go_x_exe := TRUE;
        jog_act := TRUE;
    ELSIF jog_rev AND NOT jog_fwd AND tgt_x > 1 THEN
        tgt_x := tgt_x - 1;
        go_x_exe := TRUE;
        jog_act := TRUE;
    END_IF;
END_IF;
IF jog_act AND NOT jog_fwd AND NOT jog_rev
   AND NOT ix_x.Busy AND ix_x.Done THEN
    go_x_exe := FALSE;
    jog_act := FALSE;
END_IF;

IF cmd_draw AND run AND NOT fault_latch AND all_oe AND NOT quickstop
   AND pl_step = 0 AND NOT manual_active AND NOT jog_act AND NOT home_active THEN
    plot_done := FALSE;
    md_latch := FALSE;
    manual_active := FALSE;
    jog_act := FALSE;
    pl_step := 1;
    k := 0;
END_IF;

CASE pl_step OF
    1: tgt_x := 50;
       tgt_y := 25;
       tgt_z := 10;
       go_x_exe := TRUE;
       go_y_exe := TRUE;
       go_z_exe := TRUE;
    2: IF NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done
          AND NOT ix_z.Busy AND ix_z.Done THEN
           tgt_z := 0;
           go_z_exe := TRUE;
           k := 1;
       END_IF;
    3: CASE k OF
           1:  tgt_x := 57; tgt_y := 26;
           2:  tgt_x := 63; tgt_y := 28;
           3:  tgt_x := 68; tgt_y := 32;
           4:  tgt_x := 72; tgt_y := 38;
           5:  tgt_x := 74; tgt_y := 44;
           6:  tgt_x := 75; tgt_y := 50;
           7:  tgt_x := 74; tgt_y := 57;
           8:  tgt_x := 72; tgt_y := 63;
           9:  tgt_x := 68; tgt_y := 68;
           10: tgt_x := 63; tgt_y := 72;
           11: tgt_x := 57; tgt_y := 74;
           12: tgt_x := 50; tgt_y := 75;
           13: tgt_x := 43; tgt_y := 74;
           14: tgt_x := 38; tgt_y := 72;
           15: tgt_x := 32; tgt_y := 68;
           16: tgt_x := 28; tgt_y := 63;
           17: tgt_x := 26; tgt_y := 57;
           18: tgt_x := 25; tgt_y := 50;
           19: tgt_x := 26; tgt_y := 44;
           20: tgt_x := 28; tgt_y := 38;
           21: tgt_x := 32; tgt_y := 32;
           22: tgt_x := 38; tgt_y := 28;
           23: tgt_x := 43; tgt_y := 26;
           24: tgt_x := 50; tgt_y := 25;
           ELSE tgt_x := 50; tgt_y := 25;
       END_CASE;
       tgt_z := 0;
       go_x_exe := TRUE;
       go_y_exe := TRUE;
    4: tgt_z := 10;
       go_z_exe := TRUE;
    5: tgt_x := 50;
       tgt_y := 50;
       go_x_exe := TRUE;
       go_y_exe := TRUE;
    ELSE pl_step := pl_step;
END_CASE;

IF pl_step <> 0 AND NOT quickstop AND NOT fault_latch THEN
    IF pl_step = 1 AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
       AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
        pl_step := 2;
    ELSIF pl_step = 2 AND NOT ix_z.Busy AND ix_z.Done
        AND NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done THEN
        pl_step := 3;
    ELSIF pl_step = 3 AND NOT ix_x.Busy AND NOT ix_y.Busy
        AND ix_x.Done AND ix_y.Done THEN
        IF k >= 24 THEN
            pl_step := 4;
            k := 0;
        ELSE
            k := k + 1;
        END_IF;
    ELSIF pl_step = 4 AND NOT ix_z.Busy AND ix_z.Done THEN
        pl_step := 5;
    ELSIF pl_step = 5 AND NOT ix_x.Busy AND NOT ix_y.Busy
        AND ix_x.Done AND ix_y.Done THEN
        pl_step := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := FALSE;
        plot_done := TRUE;
    END_IF;
END_IF;

fire_x := go_x_exe;
fire_y := go_y_exe;
fire_z := go_z_exe;

ix_x(fire := fire_x, pos_target := tgt_x, hold_req := quickstop,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_y(fire := fire_y, pos_target := tgt_y, hold_req := quickstop,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100.0);
ix_z(fire := fire_z, pos_target := tgt_z, hold_req := quickstop,
     VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10.0);

drv_x(cw := cwx, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
drv_y(cw := cwy, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
drv_z(cw := cwz, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

swx := drv_x.sw;
swy := drv_y.sw;
swz := drv_z.sw;

IF quickstop OR fault_latch THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    x_v := REAL_TO_INT(drv_x.v_cmd);
    y_v := REAL_TO_INT(drv_y.v_cmd);
    z_v := REAL_TO_INT(drv_z.v_cmd);
END_IF;

x_sw := WORD_TO_INT(swx);
y_sw := WORD_TO_INT(swy);
z_sw := WORD_TO_INT(swz);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := md_latch AND pl_step = 0;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
