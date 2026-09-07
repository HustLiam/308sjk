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
    state : INT := 1;
    v_out : REAL := 0.0;
    v_ff : REAL;
    pos_err : REAL := 0.0;
    sp_prev : INT := 0;
    cmd : WORD;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 6.0;
    QS_DECEL : REAL := 120.0;
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
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - QS_DECEL * SCAN_T);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + QS_DECEL * SCAN_T);
    END_IF;
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
    7: sw := 16#0018;
END_CASE;
IF state = 4 THEN
    IF ABS(pos_err) <= 2.0 AND v_ff = 0.0 THEN
        sw := sw OR 16#0400;
    END_IF;
END_IF;
END_FUNCTION_BLOCK

FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL := 60.0;
    ACCEL : REAL := 100.0;
    POSWIN : REAL := 2.0;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT := 0;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    dist : REAL;
    stop_d : REAL;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
  END_VAR
(* 连续跟踪: fire 电平保持期间目标变化即起新轨迹 *)
IF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    IF pos_target > 100 OR pos_target < 0 THEN
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
END_IF;

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

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
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
        4: pstep := 4;
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
  END_VAR
  VAR_IN_OUT
    cw : WORD;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
  END_VAR
  VAR
    busy : BOOL;
    edge_rs : BOOL;
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

PROGRAM PLC_PRG
  VAR
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    rs_y : MC_RESET;
    rs_z : MC_RESET;
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    x_cw : WORD := 16#0000;
    y_cw : WORD := 16#0000;
    z_cw : WORD := 16#0000;
    x_sww : WORD;
    y_sww : WORD;
    z_sww : WORD;
    tx : INT := 0;
    ty : INT := 0;
    tz : INT := 10;
    xs : INT := 0;
    ys : INT := 0;
    zs : INT := 0;
    jog_pos : INT := 50;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    man_go : BOOL;
    arc_on : BOOL;
    edge_draw : BOOL;
    fault_now : BOOL;
    run_ok : BOOL;
    pl_step : INT := 0;
    s_arc : REAL := 0.0;
    vt : REAL := 0.0;
    theta : REAL := 0.0;
    rem : REAL;
    stopd : REAL;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    ARCR : REAL := 25.0;
    ARCLEN : REAL := 157.0796;
    V_T : REAL := 40.0;
    A_T : REAL := 80.0;
    TH0 : REAL := 4.712389;
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
(* ---- program identity ---- *)
prog_id := 2;

(* ---- 扫描头：采样驱动器状态 / 基本状态 ---- *)
x_sww := dv_x.sw;
y_sww := dv_y.sw;
z_sww := dv_z.sw;
x_sw := WORD_TO_INT(x_sww);
y_sw := WORD_TO_INT(y_sww);
z_sw := WORD_TO_INT(z_sww);
fault_now := ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0);
fault_any := fault_now;
pen_down := z_fb <= 1;
run_ok := run AND NOT quickstop AND NOT fault_now;

(* ---- C1/C3/C4：急停 / 断使能 / 故障 → 中止序列（不自动续跑） ---- *)
IF (pl_step <> 0) AND (NOT run OR quickstop OR fault_now) THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    man_go := FALSE;
    arc_on := FALSE;
    vt := 0.0;
    s_arc := 0.0;
    plot_done := FALSE;
    move_done := FALSE;
END_IF;

(* ---- 使能 / 复位 / 快停（快停最后落笔 = 优先级最高） ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_sww);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_sww);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_sww);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sww);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_sww);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_sww);
st_x(fire := quickstop OR fault_now OR NOT run, cw := x_cw);
st_y(fire := quickstop OR fault_now OR NOT run, cw := y_cw);
st_z(fire := quickstop OR fault_now OR NOT run, cw := z_cw);

(* ---- C2/C3：手动定位（落笔互锁 + 使能联锁） ---- *)
IF cmd_go AND run_ok AND NOT pen_down AND pl_step = 0 AND NOT go_x_exe THEN
    tx := x_sp;
    ty := y_sp;
    tz := z_sp;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
    man_go := TRUE;
    move_done := FALSE;
END_IF;

(* ---- 手动回参考点：Z 回抬笔安全位 10 ---- *)
IF cmd_home AND run_ok AND pl_step = 0 AND NOT go_z_exe THEN
    tz := 10;
    go_z_exe := TRUE;
END_IF;

(* ---- NC 通道 Z 手动抬/落笔（无序列时直通） ---- *)
IF pl_step = 0 AND NOT go_z_exe AND NOT cmd_home AND run_ok THEN
    tz := z_sp;
END_IF;

