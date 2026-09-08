FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
    Done : BOOL;
    Setpoint : INT;
  END_VAR
  VAR
    tgt : REAL := 0.0;
    pos : REAL := 0.0;
    vel : REAL := 0.0;
    dist : REAL := 0.0;
    stop_d : REAL := 0.0;
    edge_ir : BOOL;
  END_VAR
  VAR CONSTANT
    VMAX : REAL := 120.0;
    ACCEL : REAL := 150.0;
    SCAN_T : REAL := 0.02;
    POSWIN : REAL := 0.499;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > 100 OR pos_target < 0 THEN
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
END_IF;
edge_ir := fire;
IF Busy AND fire AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Done := FALSE;
END_IF;
IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - ACCEL * SCAN_T); END_IF;
    IF vel < 0.0 THEN vel := MIN(0.0, vel + ACCEL * SCAN_T); END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * ACCEL);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - ACCEL * SCAN_T); END_IF;
        IF dist <= 0.0 THEN vel := MIN(0.0, vel + ACCEL * SCAN_T); END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(VMAX, vel + ACCEL * SCAN_T); END_IF;
        IF dist < 0.0 THEN vel := MAX(-VMAX, vel + ACCEL * SCAN_T); END_IF;
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
    v_cmd : INT;
    sw : WORD;
  END_VAR
  VAR
    state : INT;
    cmd : WORD;
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    sp_prev : INT;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 1.8;
    QS_DECEL : REAL := 200.0;
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
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - QS_DECEL * SCAN_T); END_IF;
    IF v_out < 0.0 THEN v_out := MIN(0.0, v_out + QS_DECEL * SCAN_T); END_IF;
ELSE
    v_out := 0.0;
END_IF;
sp_prev := Setpoint;
v_cmd := REAL_TO_INT(v_out);
sw := 16#0000;
CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0031;
    3: sw := 16#0033;
    4: sw := 16#0037;
    5: sw := 16#0014;
    6: sw := 16#0016;
    7: sw := 16#0018;
END_CASE;
IF state = 4 THEN
    IF ABS(pos_err) <= 2.0 AND v_ff = 0.0 THEN
        sw := sw OR 16#0400;
    END_IF;
END_IF;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
  END_VAR
  VAR
    pstep : INT;
  END_VAR
