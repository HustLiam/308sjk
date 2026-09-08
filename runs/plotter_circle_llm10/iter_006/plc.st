FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    v_max : REAL := 40.0;
    acc : REAL := 80.0;
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
IF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;
IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - acc * SCAN_T); END_IF;
    IF vel < 0.0 THEN vel := MIN(0.0, vel + acc * SCAN_T); END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * acc);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - acc * SCAN_T); END_IF;
        IF dist <= 0.0 THEN vel := MIN(0.0, vel + acc * SCAN_T); END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(v_max, vel + acc * SCAN_T); END_IF;
        IF dist < 0.0 THEN vel := MAX(-v_max, vel - acc * SCAN_T); END_IF;
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
  VAR_IN_OUT
    cw : WORD;
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
        2: IF (sw AND 16#0002) <> 0 THEN cw := (cw AND 16#00F0) OR 16#000F; pstep := 3; END_IF;
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
  VAR_IN_OUT
    cw : WORD;
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
  VAR_IN_OUT
    cw : WORD;
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

FUNCTION_BLOCK MC_MOVEJOG
  VAR_INPUT
    JogForward : BOOL;
    JogBackward : BOOL;
  END_VAR
  VAR_IN_OUT
    jog_pos : INT;
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
    OM_ACC : REAL := 3.2;
    OM_MAX : REAL := 1.6;
    TWO_PI : REAL := 6.2832;
  END_VAR
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
    IF (TWO_PI - theta) > ((omega * omega) / (2.0 * OM_ACC)) THEN
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
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT;
    pl_step : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    fault_latch : BOOL;
    pwr_en_x : BOOL;
    pwr_en_y : BOOL;
    pwr_en_z : BOOL;
    rs_fire : BOOL;
    st_fire : BOOL;
    go_fire_xy : BOOL;
    go_fire_z : BOOL;
    hm_fire : BOOL;
    jog_f_ok : BOOL;
    jog_r_ok : BOOL;
    cip_fire : BOOL;
    hold_all : BOOL;
    mv_ok : BOOL;
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
    prog_id AT %QW20 : INT := 3;
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
  END_VAR
prog_id := 3;

(* ---- 采样驱动器状态 ---- *)
x_sw_w := INT_TO_WORD(x_sw);
y_sw_w := INT_TO_WORD(y_sw);
z_sw_w := INT_TO_WORD(z_sw);

(* ---- 故障检测与锁存 (C4/C1) ---- *)
IF (x_sw_w AND 16#0008) <> 0 OR (y_sw_w AND 16#0008) <> 0 OR (z_sw_w AND 16#0008) <> 0 THEN
    fault_latch := TRUE;
END_IF;
IF cmd_reset AND NOT quickstop AND (x_sw_w AND 16#0008) = 0 AND (y_sw_w AND 16#0008) = 0 AND (z_sw_w AND 16#0008) = 0 THEN
    fault_latch := FALSE;
END_IF;
fault_any := fault_latch;

(* ---- 笔位信号 (C2) ---- *)
pen_down := z_fb <= 1;

(* ---- 使能/复位/快停输入条件 (C3/C1/C4) ---- *)
pwr_en_x := run AND NOT quickstop AND NOT fault_latch;
pwr_en_y := pwr_en_x;
pwr_en_z := pwr_en_x;
rs_fire := cmd_reset AND fault_latch;
st_fire := quickstop OR fault_latch OR NOT run;
hold_all := st_fire;
mv_ok := run AND NOT quickstop AND NOT fault_latch;

(* ---- 手动请求：笔安全互锁 (C2/AC5) ---- *)
go_fire_xy := cmd_go AND mv_ok AND NOT pen_down;
go_fire_z := cmd_go AND mv_ok;
hm_fire := cmd_home AND mv_ok;
jog_f_ok := jog_fwd AND NOT pen_down AND mv_ok;
jog_r_ok := jog_rev AND NOT pen_down AND mv_ok;

(* ---- 绘圆序列器：电平门控启动，无单扫描脉冲 ---- *)
(* 序列步骤：
   1: 抬笔(z=10)  2: XY 定位至圆起点 (50,25)  3: 落笔(z=0)
   4: 圆弧插补一周  5: 抬笔(z=10)  6: 回圆心 (50,50) *)
IF (pl_step = 0) AND cmd_draw AND mv_ok AND NOT plot_done THEN
    pl_step := 1;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
END_IF;

(* 完成后按钮释放，允许下一次绘圆重新武装 *)
IF (pl_step = 0) AND NOT cmd_draw THEN
    plot_done := FALSE;
END_IF;

(* 中止：急停/故障/失能 -> 序列归零并清触发线 (C1/C4/C3) *)
IF quickstop OR fault_latch OR NOT run THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    plot_done := FALSE;
END_IF;

(* 空闲时目标 = 手动通道；触发线 = 手动电平（回零目标=抬笔位） *)
IF pl_step = 0 THEN
    pl_tx := x_sp;
    pl_ty := y_sp;
    IF hm_fire THEN
        pl_tz := 10;
    ELSE
        pl_tz := z_sp;
    END_IF;
    IF go_fire_xy THEN
        go_x_exe := TRUE;
        go_y_exe := TRUE;
    END_IF;
    IF go_fire_xy OR go_fire_z OR hm_fire THEN
        go_z_exe := TRUE;
    END_IF;
END_IF;

(* ---- 各步目标（每扫描连续写入，目标变化自动起新轨迹） ---- *)
CASE pl_step OF
    0: pl_step := pl_step;
    1: pl_tx := ix_x.Setpoint;
       pl_ty := ix_y.Setpoint;
       pl_tz := 10;
    2: pl_tx := 50;
       pl_ty := 25;
       pl_tz := 10;
    3: pl_tx := 50;
       pl_ty := 25;
       pl_tz := 0;
    4: pl_tx := cip.SetpointX;
       pl_ty := cip.SetpointY;
       pl_tz := 0;
    5: pl_tx := 50;
       pl_ty := 25;
       pl_tz := 10;
    6: pl_tx := 50;
       pl_ty := 50;
       pl_tz := 10;
END_CASE;

(* ---- 步进判据：反馈到位 + 插补空闲（物理位置判据防早跳） ---- *)
IF pl_step <> 0 THEN
    IF pl_step = 1 AND z_fb >= 9 AND ix_z.Done AND NOT ix_z.Busy THEN
        pl_step := 2;
    END_IF;
    IF pl_step = 2 AND ix_x.Done AND ix_y.Done AND NOT ix_x.Busy AND NOT ix_y.Busy AND ABS(x_fb - 50) <= 2 AND ABS(y_fb - 25) <= 2 THEN
        pl_step := 3;
    END_IF;
    IF pl_step = 3 AND z_fb <= 1 AND ix_z.Done AND NOT ix_z.Busy THEN
        pl_step := 4;
    END_IF;
    IF pl_step = 4 AND cip.Done AND NOT cip.Busy THEN
        pl_step := 5;
    END_IF;
    IF pl_step = 5 AND z_fb >= 9 AND ix_z.Done AND NOT ix_z.Busy THEN
        pl_step := 6;
    END_IF;
    IF pl_step = 6 AND ix_x.Done AND ix_y.Done AND NOT ix_x.Busy AND NOT ix_y.Busy AND ABS(x_fb - 50) <= 5 AND ABS(y_fb - 50) <= 5 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := FALSE;
    END_IF;
END_IF;

(* ---- 圆弧插补触发线：step4 期间电平保持 ---- *)
IF pl_step = 4 THEN
    cip_fire := TRUE;
ELSE
    cip_fire := FALSE;
END_IF;

(* ---- 点动 ---- *)
jg_x(JogForward := jog_f_ok, JogBackward := jog_r_ok, jog_pos := x_jog_pos);

(* ---- 插补引擎：连续跟踪（fire 保持期间目标变化起新轨迹） ---- *)
ix_x(fire := go_x_exe, pos_target := pl_tx, hold_req := hold_all, v_max := 40.0, acc := 80.0);
ix_y(fire := go_y_exe, pos_target := pl_ty, hold_req := hold_all, v_max := 40.0, acc := 80.0);
ix_z(fire := go_z_exe OR hm_fire, pos_target := pl_tz, hold_req := hold_all, v_max := 20.0, acc := 100.0);
cip(fire := cip_fire);

(* ---- 插补点路由 ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- 使能/复位/快停（快停最后落笔 = 优先级最高） ---- *)
pwr_x(Enable := pwr_en_x, cw := x_cw, sw := x_sw_w);
pwr_y(Enable := pwr_en_y, cw := y_cw, sw := y_sw_w);
pwr_z(Enable := pwr_en_z, cw := z_cw, sw := z_sw_w);
rs_x(fire := rs_fire, cw := x_cw, sw := x_sw_w);
rs_y(fire := rs_fire, cw := y_cw, sw := y_sw_w);
rs_z(fire := rs_fire, cw := z_cw, sw := z_sw_w);
st_x(fire := st_fire, cw := x_cw);
st_y(fire := st_fire, cw := y_cw);
st_z(fire := st_fire, cw := z_cw);

(* ---- 驱动器：跟随插补点 ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- 状态灯 (C6) ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
IF pl_step <> 0 THEN
    move_done := FALSE;
ELSE
    move_done := all_oe AND NOT (jog_f_ok OR jog_r_ok) AND NOT go_fire_xy AND NOT hm_fire
                 AND ix_x.Done AND ix_y.Done AND ix_z.Done AND NOT any_moving;
END_IF;

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
