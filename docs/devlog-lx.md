# lx 本地开发日志

> 仅存在于 `lx` 个人分支，只做代码上的技术说明；**合并 master 前移除**（分支历史保留可回溯）。进度协调类内容写 `docs/协作看板.md`，不写这里。
> 历史回溯：`git show <commit>:docs/devlog-lx.md`（各轮入库/移除提交见 lx 分支 log）。

## 2026-09-02（第六轮：场景库重组合并 + 第五次许可制）

- 第五次许可制合并，远端无分岔纯快进；master 树核对无 devlog ✓。场景库 v2.0 进入 master（motion3axis 23/23，gc 侧 74/74 绿）。
- **注意**：本次合并动了 gc 的文件（patternlib/orchestrator docstring/三个测试/spec 基准），看板已提请 gc 评审——gc 下次拉取 master 时需重点看 `src/agent/patternlib.py`（六卡→单卡）与 `tests/`（锚点全部换为 motion3axis，负例种子改为最小变异法）。
- csk 侧受影响面：联调基准场景改为 motion3axis（其文档 §9 待办已同步）；io_map 结构落地时的对账基准也变为 motion3axis 的 16 变量 io_list（examples/specs/motion3axis.spec.json）。

### 遗留技术债（场景重组产生）

1. `</simpleValue>` 嵌套关闭标签笔误已在两个场景（pid_tank、motion3axis）各出现一次——系统性手写模式错误，候选：xml2st 校验器加"嵌套标签关闭名不匹配"的友好错误提示（现在只有行号列号）；
2. 单场景库下 gc 的负例测试依赖"最小变异法"（tmp 文件改名），场景重建后可换回天然异种子；
3. 一键回归脚本（run_regression.py）尚未建——场景库重组后它的内容更简单了（单场景），优先级不变。

### 待续（下一轮候选，不变）

- 一键回归脚本 run_regression.py；
- serve.py GET /status。

### 主方案 v1.4（负责人指令）

- §4/§5/§6/§7 内容删除仅留标题（难点表/实施计划/指标/风险表整体移除，git 可回溯）；§1–§3/§8 不动。纯文档删减，74/74 复核绿。
- 未处理项：gc 文档 3 处交叉引用指向空章节（§4 难点归属/§7 对齐 §5/§8 摘录 §6）——按指令范围未动，已在看板登记待示下。
