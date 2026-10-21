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
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE; Done := FALSE; Aborted := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE; Done := FALSE; Aborted := FALSE;
END_IF;
edge_ir := fire;

IF hold_req THEN
    IF vel > 0.0 THEN vel := MAX(0.0, vel - dec_req * SCAN_T);
    ELSIF vel < 0.0 THEN vel := MIN(0.0, vel + dec_req * SCAN_T);
    END_IF;
    IF vel = 0.0 AND Busy THEN
        Busy := FALSE; Done := FALSE; Aborted := TRUE;
    END_IF;
ELSIF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * dec_req);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - dec_req * SCAN_T);
        ELSE vel := MIN(0.0, vel + dec_req * SCAN_T);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= POSWIN THEN
            pos := tgt; Busy := FALSE; Done := TRUE; Aborted := FALSE;
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

FUNCTION_BLOCK INTERP_Z
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
    edge_irz : BOOL;
    posz : REAL := 0.0;
    velz : REAL := 0.0;
    tgtz : REAL := 0.0;
    distz : REAL := 0.0;
    stop_dz : REAL := 0.0;
    SCAN_TZ : REAL := 0.02;
    POSWINZ : REAL := 1.0;
  END_VAR
IF fire AND NOT edge_irz THEN
    IF pos_target > 10 OR pos_target < 0 THEN
        Done := TRUE;
    ELSE
        tgtz := INT_TO_REAL(pos_target);
        Busy := TRUE; Done := FALSE; Aborted := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgtz) > 0.499) THEN
    tgtz := INT_TO_REAL(pos_target);
    Busy := TRUE; Done := FALSE; Aborted := FALSE;
END_IF;
edge_irz := fire;

IF hold_req THEN
    IF velz > 0.0 THEN velz := MAX(0.0, velz - dec_req * SCAN_TZ);
    ELSIF velz < 0.0 THEN velz := MIN(0.0, velz + dec_req * SCAN_TZ);
    END_IF;
    IF velz = 0.0 AND Busy THEN
        Busy := FALSE; Done := FALSE; Aborted := TRUE;
    END_IF;
ELSIF Busy THEN
    distz := tgtz - posz;
    stop_dz := (velz * velz) / (2.0 * dec_req);
    IF ABS(distz) <= stop_dz THEN
        IF distz >= 0.0 THEN velz := MAX(0.0, velz - dec_req * SCAN_TZ);
        ELSE velz := MIN(0.0, velz + dec_req * SCAN_TZ);
        END_IF;
        IF velz = 0.0 AND ABS(distz) <= POSWINZ THEN
            posz := tgtz; Busy := FALSE; Done := TRUE; Aborted := FALSE;
        END_IF;
    ELSE
        IF distz > 0.0 THEN velz := MIN(vel_req, velz + acc_req * SCAN_TZ);
        ELSE velz := MAX(-vel_req, velz - acc_req * SCAN_TZ);
        END_IF;
    END_IF;
END_IF;

