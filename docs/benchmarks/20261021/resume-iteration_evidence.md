# 每轮问题与版本选择

本报告只读取已完成任务的原始判定与progress记录，不调用模型，不修改程序或验收。

## heptagon_r15 第1轮

耗时147.812秒；实际通过11/11项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## heptagon_r30 第1轮

耗时135.718秒；实际通过10/10项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## circle_r15 第1轮

耗时119.297秒；实际通过0/9项；版本选择promote。

matiec 编译失败:
Optimizing ST program...
ST optimizer bypassed: preserve source semantics (agent-guard-v1)
Generating C files...
./st_files/497492.st:129: error: invalid variable before ':=' in ST assignment statement.

1 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!

版本选择理由：建立首个通过静态检查的修复基底

## circle_r15 第2轮

耗时70.5秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：通过了更后面的检查阶段
新增实际验证通过的判据
PATH_CIRCLE_R15 物理路点进度增加

## circle_r30 第1轮

耗时124.89秒；实际通过9/10项；版本选择promote。

AC1: cmd_draw↑@0.516s → any_moving↑@21.516s = 21.0s <= 2.0s

版本选择理由：建立首个通过静态检查的修复基底

## circle_r30 第2轮

耗时56.61秒；实际通过9/10项；版本选择retain。

AC1: cmd_draw↑@0.516s → any_moving↑@21.516s = 21.0s <= 2.0s

版本选择理由：证据未显示推进，保留原最佳版

## circle_r30 第3轮

耗时99.593秒；实际通过10/10项；版本选择promote。

本轮无失败条目。

版本选择理由：通过了更后面的检查阶段
新增实际验证通过的判据

## rectangle_r15 第1轮

耗时94.016秒；实际通过0/11项；版本选择promote。

matiec 编译失败:
Optimizing ST program...
ST optimizer bypassed: preserve source semantics (agent-guard-v1)
Generating C files...
./st_files/853141.st:97: error: invalid statement in case element of ST 'CASE' statement.
./st_files/853141.st:169: error: invalid statement in ST statement.
./st_files/853141.st:170: error: invalid statement in ST statement.
./st_files/853141.st:172: error: invalid variable before ':=' in ST assignment statement.
./st_files/853141.st:188: error: invalid variable before ':=' in ST assignment statement.
./st_files/853141.st:199: error: invalid variable before ':=' in ST assignment statement.
./st_files/853141.st:202: error: invalid statement in ST statement.
./st_files/853141.st:96: error: invalid case element(s) defined after 'OF' in ST 'CASE' statement.

8 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!

版本选择理由：建立首个通过静态检查的修复基底

## rectangle_r15 第2轮

耗时62.156秒；实际通过8/11项；版本选择promote。

AC6: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [50, 0, 0]（tol±2.0）
AC7: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 50, 0]（tol±2.0）
AC8: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 0, 10]（tol±1.0）

版本选择理由：通过了更后面的检查阶段
新增实际验证通过的判据
RECT 物理路点进度增加

## rectangle_r15 第3轮

耗时95.406秒；实际通过8/11项；版本选择retain。

AC6: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [50, 0, 0]（tol±2.0）
AC7: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 50, 0]（tol±2.0）
AC8: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 0, 10]（tol±1.0）

版本选择理由：证据未显示推进，保留原最佳版

## rectangle_r15 第4轮

耗时94.11秒；实际通过7/11项；版本选择reject。

AC6: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [50, 0, 0]（tol±2.0）
AC7: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 50, 0]（tol±2.0）
AC8: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 0, 10]（tol±1.0）
AC9: 行程外样本 204，z_cmd:204

版本选择理由：丢失已实际验证的判据：['AC9']

## rectangle_r30 第1轮

耗时118.031秒；实际通过8/8项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## irregular_r15 第1轮

耗时117.281秒；实际通过10/10项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## irregular_r30 第1轮

耗时138.469秒；实际通过11/11项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## concave_r15 第1轮

耗时125.39秒；实际通过8/8项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## concave_r30 第1轮

耗时195.594秒；实际通过0/9项；版本选择promote。

matiec 编译失败:
Optimizing ST program...
ST optimizer bypassed: preserve source semantics (agent-guard-v1)
Generating C files...
./st_files/704991.st:68: error: invalid statement in case element of ST 'CASE' statement.
./st_files/704991.st:66: error: invalid case element(s) defined after 'OF' in ST 'CASE' statement.
./st_files/704991.st:113: error: invalid variable before ':=' in ST assignment statement.
./st_files/704991.st:124: error: invalid variable before ':=' in ST assignment statement.
./st_files/704991.st:126: error: invalid statement in ST statement.
./st_files/704991.st:127: error: invalid statement in ST statement.
./st_files/704991.st:128: error: invalid variable before ':=' in ST assignment statement.
./st_files/704991.st:140: error: invalid variable before ':=' in ST assignment statement.
./st_files/704991.st:146: error: invalid variable before ':=' in ST assignment statement.
./st_files/704991.st:153: error: invalid variable before ':=' in ST assignment statement.
./st_files/704991.st:157: error: invalid variable before ':=' in ST assignment statement.
./st_files/704991.st:158: error: invalid statement in ST statement.
./st_files/704991.st:159: error: invalid statement in ST statement.

