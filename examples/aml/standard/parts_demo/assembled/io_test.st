PROGRAM IO_TEST
  VAR
    rb_enable AT %QW120 : INT := 0;
    rb_j1_target AT %QW100 : INT := 0;
    rb_j2_target AT %QW101 : INT := 0;
    rb_j3_target AT %QW102 : INT := 0;
    rb_j4_target AT %QW103 : INT := 0;
    rb_j5_target AT %QW104 : INT := 0;
    rb_j6_target AT %QW105 : INT := 0;
    rb_feedback_seq AT %QW121 : INT := 0;
    rb_j1_actual AT %QW110 : INT := 0;
    rb_j2_actual AT %QW111 : INT := 0;
    rb_j3_actual AT %QW112 : INT := 0;
    rb_j4_actual AT %QW113 : INT := 0;
    rb_j5_actual AT %QW114 : INT := 0;
    rb_j6_actual AT %QW115 : INT := 0;
    prog_id AT %QW20 : INT := 260;
  END_VAR
(* 固定生成：io_test —— 接收目标→输出指令→读取反馈→确认扫描 *)
prog_id := 260; (* 程序身份：io_test v1 *)

IF rb_enable THEN (* 使能门控：失能时指令清零 *)
    (* rb_j1_target 由测试驱动经 Modbus 写入，透传至适配器 *)
    (* rb_j2_target 由测试驱动经 Modbus 写入，透传至适配器 *)
    (* rb_j3_target 由测试驱动经 Modbus 写入，透传至适配器 *)
    (* rb_j4_target 由测试驱动经 Modbus 写入，透传至适配器 *)
    (* rb_j5_target 由测试驱动经 Modbus 写入，透传至适配器 *)
    (* rb_j6_target 由测试驱动经 Modbus 写入，透传至适配器 *)
ELSE
    rb_j1_target := 0;
    rb_j2_target := 0;
    rb_j3_target := 0;
    rb_j4_target := 0;
    rb_j5_target := 0;
    rb_j6_target := 0;
END_IF

(* 反馈通道由适配器回写：actual_* / feedback_seq，此处仅声明 *)
END_PROGRAM

CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
