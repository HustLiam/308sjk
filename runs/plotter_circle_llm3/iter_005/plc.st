FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
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
    pos : REAL := 0.0;
    tgt : REAL := 0.0;
    vel : REAL := 0.0;
    stop_d : REAL;
    dist : REAL;
    diff_r : REAL;
    edge_ir : BOOL;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
  END_VAR
(* 轴插补引擎：电平 fire 触发 + 连续跟踪（fire 保持期间目标变化自动起新轨迹） *)
diff_r := INT_TO_REAL(pos_target) - tgt;

IF fire AND (NOT edge_ir OR (NOT Busy AND ABS(diff_r) > 0.499)) THEN
    IF pos_target > 100 OR pos_target < 0 THEN
        Done := TRUE;
    ELSIF ABS(diff_r) <= 0.499 THEN
        tgt := INT_TO_REAL(pos_target);
        pos := tgt;
        Busy := FALSE;
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
END_IF;
edge_ir := fire;

IF hold_req THEN
    IF vel > 0.0 THEN
        vel := MAX(0.0, vel - ACCEL * SCAN_T);
    ELSIF vel < 0.0 THEN
        vel := MIN(0.0, vel + ACCEL * SCAN_T);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * ACCEL);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN
            vel := MAX(0.0, vel - ACCEL * SCAN_T);
        ELSE
            vel := MIN(0.0, vel + ACCEL * SCAN_T);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN
            vel := MIN(VMAX, vel + ACCEL * SCAN_T);
        ELSE
            vel := MAX(-VMAX, vel - ACCEL * SCAN_T);
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
    cmd : WORD;
    state : INT := 1;
    v_ff : REAL;
    pos_err : REAL;
    v_out : REAL := 0.0;
    sp_prev : INT;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 8.0;
    QS_DECEL : REAL := 60.0;
  END_VAR
