FUNCTION_BLOCK DRIVE402
  VAR_INPUT
    cw : INT;
    Setpoint : INT;
    pos_fb : INT;
  END_VAR
  VAR_OUTPUT
    v_cmd : INT;
    sw : INT;
  END_VAR
  VAR
    state : INT := 1;
    v_out : REAL := 0.0;
    sp_prev : INT := 0;
    v_ff : REAL;
    pos_err : REAL;
    cmd : INT;
  END_VAR
  VAR CONSTANT
    KP : REAL := 6.0;
    SCAN_T : REAL := 0.02;
    QS_DECEL : REAL := 300.0;
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

FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    VMAX : REAL := 40.0;
    ACCEL : REAL := 80.0;
    POSWIN : REAL := 2.0;
    MAXPOS : INT := 100;
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
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
  END_VAR
  VAR CONSTANT
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

FUNCTION_BLOCK MC_POWER
  VAR_INPUT
    Enable : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : INT;
    sw : INT;
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
    cw : INT;
    sw : INT;
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
    cw : INT;
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
  VAR_OUTPUT
    jog_pos : INT;
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
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    t_c6 : TON;
    x_cw : INT;
    y_cw : INT;
    z_cw : INT;
    x_exe : BOOL;
    y_exe : BOOL;
    z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    x_jog_pos : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    step : INT;
    ang : REAL;
    rad_err : REAL;
    follow_fault : BOOL;
    drive_flt : BOOL;
    draw_prev : BOOL;
    pen_up : BOOL;
  END_VAR
  VAR CONSTANT
    D_ANG : REAL := 0.024;
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
    all_oe AT %QX1.0 : BOOL;
    move_done AT %QX1.1 : BOOL;
    any_moving AT %QX1.2 : BOOL;
    fault_any AT %QX1.3 : BOOL;
    pen_down AT %QX1.4 : BOOL;
    plot_done AT %QX1.5 : BOOL;
    x_fb AT %QW10 : INT;
    y_fb AT %QW11 : INT;
    z_fb AT %QW12 : INT;
    x_sp AT %QW13 : INT;
    y_sp AT %QW14 : INT;
    z_sp AT %QW15 : INT;
    x_sw AT %QW16 : INT;
    y_sw AT %QW17 : INT;
    z_sw AT %QW18 : INT;
    x_v AT %QW19 : INT;
    prog_id AT %QW20 : INT := 3;
    y_v AT %QW21 : INT;
    z_v AT %QW22 : INT;
  END_VAR
(* ---- program identity ---- *)
prog_id := 3;

(* ---- 扫描头：采样驱动器状态 ---- *)
x_sw := dv_x.sw;
y_sw := dv_y.sw;
z_sw := dv_z.sw;

(* ---- 笔状态 ---- *)
pen_up := z_fb >= 8;
pen_down := z_fb <= 2;

