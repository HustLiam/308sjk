FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    hold_req : BOOL;
    pos_target : INT;
    MAXPOS : INT;
    VMAX : REAL;
    ACCEL : REAL;
    POSWIN : REAL;
  END_VAR
  VAR_OUTPUT
    Setpoint : INT;
    Busy : BOOL;
    Done : BOOL;
  END_VAR
  VAR
    edge_ir : BOOL;
    tgt : REAL;
    pos : REAL;
    vel : REAL := 0.0;
    dist : REAL;
    stop_d : REAL;
    SCAN_T : REAL := 0.02;
  END_VAR
IF fire AND NOT edge_ir THEN
    IF pos_target > MAXPOS OR pos_target < 0 THEN
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
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
    cmd : WORD;
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
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

FUNCTION_BLOCK MC_MOVEABSOLUTE
  VAR_INPUT
    fire : BOOL;
    Position : INT;
  END_VAR
  VAR_IN_OUT
    interp_exe : BOOL;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
    Done : BOOL;
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

FUNCTION_BLOCK MC_HOME
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_IN_OUT
    home_exe : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
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
    jg_x : MC_MOVEJOG;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    ton_pd : TON;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    wx_sw : WORD;
    wy_sw : WORD;
    wz_sw : WORD;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    x_jog_pos : INT := 50;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    pl_step : INT;
    pl_ck : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    pha : REAL;
    homed : BOOL;
    fault_latch : BOOL;
    soft_err : BOOL;
    pen_up : BOOL;
    edge_dr : BOOL;
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

(* ---- 扫描头：采样驱动器状态 ---- *)
wx_sw := dv_x.sw;
wy_sw := dv_y.sw;
wz_sw := dv_z.sw;
x_sw := WORD_TO_INT(wx_sw);
y_sw := WORD_TO_INT(wy_sw);
z_sw := WORD_TO_INT(wz_sw);

(* ---- 笔状态 ---- *)
pen_up := z_fb >= 8;
pen_down := z_fb <= 2;

(* ---- 使能 / 复位 / 快停（快停最后落笔 = 优先级最高） ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := wx_sw);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := wy_sw);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := wz_sw);
rs_x(fire := cmd_reset, cw := x_cw, sw := wx_sw);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

