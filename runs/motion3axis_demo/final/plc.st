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
    pos : REAL;
    vel : REAL;
    tgt : REAL;
    dist : REAL;
    stop_d : REAL;
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
    pos_err : REAL;
    v_ff : REAL;
    v_out : REAL;
    sp_prev : INT;
  END_VAR
  VAR
    SCAN_T : REAL := 0.02;
    KP : REAL := 25.0;
    QS_DECEL : REAL := 120.0;
  END_VAR
(* ============ DRIVE402: 驱动器（CSP 跟随 + 位置环）============ *)
(* 接收周期位置设定点（插补点），闭位置环，输出电机速度指令。 *)
(* 不含轨迹规划——那是 PLC 侧 INTERP 的事。                  *)
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

(* ---- 位置环：P 控制 + 设定值差分前馈 ---- *)
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

FUNCTION_BLOCK MC_HALT
  VAR_INPUT
    fire : BOOL;
    ax_busy : BOOL;
  END_VAR
  VAR_OUTPUT
    Stopping : BOOL;
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_hl : BOOL;
    done_latch : BOOL;
  END_VAR
(* PLCopen MC_Halt: 受控减速到零速(不失能), 活动命令被中止(CommandAborted) *)
IF fire AND NOT edge_hl THEN
    done_latch := FALSE;
    Stopping := TRUE;
END_IF;
edge_hl := fire;
IF Stopping AND NOT ax_busy THEN
    Stopping := FALSE;
    done_latch := TRUE;
END_IF;
Busy := Stopping;
Done := done_latch AND NOT Stopping;
Error := FALSE;
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

FUNCTION_BLOCK MC_MOVERELATIVE
  VAR_INPUT
    fire : BOOL;
    Distance : INT;
    ActualPosition : INT;
    Velocity : REAL;
    Acceleration : REAL;
    Deceleration : REAL;
    sw : WORD;
    abort_bus : BOOL;
  END_VAR
  VAR_IN_OUT
    interp_exe : BOOL;
    abs_tgt : INT;
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
    edge_rl : BOOL;
    edge_ab : BOOL;
  END_VAR
