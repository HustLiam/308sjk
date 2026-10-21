# 每轮问题与版本选择

本报告只读取已完成任务的原始判定与progress记录，不调用模型，不修改程序或验收。

## rectangle_r15 第1轮

耗时96.328秒；实际通过0/9项；版本选择promote。

matiec 编译失败:
Optimizing ST program...
ST optimizer bypassed: preserve source semantics (agent-guard-v1)
Generating C files...
./st_files/74952.st:21: error: invalid variable(s) declaration.
./st_files/74952.st:103: error: invalid variable before ':=' in ST assignment statement.
./st_files/74952.st:104: error: invalid expression after '>' in ST expression.
./st_files/74952.st:105: error: invalid variable before ':=' in ST assignment statement.
./st_files/74952.st:105: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/74952.st:111: error: no expression defined after '/' in ST expression.
./st_files/74952.st:109: error: invalid test expression defined for ST 'IF' statement.

6 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!

版本选择理由：建立首个通过静态检查的修复基底

## rectangle_r15 第2轮

耗时37.891秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：通过了更后面的检查阶段
新增实际验证通过的判据
RECT_PATH 物理路点进度增加

## hexagon_r30 第1轮

耗时138.859秒；实际通过10/10项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## rectangle_r30 第1轮

耗时116.672秒；实际通过10/10项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## irregular_r30 第1轮

耗时158.875秒；实际通过9/9项；版本选择promote。

本轮无失败条目。

版本选择理由：建立首个通过静态检查的修复基底

## ellipse_r15 第1轮

耗时124.765秒；实际通过8/9项；版本选择promote。

ELLIPSE_PATH: LLM 计划的全部40个物理路点按序到达且工艺状态满足；工艺开启时偏离计划线段，最大偏差2.8284271247461903（容差2）；最差样本{"segment": 22, "t": 6.844, "actual": {"x_fb": 39, "y_fb": 48, "z_fb": 0}, "from": {"x_fb": 36, "y_fb": 47, "z_fb": 0}, "to": {"x_fb": 37, "y_fb": 46, "z_fb": 0}, "reason": "偏离计划线段"}

版本选择理由：建立首个通过静态检查的修复基底

## ellipse_r15 第2轮

耗时150.953秒；实际通过8/9项；版本选择reject。

ELLIPSE_PATH: LLM 计划的全部40个物理路点按序到达且工艺状态满足；工艺开启时偏离计划线段，最大偏差3.1622776601683795（容差2）；最差样本{"segment": 35, "t": 12.703, "actual": {"x_fb": 60, "y_fb": 45, "z_fb": 0}, "from": {"x_fb": 63, "y_fb": 46, "z_fb": 0}, "to": {"x_fb": 64, "y_fb": 47, "z_fb": 0}, "reason": "偏离计划线段"}

版本选择理由：ELLIPSE_PATH 线段偏差变差

## ellipse_r15 第3轮

耗时81.547秒；实际通过8/9项；版本选择reject。

ELLIPSE_PATH: LLM 计划的全部40个物理路点按序到达且工艺状态满足；工艺开启时偏离计划线段，最大偏差3.1622776601683795（容差2）；最差样本{"segment": 5, "t": 2.687, "actual": {"x_fb": 66, "y_fb": 53, "z_fb": 0}, "from": {"x_fb": 63, "y_fb": 54, "z_fb": 0}, "to": {"x_fb": 61, "y_fb": 55, "z_fb": 0}, "reason": "偏离计划线段"}

版本选择理由：ELLIPSE_PATH 线段偏差变差