(* ---- 故障汇总（C2/C6） ---- *)
drive_flt := ((x_sw AND 16#0008) <> 0) OR ((y_sw AND 16#0008) <> 0) OR ((z_sw AND 16#0008) <> 0);
fault_any := drive_flt OR follow_fault;

(* ---- 异常中止（C2/C3/C6）：受控减速、灭 plot_done；随动故障先抬笔（C6/C7） ---- *)
IF (quickstop OR fault_any OR NOT run) AND (step <> 0) THEN
    IF follow_fault AND NOT quickstop AND run THEN
        z_tgt := 10;
        z_exe := TRUE;
    ELSE
        z_exe := FALSE;
    END_IF;
    x_exe := FALSE;
    y_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
    plot_done := FALSE;
    step := 0;
END_IF;

(* ---- 故障复位（C7）：故障源消除且已抬笔才清除随动故障 ---- *)
IF cmd_reset AND NOT quickstop AND NOT drive_flt AND pen_up THEN
    follow_fault := FALSE;
END_IF;

(* ---- 绘图序列器（C4/C5）：抬笔->圆心->落笔->整圆->抬笔->回心 ---- *)
CASE step OF
    0: IF cmd_draw AND NOT draw_prev AND all_oe AND run AND NOT quickstop
          AND NOT fault_any AND pen_up THEN
           plot_done := FALSE;
           z_tgt := 10;
           z_exe := TRUE;
           step := 1;
       END_IF;
    1: z_tgt := 10;
       IF ix_z.Done THEN
           z_exe := FALSE;
           x_tgt := 50; y_tgt := 50;
           x_exe := TRUE; y_exe := TRUE;
           step := 2;
       END_IF;
    2: x_tgt := 50; y_tgt := 50;
       IF ix_x.Done AND ix_y.Done THEN
           x_tgt := 75; y_tgt := 50;
           step := 3;
       END_IF;
    3: x_tgt := 75; y_tgt := 50;
       IF ix_x.Done AND ix_y.Done THEN
           x_exe := FALSE; y_exe := FALSE;
           z_tgt := 0; z_exe := TRUE;
           step := 4;
       END_IF;
    4: z_tgt := 0;
       IF ix_z.Done AND pen_down THEN
           ang := 0.0;
           step := 5;
       END_IF;
    5: ang := ang + D_ANG;   (* omega=1.2rad/s -> 切向 30%/s, R=25 *)
       IF ang >= 6.2832 THEN
           x_tgt := 50; y_tgt := 50;
           z_tgt := 10; z_exe := TRUE;
           step := 6;
       END_IF;
    6: z_tgt := 10;
       IF ix_z.Done THEN
           z_exe := FALSE;
           x_tgt := 50; y_tgt := 50;
           x_exe := TRUE; y_exe := TRUE;
           step := 7;
       END_IF;
    7: x_tgt := 50; y_tgt := 50;
       IF ix_x.Done AND ix_y.Done THEN
           x_exe := FALSE; y_exe := FALSE;
           plot_done := TRUE;
           step := 0;
       END_IF;
END_CASE;

(* ---- 圆弧插补目标生成（C5）：x=R*cos, y=R*sin 连续跟踪 ---- *)
IF (step = 5) THEN
    x_tgt := REAL_TO_INT(50.0 + 25.0 * COS(ang));
    y_tgt := REAL_TO_INT(50.0 + 25.0 * SIN(ang));
END_IF;

(* ---- 随动故障检测（C6）：偏离理论圆 > 4% 且持续 300ms ---- *)
IF (step = 5) THEN
    rad_err := SQRT((INT_TO_REAL(x_fb) - 50.0) * (INT_TO_REAL(x_fb) - 50.0)
                  + (INT_TO_REAL(y_fb) - 50.0) * (INT_TO_REAL(y_fb) - 50.0)) - 25.0;
    t_c6(IN := ABS(rad_err) > 4.0, PT := T#300MS);
    IF t_c6.Q THEN
        follow_fault := TRUE;
    END_IF;
ELSE
    t_c6(IN := FALSE, PT := T#300MS);
END_IF;

(* ---- 使能 / 复位 / 快停（快停最后落笔 = 优先级最高） ---- *)
pwr_x(Enable := run AND NOT quickstop, cw := x_cw, sw := x_sw);
pwr_y(Enable := run AND NOT quickstop, cw := y_cw, sw := y_sw);
pwr_z(Enable := run AND NOT quickstop, cw := z_cw, sw := z_sw);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sw);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_sw);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_sw);
st_x(fire := quickstop OR NOT run, cw := x_cw);
st_y(fire := quickstop OR NOT run, cw := y_cw);
st_z(fire := quickstop OR NOT run, cw := z_cw);

(* ---- 手动指令层（C1：落笔时拒绝 X/Y 手动；序列期间禁止手动） ---- *)
go_x(fire := cmd_go AND pen_up AND (step = 0) AND run AND NOT quickstop AND NOT fault_any,
     Position := x_sp, interp_exe := x_exe);
go_y(fire := cmd_go AND pen_up AND (step = 0) AND run AND NOT quickstop AND NOT fault_any,
     Position := y_sp, interp_exe := y_exe);
go_z(fire := cmd_go AND (step = 0) AND run AND NOT quickstop AND NOT fault_any,
     Position := z_sp, interp_exe := z_exe);
hm_x(fire := cmd_home AND pen_up AND (step = 0) AND run AND NOT quickstop AND NOT fault_any,
     home_exe := hm_x_exe);
hm_y(fire := cmd_home AND pen_up AND (step = 0) AND run AND NOT quickstop AND NOT fault_any,
     home_exe := hm_y_exe);
hm_z(fire := cmd_home AND (step = 0) AND run AND NOT quickstop AND NOT fault_any,
     home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd AND pen_up AND (step = 0) AND run AND NOT quickstop AND NOT fault_any,
     JogBackward := jog_rev AND pen_up AND (step = 0) AND run AND NOT quickstop AND NOT fault_any,
     jog_pos := x_jog_pos);

(* ---- 插补目标路由：序列 > 回零 > 手动；Z 回零位=抬笔安全位 10 ---- *)
IF (step = 0) THEN
    IF hm_x_exe THEN
        x_tgt := 0;
    ELSE
        x_tgt := x_sp;
    END_IF;
    IF hm_y_exe THEN
        y_tgt := 0;
    ELSE
        y_tgt := y_sp;
    END_IF;
    IF hm_z_exe THEN
        z_tgt := 10;
    ELSE
        z_tgt := z_sp;
    END_IF;
END_IF;

(* ---- 插补引擎调用 ---- *)
ix_x(fire := x_exe OR hm_x_exe, pos_target := x_tgt, hold_req := FALSE,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := y_exe OR hm_y_exe, pos_target := y_tgt, hold_req := FALSE,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := z_exe OR hm_z_exe, pos_target := z_tgt, hold_req := FALSE,
     VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10);

(* ---- 触发线清除（手动/回零模式：exe 保持到 Done） ---- *)
IF ix_x.Done AND (step = 0) THEN x_exe := FALSE; END_IF;
IF ix_y.Done AND (step = 0) THEN y_exe := FALSE; END_IF;
IF ix_z.Done AND (step = 0) THEN z_exe := FALSE; END_IF;
IF ix_x.Done THEN hm_x_exe := FALSE; END_IF;
IF ix_y.Done THEN hm_y_exe := FALSE; END_IF;
IF ix_z.Done THEN hm_z_exe := FALSE; END_IF;

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

(* ---- 应用状态 ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
move_done := go_x.Done AND go_y.Done AND go_z.Done AND (step = 0);
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);

(* ---- draw 上升沿记忆 ---- *)
draw_prev := cmd_draw;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
