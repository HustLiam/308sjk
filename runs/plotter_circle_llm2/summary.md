# solve 运行总结

## 迭代历史

- iter 1: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/194653.st:16: error: invalid variable(s) declaration.
./st_files/194653.st:98: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:97: error: invalid test expression defined for ST 'IF' statement.
./st_files/194653.st:109: error: invalid expression after 'AND' in ST expression.
./st_files/194653.st:109: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:109: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/194653.st:114: error: invalid expression after 'AND' in ST expression.
./st_files/194653.st:114: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:114: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/194653.st:119: error: invalid expression after 'AND' in ST expression.
./st_files/194653.st:119: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:119: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/194653.st:126: error: invalid expression after 'AND' in ST expression.
./st_files/194653.st:126: error: ';' missing at the end of statement in ST statement.
./st_files/194653.st:126: error: invalid statement in ST statement.
./st_files/194653.st:156: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:160: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:166: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:174: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:178: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:183: error: invalid variable before ':=' in ST assignment statement.
./st_files/194653.st:189: error: invalid statement in ST statement.
./st_files/194653.st:131: error: invalid test expression defined for ST 'CASE' statement.
./st_files/194653.st:230: error: invalid expression after ':=' in ST assignment statement.
./st_files/194653.st:230: error: ';' missing at the end of statement in ST statement.
./st_files/194653.st:230: error: invalid statement in ST statement.

22 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 2: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/325799.st:17: error: invalid variable(s) declaration.
./st_files/325799.st:69: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:102: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:101: error: invalid test expression defined for ST 'IF' statement.
./st_files/325799.st:111: error: invalid expression after 'AND' in ST expression.
./st_files/325799.st:111: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:111: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/325799.st:116: error: invalid expression after 'AND' in ST expression.
./st_files/325799.st:116: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:116: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/325799.st:120: error: invalid expression after 'AND' in ST expression.
./st_files/325799.st:120: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:120: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/325799.st:126: error: invalid expression after 'AND' in ST expression.
./st_files/325799.st:126: error: ';' missing at the end of statement in ST statement.
./st_files/325799.st:126: error: invalid statement in ST statement.
./st_files/325799.st:160: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:164: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:170: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:178: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:182: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:187: error: invalid variable before ':=' in ST assignment statement.
./st_files/325799.st:193: error: invalid statement in ST statement.
./st_files/325799.st:130: error: invalid test expression defined for ST 'CASE' statement.
./st_files/325799.st:231: error: invalid expression after ':=' in ST assignment statement.
./st_files/325799.st:231: error: ';' missing at the end of statement in ST statement.
./st_files/325799.st:231: error: invalid statement in ST statement.

23 error(s) found. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 3: 闸门 **deploy** 失败
  - matiec 编译失败:
Optimizing ST program...
Generating C files...
./st_files/971172.st:17: error: invalid variable(s) declaration.
./st_files/971172.st:69: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:102: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:101: error: invalid test expression defined for ST 'IF' statement.
./st_files/971172.st:111: error: invalid expression after 'AND' in ST expression.
./st_files/971172.st:111: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:111: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/971172.st:116: error: invalid expression after 'AND' in ST expression.
./st_files/971172.st:116: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:116: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/971172.st:120: error: invalid expression after 'AND' in ST expression.
./st_files/971172.st:120: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:120: error: expecting 'THEN' after test expression in ST 'IF' statement.
./st_files/971172.st:126: error: invalid expression after 'AND' in ST expression.
./st_files/971172.st:126: error: ';' missing at the end of statement in ST statement.
./st_files/971172.st:126: error: invalid statement in ST statement.
./st_files/971172.st:160: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:164: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:170: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:178: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:182: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:187: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:193: error: invalid statement in ST statement.
./st_files/971172.st:130: error: invalid test expression defined for ST 'CASE' statement.
./st_files/971172.st:231: error: invalid expression after ':=' in ST assignment statement.
./st_files/971172.st:231: error: ';' missing at the end of statement in ST statement.
./st_files/971172.st:231: error: invalid statement in ST statement.
./st_files/971172.st:233: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:238: error: ';' missing at the end of statement in ST statement.
./st_files/971172.st:238: error: invalid variable before ':=' in ST assignment statement.
./st_files/971172.st:238: error: ';' missing at the end of statement in ST statement.
./st_files/971172.st:238: error: invalid statement in ST statement.
./st_files/971172.st:239: error: invalid statement in ST statement.

Parsing failed because of too many consecutive syntax errors. Bailing out!
Error generating C files
Compilation finished with errors!
- iter 4: 闸门 **acceptance** 失败
  - [verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml
- iter 5: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   FAIL X 轴 sw.bit0=1（0040）
  -   FAIL Y 轴 sw.bit0=1（0040）
  -   FAIL Z 轴 sw.bit0=1（0040）
  -   PASS all_oe=FALSE
  -   FAIL pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能
- iter 6: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter_circle (prog_id=3)
  - [1] 上电（run=0）：三轴就绪；初始笔位 z=0
  -   FAIL X 轴 sw.bit0=1（0040）
  -   FAIL Y 轴 sw.bit0=1（0040）
  -   FAIL Z 轴 sw.bit0=1（0040）
  -   PASS all_oe=FALSE
  -   FAIL pen_down=TRUE（初始触纸）
  - [2] run=1 → 三轴使能

## 结论

best = {"iter": 4, "gate": "acceptance", "errors": ["[verify] 程序不匹配：期望 plotter_circle (prog_id=3)，运行时当前 prog_id=2 —— 请先 python src/pipeline/run_deploy.py --xml src/plc/<对应场景>.xml"]}