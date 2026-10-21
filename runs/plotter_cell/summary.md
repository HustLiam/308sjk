# solve 运行总结

## 迭代历史

- iter 1: 闸门 **generate** 失败
  - [xml2st] XML 无法解析: unbound prefix: line 35, column 18
- iter 2: 闸门 **acceptance** 失败
  - [verify] 程序身份确认: plotter3axis (prog_id=2)
  - [1] 上电（run=0）：三轴 Ready To Switch On；初始笔位 z=0（触纸）
  -   PASS X 轴 sw.bit0=1 (实际 0031)
  -   PASS Y 轴 sw.bit0=1 (实际 0031)
  -   PASS Z 轴 sw.bit0=1 (实际 0031)
  -   PASS all_oe=FALSE
  -   PASS pen_down=TRUE（初始笔触纸）
  - [2] run=1 → 三轴使能（AC1：≤2s）
- iter 3: 闸门 **all** 通过

## 结论

best = {"iter": 3, "gate": "all", "ok": true}