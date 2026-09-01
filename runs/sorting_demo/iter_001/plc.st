FUNCTION_BLOCK EdgeFB
  VAR_INPUT
    clk : BOOL;
  END_VAR
  VAR_OUTPUT
    rising : BOOL;
  END_VAR
  VAR
    last : BOOL;
  END_VAR
rising := clk AND NOT last;
last := clk;
END_FUNCTION_BLOCK

PROGRAM PLC_PRG
  VAR
    edge_total : EdgeFB;
    edge_reject : EdgeFB;
    pusher_delay : TON;
    pusher_hold : TON;
    reject_pending : BOOL;
    pusher_on : BOOL;
  END_VAR
  VAR
    start_btn AT %QX0.0 : BOOL;
    stop_btn AT %QX0.1 : BOOL;
    e_stop AT %QX0.2 : BOOL;
    photo_inlet AT %QX0.3 : BOOL;
    photo_reject AT %QX0.4 : BOOL;
  END_VAR
  VAR
    motor AT %QX1.0 : BOOL;
    pusher AT %QX1.1 : BOOL;
    alarm AT %QX1.2 : BOOL;
    total_cnt AT %QW10 : INT := 0;
    reject_cnt AT %QW11 : INT := 0;
  END_VAR
(* ---- start/stop latch with e-stop ---- *)
motor := (start_btn OR motor) AND NOT stop_btn AND NOT e_stop;
alarm := e_stop;

(* ---- invoke every FB once per scan ---- *)
edge_total(clk := photo_inlet);
edge_reject(clk := photo_reject);
pusher_delay(IN := reject_pending, PT := T#300MS);
pusher_hold(IN := pusher_on, PT := T#500MS);

(* ---- part counting on rising edge, only while conveyor runs ---- *)
IF motor AND edge_total.rising THEN
    total_cnt := total_cnt + 1;
END_IF;

(* ---- reject sequence: detect -> wait 300ms -> extend 500ms -> retract ---- *)
IF motor AND edge_reject.rising THEN
    reject_pending := TRUE;
    reject_cnt := reject_cnt + 1;
END_IF;
IF reject_pending AND pusher_delay.Q THEN
    pusher_on := TRUE;
END_IF;
IF pusher_on AND pusher_hold.Q THEN
    pusher_on := FALSE;
    reject_pending := FALSE;
END_IF;
pusher := pusher_on;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