(* PLCopen MC_MoveRelative: 触发时绝对目标 = 实际位置 + Distance *)
(* ErrorID: 0=无 1=目标越程 2=轴故障 3=轴未使能 *)
IF fire AND NOT edge_rl THEN
    Error := FALSE;
    ErrorID := 0;
    CommandAborted := FALSE;
    IF (sw AND 16#0008) <> 0 THEN
        Error := TRUE;
        ErrorID := 2;
    ELSIF (sw AND 16#0004) = 0 THEN
        Error := TRUE;
        ErrorID := 3;
    ELSE
        abs_tgt := ActualPosition + Distance;
        IF abs_tgt < 0 OR abs_tgt > 100 THEN
            Error := TRUE;
            ErrorID := 1;
        ELSE
            interp_exe := TRUE;
            dyn_vel := Velocity;
            dyn_acc := Acceleration;
            dyn_dec := Deceleration;
        END_IF;
    END_IF;
END_IF;
edge_rl := fire;

IF interp_exe AND abort_bus AND NOT edge_ab THEN
    interp_exe := FALSE;
    CommandAborted := TRUE;
END_IF;
edge_ab := abort_bus;
Busy := interp_exe;
Active := interp_exe;
Done := NOT interp_exe AND NOT Error;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVEJOG
  VAR_INPUT
    JogForward : BOOL;
    JogBackward : BOOL;
    Velocity : REAL;
    cur_pos : INT;
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    jog_pos : INT;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    jog_step : INT;
    edge_jg : BOOL;
  END_VAR
(* PLCopen MC_MoveJog 对齐版: 步进量 = Velocity * 扫描周期(下限 1);
   起跳锚定实际位置(松开后设定点回到 INTERP, 锚定消除跨命令位置跳变) *)
IF (JogForward OR JogBackward) AND NOT edge_jg AND NOT Busy THEN
    jog_pos := cur_pos;
END_IF;
edge_jg := JogForward OR JogBackward;
jog_step := REAL_TO_INT(Velocity * 0.02);
IF jog_step < 1 THEN jog_step := 1; END_IF;
IF JogForward AND jog_pos < 97 THEN
    jog_pos := jog_pos + jog_step;
ELSIF JogBackward AND jog_pos > 3 THEN
    jog_pos := jog_pos - jog_step;
END_IF;
Busy := JogForward OR JogBackward;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_HOME
  VAR_INPUT
    fire : BOOL;
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    home_exe : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_hm : BOOL;
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

FUNCTION_BLOCK MC_READACTUALPOSITION
  VAR_INPUT
    Enable : BOOL;
    ActualPosition : INT;
  END_VAR
  VAR_OUTPUT
    Valid : BOOL;
    Position : INT;
  END_VAR
(* PLCopen MC_ReadActualPosition 精简版 *)
Valid := Enable;
Position := ActualPosition;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    run AT %QX0.0 : BOOL;
    cmd_home AT %QX0.1 : BOOL;
    cmd_go AT %QX0.2 : BOOL;
    jog_fwd AT %QX0.3 : BOOL;
    jog_rev AT %QX0.4 : BOOL;
    quickstop AT %QX0.5 : BOOL;
    inject_fault AT %QX0.6 : BOOL;
    cmd_reset AT %QX0.7 : BOOL;
    cmd_halt AT %QX2.0 : BOOL;
    cmd_rel AT %QX2.1 : BOOL;
  END_VAR
  VAR
    x_fb AT %QW0 : INT;
    y_fb AT %QW1 : INT;
    z_fb AT %QW2 : INT;
    rel_x_d AT %QW3 : INT;
    rel_y_d AT %QW4 : INT;
    rel_z_d AT %QW5 : INT;
  END_VAR
  VAR
    x_sp AT %QW10 : INT;
    y_sp AT %QW11 : INT;
    z_sp AT %QW12 : INT;
  END_VAR
  VAR
    x_sw AT %QW6 : WORD;
    y_sw AT %QW7 : WORD;
    z_sw AT %QW8 : WORD;
    x_v AT %QW13 : INT;
    y_v AT %QW14 : INT;
    z_v AT %QW15 : INT;
    x_err_id AT %QW16 : INT;
    y_err_id AT %QW17 : INT;
    z_err_id AT %QW18 : INT;
    all_oe AT %QX1.0 : BOOL;
    move_done AT %QX1.1 : BOOL;
    any_moving AT %QX1.2 : BOOL;
    fault_any AT %QX1.3 : BOOL;
    prog_id AT %QW20 : INT := 1;
  END_VAR
  VAR
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    rel_x_exe : BOOL;
    rel_y_exe : BOOL;
    rel_z_exe : BOOL;
  END_VAR
  VAR
    x_vel_bus : REAL := 40.0;
    x_acc_bus : REAL := 80.0;
    x_dec_bus : REAL := 80.0;
    y_vel_bus : REAL := 40.0;
    y_acc_bus : REAL := 80.0;
    y_dec_bus : REAL := 80.0;
    z_vel_bus : REAL := 40.0;
    z_acc_bus : REAL := 80.0;
    z_dec_bus : REAL := 80.0;
  END_VAR
  VAR
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    hlt_x : MC_HALT;
    hlt_y : MC_HALT;
    hlt_z : MC_HALT;
    go_x : MC_MOVEABSOLUTE;
    go_y : MC_MOVEABSOLUTE;
    go_z : MC_MOVEABSOLUTE;
    rel_x : MC_MOVERELATIVE;
    rel_y : MC_MOVERELATIVE;
    rel_z : MC_MOVERELATIVE;
    jg_x : MC_MOVEJOG;
    hm_x : MC_HOME;
    hm_y : MC_HOME;
    hm_z : MC_HOME;
    rds_x : MC_READSTATUS;
    rds_y : MC_READSTATUS;
    rds_z : MC_READSTATUS;
    rap_x : MC_READACTUALPOSITION;
    rap_y : MC_READACTUALPOSITION;
    rap_z : MC_READACTUALPOSITION;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
  END_VAR
(* ---- program identity ---- *)
prog_id := 1;

(* ---- 扫描头：采样驱动器状态 ---- *)
x_sw := dv_x.sw;
y_sw := dv_y.sw;
z_sw := dv_z.sw;

(* ---- 目标总线默认 = 绝对定位字（MC_MOVERELATIVE 触发时改写为 叠加目标） ---- *)
x_tgt := x_sp;
y_tgt := y_sp;
z_tgt := z_sp;

(* ---- 使能 / 复位 / 快停（快停最后落笔 = 优先级最高） ---- *)
pwr_x(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := x_cw, sw := x_sw);
pwr_y(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := y_cw, sw := y_sw);
pwr_z(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := z_cw, sw := z_sw);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sw);
st_x(fire := quickstop, v_act := x_v, cw := x_cw, sw := x_sw);
st_y(fire := quickstop, v_act := y_v, cw := y_cw, sw := y_sw);
st_z(fire := quickstop, v_act := z_v, cw := z_cw, sw := z_sw);

(* ---- MC 读块（后续命令/状态聚合消费） ---- *)
rap_x(Enable := TRUE, ActualPosition := x_fb);
rap_y(Enable := TRUE, ActualPosition := y_fb);
rap_z(Enable := TRUE, ActualPosition := z_fb);

(* ---- MC 指令层 ---- *)
go_x(fire := cmd_go, Position := x_sp, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := x_sw, abort_bus := ix_x.Aborted,
     interp_exe := go_x_exe, dyn_vel := x_vel_bus, dyn_acc := x_acc_bus, dyn_dec := x_dec_bus);
go_y(fire := cmd_go, Position := y_sp, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := y_sw, abort_bus := ix_y.Aborted,
     interp_exe := go_y_exe, dyn_vel := y_vel_bus, dyn_acc := y_acc_bus, dyn_dec := y_dec_bus);
go_z(fire := cmd_go, Position := z_sp, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := z_sw, abort_bus := ix_z.Aborted,
     interp_exe := go_z_exe, dyn_vel := z_vel_bus, dyn_acc := z_acc_bus, dyn_dec := z_dec_bus);
rel_x(fire := cmd_rel, Distance := rel_x_d, ActualPosition := rap_x.Position, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := x_sw, abort_bus := ix_x.Aborted,
     interp_exe := rel_x_exe, abs_tgt := x_tgt, dyn_vel := x_vel_bus, dyn_acc := x_acc_bus, dyn_dec := x_dec_bus);
rel_y(fire := cmd_rel, Distance := rel_y_d, ActualPosition := rap_y.Position, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := y_sw, abort_bus := ix_y.Aborted,
     interp_exe := rel_y_exe, abs_tgt := y_tgt, dyn_vel := y_vel_bus, dyn_acc := y_acc_bus, dyn_dec := y_dec_bus);
rel_z(fire := cmd_rel, Distance := rel_z_d, ActualPosition := rap_z.Position, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := z_sw, abort_bus := ix_z.Aborted,
     interp_exe := rel_z_exe, abs_tgt := z_tgt, dyn_vel := z_vel_bus, dyn_acc := z_acc_bus, dyn_dec := z_dec_bus);
hm_x(fire := cmd_home, sw := x_sw, home_exe := hm_x_exe);
hm_y(fire := cmd_home, sw := y_sw, home_exe := hm_y_exe);
hm_z(fire := cmd_home, sw := z_sw, home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd, JogBackward := jog_rev, Velocity := 50.0, cur_pos := x_fb, sw := x_sw, jog_pos := x_jog_pos);
hlt_x(fire := cmd_halt, ax_busy := ix_x.Busy);
hlt_y(fire := cmd_halt, ax_busy := ix_y.Busy);
hlt_z(fire := cmd_halt, ax_busy := ix_z.Busy);

(* ---- 插补引擎：每扫描输出插补点（回零时目标=当前定位字, 场景约定 sp=0） ---- *)
ix_x(fire := go_x_exe OR hm_x_exe OR rel_x_exe, pos_target := x_tgt,
     hold_req := hlt_x.Stopping, vel_req := x_vel_bus, acc_req := x_acc_bus, dec_req := x_dec_bus);
ix_y(fire := go_y_exe OR hm_y_exe OR rel_y_exe, pos_target := y_tgt,
     hold_req := hlt_y.Stopping, vel_req := y_vel_bus, acc_req := y_acc_bus, dec_req := y_dec_bus);
ix_z(fire := go_z_exe OR hm_z_exe OR rel_z_exe, pos_target := z_tgt,
     hold_req := hlt_z.Stopping, vel_req := z_vel_bus, acc_req := z_acc_bus, dec_req := z_dec_bus);

(* ---- INTERP 结束（完成或中止）后清触发线 ---- *)
IF ix_x.Done OR ix_x.Aborted THEN go_x_exe := FALSE; hm_x_exe := FALSE; rel_x_exe := FALSE; END_IF;
IF ix_y.Done OR ix_y.Aborted THEN go_y_exe := FALSE; hm_y_exe := FALSE; rel_y_exe := FALSE; END_IF;
IF ix_z.Done OR ix_z.Aborted THEN go_z_exe := FALSE; hm_z_exe := FALSE; rel_z_exe := FALSE; END_IF;

(* ---- 插补点路由：点动时用 jog_pos，否则用 INTERP 输出 ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- 驱动器：跟随插补点，闭位置环 ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- 应用状态聚合（经 MC_ReadStatus） ---- *)
rds_x(Enable := TRUE, sw := x_sw, interp_busy := ix_x.Busy, v_act := x_v);
rds_y(Enable := TRUE, sw := y_sw, interp_busy := ix_y.Busy, v_act := y_v);
rds_z(Enable := TRUE, sw := z_sw, interp_busy := ix_z.Busy, v_act := z_v);
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := go_x.Done AND go_y.Done AND go_z.Done;
any_moving := rds_x.Moving OR rds_y.Moving OR rds_z.Moving;
fault_any := rds_x.ErrorStop OR rds_y.ErrorStop OR rds_z.ErrorStop;

(* ---- ErrorID 诊断出口（相对命令优先报告） ---- *)
IF rel_x.Error THEN x_err_id := rel_x.ErrorID; ELSE x_err_id := go_x.ErrorID; END_IF;
IF rel_y.Error THEN y_err_id := rel_y.ErrorID; ELSE y_err_id := go_y.ErrorID; END_IF;
IF rel_z.Error THEN z_err_id := rel_z.ErrorID; ELSE z_err_id := go_z.ErrorID; END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
