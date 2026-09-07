VAR_GLOBAL
    PE1_detected AT %IW0 : WORD;  (* 输入：轮询写入，0/1 *)
    Cyl1_extend AT %QX0.0 : BOOL;   (* 输出：Isaac 客户端 FC01 读 *)
    Cyl1_pos AT %IW1 : REAL;  (* 输入：轮询写入，2 寄存器 *)
    Belt1_run AT %QX0.1 : BOOL;   (* 输出：Isaac 客户端 FC01 读 *)
END_VAR
