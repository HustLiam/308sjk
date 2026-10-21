FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    vel_req : REAL;
    acc_req : REAL;
    dec_req : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
    Aborted : BOOL;
  END_VAR
  VAR
    edge_ir : BOOL;
    pos : REAL := 0.0;
    vel : REAL := 0.0;
    tgt : REAL := 0.0;
    dist : REAL := 0.0;
    stop_d : REAL := 0.0;
    SCAN_T : REAL := 0.02;
    POSWIN : REAL := 2.0;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > 100 OR pos_target < 0 THEN
        (* 目标越程: 命令立即结束(保持原位)——MC 层应先拦截报 ErrorID=1, 此为兜底 *)
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
        Aborted := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    (* 连续跟踪分支: 序列器只改目标值, 目标变化即起新轨迹 *)
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
    Aborted := FALSE;
END_IF;
edge_ir := fire;

IF hold_req THEN
    (* 受控减速(MC_Halt): 减到零速即中止, 报 Aborted 供 MC 层置 CommandAborted *)
    IF vel > 0.0 THEN vel := MAX(0.0, vel - dec_req * SCAN_T);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + dec_req * SCAN_T);
    END_IF;
    IF vel = 0.0 AND Busy THEN
        Busy := FALSE;
        Done := FALSE;
        Aborted := TRUE;
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * dec_req);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - dec_req * SCAN_T);
        ELSE vel := MIN(0.0, vel + dec_req * SCAN_T);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
            Aborted := FALSE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(vel_req, vel + acc_req * SCAN_T);
        ELSE vel := MAX(-vel_req, vel - acc_req * SCAN_T);
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
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    v_out : REAL := 0.0;
    sp_prev : INT := 0;
    SCAN_T : REAL := 0.02;
    KP : REAL := 25.0;
    QS_DECEL : REAL := 120.0;
  END_VAR
(* ============ DRIVE402: 驱动器（CSP 跟随 + 位置环）============ *)
(* state: 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA           *)
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

(* ---- 位置环: P 控制 + 设定值差分前馈 ---- *)
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
v_cmd := REAL_TO_INT(v_out);

sw := 0;
CASE state OF
    1: sw := 16#0040;
    2: sw := 16#0031;
    3: sw := 16#0033;
    4: sw := 16#0037;
    5: sw := 16#0014;
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
    EnablePositive : BOOL;
    EnableNegative : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    pstep : INT;
  END_VAR
