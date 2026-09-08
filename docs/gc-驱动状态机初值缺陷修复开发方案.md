# 驱动状态机初值缺陷修复开发方案

| 项 | 内容 |
|---|---|
| 文档编号 | GC-PLAN-2026-09-08-01 |
| 版本 | v1.0（正式） |
| 状态 | 待评审（F1 项需 lx 契约评审） |
| 编制 | gc（智能体与闭环侧） |
| 编制日期 | 2026-09-08 |
| 数据来源 | llm10 画圆战役记录（`runs/plotter_circle_llm10/`，commit `01901a4`）及现场复现实证 |

**修订记录**

| 版本 | 日期 | 说明 | 编制 |
|---|---|---|---|
| v0.1 | 2026-09-08 | 初稿（草案） | gc |
| v1.0 | 2026-09-08 | 正式化：任务编号化、增加需求条目与追溯、删除 gc 侧旁路过渡实现，F1 以 RFC 通过为实施前置条件 | gc |
| v1.1 | 2026-09-08 | 实施回填：W1（V0）✅ 通过判据达成；W2/W3/W5 ✅ 合入（pytest 152→159）；W4（F1/R7）维持待 RFC 评审，未实施 | gc |

---

## 1. 引言

### 1.1 编制目的

针对 llm10 画圆战役第 5–7 轮暴露的"驱动状态机初值缺陷"及其暴露的框架证据盲区，给出确定性的修复设计、测试计划与实施计划，作为后续开发、评审与验收的依据。

### 1.2 适用范围

本方案覆盖生成侧（PLC 生成器知识）、闸门侧（`xml2st` 静态契约校验）、闭环证据侧（验收反馈留档、归因坑库）与迭代策略侧（repair 熔断）四类修复。不涉及五闸门结构调整与仿真侧（csk）组件。

### 1.3 术语与缩略语

| 术语 | 定义 |
|---|---|
| 402 | CiA402 伺服驱动器状态机（SOD/RTSO/SO/OE/QSA/FRA/FA，对应状态 1–7） |
| DRIVE402 | 项目内伺服驱动器仿真功能块（PLCopen XML 中的 FB 类型） |
| 闸门 1 | 编排器静态校验（`xml2st` 校验 + 转换，失败即短路） |
| repair | 定向修复生成模式（以上轮产物为基底做最小修改） |
| 零推进 | 连续迭代轮次的失败闸门与失败证据完全同质 |
| R7 | 本方案新增的静态校验规则编号（见 §5.2） |
| RFC | 契约变更评审流程（《协作开发指南》） |

### 1.4 参考文档

- 《lx-PLC代码生成与执行引擎详细设计》§3（ST 子集与显式拒绝清单，R7 的归属契约）
- 《gc-需求理解与闭环编排详细设计》§4（solve 循环与闸门语义）
- `docs/devlog-gc.md` 2026-09-07/08 各节（llm2–llm10 战役记录与能力基线）

## 2. 背景与问题分析

### 2.1 问题描述

llm10 战役（8 轮预算，`--no-curated-patterns` 泛化口径，闸门 3/4 真执行）中，第 5–7 轮（repair 模式）在线验收全败且错误完全同质：序列器冻结于步 1，诊断口签名 `pl_step=1 go_x_exe=1 go_y_exe=1 go_z_exe=1` 恒定不变，验收 trace 全程 `pos=(0,0,0) v=(0,0,0)`。repair 三轮零推进后熔断；第 8 轮全新生成行为突变，推进至差 4 项。

### 2.2 根因分析（已实证）

生成代码的 DRIVE402 功能块将状态变量声明为 `state : INT;`（`runs/plotter_circle_llm10/iter_005/plc.st:74`），**缺少 `:= 1` 初值**。因果链如下：

1. INT 缺省初值为 0；402 状态机 CASE（`iter_005/plc.st:90-113`）仅覆盖状态 1..7，状态 0 无转移出口；
2. `sw` 于 `plc.st:131` 置 `16#0000` 后无分支改写，恒为 0；`v_out` 走 ELSE 分支清零（`plc.st:126-128`），三轴速度指令恒 0；
3. MC_POWER 握手在 pstep 1 死等 `sw AND 16#0001`（`plc.st:168`）恒假，cw 停于 0x06，all_oe 恒 FALSE——**驱动器从未使能**；
4. 序列器与插补器侧完全健康（现场实测：cmd_draw 后 0.5s x_int=35、1.0s x_int=50 / y_int=25 / z_int=10，设定值全部 ramp 到位）；
5. 步 1 出口条件（`plc.st:518`）要求物理反馈 `z_fb >= 9 AND ix_z.Done AND NOT ix_z.Busy`；z_fb 由电机仿真自速度指令积分，v=0 → z_fb 恒 0 → 出口永假，序列器冻结。

