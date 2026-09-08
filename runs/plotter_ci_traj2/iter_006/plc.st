FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    pos_in : INT;
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
    tgt : REAL;
    pos : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
    edge_ir : BOOL;
    started : BOOL;
  END_VAR
IF fire AND NOT edge_ir THEN
    tgt := INT_TO_REAL(pos_target);
    pos := INT_TO_REAL(pos_in);
    vel := 0.0;
    Busy := TRUE;
    Done := FALSE;
    started := TRUE;
END_IF;
edge_ir := fire;
(* 连续跟踪：fire 电平保持期间目标变化即起新轨迹 *)
IF (NOT Busy) AND fire AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    pos := INT_TO_REAL(pos_in);
    vel := 0.0;
    Busy := TRUE;
    Done := FALSE;
END_IF;
IF started THEN
    IF Busy THEN
        dist := tgt - pos;
        stop_d := (vel * vel) / (2.0 * ACCEL);
        IF ABS(dist) <= stop_d THEN
            IF dist >= 0.0 THEN
                vel := MAX(0.0, vel - ACCEL * 0.02);
            ELSE
                vel := MIN(0.0, vel + ACCEL * 0.02);
            END_IF;
            IF (vel = 0.0) AND (ABS(dist) <= POSWIN) THEN
                pos := tgt;
                Busy := FALSE;
                Done := TRUE;
            END_IF;
        ELSE
            IF dist > 0.0 THEN
                vel := MIN(VMAX, vel + ACCEL * 0.02);
            ELSE
                vel := MAX(-VMAX, vel - ACCEL * 0.02);
            END_IF;
        END_IF;
    END_IF;
    pos := pos + vel * 0.02;
    Setpoint := REAL_TO_INT(pos);
ELSE
    Setpoint := pos_in;
END_IF;
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
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
  END_VAR
  VAR CONSTANT
    SCAN_T : REAL := 0.02;
    KP : REAL := 8.0;
  END_VAR
(* state: 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA，显式初值 1=SOD 起步 *)
cmd := cw AND 16#000F;