posz := posz + velz * SCAN_TZ;
Setpoint := REAL_TO_INT(posz);
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
    v_out : REAL := 0.0;
    v_ff : REAL := 0.0;
    pos_err : REAL := 0.0;
    sp_prev : INT := 0;
    KP : REAL := 25.0;
    QS_DECEL : REAL := 120.0;
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
    pstep : INT := 0;
  END_VAR
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
IF fire AND NOT edge_ma THEN
    Error := FALSE; ErrorID := 0; CommandAborted := FALSE;
    IF (sw AND 16#0008) <> 0 THEN
        Error := TRUE; ErrorID := 2;
    ELSIF (sw AND 16#0004) = 0 THEN
        Error := TRUE; ErrorID := 3;
    ELSIF Position < 0 OR Position > 100 THEN
        Error := TRUE; ErrorID := 1;
    ELSE
        interp_exe := TRUE;
        dyn_vel := Velocity; dyn_acc := Acceleration; dyn_dec := Deceleration;
    END_IF;
END_IF;
edge_ma := fire;

IF interp_exe AND abort_bus AND NOT edge_ab THEN
    interp_exe := FALSE;
    CommandAborted := TRUE;
END_IF;
edge_ab := abort_bus;
Busy := interp_exe;
Active := interp_exe;
Done := NOT interp_exe AND NOT Error;
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
    Done := FALSE;
END_IF;
edge_hm := fire;
IF home_exe AND (sw AND 16#0008) <> 0 THEN
    home_exe := FALSE;
END_IF;
Busy := home_exe;
Done := NOT home_exe;
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
IF (sw AND 16#0008) <> 0 THEN
    Error := TRUE;
ELSIF (sw AND 16#0004) = 0 THEN
    Error := TRUE;
ELSE
    Error := FALSE;
END_IF;
Busy := (JogForward OR JogBackward) AND NOT Error;
jog_pos := cur_pos;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_STOP
  VAR_INPUT
    fire : BOOL;
    v_act : INT;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
IF fire THEN
    cw := cw OR 16#0002;
ELSE
    cw := cw AND 16#FFFD;
END_IF;
Busy := fire AND (v_act <> 0);
Done := (v_act = 0);
Error := FALSE;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_RESET
  VAR_INPUT
    fire : BOOL;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_rs : BOOL;
    rst_pend : BOOL;
    done_latch : BOOL;
  END_VAR
IF fire AND NOT edge_rs THEN
    cw := cw OR 16#0080;
    rst_pend := TRUE;
    done_latch := FALSE;
END_IF;
edge_rs := fire;
IF rst_pend AND NOT fire THEN
    rst_pend := FALSE;
    done_latch := TRUE;
END_IF;
Done := done_latch;
Busy := rst_pend;
Error := FALSE;
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
Valid := Enable;
Moving := interp_busy OR (v_act <> 0);
StandStill := (NOT Moving) AND ((sw AND 16#0004) <> 0);
Disabled := (sw AND 16#0004) = 0;
ErrorStop := (sw AND 16#0008) <> 0;
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
Valid := Enable;
Position := ActualPosition;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    pl_step : INT := 0;
    edge_dr : BOOL;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    x_vel_bus : REAL := 40.0;
    x_acc_bus : REAL := 80.0;
    x_dec_bus : REAL := 80.0;
    y_vel_bus : REAL := 40.0;
    y_acc_bus : REAL := 80.0;
    y_dec_bus : REAL := 80.0;
    z_vel_bus : REAL := 20.0;
    z_acc_bus : REAL := 60.0;
    z_dec_bus : REAL := 60.0;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_swx : WORD;
    y_swx : WORD;
    z_swx : WORD;
    x_jog_pos : INT;
    y_jog_pos : INT;
    z_jog_pos : INT;
    go_x : MC_MOVEABSOLUTE;
    go_y : MC_MOVEABSOLUTE;
    go_z : MC_MOVEABSOLUTE;
    hm_x : MC_HOME;
    hm_y : MC_HOME;
    hm_z : MC_HOME;
    hlt_x : MC_HALT;
    hlt_y : MC_HALT;
    hlt_z : MC_HALT;
    jg_x : MC_MOVEJOG;
    jg_y : MC_MOVEJOG;
    jg_z : MC_MOVEJOG;
    rds_x : MC_READSTATUS;
    rds_y : MC_READSTATUS;
    rds_z : MC_READSTATUS;
    rap_x : MC_READACTUALPOSITION;
    rap_y : MC_READACTUALPOSITION;
    rap_z : MC_READACTUALPOSITION;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP_Z;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    rs_x : MC_RESET;
    rs_y : MC_RESET;
    rs_z : MC_RESET;
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
    prog_id AT %QW20 : INT := 2;
  END_VAR
prog_id := 2;

pen_down := (z_fb <= 2);

(* C4/C3: power handshake, run gating, quickstop de-energize path *)
pwr_x(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := x_cw, sw := x_swx);
pwr_y(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := y_cw, sw := y_swx);
pwr_z(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := z_cw, sw := z_swx);

rs_x(fire := cmd_reset, cw := x_cw);
rs_y(fire := cmd_reset, cw := y_cw);
rs_z(fire := cmd_reset, cw := z_cw);

all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
fault_any := ((x_swx AND 16#0008) <> 0) OR ((y_swx AND 16#0008) <> 0) OR ((z_swx AND 16#0008) <> 0);

(* target bus default: NC setpoints when idle *)
IF pl_step = 0 THEN
    x_tgt := x_sp;
    y_tgt := y_sp;
    z_tgt := z_sp;
END_IF;

rap_x(Enable := TRUE, ActualPosition := x_fb);
rap_y(Enable := TRUE, ActualPosition := y_fb);
rap_z(Enable := TRUE, ActualPosition := z_fb);

(* C1: manual X/Y positioning rejected while pen down *)
go_x(fire := cmd_go AND NOT pen_down, Position := x_sp, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := x_swx, abort_bus := ix_x.Aborted,
     interp_exe := go_x_exe, dyn_vel := x_vel_bus, dyn_acc := x_acc_bus, dyn_dec := x_dec_bus);
go_y(fire := cmd_go AND NOT pen_down, Position := y_sp, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := y_swx, abort_bus := ix_y.Aborted,
     interp_exe := go_y_exe, dyn_vel := y_vel_bus, dyn_acc := y_acc_bus, dyn_dec := y_dec_bus);
go_z(fire := cmd_go, Position := z_sp, Velocity := 20.0, Acceleration := 60.0, Deceleration := 60.0,
     sw := z_swx, abort_bus := ix_z.Aborted,
     interp_exe := go_z_exe, dyn_vel := z_vel_bus, dyn_acc := z_acc_bus, dyn_dec := z_dec_bus);

hm_x(fire := cmd_home, sw := x_swx, home_exe := hm_x_exe);
hm_y(fire := cmd_home, sw := y_swx, home_exe := hm_y_exe);
hm_z(fire := cmd_home, sw := z_swx, home_exe := hm_z_exe);

(* C1: jog only while pen up *)
jg_x(JogForward := jog_fwd AND NOT pen_down, JogBackward := jog_rev AND NOT pen_down, Velocity := 40.0, cur_pos := x_fb, sw := x_swx, jog_pos := x_jog_pos);
jg_y(JogForward := FALSE, JogBackward := FALSE, Velocity := 40.0, cur_pos := y_fb, sw := y_swx, jog_pos := y_jog_pos);
jg_z(JogForward := FALSE, JogBackward := FALSE, Velocity := 20.0, cur_pos := z_fb, sw := z_swx, jog_pos := z_jog_pos);

(* C3: quickstop = controlled halt *)
hlt_x(fire := quickstop OR (NOT run), ax_busy := ix_x.Busy);
hlt_y(fire := quickstop OR (NOT run), ax_busy := ix_y.Busy);
hlt_z(fire := quickstop OR (NOT run), ax_busy := ix_z.Busy);

(* C6: start acceptance *)
IF cmd_draw AND NOT edge_dr AND (pl_step = 0) AND run AND (NOT quickstop) AND (NOT fault_any) AND (NOT pen_down) THEN
    pl_step := 1;
    plot_done := FALSE;
END_IF;
edge_dr := cmd_draw;

(* sequencer step table (continuous tracking) *)
CASE pl_step OF
    0: pl_step := pl_step;
    1: x_tgt := 20; y_tgt := 20; z_tgt := 10;
    2: x_tgt := 20; y_tgt := 20; z_tgt := 0;
    3: x_tgt := 80; y_tgt := 20; z_tgt := 0;
    4: x_tgt := 80; y_tgt := 80; z_tgt := 0;
    5: x_tgt := 20; y_tgt := 80; z_tgt := 0;
    6: x_tgt := 20; y_tgt := 20; z_tgt := 0;
    7: x_tgt := 20; y_tgt := 20; z_tgt := 10;
    8: x_tgt := 50; y_tgt := 50; z_tgt := 10;
END_CASE;

IF pl_step <> 0 THEN
    go_x_exe := TRUE;
    go_y_exe := TRUE;
    go_z_exe := TRUE;
END_IF;

(* step advance: all axes at position and idle (C2: Z moves only when XY still) *)
IF (pl_step <> 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done AND (x_v = 0) AND (y_v = 0) THEN
    IF pl_step = 8 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE;
        go_y_exe := FALSE;
        go_z_exe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* C3/C4: abort sequence on quickstop / run off / fault *)
IF quickstop OR (NOT run) OR fault_any THEN
    pl_step := 0;
    plot_done := FALSE;
    go_x_exe := FALSE;
    go_y_exe := FALSE;
    go_z_exe := FALSE;
    hm_x_exe := FALSE;
    hm_y_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

ix_x(fire := go_x_exe OR hm_x_exe, pos_target := x_tgt,
     hold_req := hlt_x.Stopping, vel_req := x_vel_bus, acc_req := x_acc_bus, dec_req := x_dec_bus);
ix_y(fire := go_y_exe OR hm_y_exe, pos_target := y_tgt,
     hold_req := hlt_y.Stopping, vel_req := y_vel_bus, acc_req := y_acc_bus, dec_req := y_dec_bus);
ix_z(fire := go_z_exe OR hm_z_exe, pos_target := z_tgt,
     hold_req := hlt_z.Stopping, vel_req := z_vel_bus, acc_req := z_acc_bus, dec_req := z_dec_bus);

dv_x(cw := x_cw, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
dv_y(cw := y_cw, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
dv_z(cw := z_cw, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

x_swx := dv_x.sw;  x_v := dv_x.v_cmd;
y_swx := dv_y.sw;  y_v := dv_y.v_cmd;
z_swx := dv_z.sw;  z_v := dv_z.v_cmd;

(* C7: hard clamp on outputs (VMAX XY=40, Z=20; quickstop limit 20) *)
IF x_v > 40 THEN x_v := 40; END_IF;
IF x_v < -40 THEN x_v := -40; END_IF;
IF y_v > 40 THEN y_v := 40; END_IF;
IF y_v < -40 THEN y_v := -40; END_IF;
IF z_v > 20 THEN z_v := 20; END_IF;
IF z_v < -20 THEN z_v := -20; END_IF;

x_sw := WORD_TO_INT(x_swx);
y_sw := WORD_TO_INT(y_swx);
z_sw := WORD_TO_INT(z_swx);

rds_x(Enable := TRUE, sw := x_swx, interp_busy := ix_x.Busy, v_act := x_v);
rds_y(Enable := TRUE, sw := y_swx, interp_busy := ix_y.Busy, v_act := y_v);
rds_z(Enable := TRUE, sw := z_swx, interp_busy := ix_z.Busy, v_act := z_v);

IF ix_x.Done OR ix_x.Aborted THEN
    go_x_exe := FALSE;
    hm_x_exe := FALSE;
END_IF;
IF ix_y.Done OR ix_y.Aborted THEN
    go_y_exe := FALSE;
    hm_y_exe := FALSE;
END_IF;
IF ix_z.Done OR ix_z.Aborted THEN
    go_z_exe := FALSE;
    hm_z_exe := FALSE;
END_IF;

move_done := (pl_step = 0) AND go_x.Done AND go_y.Done AND go_z.Done;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
