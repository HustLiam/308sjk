FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    hold_req : BOOL;
    pos_init : INT;
    lim_hi : INT := 100;
    lim_lo : INT := 0;
    VMAX : REAL := 40.0;
    ACCEL : REAL := 80.0;
    POSWIN : REAL := 2.0;
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
(* 触发沿：内部位置基准与外部反馈同步（消除漂移） *)
IF fire AND NOT edge_ir THEN
    pos := INT_TO_REAL(pos_init);
    vel := 0.0;
    Done := FALSE;
END_IF;
edge_ir := fire;
(* 连续跟踪（电平保持）：fire 期间目标偏离且空闲即起新轨迹 *)
IF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - pos) > 0.499) THEN
    IF (pos_target <= lim_hi) AND (pos_target >= lim_lo) THEN
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    ELSE
        Done := TRUE;
    END_IF;
END_IF;
IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - ACCEL * 0.02);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + ACCEL * 0.02);
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * ACCEL);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - ACCEL * 0.02);
        ELSE vel := MIN(0.0, vel + ACCEL * 0.02);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
        END_IF;
    ELSE
        IF dist > 0.0 THEN vel := MIN(VMAX, vel + ACCEL * 0.02);
        ELSE vel := MAX(-VMAX, vel + ACCEL * 0.02);
        END_IF;
    END_IF;
END_IF;
pos := pos + vel * 0.02;
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
    v_out : REAL;
    v_ff : REAL;
    pos_err : REAL;
    sp_prev : INT;
  END_VAR
  VAR CONSTANT
    KP : REAL := 6.0;
    QS_DECEL : REAL := 100.0;
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
    v_ff := INT_TO_REAL(Setpoint - sp_prev) / 0.02;
    pos_err := INT_TO_REAL(Setpoint) - INT_TO_REAL(pos_fb);
    v_out := v_ff + KP * pos_err;
    IF v_out > 120.0 THEN v_out := 120.0; END_IF;
    IF v_out < -120.0 THEN v_out := -120.0; END_IF;
    IF v_ff = 0.0 AND ABS(pos_err) <= 1.5 THEN
        v_out := 0.0;
    END_IF;
ELSIF state = 5 THEN
    IF v_out > 0.0 THEN v_out := MAX(0.0, v_out - QS_DECEL * 0.02);
    ELSIF v_out < 0.0 THEN v_out := MIN(0.0, v_out + QS_DECEL * 0.02);
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
  END_VAR
  VAR_IN_OUT
    cw : WORD;
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

PROGRAM PLC_PRG
  VAR
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    rs_y : MC_RESET;
    rs_z : MC_RESET;
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
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_sww : WORD;
    y_sww : WORD;
    z_sww : WORD;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
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
    pl_step : INT;
    pl_tx : INT;
    pl_ty : INT;
    pl_tz : INT;
    theta_targ : REAL;
    arc_done : BOOL;
    mv_active : BOOL;
    gate_ok : BOOL;
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
    prog_id AT %QW20 : INT := 3;
  END_VAR
(* ---- program identity ---- *)
prog_id := 3;
(* ---- sample drive status words ---- *)
x_sww := INT_TO_WORD(x_sw);
y_sww := INT_TO_WORD(y_sw);
z_sww := INT_TO_WORD(z_sw);
(* ---- fault / pen state ---- *)
fault_any := ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0);
pen_down := z_fb <= 1;
gate_ok := run AND NOT quickstop AND NOT fault_any;
(* ---- enable / reset (C3: run=0 no power; C4: fault cuts enable) ---- *)
pwr_x(Enable := gate_ok, cw := x_cw, sw := x_sww);
pwr_y(Enable := gate_ok, cw := y_cw, sw := y_sww);
pwr_z(Enable := gate_ok, cw := z_cw, sw := z_sww);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_sww);
rs_y(fire := cmd_reset, cw := y_cw, sw := y_sww);
rs_z(fire := cmd_reset, cw := z_cw, sw := z_sww);
(* ---- MC command layer (C2: pen_down blocks jog and manual X/Y go) ---- *)
go_x(fire := cmd_go AND gate_ok AND NOT pen_down AND (pl_step = 0), interp_exe := go_x_exe);
go_y(fire := cmd_go AND gate_ok AND NOT pen_down AND (pl_step = 0), interp_exe := go_y_exe);
go_z(fire := cmd_go AND gate_ok AND (pl_step = 0), interp_exe := go_z_exe);
hm_x(fire := cmd_home AND gate_ok AND (pl_step = 0), home_exe := hm_x_exe);
hm_y(fire := cmd_home AND gate_ok AND (pl_step = 0), home_exe := hm_y_exe);
hm_z(fire := cmd_home AND gate_ok AND (pl_step = 0), home_exe := hm_z_exe);
jg_x(JogForward := jog_fwd AND gate_ok AND NOT pen_down AND (pl_step = 0), JogBackward := jog_rev AND gate_ok AND NOT pen_down AND (pl_step = 0), jog_pos := x_jog_pos);
(* ---- manual move bookkeeping (电平门控, 无边沿记忆) ---- *)
IF cmd_go AND gate_ok AND NOT pen_down AND (pl_step = 0) THEN
    mv_active := TRUE;
    move_done := FALSE;
