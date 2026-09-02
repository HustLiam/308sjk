# lx 本地开发日志

> 仅存在于 `lx` 个人分支，只做代码上的技术说明；**合并 master 前移除**（分支历史保留可回溯）。进度协调类内容写 `docs/协作看板.md`，不写这里。
> 历史回溯：`git show <commit>:docs/devlog-lx.md`（各轮入库/移除提交见 lx 分支 log）。

## 2026-09-02（第四轮：看板重置 + 第四次许可制合并）

- 看板按负责人指令清空重填：lx 区块 8✅/2🚧/1⏸ 全量实况；csk/gc 依据 master 历史与远端分支代填初始化（在途协作事项保留：Schema 评审待 csk、io_map 阻塞链、推送权限裁定）；共同议题收敛 7 项。历史变更记录清零（git 可回溯），这本身就是把"看板=当前状态快照、git=历史"的定位落实。
- 第四次许可制合并：远端预检无分岔，纯快进，流程已完全常态化（约一分钟）。

### 待续（下一轮候选，不变）

- 一键回归脚本 run_regression.py（主方案 §8.4 CI 门禁落地实体）；
- serve.py GET /status（运行时状态 + prog_id 一站式确认）。

## 2026-09-02（第五轮：场景库重组 → 三轴运动控制）

### motion3axis 设计与排障

- 结构：逐轴 ±2 死区闭环（IF err>2 fwd / err<-2 rev / else 停），Z 安全区互锁 `xy_enable := z_fb <= 30` 封锁 X/Y 输出，in_pos 三轴死区汇总，双驱互斥由分支构造性保证。
- 验收脚本扮演三轴伺服（30 单位/s 积分 fb）做位置闭环；航点设计必须尊重互锁语义（低位平移需先抬 Z），否则死锁——这正是互锁的验收价值。
- **排障 1（XML 笔误复发）**：`</simpleValue>` 嵌套关闭标签错误又出现（7 处，批量替换修复）——该错误已在 pid_tank 出现过，属系统性手写模式错误，**值得写进 xml2st 的错误提示或未来加专项检查**（技术债候选：校验器可直接给"嵌套标签关闭名不匹配"的友好提示）。
- **排障 2（伺服启动竞态，新知识）**：换目标瞬间 PLC 仍报旧的 in_pos=TRUE 且输出全静（新目标未进扫描），伺服循环首迭代即误判到位退出。修复：写目标后 sleep(0.15) 过一个扫描周期 + 到位判定三重条件（in_pos 标志 ∧ 输出全静 ∧ 实际位置入死区）——位置核对让判定对标志延迟免疫。
- Shell 陷阱记录：Git Bash 下 `grep "pattern"` 对含 `</...>` 的模式返回 0（假阴性），跨平台文本处理一律用 python pathlib。

### gc 侧适配（动了 gc 的文件，已在看板提请评审）

- patternlib CATALOG 6 卡→1 卡 + DEFAULT_PICKS；run_deploy 默认 XML 改 motion3axis；orchestrator 用法示例路径。
- 三个测试文件重写锚点：test_consistency_check（16 个定位变量、R1~R5 反例改用 x_fb/x_fwd 做变异）、test_orchestrator（负例种子从 counter.xml 改为 tmp 文件内改名变异——单场景库没有天然异种子，改用最小变异法）、test_patternlib（单卡语义：picks=2 也只回 1 张）。
- test_spec_validator 三个用例原硬编码 sorting 的数组下标，改为按 name/type 动态定位——**教训：断言别锚定布局下标**。
- 测试 75→74（counter 专属用例随场景移除）。