13 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!

版本选择理由：建立首个通过静态检查的修复基底

## concave_r30 第2轮

耗时79.906秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：通过了更后面的检查阶段
新增实际验证通过的判据
PATH 物理路点进度增加

## ellipse_r15 第1轮

耗时134.391秒；实际通过0/9项；版本选择promote。

matiec 编译失败:
./st_files/940954.st:55-17..55-22: error: Array access out of bounds (using constant value of 18, should be <= 0).
./st_files/940954.st:56-3..56-8: error: Array access out of bounds (using constant value of 19, should be <= 0).
./st_files/940954.st:56-17..56-22: error: Array access out of bounds (using constant value of 19, should be <= 0).
./st_files/940954.st:57-3..57-8: error: Array access out of bounds (using constant value of 20, should be <= 0).
./st_files/940954.st:57-17..57-22: error: Array access out of bounds (using constant value of 20, should be <= 0).
./st_files/940954.st:58-3..58-8: error: Array access out of bounds (using constant value of 21, should be <= 0).
./st_files/940954.st:58-17..58-22: error: Array access out of bounds (using constant value of 21, should be <= 0).
./st_files/940954.st:59-3..59-8: error: Array access out of bounds (using constant value of 22, should be <= 0).
./st_files/940954.st:59-17..59-22: error: Array access out of bounds (using constant value of 22, should be <= 0).
./st_files/940954.st:60-3..60-8: error: Array access out of bounds (using constant value of 23, should be <= 0).
./st_files/940954.st:60-17..60-22: error: Array access out of bounds (using constant value of 23, should be <= 0).
./st_files/940954.st:61-3..61-8: error: Array access out of bounds (using constant value of 24, should be <= 0).
./st_files/940954.st:61-17..61-22: error: Array access out of bounds (using constant value of 24, should be <= 0).
./st_files/940954.st:62-3..62-8: error: Array access out of bounds (using constant value of 25, should be <= 0).
./st_files/940954.st:62-17..62-22: error: Array access out of bounds (using constant value of 25, should be <= 0).
./st_files/940954.st:63-3..63-8: error: Array access out of bounds (using constant value of 26, should be <= 0).
./st_files/940954.st:63-17..63-22: error: Array access out of bounds (using constant value of 26, should be <= 0).
./st_files/940954.st:64-3..64-8: error: Array access out of bounds (using constant value of 27, should be <= 0).
./st_files/940954.st:64-17..64-22: error: Array access out of bounds (using constant value of 27, should be <= 0).
./st_files/940954.st:65-3..65-8: error: Array access out of bounds (using constant value of 28, should be <= 0).
./st_files/940954.st:65-17..65-22: error: Array access out of bounds (using constant value of 28, should be <= 0).
./st_files/940954.st:66-3..66-8: error: Array access out of bounds (using constant value of 29, should be <= 0).
./st_files/940954.st:66-17..66-22: error: Array access out of bounds (using constant value of 29, should be <= 0).
./st_files/940954.st:67-3..67-8: error: Array access out of bounds (using constant value of 30, should be <= 0).
./st_files/940954.st:67-17..67-22: error: Array access out of bounds (using constant value of 30, should be <= 0).
./st_files/940954.st:68-3..68-8: error: Array access out of bounds (using constant value of 31, should be <= 0).
./st_files/940954.st:68-17..68-22: error: Array access out of bounds (using constant value of 31, should be <= 0).
./st_files/940954.st:69-3..69-8: error: Array access out of bounds (using constant value of 32, should be <= 0).
./st_files/940954.st:69-17..69-22: error: Array access out of bounds (using constant value of 32, should be <= 0).
./st_files/940954.st:70-3..70-8: error: Array access out of bounds (using constant value of 33, should be <= 0).
./st_files/940954.st:70-17..70-22: error: Array access out of bounds (using constant value of 33, should be <= 0).
./st_files/940954.st:71-3..71-8: error: Array access out of bounds (using constant value of 34, should be <= 0).
./st_files/940954.st:71-17..71-22: error: Array access out of bounds (using constant value of 34, should be <= 0).
./st_files/940954.st:72-3..72-8: error: Array access out of bounds (using constant value of 35, should be <= 0).
./st_files/940954.st:72-17..72-22: error: Array access out of bounds (using constant value of 35, should be <= 0).
./st_files/940954.st:73-3..73-8: error: Array access out of bounds (using constant value of 36, should be <= 0).
./st_files/940954.st:73-17..73-22: error: Array access out of bounds (using constant value of 36, should be <= 0).
72 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!

版本选择理由：建立首个通过静态检查的修复基底

## ellipse_r15 第2轮

耗时80.422秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：通过了更后面的检查阶段
新增实际验证通过的判据
PATH 物理路点进度增加

## ellipse_r30 第1轮

耗时236.891秒；实际通过8/8项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## triangle_r15 第1轮

耗时128.859秒；实际通过8/8项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## triangle_r30 第1轮

耗时105.406秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## square_r15 第1轮