END_IF;
(* ---- 绘圆序列器 (C5): 抬笔 -> 起点(50,25) -> 落笔 -> 整圆 -> 抬笔 -> 圆心(50,50) ---- *)
CASE pl_step OF
    0: IF cmd_draw AND gate_ok AND all_oe THEN
           theta_targ := 0.0;
           arc_done := FALSE;
           plot_done := FALSE;
           move_done := FALSE;
           mv_active := FALSE;
           pl_tx := x_fb;
           pl_ty := y_fb;
           pl_tz := 10;
           go_x_exe := TRUE;
           go_y_exe := TRUE;
           go_z_exe := TRUE;
           pl_step := 1;
       END_IF;
    1: (* 抬笔至 z=10, XY 保持当前位置 *)
       pl_tz := 10;
       IF ix_z.Done AND NOT ix_z.Busy AND (z_fb >= 9) AND ix_x.Done AND ix_y.Done THEN
           pl_step := 2;
       END_IF;
    2: (* XY 定位至圆起点 (50,25) *)
       pl_tx := 50;
       pl_ty := 25;
       IF ix_x.Done AND NOT ix_x.Busy AND ix_y.Done AND NOT ix_y.Busy
          AND (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 25) <= 2) THEN
           pl_step := 3;
       END_IF;
    3: (* 落笔至 0 *)
       pl_tz := 0;
       IF ix_z.Done AND NOT ix_z.Busy AND (z_fb <= 1) THEN
           pl_step := 4;
       END_IF;
    4: (* 圆弧插补一周: 分段推进目标, 插补连续跟踪自动起新轨迹 *)
       IF NOT ix_x.Busy AND NOT ix_y.Busy AND ix_x.Done AND ix_y.Done THEN
           IF arc_done AND (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 25) <= 2) THEN
               pl_tz := 10;
               pl_step := 5;
           ELSE
               theta_targ := theta_targ + 0.3;
               IF theta_targ >= 6.2832 THEN
                   theta_targ := 6.2832;
                   arc_done := TRUE;
               END_IF;
               pl_tx := REAL_TO_INT(50.0 - 25.0 * SIN(theta_targ));
               pl_ty := REAL_TO_INT(50.0 - 25.0 * COS(theta_targ));
               pl_tz := 0;
           END_IF;
       END_IF;
    5: (* 抬笔 *)
       pl_tz := 10;
       IF ix_z.Done AND NOT ix_z.Busy AND (z_fb >= 9) THEN
           pl_step := 6;
       END_IF;
    6: (* 回圆心 (50,50) *)
       pl_tx := 50;
       pl_ty := 50;
       IF ix_x.Done AND NOT ix_x.Busy AND ix_y.Done AND NOT ix_y.Busy AND ix_z.Done AND NOT ix_z.Busy
          AND (ABS(x_fb - 50) <= 2) AND (ABS(y_fb - 50) <= 2) AND (z_fb >= 9) THEN
           plot_done := TRUE;
           go_x_exe := FALSE;
           go_y_exe := FALSE;
           go_z_exe := FALSE;
           pl_step := 0;
       END_IF;
END_CASE;
(* ---- target routing ---- *)
x_tgt := x_sp;
y_tgt := y_sp;
z_tgt := z_sp;
IF hm_x_exe THEN x_tgt := 0; END_IF;
IF hm_y_exe THEN y_tgt := 0; END_IF;
IF hm_z_exe THEN z_tgt := 10; END_IF;
IF pl_step <> 0 THEN
    x_tgt := pl_tx;
    y_tgt := pl_ty;
    z_tgt := pl_tz;
END_IF;
(* ---- interp engines (触发线电平保持, 连续跟踪) ---- *)
ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_tgt, hold_req := FALSE, pos_init := x_fb, lim_hi := 100, lim_lo := 0, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_tgt, hold_req := FALSE, pos_init := y_fb, lim_hi := 100, lim_lo := 0, VMAX := 40.0, ACCEL := 80.0, POSWIN := 2.0);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_tgt, hold_req := FALSE, pos_init := z_fb, lim_hi := 10, lim_lo := 0, VMAX := 20.0, ACCEL := 60.0, POSWIN := 1.0);
(* ---- clear trigger lines only in manual mode ---- *)
IF pl_step = 0 THEN
    IF ix_x.Done THEN go_x_exe := FALSE; hm_x_exe := FALSE; END_IF;
    IF ix_y.Done THEN go_y_exe := FALSE; hm_y_exe := FALSE; END_IF;
    IF ix_z.Done THEN go_z_exe := FALSE; hm_z_exe := FALSE; END_IF;
END_IF;
(* ---- interp point routing ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;
(* ---- drives ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_sw := WORD_TO_INT(dv_x.sw);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_sw := WORD_TO_INT(dv_y.sw);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_sw := WORD_TO_INT(dv_z.sw);
z_v := dv_z.v_cmd;
(* ---- axis speed limits (AC7) ---- *)
IF x_v > 40 THEN x_v := 40; END_IF;
IF x_v < -40 THEN x_v := -40; END_IF;
IF y_v > 40 THEN y_v := 40; END_IF;
IF y_v < -40 THEN y_v := -40; END_IF;
IF z_v > 20 THEN z_v := 20; END_IF;
IF z_v < -20 THEN z_v := -20; END_IF;
(* ---- C1/C3/C4: quickstop / fault / run-off abort and zero speed commands ---- *)
IF (NOT run) OR quickstop OR fault_any THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
    pl_step := 0;
    theta_targ := 0.0;
    arc_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
    mv_active := FALSE;
    plot_done := FALSE;
END_IF;
(* ---- status outputs ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
IF mv_active AND ix_x.Done AND ix_y.Done AND NOT go_x_exe AND NOT go_y_exe THEN
    mv_active := FALSE;
    move_done := TRUE;
END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