对照证据：iter_008 声明 `state : INT := 1;`（`iter_008/plc.st:75`），驱动立即使能、轴动、画圆推进至差 4 项；已验证轨道为 `state := 1`（`src/plc/plotter3axis.xml:276`）。一字符初值即第 5–7 轮与第 8 轮的分界。

现场复现（2026-09-08）：对 iter_005 程序注入诊断口后部署（OpenPLC VM 192.168.12.131），按验收剧本激励——run=1 三秒后 sw 全 0（正常使能应读到 0x0037）；cmd_draw 后设定值 1 秒内到位而 v/sw 持续 12 秒恒 0。复现后运行时已恢复 plotter3axis（prog_id=2，RUNNING）。

### 2.3 缺陷分类

| 编号 | 类别 | 缺陷 | 修复归属 |
|---|---|---|---|
| D1 | 生成物缺陷 | LLM 生成状态机变量漏初值（本战役 4/8 轮命中） | F2c |
| D2 | 框架盲区 | 静态闸门不解析 ST 语义，该缺陷仅在线验收（第 5 闸门）暴露 | F1 |
| D3 | 框架盲区 | 诊断口变量挑选按命名启发仅命中序列器域（`orchestrator.py:234-262`），反馈指向错误层 | F2b |
| D4 | 框架盲区 | 验收反馈仅保留尾部 40 行（`orchestrator.py:190`），[2] 使能段 FAIL（sw 全 0 旁证）被截除 | F2a |
| D5 | 框架盲区 | repair 连败 3 次方熔断（`orchestrator.py:427`），同质失败空转 2 轮预算 | F3 |

## 3. 修复目标与范围

### 3.1 目标（需求条目）

| 编号 | 需求 | 验证方式 |
|---|---|---|
| OBJ-1 | "CASE 选择器无初值且无 ELSE 兜底"类缺陷在闸门 1 被确定性拒绝（毫秒级、零运行时依赖） | TC-INT-1/2 |
| OBJ-2 | 该缺陷漏网至在线验收时，归因引擎命中坑库并给出修法，repair 一轮收敛 | TC-INT-3 |
| OBJ-3 | repair 对零推进失败提前熔断：连续 2 轮失败签名同质即回退全新生成 | TC-UNIT-4/5 |
| OBJ-4 | 验收证据全量留档（gate.json），LLM 反馈截断时显式注明 | TC-UNIT-3 |

### 3.2 非目标

- 不追求画圆场景 8 轮行为级全收敛（插补精度/急停语义的文字证据→ST 翻译为当前模型弱项，另案跟踪）；
- 不改变五闸门结构与裁定语义（归因只进反馈不裁定的红线不变）；
- 不引入完整 ST 语法分析（`xml2st` 保持纯标准库、毫秒级）。

## 4. 总体设计

修复分四项，编号与缺陷分类（§2.3）的对应关系如下：

| 编号 | 名称 | 实施点 | 性质 | 对应缺陷 |
|---|---|---|---|---|
| V0 | 根因验证 | 战役产物临时操作（不入库） | 一次性验证 | — |
| F1 | 静态校验规则 R7 | `src/pipeline/xml2st.py` | 根治（RFC 项） | D2 |
| F2a | 验收反馈全量落盘 | `src/agent/orchestrator.py` | 证据系统 | D4 |
| F2b | 坑库条目 P18 | `src/agent/knowledge/pitfalls.json` | 证据系统 | D3 |
| F2c | 生成侧硬规则 | `src/agent/prompts/plcgen_skill.md` | 生成侧防患 | D1 |
| F3 | 零推进提前熔断 | `src/agent/orchestrator.py` | 迭代策略 | D5 |

各层与既有回路的关系：F1 在闸门 1 拦截（生成→静态校验→部署链路最前端）；F2a/F2b 作用于闸门 4 失败后的反馈组装与归因；F2c 作用于生成侧 prompt；F3 作用于 solve 循环的生成策略路由。四项相互独立，可并行实施；F1 为契约变更，实施前置条件见 §9.1。

## 5. 详细设计

### 5.1 V0：根因验证

**内容**：对 `runs/plotter_circle_llm10/iter_005/plcopen.xml` 的 DRIVE402 接口声明 `state` 补 `:= 1` 初值（FB 类型定义一处，三实例共享），经 POST /deploy 重部署后执行 `python src/pipeline/scenario_plotter_circle.py`。