耗时106.703秒；实际通过0/9项；版本选择promote。

matiec 编译失败:
./st_files/786529.st:179: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:179: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/786529.st:185: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:187: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:188: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:196: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:200: error: invalid statement in case element of ST 'CASE' statement.
./st_files/786529.st:200: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:200: error: ':' missing after case list in ST 'CASE' statement.
./st_files/786529.st:201: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:201: error: ':' missing after case list in ST 'CASE' statement.
./st_files/786529.st:202: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:202: error: ':' missing after case list in ST 'CASE' statement.
./st_files/786529.st:203: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:203: error: ':' missing after case list in ST 'CASE' statement.
./st_files/786529.st:204: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:205: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:204: error: ':' missing after case list in ST 'CASE' statement.
./st_files/786529.st:208: error: invalid statement in case element of ST 'CASE' statement.
./st_files/786529.st:208: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:210: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:208: error: ':' missing after case list in ST 'CASE' statement.
./st_files/786529.st:212: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:212: error: ':' missing after case list in ST 'CASE' statement.
./st_files/786529.st:213: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:217: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:218: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:221: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:213: error: ':' missing after case list in ST 'CASE' statement.
./st_files/786529.st:228: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:237: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:238: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:239: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:247: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:251: error: invalid variable before ':=' in ST assignment statement.
./st_files/786529.st:65: error: invalid test expression defined for ST 'CASE' statement.

129 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!

版本选择理由：建立首个通过静态检查的修复基底

## square_r15 第2轮

耗时26.906秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：通过了更后面的检查阶段
新增实际验证通过的判据
SQUARE_R15_CCW_PATH 物理路点进度增加

## square_r30 第1轮

耗时113.813秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## pentagon_r15 第1轮

耗时252.812秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## pentagon_r30 第1轮

耗时101.594秒；实际通过0/10项；版本选择promote。

matiec 编译失败:
Optimizing ST program...
ST optimizer bypassed: preserve source semantics (agent-guard-v1)
Generating C files...
./st_files/656755.st:117: error: invalid statement in case element of ST 'CASE' statement.
./st_files/656755.st:177: error: invalid statement in ST statement.
./st_files/656755.st:205: error: invalid variable before ':=' in ST assignment statement.
./st_files/656755.st:219: error: invalid variable before ':=' in ST assignment statement.
./st_files/656755.st:235: error: invalid variable before ':=' in ST assignment statement.
./st_files/656755.st:247: error: invalid variable before ':=' in ST assignment statement.
./st_files/656755.st:255: error: invalid variable before ':=' in ST assignment statement.
./st_files/656755.st:115: error: invalid case element(s) defined after 'OF' in ST 'CASE' statement.

8 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!

版本选择理由：建立首个通过静态检查的修复基底

## pentagon_r30 第2轮

耗时78.297秒；实际通过10/10项；版本选择promote。

本轮无失败条目。

版本选择理由：通过了更后面的检查阶段
新增实际验证通过的判据
PATH_PENTAGON_R30 物理路点进度增加

## hexagon_r30 第1轮

耗时230.546秒；实际通过3/11项；版本选择promote。

AC1: plot_done↑ 未在观测窗内置位（cmd_draw↑@0.515）
AC2: cmd_draw↑@0.515s → pen_down↑@20.781s = 20.27s <= 10s
AC6: 终态 (x_fb=44, y_fb=76, z_fb=9) vs 中心 [50, 0, 0]（tol±2.0）
AC7: 终态 (x_fb=44, y_fb=76, z_fb=9) vs 中心 [0, 50, 0]（tol±2.0）
AC8: 终态 (x_fb=44, y_fb=76, z_fb=9) vs 中心 [0, 0, 10]（tol±1.0）
EXECUTION: PlanTimeout: 完成条件未满足
PATH: 未按顺序到达路点4：{'values': {'x_fb': 35, 'y_fb': 76, 'z_fb': 0}, 'tolerance': {'x_fb': 2, 'y_fb': 2, 'z_fb': 1}, 'states': {'pen_down': True}}

版本选择理由：建立首个通过静态检查的修复基底

## hexagon_r30 第2轮

耗时122.782秒；实际通过8/11项；版本选择promote。

AC6: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [50, 0, 0]（tol±2.0）
AC7: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 50, 0]（tol±2.0）
AC8: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 0, 10]（tol±1.0）

版本选择理由：新增实际验证通过的判据
PATH 物理路点进度增加

## hexagon_r30 第3轮

耗时85.5秒；实际通过8/11项；版本选择retain。

AC6: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [50, 0, 0]（tol±2.0）
AC7: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 50, 0]（tol±2.0）
AC8: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 0, 10]（tol±1.0）

版本选择理由：证据未显示推进，保留原最佳版

## hexagon_r30 第4轮

耗时119.89秒；实际通过8/11项；版本选择retain。

AC6: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [50, 0, 0]（tol±2.0）
AC7: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 50, 0]（tol±2.0）
AC8: 终态 (x_fb=50, y_fb=50, z_fb=9) vs 中心 [0, 0, 10]（tol±1.0）

版本选择理由：证据未显示推进，保留原最佳版
