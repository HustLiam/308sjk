# solve 运行总结

## 迭代历史

- iter 1: 闸门 **acceptance** 失败
  -   PASS Z 抬笔到位（实际 10）
  -   PASS pen_down=FALSE（已抬笔）
  -   PASS X/Y 仍为 0
  - [5] cmd_go P(60,40,10)：抬笔态三轴并发定位
  -   PASS 到位 move_done=TRUE
  -   PASS 运动期间 any_moving 曾置位
  -   PASS X 定位 |60-58|<=容差（实际 58）
  -   PASS Y 定位 |40-39|<=容差（实际 39）
- iter 2: 闸门 **all** 通过

## 结论

best = {"iter": 2, "gate": "all", "ok": true}