**通过判据**：步 1 冻结消失、序列器前进（预期终态接近 iter_008 水平；其后包围盒/急停语义等独立缺陷不在本项范围）。

**约束**：操作对象为 run 历史产物，验证完成后恢复运行时为 `src/plc/plotter3axis.xml`；产物补丁不提交（保持战役记录原貌），验证结论记入本档附录 A。

### 5.2 F1：静态校验规则 R7（根治项）

**规则定义**：

> 若 POU body ST 中存在 `CASE <选择器> OF`，且该 CASE 块无 `ELSE` 分支，且选择器变量在本 POU 接口/局部声明中无 `:= 初值`，则拒绝，追加 problem：
> `"R7: 状态机选择器 %r 无初值且 CASE 无 ELSE 兜底——上电落入未定义状态（如 0），状态机不可达。修法：声明补 ':= 1' 类初值，或增加 ELSE 分支。"`

**实现设计**（`src/pipeline/xml2st.py`，不引入完整语法分析）：

1. 复用 `parse()` 已构建的 pous/iface/body 结构（`xml2st.py:121` 起）；
2. 对每个 POU 的 body ST 文本，以正则 `CASE\s+([A-Za-z_][A-Za-z0-9_]*)\s+OF` 提取选择器名及 CASE 块起点；
3. CASE 块范围取起点至匹配的 `END_CASE`（按嵌套计数配对），块内检测行首 `ELSE` 存在性；
4. 选择器声明解析复用接口行正则（同 `orchestrator.py:250` 的声明行模式），检查是否含 `:=` 初值；
5. 双条件（无 ELSE 且无初值）同时成立方追加 problem，进入 `parse()` 返回的 problems 列表，闸门 1 现有短路机制自然生效。

**误报控制**：
- 选择器带初值（含有意 `:= 0`）即豁免；
- 有 ELSE 分支即豁免；
- 回归约束：`src/plc/motion3axis.xml`、`src/plc/plotter3axis.xml`、`src/plc/plotter_circle.xml` 三个已验证程序必须全部通过；
- 若实测出现非状态机 CASE 误报，将规则类型域收敛至 INT（记入修订记录）。

**错误处理**：body 为空或 ST 文本缺失的 POU 跳过本规则；正则不匹配不产生 problem（规则只做拒绝，不做改写）。

### 5.3 F2：证据系统

**F2a 验收反馈全量落盘**：`acceptance_gate`（`orchestrator.py:171-190`）返回全量输出；`_dump_gate` 写 `gate.json` 使用全量；仅 `_pack_feedback`（进 LLM 反馈包）保留尾部截断，并在反馈文本中注明 `"（验收输出已截断至尾部 40 行，全量见 gate.json）"`。LLM token 预算不变，证据不再丢失。

**F2b 坑库条目 P18**（`src/agent/knowledge/pitfalls.json`，现有 P01–P17 之后）：

```json
{
 "id": "P18",
 "domain": "runtime",
 "title": "驱动状态机 state 无初值，402 卡在未定义状态",
 "signatures": ["sw 全 0", "驱动器未使能", "all_oe=FALSE", "v=(0,0,0) 恒零"],
 "diagnosis": "DRIVE402 的 state 声明缺 ':= 1' 初值，上电停在 CASE 未覆盖的状态 0，无转移出口：sw 恒 0、v_out 恒 0、MC_POWER 握手死等 sw.bit0。序列器与插补器本身健康（设定值能 ramp 到位）。",
 "fix": "FB 接口声明改 state : INT := 1（SOD 起步）；所有 CASE 状态机选择器同规则检查。",
 "source": "plotter_circle_llm10 iter5-7 实证 + 现场复现 2026-09-08"
}
```

归因引擎走既有 KB 优先匹配机制（`attribution.py`），无代码改动。

**F2c 生成侧硬规则**（`src/agent/prompts/plcgen_skill.md` 新增条目，与既有"CiA402 空闲态约定"同族）：

> **状态机变量必须显式初值**：一切被 CASE 索引的状态变量（402 的 state、序列器的 step/seq）声明时必须带 `:= 初值`；402 状态机从 1=SOD 起步（0 不是合法状态）。CASE 一律带 ELSE 兜底（记错状态/复位用）。

### 5.4 F3：repair 零推进提前熔断

**现状**：`orchestrator.py:427` 以 `repair_fails < 3` 为回退全新生成的条件；本战役第 5/6/7 轮错误完全同质，第 6 轮即可判定零推进。

**设计**：