(* ---- 故障检测与锁存（C4） ---- *)
fault_any := ((wx_sw AND 16#0008) <> 0) OR ((wy_sw AND 16#0008) <> 0) OR ((wz_sw AND 16#0008) <> 0);
fault_latch := fault_latch OR fault_any;
IF cmd_reset AND NOT fault_any THEN
    fault_latch := FALSE;
    soft_err := FALSE;
END_IF;

(* ---- 回参考标志（C6：run 断开后必须重新回零） ---- *)
IF NOT run THEN
    homed := FALSE;
END_IF;
IF hm_x.Done AND hm_y.Done AND hm_z.Done THEN
    homed := TRUE;
END_IF;

(* ---- 序列中止：急停 / 故障（C3/C4），plot_done 熄灭 ---- *)
IF (quickstop OR fault_any) AND (pl_step <> 0) THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- 软限界异常（C5）：越界立即停止插补，抬笔并退回行程内(中心) ---- *)
IF (pl_step = 4) AND ((x_fb < 2) OR (x_fb > 98) OR (y_fb < 2) OR (y_fb > 98)) THEN
    soft_err := TRUE;
    pl_step := 5;
END_IF;

(* ---- 绘图序列器：cmd_draw 上升沿启动，7 步目标表 ---- *)
CASE pl_step OF
    0: IF cmd_draw AND (NOT edge_dr) AND all_oe AND homed AND (NOT quickstop)
          AND (NOT fault_any) AND (NOT fault_latch) AND (NOT soft_err) THEN
           plot_done := FALSE;
           pl_tx := x_fb; pl_ty := y_fb; pl_tz := 10;
           pl_ck := 0;
           go_x_exe := TRUE;
           go_y_exe := TRUE;
           go_z_exe := TRUE;
           pl_step := 1;
       END_IF;
    1: pl_tx := x_fb; pl_ty := y_fb; pl_tz := 10;
    2: pl_tx := 50; pl_ty := 50; pl_tz := 10;
    3: pl_tx := 50; pl_ty := 50; pl_tz := 0;
    4: pha := INT_TO_REAL(pl_ck) * 0.1571;
       pl_tx := REAL_TO_INT(50.0 + 20.0 * COS(pha));
       pl_ty := REAL_TO_INT(50.0 + 20.0 * SIN(pha));
       pl_tz := 0;
    5: pl_tz := 10;
    6: pl_tx := 50; pl_ty := 50; pl_tz := 10;
END_CASE;
edge_dr := cmd_draw;

(* ---- 落笔稳定 0.3s（C1，TON 落地） ---- *)
ton_pd(IN := (pl_step = 3) AND (z_fb <= 1), PT := T#300MS);

(* ---- MC 指令层：落笔期间 X/Y 手动定位与点动被互锁拒绝（C3） ---- *)
go_x(fire := cmd_go AND pen_up AND (pl_step = 0) AND (NOT quickstop), Position := x_sp, interp_exe := go_x_exe);
go_y(fire := cmd_go AND pen_up AND (pl_step = 0) AND (NOT quickstop), Position := y_sp, interp_exe := go_y_exe);
go_z(fire := cmd_go AND (pl_step = 0) AND (NOT quickstop), Position := z_sp, interp_exe := go_z_exe);
hm_x(fire := cmd_home AND (pl_step = 0) AND (NOT quickstop), home_exe := hm_x_exe);
hm_y(fire := cmd_home AND (pl_step = 0) AND (NOT quickstop), home_exe := hm_y_exe);
hm_z(fire := cmd_home AND (pl_step = 0) AND (NOT quickstop), home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd AND pen_up AND (pl_step = 0) AND (NOT quickstop),
     JogBackward := jog_rev AND pen_up AND (pl_step = 0) AND (NOT quickstop), jog_pos := x_jog_pos);

(* ---- 插补目标路由：序列 > 回零 > 手动；Z 回零位=抬笔安全位 10 ---- *)
IF (pl_step <> 0) THEN
    x_tgt := pl_tx;
ELSIF hm_x_exe THEN
    x_tgt := 0;
ELSE
    x_tgt := x_sp;
END_IF;
IF (pl_step <> 0) THEN
    y_tgt := pl_ty;
ELSIF hm_y_exe THEN
    y_tgt := 0;
ELSE
    y_tgt := y_sp;
END_IF;
IF (pl_step <> 0) THEN
    z_tgt := pl_tz;
ELSIF hm_z_exe THEN
    z_tgt := 10;
ELSE
    z_tgt := z_sp;
END_IF;

(* ---- 插补引擎：连续跟踪模式，急停时受控减速（hold_req） ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_tgt, hold_req := quickstop,
     VMAX := 25.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_tgt, hold_req := quickstop,
     VMAX := 25.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_tgt, hold_req := quickstop,
     VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10);

(* ---- 触发线清除 ---- *)
IF ix_x.Done AND (pl_step = 0) THEN go_x_exe := FALSE; END_IF;
IF ix_y.Done AND (pl_step = 0) THEN go_y_exe := FALSE; END_IF;
IF ix_z.Done AND (pl_step = 0) THEN go_z_exe := FALSE; END_IF;
IF ix_x.Done THEN hm_x_exe := FALSE; END_IF;
IF ix_y.Done THEN hm_y_exe := FALSE; END_IF;
IF ix_z.Done THEN hm_z_exe := FALSE; END_IF;

(* ---- 步进：三轴插补全部空闲且 Done，落笔步需 0.3s 稳定 ---- *)
IF (pl_step <> 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    CASE pl_step OF
        1: pl_step := 2;
        2: pl_step := 3;
        3: IF ton_pd.Q THEN pl_step := 4; END_IF;
        4: IF pl_ck >= 39 THEN
               pl_ck := 0;
               pl_step := 5;
           ELSE
               pl_ck := pl_ck + 1;
           END_IF;
        5: pl_step := 6;
        6: plot_done := TRUE;
           pl_step := 0;
           go_x_exe := FALSE;
           go_y_exe := FALSE;
           go_z_exe := FALSE;
    END_CASE;
END_IF;

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
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);

(* ---- 速度指令输出：急停立即置 0（AC3/C3），圆插补限速 28（C2），Z 限 20 ---- *)
IF quickstop THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    x_v := dv_x.v_cmd;
    y_v := dv_y.v_cmd;
    z_v := dv_z.v_cmd;
END_IF;
IF x_v > 28 THEN x_v := 28; END_IF;
IF x_v < -28 THEN x_v := -28; END_IF;
IF y_v > 28 THEN y_v := 28; END_IF;
IF y_v < -28 THEN y_v := -28; END_IF;
IF z_v > 20 THEN z_v := 20; END_IF;
IF z_v < -20 THEN z_v := -20; END_IF;

(* ---- 应用状态 ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := go_x.Done AND go_y.Done AND go_z.Done AND (pl_step = 0);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
