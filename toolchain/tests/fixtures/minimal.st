(* L3 端到端最小工程：链路 A 编译 → DLL → ctypes 回环（需本机具备 matiec + gcc） *)
(* 结构遵循契约② §3.3：xml2st 装配 CONFIGURATION 的形态；定位变量统一 %Q 区，     *)
(* 方向由 io_map 声明——本工程 run 为输入（传感注入），echo/prog_id 为输出。        *)

CONFIGURATION Cfg
  RESOURCE StdRes ON PLC
    TASK Main(INTERVAL := t#20ms, PRIORITY := 0);
    PROGRAM inst WITH Main : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION

PROGRAM PLC_PRG
  VAR
    run AT %QX0.0 : BOOL;            (* 输入：传感注入（io_map dir=input） *)
  END_VAR
  VAR_OUTPUT
    echo    AT %QX1.0 : BOOL;        (* 输出：输入直通，验证写读回环 *)
    prog_id AT %QW20 : INT;          (* 契约② v1.1 程序身份 *)
  END_VAR
  echo := run;
  prog_id := 1;
END_PROGRAM