IF Enable AND pstep = 4 AND (sw AND 16#0004) = 0 AND (sw AND 16#0008) = 0 THEN
    pstep := 0;
END_IF;
IF Enable THEN
    CASE pstep OF
        0: cw := (cw AND 16#00F0) OR 16#0006; pstep := 1;
        1: IF (sw AND 16#0001) <> 0 THEN cw := (cw AND 16#00F0) OR 16#0007; pstep := 2; END_IF;
        2: IF (sw AND 16#0002) <> 0 THEN cw := (cw AND 16#00F0) OR 16#0F; pstep := 3; END_IF;
        3: IF (sw AND 16#0004) <> 0 THEN pstep := 4; END_IF;
    END_CASE;
ELSE
    cw := (cw AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
Status := (sw AND 16#0004) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_RESET
  VAR_INPUT
    fire : BOOL;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
  END_VAR
  VAR
    edge_rs : BOOL;
    busy : BOOL;
  END_VAR
IF fire AND NOT edge_rs THEN
    busy := TRUE;
END_IF;
edge_rs := fire;
IF busy THEN
    IF (sw AND 16#0008) <> 0 THEN
        cw := cw OR 16#0080;
    ELSE
        cw := cw AND 16#FF7F;
        busy := FALSE;
    END_IF;
END_IF;
Done := NOT busy AND (sw AND 16#0008) = 0 AND NOT fire;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_STOP
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
  END_VAR
IF fire THEN
    cw := (cw AND 16#00F0) OR 16#0002;
    Done := FALSE;
ELSE
    Done := TRUE;
END_IF;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVEABSOLUTE
  VAR_INPUT
    fire : BOOL;
    Position : INT;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
  END_VAR
  VAR_OUTPUT
    interp_exe : BOOL;
  END_VAR
  VAR
    edge_ma : BOOL;
  END_VAR
IF fire AND NOT edge_ma THEN
    interp_exe := TRUE;
END_IF;
edge_ma := fire;
Done := NOT interp_exe;
Busy := interp_exe;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVEJOG
  VAR_INPUT
    JogForward : BOOL;
    JogBackward : BOOL;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
  END_VAR
IF JogForward AND jog_pos < 97 THEN
    jog_pos := jog_pos + 1;
ELSIF JogBackward AND jog_pos > 3 THEN
    jog_pos := jog_pos - 1;
END_IF;
Busy := JogForward OR JogBackward;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_HOME
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
  END_VAR
  VAR_OUTPUT
    home_exe : BOOL;
  END_VAR
  VAR
    edge_hm : BOOL;
  END_VAR
IF fire AND NOT edge_hm THEN
    home_exe := TRUE;
END_IF;
edge_hm := fire;
Done := NOT home_exe;
END_FUNCTION_BLOCK

FUNCTION_BLOCK CIRCLE_IP
  VAR_INPUT
    fire : BOOL;
    cx : REAL := 50.0;
    cy : REAL := 50.0;
    radius : REAL := 25.0;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
    Done : BOOL;
    SetpointX : INT;
    SetpointY : INT;
  END_VAR
  VAR
    theta : REAL := 0.0;
    omega : REAL := 0.0;
    edge_ci : BOOL;
    sp_x : REAL := 50.0;
    sp_y : REAL := 25.0;
    px : REAL := 50.0;
    py : REAL := 25.0;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    V_TAN : REAL := 40.0;
    OM_ACC : REAL := 3.2;
    OM_MAX : REAL := 1.6;
    TWO_PI : REAL := 6.2832;
  END_VAR
(* theta 从 0 起点开始，逆时针一圈回到起点；omega 梯形加减速，OM_MAX=V_TAN/radius *)
IF fire AND NOT edge_ci THEN
    theta := 0.0;
    omega := 0.0;
    px := cx + radius;
    py := cy;
    Busy := TRUE;
    Done := FALSE;
END_IF;
edge_ci := fire;
IF Busy THEN
    IF TWO_PI - theta > (omega * omega) / (2.0 * OM_ACC) THEN
        omega := MIN(OM_MAX, omega + OM_ACC * SCAN_T);
    ELSE
        omega := MAX(0.0, omega - OM_ACC * SCAN_T);
    END_IF;
    theta := theta + omega * SCAN_T;
    IF theta >= TWO_PI THEN
        theta := TWO_PI;
        omega := 0.0;
        px := cx + radius;
        py := cy;
        Busy := FALSE;
        Done := TRUE;
    ELSE
        px := cx + radius * COS(theta);
        py := cy - radius * SIN(theta);
    END_IF;
    sp_x := px;
    sp_y := py;
END_IF;
SetpointX := REAL_TO_INT(sp_x);
SetpointY := REAL_TO_INT(sp_y);
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    rs_y : MC_RESET;
    rs_z : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    go_x : MC_MOVEABSOLUTE;
    go_y : MC_MOVEABSOLUTE;
    go_z : MC_MOVEABSOLUTE;
    hm_z : MC_HOME;
    jg_x : MC_MOVEJOG;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    cip : CIRCLE_IP;
  END_VAR
  VAR
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_sw_w : WORD;
    y_sw_w : WORD;
    z_sw_w : WORD;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_z_exe : BOOL;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT;
    pl_step : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    edge_draw : BOOL;
    fault_latch : BOOL;
    abort_seq : BOOL;
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
    prog_id AT %QW20 : INT := 2;
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
prog_id := 2;

(* ---- 采样驱动器状态 ---- *)
x_sw_w := WORD(x_sw);
y_sw_w := WORD(y_sw);
z_sw_w := WORD(z_sw);

(* ---- 故障检测与锁存 (C4/C1) ---- *)
IF (x_sw_w AND 16#0008) <> 0 OR (y_sw_w AND 16#0008) <> 0 OR (z_sw_w AND 16#0008) <> 0 THEN
    fault_latch := TRUE;
END_IF;
IF cmd_reset AND NOT quickstop AND (x_sw_w AND 16#0008) = 0 AND (y_sw_w AND 16#0008) = 0 AND (z_sw_w AND 16#0008) = 0 THEN
    fault_latch := FALSE;
    abort_seq := FALSE;
END_IF;
fault_any := fault_latch;

(* ---- 使能/复位/快停 ---- *)
pwr_x(Enable := run AND NOT quickstop AND NOT fault_latch, cw := x_cw, sw := x_sw_w);
pwr_y(Enable := run AND NOT quickstop AND NOT fault_latch, cw := y_cw, sw := y_sw_w);
pwr_z(Enable := run AND NOT quickstop AND NOT fault_latch, cw := z_cw, sw := z_sw_w);
rs_x(fire := cmd_reset AND fault_latch, cw := x_cw, sw := x_sw_w);
rs_y(fire := cmd_reset AND fault_latch, cw := y_cw, sw := y_sw_w);
rs_z(fire := cmd_reset AND fault_latch, cw := z_cw, sw := z_sw_w);
st_x(fire := quickstop OR fault_latch OR NOT run, cw := x_cw);
st_y(fire := quickstop OR fault_latch OR NOT run, cw := y_cw);
st_z(fire := quickstop OR fault_latch OR NOT run, cw := z_cw);

(* ---- 笔位信号 (C2) ---- *)
pen_down := z_fb <= 1;

(* ---- 手动定位请求：笔安全互锁 (C2/AC5) ---- *)
go_x(fire := cmd_go AND run AND NOT quickstop AND NOT fault_latch AND NOT pen_down, Position := x_sp, interp_exe => go_x_exe);
go_y(fire := cmd_go AND run AND NOT quickstop AND NOT fault_latch AND NOT pen_down, Position := y_sp, interp_exe => go_y_exe);
go_z(fire := cmd_go AND run AND NOT quickstop AND NOT fault_latch, Position := z_sp, interp_exe => go_z_exe);
hm_z(fire := cmd_home AND run AND NOT quickstop AND NOT fault_latch, home_exe => hm_z_exe);

(* ---- 绘圆序列器 (C5/C6) ---- *)
IF cmd_draw AND NOT edge_draw AND run AND NOT quickstop AND NOT fault_latch AND pl_step = 0 THEN
    plot_done := FALSE;
    pl_step := 1;
END_IF;
edge_draw := cmd_draw;

(* 序列步骤：
   1: 确认抬笔(z=10)  2: XY 定位至 (50,25)  3: 落笔(z=0)
   4: 圆弧插补  5: 抬笔(z=10)  6: 回圆心(50,50) *)
IF quickstop OR fault_latch OR NOT run THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    plot_done := FALSE;
END_IF;

CASE pl_step OF
    0: pl_step := pl_step;
    1: pl_tx := x_sp;
       pl_ty := y_sp;
       pl_tz := 10;
       go_x_exe := TRUE;
       go_y_exe := TRUE;
       go_z_exe := TRUE;
       IF z_fb = 10 AND ix_z.Done THEN
           go_x_exe := FALSE;
           go_y_exe := FALSE;
           go_z_exe := FALSE;
           pl_step := 2;
       END_IF;
    2: pl_tx := 50;
       pl_ty := 25;
       pl_tz := 10;
       go_x_exe := TRUE;
       go_y_exe := TRUE;
       go_z_exe := TRUE;
       IF ix_x.Done AND ix_y.Done AND NOT ix_x.Busy AND NOT ix_y.Busy AND ABS(x_fb - 50) <= 2 AND ABS(y_fb - 25) <= 2 THEN
           go_x_exe := FALSE;
           go_y_exe := FALSE;
           go_z_exe := FALSE;
           pl_step := 3;
       END_IF;
    3: pl_tx := 50;
       pl_ty := 25;
       pl_tz := 0;
       go_z_exe := TRUE;
       IF z_fb <= 1 AND ix_z.Done AND NOT ix_z.Busy THEN
           go_z_exe := FALSE;
           pl_step := 4;
       END_IF;
    4: cip(fire := TRUE);
       IF cip.Done AND NOT cip.Busy THEN
           pl_step := 5;
       END_IF;
    5: pl_tx := cip.SetpointX;
       pl_ty := cip.SetpointY;
       pl_tz := 10;
       go_z_exe := TRUE;
       IF z_fb = 10 AND ix_z.Done AND NOT ix_z.Busy THEN
           go_z_exe := FALSE;
           pl_step := 6;
       END_IF;
    6: pl_tx := 50;
       pl_ty := 50;
       pl_tz := 10;
       go_x_exe := TRUE;
       go_y_exe := TRUE;
       IF ix_x.Done AND ix_y.Done AND NOT ix_x.Busy AND NOT ix_y.Busy AND ABS(x_fb - 50) <= 5 AND ABS(y_fb - 50) <= 5 THEN
           go_x_exe := FALSE;
           go_y_exe := FALSE;
           plot_done := TRUE;
           pl_step := 0;
       END_IF;
END_CASE;

(* ---- 点动：仅抬笔状态允许 (C2) ---- *)
jg_x(JogForward := jog_fwd AND NOT pen_down AND run AND NOT quickstop AND NOT fault_latch, JogBackward := jog_rev AND NOT pen_down AND run AND NOT quickstop AND NOT fault_latch, jog_pos := x_jog_pos);

(* ---- 插补引擎调用 ---- *)
ix_x(fire := go_x_exe, pos_target := pl_tx, hold_req := quickstop OR fault_latch OR NOT run);
ix_y(fire := go_y_exe, pos_target := pl_ty, hold_req := quickstop OR fault_latch OR NOT run);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := pl_tz, hold_req := quickstop OR fault_latch OR NOT run);

(* ---- 手动模式目标选择 ---- *)
IF pl_step = 0 AND go_x_exe AND cmd_go THEN pl_tx := x_sp; END_IF;
IF pl_step = 0 AND go_y_exe AND cmd_go THEN pl_ty := y_sp; END_IF;
IF pl_step = 0 AND (go_z_exe OR hm_z_exe) THEN pl_tz := z_sp; END_IF;

(* ---- 完成清线 ---- *)
IF ix_z.Done THEN hm_z_exe := FALSE; END_IF;

(* ---- 插补点路由 ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSIF pl_step = 4 THEN
    x_interp := cip.SetpointX;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
IF pl_step = 4 THEN
    y_interp := cip.SetpointY;
ELSE
    y_interp := ix_y.Setpoint;
END_IF;
z_interp := ix_z.Setpoint;

(* ---- 驱动器 ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- 状态灯 (C6) ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
IF pl_step <> 0 THEN
    move_done := FALSE;
ELSE
    move_done := go_x.Done AND go_y.Done AND go_z.Done AND NOT go_x_exe AND NOT go_y_exe AND NOT go_z_exe;
END_IF;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);

(* ---- 急停强制零速 (C1/AC2/AC3/AC4) ---- *)
IF quickstop OR fault_latch OR NOT run THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

(* ---- 状态字回写（诊断通道 INT） ---- *)
x_sw := WORD_TO_INT(dv_x.sw);
y_sw := WORD_TO_INT(dv_y.sw);
z_sw := WORD_TO_INT(dv_z.sw);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
