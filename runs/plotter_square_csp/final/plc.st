PROGRAM PLC_PRG
  VAR
    run AT %QX0.0 : BOOL;
    cmd_reset AT %QX0.7 : BOOL;
    cmd_draw AT %QX2.0 : BOOL;
  END_VAR
  VAR
    any_moving AT %QX1.2 : BOOL;
    pen_down AT %QX2.1 : BOOL;
    plot_done AT %QX2.2 : BOOL;
  END_VAR
  VAR
    x_cmd AT %QW3 : INT;
    y_cmd AT %QW4 : INT;
    z_cmd AT %QW5 : INT;
    prog_id AT %QW20 : INT;
  END_VAR
  VAR
    x_fb AT %QW0 : INT;
    y_fb AT %QW1 : INT;
    z_fb AT %QW2 : INT;
  END_VAR
  VAR
    state : INT := 0;
    x_tgt : INT := 50;
    y_tgt : INT := 50;
    z_tgt : INT := 10;
  END_VAR
prog_id := 4;

(* CSP 形态：目标位置上总线（cmd %QW3-5，反馈 %QW0-2）；驱动器（仿真侧）轴速限速跟随并闭位置环 *)
x_cmd := x_tgt;
y_cmd := y_tgt;
z_cmd := z_tgt;

(* 状态输出 *)
pen_down := z_fb <= 0;
any_moving := (x_fb > x_cmd + 1) OR (x_fb < x_cmd - 1)
           OR (y_fb > y_cmd + 1) OR (y_fb < y_cmd - 1)
           OR (z_fb > z_cmd + 1) OR (z_fb < z_cmd - 1);

IF cmd_reset THEN
    state := 0;
    plot_done := FALSE;
    x_tgt := 50; y_tgt := 50; z_tgt := 1;
ELSIF state = 0 THEN
    x_tgt := 50; y_tgt := 50; z_tgt := 1;      (* 待机：抬笔回中心 *)
    IF cmd_draw AND run THEN
        state := 1;
    END_IF;
ELSE
    CASE state OF
        1:                                      (* 抬笔至安全高度 *)
            z_tgt := 1;
            IF z_fb >= 1 THEN
                state := 2;
            END_IF;
        2:                                      (* 抬笔态定位起点 (20,20) *)
            x_tgt := 20; y_tgt := 20; z_tgt := 1;
            IF (x_fb <= 21) AND (x_fb >= 19) AND (y_fb <= 21) AND (y_fb >= 19) THEN
                state := 3;
            END_IF;
        3:                                      (* 落笔（接触建立，不判位置相等） *)
            z_tgt := 0;
            IF z_fb <= 0 THEN
                state := 4;
            END_IF;
        4:                                      (* 边 1：(20,20)->(80,20) *)
            x_tgt := 80;
            IF (x_fb <= 81) AND (x_fb >= 79) THEN
                state := 5;
            END_IF;
        5:                                      (* 边 2：(80,20)->(80,80) *)
            y_tgt := 80;
            IF (y_fb <= 81) AND (y_fb >= 79) THEN
                state := 6;
            END_IF;
        6:                                      (* 边 3：(80,80)->(20,80) *)
            x_tgt := 20;
            IF (x_fb <= 21) AND (x_fb >= 19) THEN
                state := 7;
            END_IF;
        7:                                      (* 边 4：(20,80)->(20,20) 闭合 *)
            y_tgt := 20;
            IF (y_fb <= 21) AND (y_fb >= 19) THEN
                state := 8;
            END_IF;
        8:                                      (* 抬笔 *)
            z_tgt := 1;
            IF z_fb >= 1 THEN
                state := 9;
            END_IF;
        9:                                      (* 回中心并完成 *)
            x_tgt := 50; y_tgt := 50;
            IF (x_fb <= 51) AND (x_fb >= 49) AND (y_fb <= 51) AND (y_fb >= 49) THEN
                state := 10;
                plot_done := TRUE;
            END_IF;
    ELSE
        state := 0;                              (* 意外状态：回待机 *)
    END_CASE;
END_IF;
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