IF (state = 7) AND ((cw AND 16#0080) <> 0) THEN
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
       (* 兜底：落入未定义状态（如 0）回到 SOD，保证握手可恢复 *)
       state := 1;
END_CASE;

(* 位置环：P 控制 + 设定值差分前馈 *)
IF (state >= 4) AND (state <= 6) THEN
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / SCAN_T;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF (v_ff = 0.0) AND (ABS(pos_err) <= 1.5) THEN
        v_out := 0.0;
    END_IF;
ELSE
    (* 非使能/快停态：速度指令立即归零 —— 满足 AC5/AC6 禁止态 *)
    v_out := 0.0;
    v_ff := 0.0;
    pos_err := 0.0;
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
    6: sw := 16#0011;
    7: sw := 16#0018;
    ELSE
       sw := sw;
END_CASE;
IF state = 4 THEN
    IF (ABS(pos_err) <= 2.0) AND (v_ff = 0.0) THEN
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
    pstep : INT := 0;
  END_VAR
IF Enable AND (pstep = 4) AND ((sw AND 16#0004) = 0) AND ((sw AND 16#0008) = 0) THEN
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
    (* 未使能期间持续 shutdown，驱动器保持 RTSO *)
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
Done := (NOT busy) AND ((sw AND 16#0008) = 0) AND (NOT fire);
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
  END_VAR
  VAR_IN_OUT
    interp_exe : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
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
IF JogForward AND (jog_pos < 97) THEN
    jog_pos := jog_pos + 1;
ELSIF JogBackward AND (jog_pos > 3) THEN
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
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_sw_w : WORD;
    y_sw_w : WORD;
    z_sw_w : WORD;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT := 50;
    ptx : INT;
    pty : INT;
    ptz : INT;
    pl_step : INT := 0;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    ang : REAL;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    draw_prev : BOOL;
    rs_prev : BOOL;
    draw_rise : BOOL;
    rs_rise : BOOL;
    z_touch_cmd : BOOL;
    abort_latch : BOOL;
    go_ok : BOOL;
    jog_ok : BOOL;
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
    hm_x : MC_HOME;
    hm_y : MC_HOME;
    hm_z : MC_HOME;
    jg_x : MC_MOVEJOG;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
  END_VAR
  VAR
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
    prog_id AT %QW20 : INT := 3;
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
  END_VAR
(* ---- program identity（契约② v1.1：每扫描周期写入）---- *)
prog_id := 3;

(* ---- 边沿检测 ---- *)
draw_rise := cmd_draw AND NOT draw_prev;
draw_prev := cmd_draw;
rs_rise := cmd_reset AND NOT rs_prev;
rs_prev := cmd_reset;

(* ---- 采样驱动器状态 / 故障汇总 ---- *)
x_sw_w := dv_x.sw;
y_sw_w := dv_y.sw;
z_sw_w := dv_z.sw;
x_sw := WORD_TO_INT(x_sw_w);
y_sw := WORD_TO_INT(y_sw_w);
z_sw := WORD_TO_INT(z_sw_w);
fault_any := ((x_sw_w AND 16#0008) <> 0) OR ((y_sw_w AND 16#0008) <> 0) OR ((z_sw_w AND 16#0008) <> 0);

(* ---- 使能 / 复位 / 快停（快停优先级最高） ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_sw_w);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_sw_w);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_sw_w);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sw_w);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_sw_w);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_sw_w);
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;

(* ---- C5 急停/故障中止锁存：须 cmd_reset 上升沿且 run=1 方可重启 ---- *)
IF quickstop OR fault_any THEN
    abort_latch := TRUE;
END_IF;
IF rs_rise AND run THEN
    abort_latch := FALSE;
END_IF;

(* ---- 急停/故障/失使能：清序列、清触发线、plot_done 熄灭 ---- *)
IF (quickstop OR fault_any OR (NOT run)) AND (pl_step <> 0) THEN
    pl_step := 0;
    plot_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

(* ---- 笔态输出：上电/未使能/急停/故障期间强制熄灭（修复段[1]：初始 z=0 但未命令不得报触纸） ---- *)
z_touch_cmd := ((pl_step >= 2) AND (pl_step <= 26)) OR (go_z_exe AND (pl_step = 0) AND (z_sp <= 2));
pen_down := run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND z_touch_cmd AND (z_fb <= 2);

(* ---- C1 落笔互锁 + C2 使能互锁：手动定位/点动/回零门控 ---- *)
go_ok := run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND (NOT pen_down) AND (pl_step = 0);
jog_ok := go_ok;

(* ---- MC 指令层 ---- *)
go_x(fire := cmd_go AND go_ok, interp_exe := go_x_exe);
go_y(fire := cmd_go AND go_ok, interp_exe := go_y_exe);
go_z(fire := cmd_go AND go_ok, interp_exe := go_z_exe);
hm_x(fire := cmd_home AND go_ok, home_exe := hm_x_exe);
hm_y(fire := cmd_home AND go_ok, home_exe := hm_y_exe);
hm_z(fire := cmd_home AND go_ok, home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd AND jog_ok, JogBackward := jog_rev AND jog_ok, jog_pos := x_jog_pos);

(* ---- 绘图序列启动（上升沿 + 全联锁；步表步 1：抬笔定位圆周起点） ---- *)
IF (pl_step = 0) AND draw_rise AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any) AND (NOT abort_latch) THEN
    plot_done := FALSE;
    pl_tx := 50;
    pl_ty := 25;
    pl_tz := 10;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
    pl_step := 1;
END_IF;

(* ---- 多段轨迹序列器：每步只改目标值（坐标权威步表逐字落地） ---- *)
CASE pl_step OF
    0: pl_tx := pl_tx;
    1: pl_tx := 50; pl_ty := 25; pl_tz := 10;   (* 步1 pen_up_move *)
    2: pl_tx := 50; pl_ty := 25; pl_tz := 0;    (* 步2 pen_down_move *)
    3..26:                                       (* 步3 circle_sweep：24 段折线 k=1..23 *)
       ang := INT_TO_REAL(pl_step - 2) * 0.2617994;
       pl_tx := REAL_TO_INT(50.0 + 25.0 * SIN(ang));
       pl_ty := REAL_TO_INT(50.0 - 25.0 * COS(ang));
       pl_tz := 0;
    27: pl_tx := 50; pl_ty := 25; pl_tz := 10;  (* 步4 pen_up_move 闭合回起点并抬笔 *)
    28: pl_tx := 50; pl_ty := 50; pl_tz := 10;  (* 步5 pen_up_move 回圆心=完成位 *)
    ELSE
       pl_step := 0;
END_CASE;

(* ---- 目标路由：序列用 pl_t*，手动定位用 NC 设定值，回零 X/Y=0、Z=10 ---- *)
IF pl_step <> 0 THEN
    ptx := pl_tx;
    pty := pl_ty;
    ptz := pl_tz;
ELSIF hm_x_exe OR hm_y_exe OR hm_z_exe THEN
    ptx := 0;
    pty := 0;
    ptz := 10;
ELSE
    ptx := x_sp;
    pty := y_sp;
    ptz := z_sp;
END_IF;

(* ---- 插补引擎（连续跟踪，XY 限速 40 / Z 限速 20，POSWIN XY=2 / Z=1） ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := ptx, pos_in := x_fb, VMAX := 40.0, ACCEL := 120.0, POSWIN := 2.0);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := pty, pos_in := y_fb, VMAX := 40.0, ACCEL := 120.0, POSWIN := 2.0);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := ptz, pos_in := z_fb, VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0);

(* ---- 步进：三轴插补全部空闲且 Done（C3/C4/C6 时序） ---- *)
IF (pl_step <> 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    IF pl_step = 28 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- 手动/回零完成：清触发线 ---- *)
IF (pl_step = 0) AND ix_x.Done THEN
    go_x_exe := FALSE;
    hm_x_exe := FALSE;
END_IF;
IF (pl_step = 0) AND ix_y.Done THEN
    go_y_exe := FALSE;
    hm_y_exe := FALSE;
END_IF;
IF (pl_step = 0) AND ix_z.Done THEN
    go_z_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

(* ---- 插补点路由：点动时用 jog_pos ---- *)
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

(* ---- 应用状态输出 ---- *)
move_done := (pl_step = 0) AND go_x.Done AND go_y.Done AND go_z.Done AND (NOT pen_down) AND (NOT cmd_go);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