1. `_fail` 归档时对 `errors` 计算归一化签名：剥离行号、时间戳、st 文件名等易变前缀后哈希；
2. 连续 2 轮失败闸门相同且签名同质 → 视同熔断，下一轮强制 fresh（全新生成）；
3. 叙述器播报同步："连续两轮失败证据同质，切换全新生成策略"；
4. 归一化规则记入 devlog。

**误判分析**：归一化过粗可能将有微小推进的轮次判为同质；其后果为提前进入既有熔断路径（fresh 生成本就是兜底策略），误伤面可控。

## 6. 测试计划

### 6.1 单元测试

| 编号 | 用例 | 断言 | 所属文件 |
|---|---|---|---|
| TC-UNIT-1 | 无初值且无 ELSE 的 CASE 选择器 | 闸门 1 拒绝，报文含 "R7" | `tests/test_xml2st.py` |
| TC-UNIT-2 | 选择器有初值（或 CASE 有 ELSE） | 校验通过 | `tests/test_xml2st.py` |
| TC-UNIT-3 | 反馈包截断注明 + gate.json 全量 | 两处文本/字段断言 | `tests/test_orchestrator.py` |
| TC-UNIT-4 | 连续 2 轮同签名失败 | 第 3 轮走 fresh 路径 | `tests/test_orchestrator.py` |
| TC-UNIT-5 | 失败文本变化（签名不同） | 不触发提前熔断 | `tests/test_orchestrator.py` |
| TC-UNIT-6 | 报文含 "all_oe"/"sw 全 0" | 归因命中 P18 | 既有归因测试文件 |

### 6.2 集成测试（重放）

| 编号 | 用例 | 通过判据 |
|---|---|---|
| TC-INT-1 | `runs/plotter_circle_llm10/iter_004..007/plcopen.xml` 逐一过闸门 1 | 全部被 R7 拒绝 |
| TC-INT-2 | `src/plc/` 三个已验证程序过闸门 1 | 全部通过（回归） |
| TC-INT-3 | 以 iter_005 验收全量输出（含 [2] 使能段）喂归因引擎 | 命中 P18，反馈含 `state : INT := 1` 修法字样 |

### 6.3 战役级验证（可选，需 OpenPLC 在线）

同口径（`--no-curated-patterns --deploy --acceptance --scenario plotter_circle --max-iters 8`）重跑画圆：编译级收敛轮次不劣化；§2.1 型冻结不再出现，或生成侧漏初值时在 1–2 轮内被 R7/P18 回路修复。

### 6.4 回归基线

全套 pytest 全绿；基线 152 例，本方案预期新增 6 例（152 → 158）。

## 7. 实施计划

### 7.1 任务分解（WBS）

| 任务 | 内容 | 前置条件 | 产出 | 工作量 | 负责侧 |
|---|---|---|---|---|---|
| W1 | V0 根因验证 | OpenPLC 在线 | 验证结论（附录 A） | 0.5h | gc |
| W2 | F2b 坑库 P18 + F2c skill 规则 | 无 | pitfalls.json/plcgen_skill.md + TC-UNIT-6 | 1h | gc |
| W3 | F2a 反馈全量落盘 | 无 | orchestrator.py + TC-UNIT-3 | 0.5h | gc |
| W4 | F1 静态校验 R7 | **RFC 评审通过（§9.1）** | xml2st.py + TC-UNIT-1/2 + TC-INT-1/2 | 1.5h | gc 实现 / lx 评审 |
| W5 | F3 零推进熔断 | 无 | orchestrator.py + TC-UNIT-4/5 | 1.5h | gc |
| W6 | 战役级验证（可选） | W2–W5 完成 + OpenPLC 在线 | 新战役 runs/ 记录 | 0.5h（机器时间另计） | gc |

### 7.2 顺序与里程碑

- M1（W1 完成）：根因验证结论归档；
- M2（W2、W3、W5 完成）：gc 自有项全部合入，pytest 全绿；
- M3（RFC 评审通过后 W4 完成）：R7 进入闸门 1，TC-INT-1/2 通过；
- M4（可选，W6）：战役级验证报告。

W2/W3/W5 与 RFC 评审并行推进，互不阻塞；W4 严格以 RFC 通过为前置条件，**不设临时或旁路实现**。

## 8. 风险管理

| 风险 | 等级 | 对策 |
|---|---|---|
| R7 误报（非常规 CASE 用法） | 中 | 双条件豁免（有初值或有 ELSE 即通过）；三个已验证程序作回归（TC-INT-2）；必要时类型域收敛至 INT 并记修订 |
| RFC 评审周期延长，F1 滞后 | 中 | F2b/P18 先行兜住归因路径；F1 不设旁路，等待契约正式变更 |
| gate.json 因全量输出膨胀 | 低 | 验收输出量级实测为数百行内，JSON 落盘无碍；LLM 反馈仍截断 |
| F3 签名归一化误判 | 低 | 剥离易变前缀后哈希；误判后果为提前走既有熔断路径；规则记 devlog |
| 验证/重放期间运行时占用冲突 | 低 | 操作后即恢复 plotter3axis（prog_id=2）；与看板同步占用窗口 |

