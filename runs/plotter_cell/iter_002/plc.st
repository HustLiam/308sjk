FUNCTION_BLOCK INTERP
  VAR_INPUT
    fire : BOOL;
    pos_target : INT;
    vel_req : REAL;
    acc_req : REAL;
    dec_req : REAL;
    poswin : REAL;
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
    IF pos_target > 100 OR pos_target < 0 THEN
        (* target out of range: stay *)
        Done := TRUE;
    ELSE
        tgt := INT_TO_REAL(pos_target);
        Busy := TRUE;
        Done := FALSE;
    END_IF;
ELSIF fire AND NOT Busy AND (ABS(INT_TO_REAL(pos_target) - tgt) > 0.499) THEN
    (* continuous tracking: new target starts new trajectory while fire held *)
    tgt := INT_TO_REAL(pos_target);
    Busy := TRUE;
    Done := FALSE;
END_IF;
edge_ir := fire;

IF Busy THEN
    dist := tgt - pos;
    stop_d := (vel * vel) / (2.0 * dec_req);
    IF ABS(dist) <= stop_d THEN
        IF dist >= 0.0 THEN vel := MAX(0.0, vel - dec_req * SCAN_T);
        ELSE vel := MIN(0.0, vel + dec_req * SCAN_T);
        END_IF;
        IF vel = 0.0 AND ABS(dist) <= poswin THEN
            pos := tgt;
            Busy := FALSE;
            Done := TRUE;
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
    EnablePositive : BOOL;
    EnableNegative : BOOL;
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
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

FUNCTION_BLOCK MC_RESET
  VAR_INPUT
    fire : BOOL;
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    cw : WORD;
  END_VAR
  VAR_OUTPUT
    Busy : BOOL;
    Done : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_rs : BOOL;
    busy_rs : BOOL;
  END_VAR
IF fire AND NOT edge_rs THEN
    busy_rs := TRUE;
