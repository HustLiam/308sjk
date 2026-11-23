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
    tgtz : REAL := 10.0;
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
    pstep : INT;
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

FUNCTION_BLOCK MC_MOVEABSOLUTE_Z
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
    edge_maz : BOOL;
    edge_abz : BOOL;
  END_VAR
IF fire AND NOT edge_maz THEN
    Error := FALSE; ErrorID := 0; CommandAborted := FALSE;
    IF (sw AND 16#0008) <> 0 THEN
        Error := TRUE; ErrorID := 2;
    ELSIF (sw AND 16#0004) = 0 THEN
        Error := TRUE; ErrorID := 3;
    ELSIF Position < 0 OR Position > 10 THEN
        Error := TRUE; ErrorID := 1;
    ELSE
        interp_exe := TRUE;
        dyn_vel := Velocity; dyn_acc := Acceleration; dyn_dec := Deceleration;
    END_IF;
END_IF;
edge_maz := fire;

IF interp_exe AND abort_bus AND NOT edge_abz THEN
    interp_exe := FALSE;
    CommandAborted := TRUE;
END_IF;
edge_abz := abort_bus;
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
Busy := home_exe;
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
  VAR
    inc_step : INT;
  END_VAR
inc_step := REAL_TO_INT(Velocity * 0.02);
IF inc_step < 1 THEN inc_step := 1; END_IF;

IF (sw AND 16#0008) <> 0 OR (sw AND 16#0004) = 0 THEN
    Error := TRUE;
    Busy := FALSE;
ELSIF JogForward AND NOT JogBackward THEN
    Error := FALSE;
    IF NOT Busy THEN
        jog_pos := cur_pos;
        Busy := TRUE;
    END_IF;
    IF jog_pos < 100 THEN
        jog_pos := jog_pos + inc_step;
        IF jog_pos > 100 THEN jog_pos := 100; END_IF;
    END_IF;
ELSIF JogBackward AND NOT JogForward THEN
    Error := FALSE;
    IF NOT Busy THEN
        jog_pos := cur_pos;
        Busy := TRUE;
    END_IF;
    IF jog_pos > 0 THEN
        jog_pos := jog_pos - inc_step;
        IF jog_pos < 0 THEN jog_pos := 0; END_IF;
    END_IF;
ELSE
    Busy := FALSE;
    Error := FALSE;
END_IF;
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
Moving := Enable AND (interp_busy OR (v_act <> 0));
StandStill := Enable AND NOT Moving;
Disabled := Enable AND ((sw AND 16#0004) = 0);
ErrorStop := Enable AND ((sw AND 16#0008) <> 0);
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
    seg_t : REAL := 0.0;
    fault_lat : BOOL;
    jog_ok : BOOL;
    man_go : BOOL;
    man_hm : BOOL;
    draw_ok : BOOL;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT := 10;
    x_jog_pos : INT;
    x_cw : WORD := 6;
    y_cw : WORD := 6;
    z_cw : WORD := 6;
    x_sww : WORD;
    y_sww : WORD;
    z_sww : WORD;
    go_x_exe : BOOL;
    go_y_exe : BOOL;
    go_z_exe : BOOL;
    hm_x_exe : BOOL;
    hm_y_exe : BOOL;
    hm_z_exe : BOOL;
    halt_all : BOOL;
    x_vel_bus : REAL := 40.0;
    x_acc_bus : REAL := 80.0;
    x_dec_bus : REAL := 80.0;
    y_vel_bus : REAL := 40.0;
    y_acc_bus : REAL := 80.0;
    y_dec_bus : REAL := 80.0;
    z_vel_bus : REAL := 20.0;
    z_acc_bus : REAL := 60.0;
    z_dec_bus : REAL := 60.0;
    pwr_x : MC_POWER;
    pwr_y : MC_POWER;
    pwr_z : MC_POWER;
    go_x : MC_MOVEABSOLUTE;
    go_y : MC_MOVEABSOLUTE;
    go_z : MC_MOVEABSOLUTE_Z;
    hm_x : MC_HOME;
    hm_y : MC_HOME;
    hm_z : MC_HOME;
    jg_x : MC_MOVEJOG;
    hlt_x : MC_HALT;
    hlt_y : MC_HALT;
    hlt_z : MC_HALT;
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
prog_id := 2;

(* ---- pen state: pure physical judgment ---- *)
pen_down := (z_fb <= 2);

(* ---- fault latch & reset (C3/C6) ---- *)
IF quickstop OR ((x_sww AND 16#0008) <> 0) OR ((y_sww AND 16#0008) <> 0) OR ((z_sww AND 16#0008) <> 0) THEN
    fault_lat := TRUE;
END_IF;
IF cmd_reset AND (NOT quickstop) AND run
   AND ((x_sww AND 16#0008) = 0) AND ((y_sww AND 16#0008) = 0) AND ((z_sww AND 16#0008) = 0) THEN
    fault_lat := FALSE;
END_IF;
fault_any := fault_lat;

(* ---- enable chain (C4/AC1) ---- *)
halt_all := quickstop OR fault_lat OR (NOT run);
pwr_x(Enable := run, EnablePositive := TRUE, EnableNegative := TRUE, cw := x_cw, sw := x_sww);
pwr_y(Enable := run, EnablePositive := TRUE, EnableNegative := TRUE, cw := y_cw, sw := y_sww);
pwr_z(Enable := run, EnablePositive := TRUE, EnableNegative := TRUE, cw := z_cw, sw := z_sww);
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;

(* ---- manual gating: pen interlock C1, enable C4 ---- *)
jog_ok := all_oe AND (NOT pen_down) AND (NOT halt_all);
man_go := cmd_go AND all_oe AND (NOT pen_down) AND (NOT halt_all);
man_hm := cmd_home AND all_oe AND (NOT halt_all);

(* ---- target bus defaults ---- *)
x_tgt := x_sp;
y_tgt := y_sp;

(* ---- jog (X only) ---- *)
jg_x(JogForward := jog_fwd AND jog_ok, JogBackward := jog_rev AND jog_ok,
     Velocity := 40.0, cur_pos := x_fb, sw := x_sww, jog_pos := x_jog_pos);
IF (jog_fwd OR jog_rev) AND jog_ok AND jg_x.Busy THEN
    x_tgt := x_jog_pos;
END_IF;

(* ---- draw sequencer (C2/C5, trajectory step table) ---- *)
draw_ok := cmd_draw AND run AND all_oe AND (NOT pen_down) AND (NOT fault_lat) AND (NOT quickstop);
IF draw_ok AND (pl_step = 0) THEN
    go_z_exe := TRUE; go_x_exe := TRUE; go_y_exe := TRUE;
    plot_done := FALSE;
    pl_step := 1;
    seg_t := 0.0;
END_IF;

CASE pl_step OF
    1: x_tgt := 20; y_tgt := 20; z_tgt := 10;   (* pen_up_move (20,20,10) *)
    2: x_tgt := 20; y_tgt := 20; z_tgt := 0;    (* pen_down_move (20,20,0) *)
    3: x_tgt := 80; y_tgt := 20; z_tgt := 0;    (* move (80,20,0) *)
    4: x_tgt := 80; y_tgt := 80; z_tgt := 0;    (* move (80,80,0) *)
    5: x_tgt := 20; y_tgt := 80; z_tgt := 0;    (* move (20,80,0) *)
    6: x_tgt := 20; y_tgt := 20; z_tgt := 0;    (* move (20,20,0) close *)
    7: x_tgt := 20; y_tgt := 20; z_tgt := 10;   (* pen_up_move (20,20,10) *)
    8: x_tgt := 50; y_tgt := 50; z_tgt := 10;   (* pen_up_move (50,50,10) *)
END_CASE;

(* manual Z setpoint when idle (no sequencer run) *)
IF pl_step = 0 THEN
    z_tgt := z_sp;
END_IF;

(* segment timeout watchdog (C6) *)
IF pl_step = 0 THEN
    seg_t := 0.0;
ELSIF ix_x.Busy OR ix_y.Busy OR ix_z.Busy THEN
    seg_t := seg_t + 0.02;
    IF seg_t > 6.0 THEN
        fault_lat := TRUE;
    END_IF;
ELSE
    seg_t := 0.0;
END_IF;

(* step advance: all axes in-position (C2 in-pos sequence) *)
IF (pl_step <> 0) AND (NOT ix_x.Busy) AND (NOT ix_y.Busy) AND (NOT ix_z.Busy)
   AND ix_x.Done AND ix_y.Done AND ix_z.Done AND (NOT fault_lat) AND (NOT quickstop) THEN
    IF pl_step = 8 THEN
        plot_done := TRUE;
        pl_step := 0;
        go_x_exe := FALSE; go_y_exe := FALSE; go_z_exe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* abort: quickstop / fault / run-off -> reset sequencer, kill plot_done (C3) *)
IF halt_all OR fault_lat THEN
    IF pl_step <> 0 THEN
        pl_step := 0;
        go_x_exe := FALSE; go_y_exe := FALSE; go_z_exe := FALSE;
    END_IF;
    plot_done := FALSE;
END_IF;

(* ---- MC command layer ---- *)
rap_x(Enable := TRUE, ActualPosition := x_fb);
rap_y(Enable := TRUE, ActualPosition := y_fb);
rap_z(Enable := TRUE, ActualPosition := z_fb);

go_x(fire := man_go AND (NOT go_x_exe), Position := x_tgt, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := x_sww, abort_bus := (ix_x.Aborted OR halt_all),
     interp_exe := go_x_exe, dyn_vel := x_vel_bus, dyn_acc := x_acc_bus, dyn_dec := x_dec_bus);
go_y(fire := man_go AND (NOT go_y_exe), Position := y_tgt, Velocity := 40.0, Acceleration := 80.0, Deceleration := 80.0,
     sw := y_sww, abort_bus := (ix_y.Aborted OR halt_all),
     interp_exe := go_y_exe, dyn_vel := y_vel_bus, dyn_acc := y_acc_bus, dyn_dec := y_dec_bus);
go_z(fire := man_hm AND (NOT go_z_exe), Position := 10, Velocity := 20.0, Acceleration := 60.0, Deceleration := 60.0,
     sw := z_sww, abort_bus := (ix_z.Aborted OR halt_all),
     interp_exe := go_z_exe, dyn_vel := z_vel_bus, dyn_acc := z_acc_bus, dyn_dec := z_dec_bus);

hm_x(fire := man_hm, sw := x_sww, home_exe := hm_x_exe);
hm_y(fire := man_hm, sw := y_sww, home_exe := hm_y_exe);
hm_z(fire := man_hm, sw := z_sww, home_exe := hm_z_exe);
IF hm_z.Busy THEN z_tgt := 10; END_IF;

hlt_x(fire := halt_all, ax_busy := ix_x.Busy);
hlt_y(fire := halt_all, ax_busy := ix_y.Busy);
hlt_z(fire := halt_all, ax_busy := ix_z.Busy);

rds_x(Enable := TRUE, sw := x_sww, interp_busy := ix_x.Busy, v_act := x_v);
rds_y(Enable := TRUE, sw := y_sww, interp_busy := ix_y.Busy, v_act := y_v);
rds_z(Enable := TRUE, sw := z_sww, interp_busy := ix_z.Busy, v_act := z_v);

(* ---- shared interpolation cores (OR trigger lines) ---- *)
ix_x(fire := (go_x_exe OR hm_x_exe), pos_target := x_tgt,
     hold_req := hlt_x.Stopping, vel_req := x_vel_bus, acc_req := x_acc_bus, dec_req := x_dec_bus);
ix_y(fire := (go_y_exe OR hm_y_exe), pos_target := y_tgt,
     hold_req := hlt_y.Stopping, vel_req := y_vel_bus, acc_req := y_acc_bus, dec_req := y_dec_bus);
ix_z(fire := (go_z_exe OR hm_z_exe), pos_target := z_tgt,
     hold_req := hlt_z.Stopping, vel_req := z_vel_bus, acc_req := z_acc_bus, dec_req := z_dec_bus);

dv_x(cw := x_cw, Setpoint := ix_x.Setpoint, pos_fb := x_fb);
dv_y(cw := y_cw, Setpoint := ix_y.Setpoint, pos_fb := y_fb);
dv_z(cw := z_cw, Setpoint := ix_z.Setpoint, pos_fb := z_fb);

x_sw := WORD_TO_INT(dv_x.sw);
y_sw := WORD_TO_INT(dv_y.sw);
z_sw := WORD_TO_INT(dv_z.sw);
x_sww := dv_x.sw;  y_sww := dv_y.sw;  z_sww := dv_z.sw;
x_v := dv_x.v_cmd;  y_v := dv_y.v_cmd;  z_v := dv_z.v_cmd;

IF ix_x.Done OR ix_x.Aborted THEN go_x_exe := FALSE; hm_x_exe := FALSE; END_IF;
IF ix_y.Done OR ix_y.Aborted THEN go_y_exe := FALSE; hm_y_exe := FALSE; END_IF;
IF ix_z.Done OR ix_z.Aborted THEN go_z_exe := FALSE; hm_z_exe := FALSE; END_IF;

(* ---- status lamps ---- *)
move_done := (NOT rds_x.Moving) AND (NOT rds_y.Moving) AND (NOT rds_z.Moving)
             AND (pl_step = 0) AND (NOT fault_any) AND (NOT quickstop) AND all_oe;
any_moving := rds_x.Moving OR rds_y.Moving OR rds_z.Moving;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