## 9. 协作与变更管理

### 9.1 RFC 事项（F1）

`xml2st` 的校验规则与显式拒绝清单归 lx 侧契约（《lx-PLC代码生成与执行引擎详细设计》§3）。F1 按以下流程执行：

1. 本档 §5.2 即 RFC 提案文本（规则、实现要点、误报控制、测试口径）；
2. lx 评审并裁决：规则编号（建议 R7）、报文措辞、清单归属（并入 lx 档 §3）；
3. 评审通过为 W4 的唯一前置条件；评审提出的修改意见回灌本档并升版本；
4. 评审未通过期间，F1 不以任何临时形式（gc 侧旁路检查、编排器前置钩子等）实现。

### 9.2 变更控制

- 本档为 F1–F3 的权威设计基线；实施中的偏差需回档修订并记入修订记录；
- 实施合入时同步登记 `docs/协作看板.md` 与 `docs/changelog.md`；
- 战役级验证产物按既有口径入 `runs/`（全量入 git）。

## 附录 A：证据记录

- 战役记录：`runs/plotter_circle_llm10/`（commit `01901a4`）；iter5–7 `gate.json` 诊断口时间线（pl_step=1、go_*_exe=1 恒定）；iter8 全量 FAIL 明细（差 4 项：AC7 包围盒越界 3 次、急停静止 3.05s>3s、急停复跑超时、失能 all_oe）。
- 现场复现（2026-09-08，V0 前置证据）：iter_005 诊断注入版部署于 OpenPLC VM 192.168.12.131；run=1 三秒后 sw 全 0；cmd_draw 后设定值 1 秒内 ramp 至 50/25/10 而 v/sw 持续 12 秒恒 0。复现后运行时恢复 plotter3axis（prog_id=2，RUNNING）。
- V0 验证结论（2026-09-08 实施，W1）：**通过判据达成**。对 iter_005 副本（workspace/v0_iter005_state_init.xml，不入库）的 DRIVE402 接口 `state` 补 `<initialValue><simpleValue value="1"/></initialValue>`（FB 类型定义一处），xml2st 静态校验通过（转换产物含 `state : INT := 1`）→ POST /deploy 编译 OK → `scenario_plotter_circle.py` 在线验收：**步 1 冻结消失**（z 反馈到达 10，`z_fb>=9` 出口通过）、**序列器前进至步 3**（XY 定位 (51,26)——步 2 容差内通过；落笔 z=0 到达；[5] 段"第二次画圆已启动""释放后重新使能" PASS——驱动器使能链路复活），与 iter_008（`state : INT := 1`）行为同型。残留失败（步 3 出口 z 目标 0↔10 振荡致 ix_z.Done 不翻转、急停静止 3.04s>3s、复跑超时、失能 all_oe）为该程序其他独立缺陷，属 §3.2 非目标范围。验证后运行时已恢复 plotter3axis（prog_id=2，RUNNING）。

## 附录 B：关键代码位置

| 位置 | 内容 |
|---|---|
| `runs/plotter_circle_llm10/iter_005/plc.st:74` | `state : INT;`（缺陷：缺初值） |
| `runs/plotter_circle_llm10/iter_005/plc.st:90-113` | 402 CASE，无状态 0 分支 |
| `runs/plotter_circle_llm10/iter_005/plc.st:126-131` | v_out ELSE 清零 / sw 置 0 后无分支改写 |
| `runs/plotter_circle_llm10/iter_005/plc.st:168` | MC_POWER pstep 1 死等 sw.bit0 |
| `runs/plotter_circle_llm10/iter_005/plc.st:518` | 序列器步 1 出口（z_fb>=9 物理条件） |
| `runs/plotter_circle_llm10/iter_008/plc.st:75` | 对照：`state : INT := 1;` |
| `src/plc/plotter3axis.xml:276` | 已验证轨道：`state := 1` |
| `src/agent/orchestrator.py:190` | 验收反馈 `[-40:]` 截断（F2a 改点） |
| `src/agent/orchestrator.py:234-262` | 诊断口变量挑选启发式（D3 依据） |
| `src/agent/orchestrator.py:427` | repair 熔断条件（F3 改点） |
| `src/pipeline/xml2st.py:121` | `parse()` problems 链（F1 挂点） |
