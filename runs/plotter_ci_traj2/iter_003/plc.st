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
(* state: 1=SOD 2=RTSO 3=SO 4=OE 5=QSA 6=FRA 7=FA *)
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
       state := state;
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
    seg : INT;
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
    abort_latch : BOOL;
    go_ok : BOOL;
    jog_ok : BOOL;
    pen_cmd : BOOL;
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

(* ---- 边沿检测 ---- *)
draw_rise := cmd_draw AND NOT draw_prev;
draw_prev := cmd_draw;
rs_rise := cmd_reset AND NOT rs_prev;
rs_prev := cmd_reset;

(* ---- 采样驱动器状态 ---- *)
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
st_x(fire := quickstop, cw := x_cw);
st_y(fire := quickstop, cw := y_cw);
st_z(fire := quickstop, cw := z_cw);

all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;

(* ---- 笔态：上电/急停/故障/未使能/静止且无落笔命令时绝不报触纸 —— 段[1]/AC4
        （初始 z_fb=0 但系统未就绪，pen_down 必须为 FALSE） *)
pen_cmd := run AND all_oe AND (NOT quickstop) AND (NOT fault_any)
           AND (NOT go_x_exe) AND (NOT go_y_exe) AND (NOT go_z_exe)
           AND (NOT hm_x_exe) AND (NOT hm_y_exe) AND (NOT hm_z_exe)
           AND ((NOT jg_x.Busy));
pen_down := ((pl_step >= 3) AND (pl_step <= 27) AND run AND all_oe AND (NOT quickstop) AND (NOT fault_any))
            OR (pen_cmd AND (z_sp <= 2) AND (pl_step = 0))
           ) AND (z_fb <= 2);
pen_down := (z_cmd_down AND (z_fb <= 2)) OR FALSE;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