END_IF;
edge_rs := fire;
IF busy_rs THEN
    IF (sw AND 16#0008) <> 0 THEN
        cw := cw OR 16#0080;
    ELSE
        cw := cw AND 16#FF7F;
        busy_rs := FALSE;
    END_IF;
END_IF;
Busy := busy_rs;
Error := (sw AND 16#0008) <> 0 AND NOT busy_rs;
Done := NOT busy_rs AND (sw AND 16#0008) = 0 AND NOT fire;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_STOP
  VAR_INPUT
    fire : BOOL;
    v_act : INT;
    sw : WORD;
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
    cw := (cw AND 16#00F0) OR 16#0002;
    Done := FALSE;
ELSE
    Done := TRUE;
END_IF;
Busy := fire AND v_act <> 0;
Error := (sw AND 16#0008) <> 0;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVEABSOLUTE
  VAR_INPUT
    fire : BOOL;
    Position : INT;
    sw : WORD;
  END_VAR
  VAR_IN_OUT
    interp_exe : BOOL;
  END_VAR
  VAR_OUTPUT
    Done : BOOL;
    Busy : BOOL;
    Error : BOOL;
    ErrorID : INT;
  END_VAR
  VAR
    edge_ma : BOOL;
  END_VAR
IF fire AND NOT edge_ma THEN
    Error := FALSE;
    ErrorID := 0;
    IF (sw AND 16#0008) <> 0 THEN
        Error := TRUE;
        ErrorID := 2;
    ELSIF (sw AND 16#0004) = 0 THEN
        Error := TRUE;
        ErrorID := 3;
    ELSIF Position < 0 OR Position > 100 THEN
        Error := TRUE;
        ErrorID := 1;
    ELSE
        interp_exe := TRUE;
    END_IF;
END_IF;
edge_ma := fire;
Busy := interp_exe;
Done := NOT interp_exe AND NOT Error;
END_FUNCTION_BLOCK

FUNCTION_BLOCK MC_MOVEJOG
  VAR_INPUT
    JogForward : BOOL;
    JogBackward : BOOL;
    Velocity : REAL;
    cur_pos : INT;
    sw : WORD;
  END_VAR
  VAR_OUTPUT
    jog_pos : INT;
    Busy : BOOL;
    Error : BOOL;
  END_VAR
  VAR
    edge_jg : BOOL;
    jog_step : INT;
  END_VAR
IF (JogForward OR JogBackward) AND NOT edge_jg AND NOT Busy THEN
    jog_pos := cur_pos;
END_IF;
edge_jg := JogForward OR JogBackward;
jog_step := REAL_TO_INT(Velocity * 0.02);
IF jog_step < 1 THEN jog_step := 1; END_IF;
IF JogForward AND jog_pos < 97 THEN
    jog_pos := jog_pos + jog_step;
ELSIF JogBackward AND jog_pos > 3 THEN
    jog_pos := jog_pos - jog_step;
END_IF;
Busy := JogForward OR JogBackward;
Error := (sw AND 16#0008) <> 0;
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
    jg_x : MC_MOVEJOG;
    ix_x : INTERP;
    ix_y : INTERP;
    ix_z : INTERP;
    dv_x : DRIVE402;
    dv_y : DRIVE402;
    dv_z : DRIVE402;
    tn_settle : TON;
    x_cw : WORD;
    y_cw : WORD;
    z_cw : WORD;
    x_swi : WORD;
    y_swi : WORD;
    z_swi : WORD;
    x_tgt : INT;
    y_tgt : INT;
    z_tgt : INT;
    x_interp : INT;
    y_interp : INT;
    z_interp : INT;
    x_jog_pos : INT;
    pl_step : INT := 0;
    pl_tx : INT := 50;
    pl_ty : INT := 50;
    pl_tz : INT := 10;
    dxe : BOOL;
    hm_z_exe : BOOL;
    m_x_exe : BOOL;
    m_y_exe : BOOL;
    m_z_exe : BOOL;
    m_was : BOOL;
    mdone : BOOL;
    man_ok : BOOL;
    all_done : BOOL;
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

(* ---- sample drive status words ---- *)
x_swi := dv_x.sw;
y_swi := dv_y.sw;
z_swi := dv_z.sw;

(* ---- pen state: pure physical judgment ---- *)
pen_down := z_fb <= 2;
man_ok := NOT pen_down;

(* ---- fault aggregation (sw bit3) ---- *)
fault_any := ((x_swi AND 16#0008) OR (y_swi AND 16#0008) OR (z_swi AND 16#0008)) <> 0;

(* ---- abort: quickstop / power off / fault (level abort, no extra latch) ---- *)
IF quickstop OR NOT run OR fault_any THEN
    pl_step := 0;
    dxe := FALSE;
    plot_done := FALSE;
END_IF;

(* ---- manual Z home (pen up reference) ---- *)
hm_z_exe := cmd_home AND (pl_step = 0);

(* ---- manual go: X/Y gated by pen interlock, Z free ---- *)
IF cmd_go AND man_ok AND (pl_step = 0) THEN
    m_x_exe := TRUE;
    m_y_exe := TRUE;
    mdone := FALSE;
    m_was := FALSE;
END_IF;
IF cmd_go AND (pl_step = 0) THEN
    m_z_exe := TRUE;
END_IF;
IF pen_down THEN
    mdone := FALSE;
END_IF;

(* ---- draw sequence start (level gate) ---- *)
IF (pl_step = 0) AND cmd_draw AND run AND all_oe AND NOT fault_any AND NOT quickstop THEN
    dxe := TRUE;
    pl_step := 1;
    plot_done := FALSE;
END_IF;

(* ---- sequence step table ---- *)
CASE pl_step OF
    0: pl_tx := pl_tx; pl_ty := pl_ty; pl_tz := pl_tz;
    1: pl_tx := 20; pl_ty := 20; pl_tz := 10;
    2: pl_tx := 20; pl_ty := 20; pl_tz := 0;
    3: pl_tx := 80; pl_ty := 20; pl_tz := 0;
    4: pl_tx := 80; pl_ty := 80; pl_tz := 0;
    5: pl_tx := 20; pl_ty := 80; pl_tz := 0;
    6: pl_tx := 20; pl_ty := 20; pl_tz := 0;
    7: pl_tx := 20; pl_ty := 20; pl_tz := 10;
    8: pl_tx := 50; pl_ty := 50; pl_tz := 10;
END_CASE;

(* ---- settle confirm (all interp idle + done, hold 200ms) then advance ---- *)
all_done := ix_x.Done AND ix_y.Done AND ix_z.Done AND NOT ix_x.Busy AND NOT ix_y.Busy AND NOT ix_z.Busy;
tn_settle(IN := (pl_step <> 0) AND all_done, PT := T#200MS);
IF tn_settle.Q THEN
    IF pl_step = 8 THEN
        plot_done := TRUE;
        pl_step := 0;
        dxe := FALSE;
    ELSE
        pl_step := pl_step + 1;
    END_IF;
END_IF;

(* ---- target bus ---- *)
x_tgt := x_sp;
y_tgt := y_sp;
z_tgt := z_sp;
IF hm_z_exe THEN
    z_tgt := 10;
END_IF;
IF pl_step <> 0 THEN
    x_tgt := pl_tx;
    y_tgt := pl_ty;
    z_tgt := pl_tz;
END_IF;

(* ---- power / reset / quickstop ---- *)
pwr_x(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := x_cw, sw := x_swi);
pwr_y(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := y_cw, sw := y_swi);
pwr_z(Enable := run AND NOT quickstop, EnablePositive := TRUE, EnableNegative := TRUE, cw := z_cw, sw := z_swi);
rs_x(fire := cmd_reset, cw := x_cw, sw := x_swi);
st_x(fire := quickstop, v_act := x_v, cw := x_cw, sw := x_swi);
st_y(fire := quickstop, v_act := y_v, cw := y_cw, sw := y_swi);
st_z(fire := quickstop, v_act := z_v, cw := z_cw, sw := z_swi);

(* ---- MC command layer ---- *)
go_x(fire := cmd_go AND man_ok, Position := x_sp, sw := x_swi, interp_exe := m_x_exe);
go_y(fire := cmd_go AND man_ok, Position := y_sp, sw := y_swi, interp_exe := m_y_exe);
go_z(fire := cmd_go, Position := z_tgt, sw := z_swi, interp_exe := m_z_exe);
jg_x(JogForward := jog_fwd AND man_ok AND (pl_step = 0), JogBackward := jog_rev AND man_ok AND (pl_step = 0), Velocity := 40.0, cur_pos := x_fb, sw := x_swi, jog_pos := x_jog_pos);

(* ---- interp engines (level-held fire lines) ---- *)
ix_x(fire := m_x_exe OR dxe, pos_target := x_tgt, vel_req := 40.0, acc_req := 80.0, dec_req := 80.0, poswin := 2.0);
ix_y(fire := m_y_exe OR dxe, pos_target := y_tgt, vel_req := 40.0, acc_req := 80.0, dec_req := 80.0, poswin := 2.0);
ix_z(fire := m_z_exe OR dxe OR hm_z_exe, pos_target := z_tgt, vel_req := 20.0, acc_req := 60.0, dec_req := 60.0, poswin := 1.0);

(* ---- clear manual trigger lines when motion finished ---- *)
IF ix_x.Done AND m_x_exe THEN m_x_exe := FALSE; END_IF;
IF ix_y.Done AND m_y_exe THEN m_y_exe := FALSE; END_IF;
IF ix_z.Done AND m_z_exe THEN m_z_exe := FALSE; END_IF;
IF ix_z.Done AND hm_z_exe THEN hm_z_exe := FALSE; END_IF;

(* ---- manual move done latch ---- *)
IF m_x_exe OR m_y_exe OR m_z_exe THEN
    m_was := TRUE;
END_IF;
IF m_was AND ix_x.Done AND ix_y.Done AND ix_z.Done AND NOT m_x_exe AND NOT m_y_exe AND NOT m_z_exe THEN
    mdone := TRUE;
    m_was := FALSE;
END_IF;

(* ---- setpoint routing: jog overrides X ---- *)
IF jg_x.Busy THEN
    x_interp := x_jog_pos;
ELSE
    x_interp := ix_x.Setpoint;
END_IF;
y_interp := ix_y.Setpoint;
z_interp := ix_z.Setpoint;

(* ---- drives ---- *)
dv_x(cw := x_cw, Setpoint := x_interp, pos_fb := x_fb);
x_v := dv_x.v_cmd;
dv_y(cw := y_cw, Setpoint := y_interp, pos_fb := y_fb);
y_v := dv_y.v_cmd;
dv_z(cw := z_cw, Setpoint := z_interp, pos_fb := z_fb);
z_v := dv_z.v_cmd;

(* ---- status outputs ---- *)
all_oe := pwr_x.Status AND pwr_y.Status AND pwr_z.Status;
any_moving := (x_v <> 0) OR (y_v <> 0) OR (z_v <> 0);
move_done := mdone AND man_ok AND (pl_step = 0);

(* ---- enable interlock: run off = all velocity commands zero ---- *)
IF NOT run THEN
    x_v := 0;
    y_v := 0;
    z_v := 0;
END_IF;

x_sw := WORD_TO_INT(x_swi);
y_sw := WORD_TO_INT(y_swi);
z_sw := WORD_TO_INT(z_swi);
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
