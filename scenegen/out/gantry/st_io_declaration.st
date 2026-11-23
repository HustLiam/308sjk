VAR_GLOBAL
    AxisX_cmd AT %QW0 : INT;   (* 输出：工程量定点 int16，换算归桥 *)
    AxisY_cmd AT %QW1 : INT;   (* 输出：工程量定点 int16，换算归桥 *)
    AxisZ_cmd AT %QW2 : INT;   (* 输出：工程量定点 int16，换算归桥 *)
    AxisX_pos AT %QW3 : INT;   (* 输入反馈：工程量定点 int16，换算归桥 *)
    AxisY_pos AT %QW4 : INT;   (* 输入反馈：工程量定点 int16，换算归桥 *)
    AxisZ_pos AT %QW5 : INT;   (* 输入反馈：工程量定点 int16，换算归桥 *)
END_VAR
