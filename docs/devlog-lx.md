# lx 本地开发日志（个人分支，合入 master 前移除）

## 2026-09-03 场景库精简为 motion3axis 单场景（负责人指令）

- **变更**：`git rm` axis_osc / xy_pick / z_lift / mixed_lin_rot 四个场景 XML；场景库只留三轴控制 motion3axis。
- **验证**：pytest 74/74 绿；motion3axis `xml2st --check` 通过；`run_deploy --xml src/plc/motion3axis.xml` 部署 OK；`scenario_motion3axis.py` 验收全部通过——精简未破坏链路 B。
- **顺带修正**：
  - lx 文档 §8 索引"4 个场景"漂移 → 改为"motion3axis（当前唯一场景）"；
  - 总体实施方案架构图对齐恢复 c68ecee 版（上次 master 合并冲突解决残留的 9 行空格漂移）；
  - 协作指南附录 A 走查示例去掉了对已删 xy_pick 的具体引用（改为假想场景 + prog_id 从 2 顺延）。
- **保留物**：`tools/gen_scenarios.py` 生成器与《运动控制代码生成方案》不动——生成器可按需再生被删场景（其 `__main__` 演示段写 mixed_lin_rot 输出路径，纯输出不读取，无依赖问题）。
- **分支操作**：lx 先 fast-forward 到 master（48e24d9）消分叉，再做变更；本次 master 合并为普通 merge 无冲突。
- **prog_id 语义**：回归 motion3axis=1，未来新场景从 2 顺延（看板/lx 文档/协作指南三处同步）。