(* PLCopen MC_Power 对齐版; CiA402 不分方向, EnablePositive/Negative 仅作门控 *)
IF Enable AND (EnablePositive OR EnableNegative) AND pstep = 4 AND (sw AND 16#0004) = 0 AND (sw AND 16#0008) = 0 THEN
    pstep := 0;
END_IF;
IF Enable AND (EnablePositive OR EnableNegative) THEN
    CASE pstep OF
        0: cw := (cw AND 16#00F0) OR 16#0006;  pstep := 1;
        1: IF (sw AND 16#0001) <> 0 THEN cw := (cw AND 16#00F0) OR 16#0007; pstep := 2; END_IF;
        2: IF (sw AND 16#0002) <> 0 THEN cw := (cw AND 16#00F0) OR 16#000F; pstep := 3; END_IF;
        3: IF (sw AND 16#0004) <> 0 THEN pstep := 4; END_IF;
    END_CASE;
ELSE
    cw := (cw AND 16#00F0) OR 16#0006;
    pstep := 0;
END_IF;
Status := (sw AND 16#0004) <> 0;
Busy := Enable AND pstep < 4;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_RESET
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_rs : BOOL;
    busy_rs : BOOL;
  END_VAR
IF fire AND NOT edge_rs THEN
    busy_rs := TRUE;
END_IF;
edge_rs := fire;
IF busy_rs THEN
    IF (sw AND 16#0008) <> 0 THEN
        cw := cw OR 16#0080;
    ELSE
        cw := cw AND 16#FF7F;
        busy_rs := FALSE;
    END_IF;
END_IF;
Busy := busy_rs;
Error := (sw AND 16#0008) <> 0 AND NOT busy_rs;
Done := NOT busy_rs AND (sw AND 16#0008) = 0 AND NOT fire;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_STOP
  VAR_INPUT
    fire : BOOL;
    v_act : INT;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
IF fire THEN
    cw := (cw AND 16#00F0) OR 16#0002;
    Done := FALSE;
ELSE
    Done := TRUE;
END_IF;
Busy := fire AND v_act <> 0;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_HOME
  VAR_INPUT
    fire : BOOL;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
    home_exe : BOOL;
  END_VAR
IF fire AND NOT edge_hm THEN
    home_exe := TRUE;
END_IF;
edge_hm := fire;
(* home_exe 保持 TRUE 直到运动完成 *)
Busy := home_exe;
Done := NOT home_exe;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVEABSOLUTE
  VAR_INPUT
    fire : BOOL;
    Position : INT;
    Velocity : REAL;
    Acceleration : REAL;
    Deceleration : REAL;
    sw : WORD;
    abort_bus : BOOL;
  END_VAR
  VAR_IN_OUT
    interp_exe : BOOL;
    dyn_vel : REAL;
    dyn_acc : REAL;
    dyn_dec : REAL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Active : BOOL;
    CommandAborted : BOOL;
    Error : BOOL;
    ErrorID : INT;
  END_VAR
  VAR
    edge_ma : BOOL;
    edge_ab : BOOL;
  END_VAR
(* PLCopen MC_MoveAbsolute 对齐版; fire=Execute(matiec 保留字映射) *)
(* ErrorID: 0=无 1=目标越程 2=轴故障 3=轴未使能 *)
IF fire AND NOT edge_ma THEN
    Error := FALSE;
    ErrorID := 0;
    CommandAborted := FALSE;
    IF (sw AND 16#0008) <> 0 THEN
        Error := TRUE;
        ErrorID := 2;
    ELSIF (sw AND 16#0004) = 0 THEN
        Error := TRUE;
        ErrorID := 3;
    ELSIF Position < 0 OR Position > 100 THEN
        Error := TRUE;
        ErrorID := 1;
    ELSE
        interp_exe := TRUE;
        dyn_vel := Velocity;
        dyn_acc := Acceleration;
        dyn_dec := Deceleration;
    END_IF;
END_IF;
edge_ma := fire;

(* 中止(STOP/HALT/失能)传播: 取中止信号上升沿, 避免上一命令的锁存中止误杀新命令 *)
IF interp_exe AND abort_bus AND NOT edge_ab THEN
    interp_exe := FALSE;
    CommandAborted := TRUE;
END_IF;
edge_ab := abort_bus;
Busy := interp_exe;
Active := interp_exe;
Done := NOT interp_exe AND NOT Error;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_READSTATUS
  VAR_INPUT
    Enable : BOOL;
    sw : WORD;
    interp_busy : BOOL;
    v_act : INT;
  END_VAR
  VAR_OUTPUT
    Valid : BOOL;
    Moving : BOOL;
    StandStill : BOOL;
    Disabled : BOOL;
    ErrorStop : BOOL;
  END_VAR
(* PLCopen MC_ReadStatus 精简子集: 状态字映射 *)
Valid := Enable;
Disabled := Enable AND (sw AND 16#0004) = 0;
ErrorStop := Enable AND (sw AND 16#0008) <> 0;
Moving := Enable AND (interp_busy OR v_act <> 0);
StandStill := Enable AND (sw AND 16#0004) <> 0 AND NOT interp_busy AND v_act = 0;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    go_x : MC_MOVEABSOLUTE;
    go_y : MC_MOVEABSOLUTE;
    go_z : MC_MOVEABSOLUTE;
    hm_x : MC_HOME;
    hm_y : MC_HOME;
    hm_z : MC_HOME;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    rds_x : MC_READSTATUS;
    rds_y : MC_READSTATUS;
    rds_z : MC_READSTATUS;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    cw_x : WORD;
    cw_y : WORD;
    cw_z : WORD;
    sw_x : WORD;
    sw_y : WORD;
    sw_z : WORD;
    x_vel_bus : REAL := 40.0;
    x_acc_bus : REAL := 80.0;
    x_dec_bus : REAL := 80.0;
    y_vel_bus : REAL := 40.0;
    y_acc_bus : REAL := 80.0;
    y_dec_bus : REAL := 80.0;
    z_vel_bus : REAL := 20.0;
    z_acc_bus : REAL := 60.0;
    z_dec_bus : REAL := 60.0;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    pl_step : INT := 0;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    edge_draw : BOOL;
    edge_jg : BOOL;
    fault_w : BOOL;
    go_ok : BOOL;
    jog_ok : BOOL;
    jog_act : BOOL;
    x_jog_pos : INT := 0;
    tx : INT;
    ty : INT;
    tz : INT;
    fx : BOOL;
    fy : BOOL;
    fz : BOOL;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
  END_VAR
  VAR
    prog_id AT %QW20 : INT := 2;
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
(* ---- program identity ---- *)
prog_id := 2;

(* ---- 纯物理判定: 笔触纸(z 下限区, 含入场容差), 与运行状态无关 ---- *)
pen_down := z_fb <= 2;

(* ---- 扫描头: 采样驱动器状态 ---- *)
sw_x := dv_x.sw;
sw_y := dv_y.sw;
sw_z := dv_z.sw;
x_sw := WORD_TO_INT(sw_x);
y_sw := WORD_TO_INT(sw_y);
z_sw := WORD_TO_INT(sw_z);

(* ---- 故障聚合 (C4) ---- *)
fault_w := ((sw_x AND 16#0008) <> 0) OR ((sw_y AND 16#0008) <> 0) OR ((sw_z AND 16#0008) <> 0);
fault_any := fault_w;

(* ---- 动力学总线默认值 (C6: XY<=40, Z<=20) ---- *)
x_vel_bus := 40.0; x_acc_bus := 80.0; x_dec_bus := 80.0;
y_vel_bus := 40.0; y_acc_bus := 80.0; y_dec_bus := 80.0;
z_vel_bus := 20.0; z_acc_bus := 60.0; z_dec_bus := 60.0;

(* ---- 手动门控 (C1: 落笔期间拒绝 jog/cmd_go) ---- *)
jog_ok := run AND NOT quickstop AND NOT fault_any AND NOT pen_down AND pl_step = 0;
go_ok := run AND NOT quickstop AND NOT fault_any AND NOT pen_down AND pl_step = 0;

(* ---- 使能 / 复位 / 快停 (C2: run 断开=受控断电; 快停优先级最高) ---- *)
pwr_x(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := cw_x, sw := sw_x);
pwr_y(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := cw_y, sw := sw_y);
pwr_z(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := cw_z, sw := sw_z);
rs_x(fire := cmd_reset, cw := cw_x, sw := sw_x);
st_x(fire := quickstop, v_act := x_v, cw := cw_x, sw := sw_x);
st_y(fire := quickstop, v_act := y_v, cw := cw_y, sw := sw_y);
st_z(fire := quickstop, v_act := z_v, cw := cw_z, sw := sw_z);

(* ---- 异常中止 (C4): 急停/故障 -> 序列清零, plot_done 熄灭 ---- *)
IF quickstop OR fault_any THEN
    pl_step := 0;
    plot_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

(* ---- 绘图启动 (C3): cmd_draw 上升沿, 就绪条件下起序列 ---- *)
IF cmd_draw AND NOT edge_draw AND run AND NOT quickstop AND NOT fault_any AND pl_step = 0 THEN
    pl_step := 1;
    plot_done := FALSE;
END_IF;
edge_draw := cmd_draw;

(* ---- 序列器步表 (C5: Z 步冻结 XY 于当前反馈, XY 步冻结 Z) ---- *)
CASE pl_step OF
    0: pl_step := pl_step;
    1: pl_tx := x_fb; pl_ty := y_fb; pl_tz := 10;   (* 抬笔定位准备 *)
    2: pl_tx := x_fb; pl_ty := y_fb; pl_tz := 0;    (* 落笔 *)
    3: pl_tx := 80; pl_ty := 20; pl_tz := z_fb;     (* +X 边 *)
    4: pl_tx := 80; pl_ty := 80; pl_tz := z_fb;     (* +Y 边 *)
    5: pl_tx := 20; pl_ty := 80; pl_tz := z_fb;     (* -X 边 *)
    6: pl_tx := 20; pl_ty := 20; pl_tz := z_fb;     (* -Y 边闭合 *)
    7: pl_tx := x_fb; pl_ty := y_fb; pl_tz := 10;   (* 抬笔 *)
    8: pl_tx := 50; pl_ty := 50; pl_tz := 10;       (* 回中心 *)
END_CASE;

(* ---- 目标总线: 序列优先, 回零次之, 手动定位最后 ---- *)
IF pl_step <> 0 THEN
    tx := pl_tx; ty := pl_ty; tz := pl_tz;
ELSIF hm_x_exe OR hm_y_exe OR hm_z_exe THEN
    tx := 0; ty := 0; tz := 0;
ELSE
    tx := x_sp; ty := y_sp; tz := z_sp;
END_IF;

(* ---- MC 指令层: 手动绝对定位 (受 C1 互锁门控) ---- *)
go_x(fire := cmd_go AND go_ok, Position := x_sp, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := sw_x, abort_bus := ix_x.Aborted,
     interp_exe := go_x_exe, dyn_vel := x_vel_bus, dyn_acc := x_acc_bus, dyn_dec := x_dec_bus);
go_y(fire := cmd_go AND go_ok, Position := y_sp, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := sw_y, abort_bus := ix_y.Aborted,
     interp_exe := go_y_exe, dyn_vel := y_vel_bus, dyn_acc := y_acc_bus, dyn_dec := y_dec_bus);
go_z(fire := cmd_go AND go_ok, Position := z_sp, Velocity := 20.0, Acceleration := 60.0, Deceleration := 60.0,
     sw := sw_z, abort_bus := ix_z.Aborted,
     interp_exe := go_z_exe, dyn_vel := z_vel_bus, dyn_acc := z_acc_bus, dyn_dec := z_dec_bus);
hm_x(fire := cmd_home, sw := sw_x, home_exe := hm_x_exe);
hm_y(fire := cmd_home, sw := sw_y, home_exe := hm_y_exe);
hm_z(fire := cmd_home, sw := sw_z, home_exe := hm_z_exe);

(* ---- 插补引擎: 触发线电平保持 (序列期间恒 TRUE) ---- *)
fx := go_x_exe OR hm_x_exe OR (pl_step <> 0);
fy := go_y_exe OR hm_y_exe OR (pl_step <> 0);
fz := go_z_exe OR hm_z_exe OR (pl_step <> 0);
ix_x(fire := fx, pos_target := tx, hold_req := FALSE, vel_req := x_vel_bus, acc_req := x_acc_bus, dec_req := x_dec_bus);
ix_y(fire := fy, pos_target := ty, hold_req := FALSE, vel_req := y_vel_bus, acc_req := y_acc_bus, dec_req := y_dec_bus);
ix_z(fire := fz, pos_target := tz, hold_req := FALSE, vel_req := z_vel_bus, acc_req := z_acc_bus, dec_req := z_dec_bus);

(* ---- INTERP 结束后清手动/回零触发线 ---- *)
IF ix_x.Done OR ix_x.Aborted THEN go_x_exe := FALSE; hm_x_exe := FALSE; END_IF;
IF ix_y.Done OR ix_y.Aborted THEN go_y_exe := FALSE; hm_y_exe := FALSE; END_IF;
IF ix_z.Done OR ix_z.Aborted THEN go_z_exe := FALSE; hm_z_exe := FALSE; END_IF;

(* ---- 步进: 三轴插补全空闲且 Done (每边到位确认后才进下一边, C3) ---- *)
IF (pl_step <> 0) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 8 THEN
        plot_done := TRUE;   (* C7: 完整闭合+抬笔+回中心 *)
        pl_step := 0;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- X 手动点动 (C1: 仅抬笔且空闲时允许) ---- *)
IF (jog_fwd OR jog_rev) AND NOT edge_jg AND jog_ok THEN
    x_jog_pos := x_fb;
END_IF;
edge_jg := jog_fwd OR jog_rev;
jog_act := jog_ok AND (jog_fwd OR jog_rev);
IF jog_act THEN
    IF jog_fwd AND x_jog_pos < 97 THEN x_jog_pos := x_jog_pos + 1; END_IF;
    IF jog_rev AND x_jog_pos > 3 THEN x_jog_pos := x_jog_pos - 1; END_IF;
END_IF;

(* ---- 插补点路由: 点动时用 jog 位置 ---- *)
IF jog_act THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- 驱动器: 跟随插补点, 闭位置环 ---- *)
dv_x(cw := cw_x, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := cw_y, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := cw_z, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- 应用状态聚合 ---- *)
rds_x(Enable := TRUE, sw := sw_x, interp_busy := ix_x.Busy, v_act := x_v);
rds_y(Enable := TRUE, sw := sw_y, interp_busy := ix_y.Busy, v_act := y_v);
rds_z(Enable := TRUE, sw := sw_z, interp_busy := ix_z.Busy, v_act := z_v);
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := go_x.Done AND go_y.Done AND go_z.Done AND pl_step = 0 AND NOT jog_act;
any_moving := rds_x.Moving OR rds_y.Moving OR rds_z.Moving;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
