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
    pos : REAL;
    tgt : REAL;
    vel : REAL;
    dist : REAL;
    stop_d : REAL;
    edge_ir : BOOL;
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
    cw : INT;
    Setpoint : INT;
    pos_fb : INT;
  END_VAR
  VAR_OUTPUT
    sw : INT;
    v_cmd : INT;
  END_VAR
  VAR
    state : INT := 1;
    cmd : INT;
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
    SCAN_T : REAL := 0.02;
    KP : REAL := 8.0;
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
    sw : INT;
  END_VAR
  VAR_IN_OUT
    cw : INT;
  END_VAR
  VAR_OUTPUT
    Status : BOOL;
  END_VAR
  VAR
    pstep : INT;
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
    sw : INT;
  END_VAR
  VAR_IN_OUT
    cw : INT;
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
Done := NOT busy AND ((sw AND 16#0008) = 0) AND NOT fire;
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
    x_cw : INT;
    y_cw : INT;
    z_cw : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    pl_step : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    x_jog_pos : INT := 50;
    edge_dr : BOOL;
    pen_up : BOOL;
    fault_x : BOOL;
    fault_y : BOOL;
    fault_z : BOOL;
    angle : REAL;
    x_c : REAL;
    y_c : REAL;
    OMEGA : REAL := 1.6;
    SCAN_T : REAL := 0.02;
    manual_ok : BOOL;
  END_VAR
  VAR
    prog_id AT %QW20 : INT := 2;
    run AT %QX30.0 : BOOL;
    cmd_home AT %QX30.1 : BOOL;
    cmd_go AT %QX30.2 : BOOL;
    jog_fwd AT %QX30.3 : BOOL;
    jog_rev AT %QX30.4 : BOOL;
    quickstop AT %QX30.5 : BOOL;
    cmd_reset AT %QX30.6 : BOOL;
    cmd_draw AT %QX30.7 : BOOL;
    all_oe AT %QX31.0 : BOOL;
    move_done AT %QX31.1 : BOOL;
    any_moving AT %QX31.2 : BOOL;
    fault_any AT %QX31.3 : BOOL;
    pen_down AT %QX31.4 : BOOL;
    plot_done AT %QX31.5 : BOOL;
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
  END_VAR
(* ---- program identity ---- *)
prog_id := 2;

(* ---- 故障位解码（INT 位运算结果显式转 BOOL） ---- *)
fault_x := ((x_sw AND 16#0008) <> 0);
fault_y := ((y_sw AND 16#0008) <> 0);
fault_z := ((z_sw AND 16#0008) <> 0);
fault_any := fault_x OR fault_y OR fault_z;

(* ---- 笔状态 ---- *)
pen_up := (z_fb >= 8);
pen_down := (z_fb <= 1);

(* ---- C3 使能联锁 / C1 急停 / C4 故障：驱动器电源与快停 ---- *)
pwr_x(Enable := run AND NOT quickstop AND NOT fault_any, sw := x_sw, cw := x_cw);
pwr_y(Enable := run AND NOT quickstop AND NOT fault_any, sw := y_sw, cw := y_cw);
pwr_z(Enable := run AND NOT quickstop AND NOT fault_any, sw := z_sw, cw := z_cw);
rs_x(fire := cmd_reset, sw := x_sw, cw := x_cw);
rs_y(fire := cmd_reset, sw := y_sw, cw := y_cw);
rs_z(fire := cmd_reset, sw := z_sw, cw := z_cw);
st_x(fire := quickstop OR fault_any OR NOT run, cw := x_cw);
st_y(fire := quickstop OR fault_any OR NOT run, cw := y_cw);
st_z(fire := quickstop OR fault_any OR NOT run, cw := z_cw);

all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;

(* ---- 手动允许：抬笔 + 空闲 + 使能 + 无急停无故障（C2/C3） ---- *)
manual_ok := pen_up AND (pl_step = 0) AND all_oe AND NOT quickstop AND NOT fault_any;

(* ---- 点动（仅抬笔，C2） ---- *)
IF manual_ok THEN
    IF jog_fwd AND (x_jog_pos < 97) THEN
        x_jog_pos := x_jog_pos + 1;
    ELSIF jog_rev AND (x_jog_pos > 3) THEN
        x_jog_pos := x_jog_pos - 1;
    END_IF;
END_IF;

(* ---- 手动/回零触发线 ---- *)
IF cmd_go AND manual_ok AND NOT go_x_exe THEN
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
END_IF;
IF cmd_home AND manual_ok AND NOT hm_x_exe THEN
    hm_x_exe := TRUE;
    hm_y_exe := TRUE;
    hm_z_exe := TRUE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
END_IF;

(* ---- 急停/故障中止绘圆（C1/C4）：plot_done 熄灭，需复位 ---- *)
IF (quickstop OR fault_any OR NOT run) AND (pl_step <> 0) THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- 绘圆序列器 ---- *)
IF (pl_step = 0) AND cmd_draw AND NOT edge_dr AND all_oe
   AND NOT quickstop AND NOT fault_any AND pen_up THEN
    plot_done := FALSE;
    pl_step := 1;
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
    angle := 0.0;
END_IF;
edge_dr := cmd_draw;

CASE pl_step OF
    1: (* XY 定位至圆起点 (50,25)，保持抬笔 *)
       pl_tx := 50; pl_ty := 25; pl_tz := 10;
    2: (* 落笔 z=0 *)
       pl_tx := 50; pl_ty := 25; pl_tz := 0;
    3: (* 圆弧插补：圆心(50,50) 半径25，切向速度 40%/s → ω=1.6 rad/s *)
       IF NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done THEN
           IF angle >= 6.2832 THEN
               pl_step := 4;
           ELSE
               angle := angle + OMEGA * SCAN_T;
           END_IF;
       END_IF;
       x_c := 50.0 + 25.0 * COS(angle);
       y_c := 50.0 + 25.0 * SIN(angle);
       pl_tx := REAL_TO_INT(x_c);
       pl_ty := REAL_TO_INT(y_c);
       pl_tz := 0;
    4: (* 抬笔 z=10 *)
       pl_tx := 50; pl_ty := 25; pl_tz := 10;
    5: (* 回圆心 (50,50) *)
       pl_tx := 50; pl_ty := 50; pl_tz := 10;
END_CASE;

(* ---- 步进判据：三轴到位 ---- *)
IF (pl_step = 1) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    pl_step := 2;
    angle := 0.0;
END_IF;
IF (pl_step = 2) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    pl_step := 3;
    angle := 0.0;
END_IF;
IF (pl_step = 4) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    pl_step := 5;
END_IF;
IF (pl_step = 5) AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy
   AND ix_x.Done AND ix_y.Done AND ix_z.Done THEN
    pl_step := 0;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    plot_done := TRUE;
END_IF;

(* ---- 目标路由：序列 > 回零 > 手动 ---- *)
IF (pl_step <> 0) THEN
    x_tgt := pl_tx;
    y_tgt := pl_ty;
    z_tgt := pl_tz;
ELSE
    IF hm_x_exe THEN x_tgt := 0; ELSE x_tgt := x_sp; END_IF;
    IF hm_y_exe THEN y_tgt := 0; ELSE y_tgt := y_sp; END_IF;
    IF hm_z_exe THEN z_tgt := 10; ELSE z_tgt := z_sp; END_IF;
END_IF;

(* ---- 插补引擎（连续跟踪模式） ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_tgt, hold_req := FALSE,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_tgt, hold_req := FALSE,
     VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0, MAXPOS := 100);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_tgt, hold_req := FALSE,
     VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0, MAXPOS := 10);

(* ---- 触发线清除（序列期间为序列持有） ---- *)
IF ix_x.Done AND (pl_step = 0) THEN go_x_exe := FALSE; END_IF;
IF ix_y.Done AND (pl_step = 0) THEN go_y_exe := FALSE; END_IF;
IF ix_z.Done AND (pl_step = 0) THEN go_z_exe := FALSE; END_IF;
IF ix_x.Done THEN hm_x_exe := FALSE; END_IF;
IF ix_y.Done THEN hm_y_exe := FALSE; END_IF;
IF ix_z.Done THEN hm_z_exe := FALSE; END_IF;

(* ---- 插补点路由：点动优先 ---- *)
IF jog_fwd AND manual_ok THEN
    x_interp := x_jog_pos;
ELSIF jog_rev AND manual_ok THEN
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

(* ---- 速度指令输出：急停/故障/未使能立即归零（C1/C3/C4） ---- *)
IF quickstop OR fault_any OR NOT run THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
ELSE
    x_v := dv_x.v_cmd;
    y_v := dv_y.v_cmd;
    z_v := dv_z.v_cmd;
END_IF;

(* ---- 应用状态（C6） ---- *)
move_done := (pl_step = 0) AND ix_x.Done AND ix_y.Done AND ix_z.Done
             AND NOT go_x_exe AND NOT go_y_exe AND NOT go_z_exe;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