(* ---- C5：绘圆序列器（电平持有触发，多段轨迹） ---- *)
CASE pl_step OF
    0: IF cmd_draw AND NOT edge_draw AND run_ok AND (z_fb >= 10) AND NOT go_x_exe THEN
           tx := 50;
           ty := 25;
           tz := 10;
           go_x_exe := TRUE;
           go_y_exe := TRUE;
           go_z_exe := TRUE;
           plot_done := FALSE;
           move_done := FALSE;
           pl_step := 1;
       END_IF;
    1: tx := 50;      (* XY 定位圆起点 (50,25) *)
       ty := 25;
    2: tz := 0;       (* 落笔至 0 *)
       arc_on := TRUE; (* 进入圆弧准备 *)
    3: arc_on := TRUE;  (* 圆弧插补中，目标由速度剖面生成 *)
       tx := tx;        (* 目标在 CASE 外由圆弧生成器覆写 *)
    4: tz := 10;      (* 抬笔 *)
    5: tx := 50;      (* 回圆心 *)
       ty := 50;
END_CASE;

(* ---- 圆弧生成：切向速度梯形（限 40%/s，加减速 80%/s²），起点角 -90° ---- *)
IF pl_step = 3 THEN
    rem := ARCLEN - s_arc;
    stopd := (vt * vt) / (2.0 * A_T);
    IF rem <= stopd THEN
        vt := MAX(0.0, vt - A_T * SCAN_T);
    ELSE
        vt := MIN(V_T, vt + A_T * SCAN_T);
    END_IF;
    IF vt > rem THEN vt := rem; END_IF;
    s_arc := MIN(ARCLEN, s_arc + vt * SCAN_T);
    theta := TH0 + s_arc / ARCR;
    tx := 50 + REAL_TO_INT(ARCR * COS(theta) + 0.5);
    ty := 50 + REAL_TO_INT(ARCR * SIN(theta) + 0.5);
END_IF;

(* ---- 步进：各轴插补空闲且到位 → 下一目标 ---- *)
IF pl_step <> 0 THEN
    IF pl_step = 3 THEN
        IF (s_arc >= ARCLEN) AND (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done THEN
            arc_on := FALSE;
            vt := 0.0;
            s_arc := 0.0;
            pl_step := 4;
        END_IF;
    ELSIF (pl_step = 1) OR (pl_step = 5) THEN
        IF (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done THEN
            IF pl_step = 5 THEN
                plot_done := TRUE;
                pl_step := 0;
                go_x_exe := FALSE;
                go_y_exe := FALSE;
                go_z_exe := FALSE;
            ELSE
                pl_step := pl_step + 1;
            END_IF;
        END_IF;
    ELSE
        IF (NOT ix_z.Busy) AND ix_z.Done AND (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done THEN
            pl_step := pl_step + 1;
        END_IF;
    END_IF;
END_IF;

(* ---- 手动定位完成汇总 ---- *)
IF man_go AND (NOT ix_x.Busy) AND ix_x.Done AND (NOT ix_y.Busy) AND ix_y.Done AND (NOT ix_z.Busy) AND ix_z.Done THEN
    man_go := FALSE;
    move_done := TRUE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
END_IF;

(* ---- 插补引擎：连续跟踪，急停/故障/断使能期间受控减速 ---- *)
ix_x(fire := go_x_exe, pos_target := tx, hold_req := NOT run_ok, VMAX := 45.0, ACCEL := 80.0, POSWIN := 0.5);
ix_y(fire := go_y_exe, pos_target := ty, hold_req := NOT run_ok, VMAX := 45.0, ACCEL := 80.0, POSWIN := 0.5);
ix_z(fire := go_z_exe, pos_target := tz, hold_req := NOT run_ok, VMAX := 20.0, ACCEL := 80.0, POSWIN := 0.5);

(* ---- C2：点动（仅抬笔 + 空闲 + 使能） ---- *)
IF run_ok AND NOT pen_down AND pl_step = 0 THEN
    IF jog_fwd AND (jog_pos < 97) THEN
        jog_pos := jog_pos + 1;
    ELSIF jog_rev AND (jog_pos > 3) THEN
        jog_pos := jog_pos - 1;
    END_IF;
END_IF;

(* ---- 插补点路由：点动时用 jog_pos，否则用 INTERP 输出 ---- *)
IF (jog_fwd OR jog_rev) AND run_ok AND NOT pen_down AND pl_step = 0 THEN
    xs := jog_pos;
ELSE
    xs := ix_x.Setpoint;
END_IF;
ys := ix_y.Setpoint;
zs := ix_z.Setpoint;

(* ---- 驱动器：跟随插补点，闭位置环 ---- *)
dv_x(cw := x_cw, Setpoint := xs, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := ys, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := zs, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- C1/C3/C4：急停 / 故障 / 断使能期间速度指令强制归零 ---- *)
IF quickstop OR fault_now OR NOT run THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

(* ---- 应用状态输出 ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
IF pl_step <> 0 THEN
    move_done := FALSE;
END_IF;

edge_draw := cmd_draw;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
