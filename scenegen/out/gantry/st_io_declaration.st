VAR_GLOBAL
    AxisX_cmd AT %QW0 : REAL;  (* 输出：Isaac 客户端 FC03 读，2 寄存器 *)
    AxisY_cmd AT %QW2 : REAL;  (* 输出：Isaac 客户端 FC03 读，2 寄存器 *)
    AxisZ_cmd AT %QW4 : REAL;  (* 输出：Isaac 客户端 FC03 读，2 寄存器 *)
    AxisX_pos AT %IW0 : REAL;  (* 输入：轮询写入，2 寄存器 *)
    AxisY_pos AT %IW2 : REAL;  (* 输入：轮询写入，2 寄存器 *)
    AxisZ_pos AT %IW4 : REAL;  (* 输入：轮询写入，2 寄存器 *)
END_VAR