(* DRIVE402: CSP 跟随 + 位置环; state 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA *)
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
        0: cw := (cw AND 16#00F0) OR 16#0006;  pstep := 1;
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
  VAR_IN_OUT
    cw : WORD;
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

FUNCTION_BLOCK MC_MOVEJOG
  VAR_INPUT
    JogForward : BOOL;
    JogBackward : BOOL;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
    jog_pos : INT;
  END_VAR
IF JogForward AND jog_pos < 97 THEN
    jog_pos := jog_pos + 1;
ELSIF JogBackward AND jog_pos > 3 THEN
    jog_pos := jog_pos - 1;
END_IF;
Busy := JogForward OR JogBackward;
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
    st_x : MC_STOP;
    st_y : MC_STOP;
    st_z : MC_STOP;
    jg_x : MC_MOVEJOG;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    xs : WORD;
    ys : WORD;
    zs : WORD;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    man_busy : BOOL;
    man_done : BOOL;
    aborted : BOOL;
    fault_now : BOOL;
    go_fire : BOOL;
    x_jog_pos : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    pl_step : INT;
    pl_seg : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    theta : REAL;
  END_VAR
  VAR
    run AT %QX0.0 : BOOL;
    cmd_home AT %QX0.1 : BOOL;
    cmd_go AT %QX0.2 : BOOL;
    jog_fwd AT %QX0.3 : BOOL;
    jog_rev AT %QX0.4 : BOOL;
    quickstop AT %QX0.5 : BOOL;
    cmd_reset AT %QX0.6 : BOOL;
    cmd_draw AT %QX0.7 : BOOL;
    all_oe AT %QX0.8 : BOOL;
    move_done AT %QX0.9 : BOOL;
    any_moving AT %QX0.10 : BOOL;
    fault_any AT %QX0.11 : BOOL;
    pen_down AT %QX0.12 : BOOL;
    plot_done AT %QX0.13 : BOOL;
    x_fb AT %QW0 : INT;
    y_fb AT %QW1 : INT;
    z_fb AT %QW2 : INT;
    x_sp AT %QW3 : INT;
    y_sp AT %QW4 : INT;
    z_sp AT %QW5 : INT;
    x_sw AT %QW6 : INT;
    y_sw AT %QW7 : INT;
    z_sw AT %QW8 : INT;
    x_v AT %QW9 : INT;
    y_v AT %QW10 : INT;
    z_v AT %QW11 : INT;
    prog_id AT %QW20 : INT := 3;
  END_VAR
(* ---- program identity ---- *)
prog_id := 3;

(* ---- 采样驱动器状态字 (WORD 运算, INT 输出用显式转换) ---- *)
xs := dv_x.sw;
ys := dv_y.sw;
zs := dv_z.sw;
x_sw := WORD_TO_INT(xs);
y_sw := WORD_TO_INT(ys);
z_sw := WORD_TO_INT(zs);

(* ---- 应用状态 ---- *)
fault_now := ((xs AND 16#0008) <> 0) OR ((ys AND 16#0008) <> 0) OR ((zs AND 16#0008) <> 0);
fault_any := fault_now;
pen_down := all_oe AND (z_fb <= 1);   (* 笔触纸: 仅驱动器使能时生效, 上电位不亮 *)

(* ---- C1/C3/C4: 急停/故障/run 断开 -> 中止序列并熄灭 plot_done (电平) ---- *)
IF quickstop OR fault_now OR (NOT run) THEN
    IF pl_step <> 0 THEN
        aborted := TRUE;
    END_IF;
    pl_step := 0;
    pl_seg := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
    man_busy := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- C1/C4: 复位清除中止锁存 (电平, 需故障已消除) ---- *)
IF cmd_reset AND (NOT fault_now) THEN
    aborted := FALSE;
END_IF;

(* ---- 绘圆序列器: 电平门控启动 (无单扫描脉冲) ---- *)
IF pl_step = 0 THEN
    IF cmd_draw AND run AND all_oe AND (NOT aborted) AND (NOT quickstop) AND (NOT fault_now) THEN
        pl_step := 1;
        pl_seg := 0;
        plot_done := FALSE;
        man_done := FALSE;
    END_IF;
END_IF;

(* ---- 序列步: 只改目标值, 触发线电平保持 ---- *)
CASE pl_step OF
    0: pl_step := pl_step;
    1: pl_tz := 10;                       (* 确认抬笔 *)
       go_z_exe := TRUE;
    2: pl_tx := 50;                       (* 定位圆起点 (50,25) *)
       pl_ty := 25;
       go_x_exe := TRUE;
       go_y_exe := TRUE;
    3: pl_tz := 0;                        (* 落笔 *)
       go_z_exe := TRUE;
    4: theta := INT_TO_REAL(pl_seg) * 0.2618;   (* 圆弧插补 24 段/整圈 *)
       pl_tx := REAL_TO_INT(50.0 + 25.0 * COS(theta));
       pl_ty := REAL_TO_INT(50.0 + 25.0 * SIN(theta));
    5: pl_tz := 10;                       (* 抬笔 *)
       go_z_exe := TRUE;
    6: pl_tx := 50;                       (* 回圆心 (50,50) *)
       pl_ty := 50;
       go_x_exe := TRUE;
       go_y_exe := TRUE;
END_CASE;

(* ---- 步进: 三轴全部空闲且 Done (连续跟踪: 目标变化自动起新轨迹) ---- *)
IF (pl_step <> 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 6 THEN
        plot_done := TRUE;
        pl_step := 0;
        pl_seg := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := FALSE;
    ELSIF pl_step = 4 THEN
        IF pl_seg >= 23 THEN
            pl_step := 5;
            pl_seg := 0;
        ELSE
            pl_seg := pl_seg + 1;
        END_IF;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- 手动定位 (C2: 落笔互锁; C3: run 联锁; 序列期间禁止; 电平门控) ---- *)
go_fire := cmd_go AND run AND (NOT pen_down) AND (pl_step = 0) AND (NOT quickstop)
           AND (NOT fault_now) AND (NOT man_busy) AND (NOT man_done);
IF NOT cmd_go THEN
    man_done := FALSE;
END_IF;
IF go_fire THEN
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
    man_busy := TRUE;
END_IF;

IF man_busy AND (pl_step = 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    man_busy := FALSE;
    man_done := TRUE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
END_IF;

(* ---- 回参考点 (Z 参考=抬笔安全位 10; 电平门控) ---- *)
IF cmd_home AND run AND (pl_step = 0) AND (NOT hm_x_exe) AND (NOT hm_y_exe) AND (NOT hm_z_exe) THEN
    hm_x_exe := TRUE;
    hm_y_exe := TRUE;
    hm_z_exe := TRUE;
END_IF;

IF (pl_step = 0) AND (NOT man_busy) AND (NOT hm_x_exe) AND (NOT hm_y_exe) AND (NOT hm_z_exe)
   AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

(* ---- 使能 / 复位 / 快停 ---- *)
pwr_x(Enable := run AND (NOT quickstop) AND (NOT fault_now), cw := x_cw, sw := xs);
pwr_y(Enable := run AND (NOT quickstop) AND (NOT fault_now), cw := y_cw, sw := ys);
pwr_z(Enable := run AND (NOT quickstop) AND (NOT fault_now), cw := z_cw, sw := zs);
rs_x(fire := cmd_reset, cw := x_cw, sw := xs);
st_x(fire := quickstop OR fault_now, cw := x_cw);
st_y(fire := quickstop OR fault_now, cw := y_cw);
st_z(fire := quickstop OR fault_now, cw := z_cw);

(* ---- 点动 (C2: 落笔互锁; 电平有效) ---- *)
jg_x(JogForward := jog_fwd AND run AND (NOT pen_down) AND (pl_step = 0) AND (NOT quickstop),
     JogBackward := jog_rev AND run AND (NOT pen_down) AND (pl_step = 0) AND (NOT quickstop));
x_jog_pos := jg_x.jog_pos;

(* ---- 目标路由: 回零 > 序列 > 手动 NC 设定 ---- *)
IF hm_x_exe THEN
    x_tgt := 0;
ELSIF pl_step >= 2 THEN
    x_tgt := pl_tx;
ELSE
    x_tgt := x_sp;
END_IF;

IF hm_y_exe THEN
    y_tgt := 0;
ELSIF pl_step >= 2 THEN
    y_tgt := pl_ty;
ELSE
    y_tgt := y_sp;
END_IF;

IF hm_z_exe THEN
    z_tgt := 10;
ELSIF pl_step >= 1 THEN
    z_tgt := pl_tz;
ELSE
    z_tgt := z_sp;
END_IF;

(* ---- 插补引擎: X/Y 40%/s, 80%/s^2; Z 限 20 ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_tgt,
     hold_req := quickstop OR fault_now, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_tgt,
     hold_req := quickstop OR fault_now, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_tgt,
     hold_req := quickstop OR fault_now, VMAX := 20.0, ACCEL := 40.0, POSWIN := 1.0);

(* ---- 插补点路由 ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- 驱动器: CSP 跟随 + 位置环 ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- C1: 急停/故障/失电期间速度指令立即归零 ---- *)
IF quickstop OR fault_now OR (NOT run) THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

(* ---- 输出状态 ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := man_done AND (pl_step = 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
