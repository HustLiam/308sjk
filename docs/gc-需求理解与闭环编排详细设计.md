# 需求理解与闭环编排详细设计（gc 负责部分）

> 本文档是《总体实施方案》中 **① 需求理解模块**、**②a/②b 生成智能体的 LLM 本体**、**端到端闭环编排器（solve 循环、迭代管理、归因反馈的权威定义在本档 §3.2 / §4，csk 文档 §7.2–7.4 指向此处）**与**跨模块契约一致性**的详细设计，负责人：gc（智能体与闭环侧）。
>
> 三人分工全景：**PLC 执行侧（lx，见《lx-PLC代码生成与执行引擎详细设计》）** 负责代码契约与链路 B；**仿真验证侧（csk，见《csk-仿真环境与IO闭环详细设计》）** 负责 SceneSpec 规范/校验器、MJCF 构建与组件库（③b）、MuJoCo 运行时、判定引擎与链路 A 构建，兼 ②b 场景描述的**评审方**；**本侧（gc）** 负责双生成本体（②a PLC 代码 + ②b 场景描述）与闭环大脑——听懂需求、生成代码与场景、判定后归因、定向重生成、管住迭代直至收敛。

---

## 0. 职责范围（TL;DR）

| 总体方案模块 | 本侧职责 | 关键产物 | 状态 |
|---|---|---|---|
| ⓪ AutomationML 解析 | IEC 62714 AML → device_model.json（设备/IO/拓扑/运动学） | `src/agent/aml_parser.py` + CLI `tools/aml_parser.py` | ✅ 完成（`schemas/device_model.schema.json` v1.0.0-draft.1 + 双示例：motion3axis_station + **plotter3axis_station（CAEX 3.0 全结构新参考样式**：InstanceHierarchy+三类类库、信号方向挂 PLC 通道、InternalLink=电气接线）；确定性解析 + io_list 预填契约测试 ×2） |
| ① 需求理解模块 | 自然语言 → 结构化需求规格；**requirement_spec.json Schema 的定义权** | `requirement_spec.json` + JSON Schema | 🟨 Schema **v1.0.0-draft.3**（draft.2 + lx 收紧建议落实：range 完整落入有符号/无符号域之一），待 csk 评审后冻结；**模块 v0 已落地**（`src/agent/requirement.py`：LLM 模式 io_list 逐字锚定预填+修复回路 / 离线模板模式 / pending 澄清；绘图仪 spec 由 glm-5.3 一轮产出+人工介入点 1 修正后冻结 `examples/specs/plotter3axis.spec.json`）；LLM 多轮澄清未接 |
| ②a/②b 的 LLM 生成本体 | PLCopen XML 生成器（在 lx 契约上）、失败归因分析 LLM | 生成器 Prompt 工程 + ST 模式库 | 🟨 生成器 v0 已实现（`src/agent/pipeline.py` + `patternlib.py` + `prompts/plcgen_skill.md`，种子=motion3axis+plotter3axis，xml2st+一致性双闸门回灌；client 流式降级支持万 token 长生成）；**绘图仪种子已落地**（`src/plc/plotter3axis.xml`：CSP 栈逐字复用 + INTERP_Z 笔轴 + 9 步绘图序列器 + 笔互锁，双闸门绿）；归因 LLM 未启动 |
| 闭环编排器 | solve 循环、两个编译/校验短路、迭代记忆、终止与 best-effort、反馈包拼装与路由 | `orchestrator/` | 🟨 半环五闸门（`src/agent/orchestrator.py`：生成→xml2st→一致性→**scene（②b 产物自检 + R5 腿）**→部署可选→链路 B 验收可选；/status 观测仅记录不裁定；CLI `--aml/--request` 直通 ⓪→①；runs/plotter3axis_demo final 冻结）；仿真全环等 csk 接口 |
| ②b 场景描述生成 | spec → **scene.spec.json（内嵌 io_map，契约 v1.1）**（csk 分支 f39debd 收窄：SceneSpec JSON 生成归 gc） | `src/agent/scene_gen.py` | ✅ **v1 契约对齐已落地**（确定性生成：组件注册表运行时加载 `contract/components.v*.json`；io_map 只含可绑物理通道（fb→pos/cmd→cmd，range SI 米制）；地址分配移交 csk build-mjcf；自检 V0~V4 含路由覆盖；编排器闸门2b 消费——**受阻项：csk master REGISTRY 缺 6 个 MJCF 类型，见看板共同议题**；LLM 布局创意后续仅限 pose/params） |
| 跨模块一致性 | io_list 单一源头的落地：**三方一致性检查器**（定位变量 ≡ io_list ≡ io_map） | `consistency_check.py` | ✅ 完成（R1~R5；R5 腿按契约 v1.1 改子集覆盖语义——io_map ⊆ io_list（ioEntry 对账 bool↔BOOL/float↔INT，bind={asset,quantity}），反向覆盖由 ②b 路由覆盖自检保证；io_map 腿由 ②b 产物供给，每轮闸门2b 执行） |

不归本侧的：xml2st 校验/部署/Modbus 验收（PLC 侧）；SceneSpec→MJCF 构建、MuJoCo lockstep 运行、trace 采集、**确定性判定引擎**（仿真验证侧）。判定引擎给出 PASS/FAIL，本侧消费它并决定下一步。

## 1. 在总体架构中的位置

```
AutomationML(设备描述)              用户（自然语言）
   │                                    │
   ▼                                    ▼
【本侧】⓪ AML 解析器                ① 需求理解
   │ device_model.json ──────────────► │（自然语言 + 设备模型）
                                      ▼
                              requirement_spec.json（io_list 从设备模型自动填充）
   │
   ▼
【本侧】编排器 solve() 循环 ──────────────────────────────────┐
   │  ②a PLC 代码生成（本侧 LLM，产出受 lx 契约约束）          │
   │     └► 编译闸门：lx 的 xml2st / POST /deploy（失败即短路） │
   │  ②b 场景生成（仿真侧 SceneSpec LLM）                      │
   │     └► 校验闸门：仿真侧 Schema/物理校验（失败即短路）      │
   │  ③ 仿真执行：③a DLL + ③b json→MJCF→run_sim_headless    │
   │  ④ 判定：仿真侧确定性规则引擎 ──► verdict.json             │
   │  归因（本侧 LLM）：区分代码问题 / 场景问题，路由重生成      │
   └── 未通过：带迭代记忆进入下一轮（≤6 轮）◄──────────────────┘
        通过：冻结 final/ → 转 lx 的链路 B（OpenPLC）做软 PLC 验收
```

本侧是环上唯一"懂语义"的节点：两端提供确定性的校验/编译/判定/执行能力，本侧决定**生成什么、往哪返工、何时停**。

## 2. ① 需求理解模块

**功能**：把用户自然语言指令 + ⓪ 输出的设备模型（device_model.json）变成机器可执行、可验证的结构化需求规格；歧义主动追问；io_list 从设备模型自动填充。

**requirement_spec.json（本侧拥有 Schema 定义权，冻结前需三方联签）**：

> **落地状态**：草案 **v1.0.0-draft.3** 已实现——评审视图 `schemas/requirement_spec.schema.json`，
> 运行权威 `src/agent/spec_validator.py`（两者同一 RFC 内同步修改），基准示例
> `examples/specs/motion3axis.spec.json` + `examples/specs/plotter3axis.spec.json`
>（io_list 各与其场景 XML 定位变量逐字对齐）。Schema 文件表达不了的语义规则：
> S1 唯一性（io 名/AC id/C id）、S2 量程（INT 必带 range 且**完整落入有符号域
> [-32768,32767] 或无符号域 [0,65535] 之一**——lx 位宽表 %QW 承载 INT/UINT/WORD，
> lx 2026-09-03 收紧建议、draft.3 落实；BOOL 禁带）、S3 信号引用必须落在 io_list、
> S4 时间阈值 ≥100ms——均在校验器实现。
>
> **io_list 自动填充（⓪→① 数据流，主方案 §3.1）**：`aml_parser.build_io_list(model)`
> 把 device_model 的 IO 点位确定性映射为 io_list 初始值（name/dir/type/range/unit/
> device=信号语义）；INT 缺量程的条目进 pending 列表显式提示补充——LLM 校验和补充
> 而非从零生成。契约测试：两份示例 AML 的预填与基准 spec 的 io_list 逐字等价。
>
> **模块 v0（`src/agent/requirement.py`，2026-09-07）**：`RequirementUnderstander.
> understand(request_text, device_model)` 三形态——LLM 模式（spec 主体由 LLM 组织，
> io_list 逐字锚定预填：name/dir/type/range 不得动，校验+锚定失败回灌修复 ≤3 轮）、
> 模板模式（无 client 离线回归）、澄清问题（pending 随报告返回，人工介入点 1 的
> 机器侧形态）。绘图仪 spec 由该模块真实产出（glm-5.3 一轮通过），经人工介入点 1
> 复核修正 3 处不健全验收准则后冻结——LLM 产出必须复核的实证样本（详见 devlog）。

| 字段 | 内容 | 约束 |
|---|---|---|
| `task_goal` | 被对象与工艺动作序列描述 | 自然语言，供②a/②b共享 |
| `io_list` | IO 清单：`name / dir(input\|output) / type / range / device` | **三方一致性唯一源头**（②ST 定位变量、③io_map、④地址映射）；跨链路类型规则见 §6 |
| `constraints` | 时序约束、互锁条件、异常处理策略 | 供②a生成逻辑与④映射为 forbidden_state 类准则 |
| `acceptance` | 可量化验收准则，**封闭四类**：`event_delay` / `region_containment` / `forbidden_state` / `sim_health` | 结构与仿真侧判定引擎逐字对齐（csk 文档 §7.1 的 JSON 即权威结构）；落不进四类的一律退回重新组织 |

**处理要点**：

1. LLM Agent 注入工业自动化领域知识（PLC 编程规范、典型工艺、安全联锁规则）；
2. 多轮澄清协议：规格回显 → 用户确认或修正 → 才进入生成阶段（**人工介入点 1/2**：环前确认、环后兜底）；
3. `acceptance` 每条准则必须带 `id / desc / type` 及类型专属字段，Schema 校验不过直接退回；
4. 时间类阈值强制 ≥100ms（通信时序约束，写入 Schema 校验规则）。

## 3. 生成智能体

### 3.1 PLC 代码生成器（② 的 LLM 本体）

- **输入**：requirement_spec（task_goal/io_list/constraints）+ 反馈包（迭代时）+ 知识库；
- **输出**：`plc_project.xml`（IEC 61131-10，唯一交付格式）——必须落在 lx 的 xml2st 契约内（ST 本体子集、定位变量 + 位宽契约、显式拒绝清单，见其文档 §3）；
- **知识资产（本侧维护）**：ST 代码模式库（顺序控制、状态机、PID、联锁——六个已验收场景 XML 是首批种子（含 PID 模式））、PLCopen XML 模板、历史项目片段（RAG）；
- **质量目标**：首次生成编译通过率 ≥80%（matiec 前置拦截 + lx 校验器错误文本定向反馈是达标的手段）。

### 3.2 归因分析 LLM（消费判定结果）——✅ v0 已落地

> **落地状态（2026-09-07）**：`src/agent/attribution.py`——**确定性坑库优先、
> LLM 兜底**两层：错误文本先签匹配 `knowledge/pitfalls.json`（14 条：lx 避坑
> 1~8 + 联调新增 9~14）与情景记忆修复对（`memory.py`，跨会话）；未命中才 LLM
> 小上下文诊断（输出标 advisory）。红线不变：归因只进反馈包（`_pack_feedback`
> 增强）与 gate.json 留档，**不改变任何闸门裁定**。编排器 7 个失败点统一走
> `_fail()`；final+在线验收 ok 触发 `_consolidate()` 知识沉淀（修复对 +
> 模式卡自动策展 patterns.json）。

- 输入：仿真侧 `verdict.json`（确定性证据，如"t=4.286s PE1 上升沿 → t=5.431s 气缸推出，延迟 1.145s > 0.5s"）+ 相关 trace 窗口 + 当前代码/场景；
- 输出：`report.md` 归因报告，**区分代码问题（→②）与场景问题（→③）**并路由；
- 红线：归因不改变 PASS/FAIL 结论；判定永远由仿真侧确定性引擎做出，LLM 不判卷。

### 3.3 场景描述生成器（②b 的 LLM 本体）

- **输入**：requirement_spec（task_goal / io_list）+ **契约包 `contract/`（csk→gc，v1.1 唯一权威：组件类型/参数/quantity/单位制）** + 场景类失败反馈（迭代时）；
- **输出**：`scene.spec.json` **单一工件，内嵌 io_map**（ioEntry 数组）——先过 csk 的 `scenegen.cli validate` 闸门（失败即短路回喂），再交 csk `build-mjcf` 确定性构建（io_map 地址分配在该步：输出 %QX/%QW/%IW 分配 + `st_io_declaration.st`）；
- **约定**：LLM 不写 USD；`type` 封闭枚举以 `contract/components.v*.json` 运行时加载（契约换版只换文件，代码不动）；发明新类型走 RFC。

> **落地状态（v1，2026-09-09，契约 v1.1 对齐）**：`src/agent/scene_gen.py`——
> **确定性生成**（同输入逐字节同输出）。产物 scene.spec.json：资产契约 15 类型
> 注册表驱动（v0 的 gc 自维护目录废止）；**io_map 内嵌**且只收录可绑物理通道
>（路由：`<axis>_fb`→`<axis>_axis`.pos / `<axis>_cmd`→cmd；range=stroke×
> scale_m_per_unit **SI 米制**）；按钮/灯不进 io_map（hmi_panel 无注册 quantity，
> 落 panel.params.buttons/lamps，csk 运行时按名接线）；**地址字段移除**——地址
> 分配归 csk build-mjcf 按声明顺序确定性产出（`contract/example1_iomap.json` 形态）；
> NC 设定值（sp）/状态字（sw）/速度指令（v）为非物理通道不进 io_map（速度指令轴
> 需仿真支持须 RFC 增 quantity）。自检 V0~V4（场景骨架/契约参数规则（枚举数值界
> 未知参数 parent）/ioEntry 结构/io_map ⊆ io_list + **路由覆盖**（可绑通道不得
> 静默丢失）/bind 合法性（asset 存在·quantity 注册·方向 dtype 匹配））。交叉
> 验证：契约自带 example1.json（gantry）过 csk 真闸门；绘图范本与 gc 生成物受阻
> 于 csk master 实现缺口（其 REGISTRY 仅 9 个 USD 类型，6 个 MJCF 类型已在契约
> JSON 声明但未合入其运行时——见看板共同议题）。LLM 布局创意后续只允许改
> pose/params——io_map 骨架是对账契约，不交给概率性组件。

## 4. 端到端编排器（闭环本体——本节为 solve 循环的权威定义）

```
solve(request):
  spec = understand(request)                          # ① 含用户确认
  history = []                                        # 迭代记忆
  for i in 1..MAX_ITERS(默认 6):
    xml = gen_plc_code(spec, history)                 # ②a 本侧生成器
    if not compile_gate(xml):                         # 短路①：lx xml2st/--check 或 /deploy
        history.append(compile_error); continue       #   不进仿真，省最贵一步
    scene = gen_scene_spec(spec, history)             # ②b 仿真侧 LLM
    if errs := validate_scene(scene):                 # 短路②：仿真侧校验器（②b 产物）
        history.append(scene_error); continue
    usd, io_map = build_usd(scene)                    # ③b 确定性构建（json→USD）
    consistency_check(xml, spec.io_list, io_map)      # 本侧：三方一致性（见 §5）
    trace = run_sim_headless(mjcf, io_map, dll)      # ③ 仿真执行（③a DLL × ③b）
    verdict = evaluate(spec.acceptance, trace)        # ④ 仿真侧确定性判定
    if verdict.ok: return finalize(i)                 # 冻结 final/ → 链路 B 验收
    history.append(verdict, analyze(trace, verdict))  # 归因入记忆 → 下一轮
  return best_effort()                                # 最优轮 + 失败报告 → 人工介入
```

- **编译闸门优先本地**：先 `xml2st --check`（毫秒级、无需运行时），过了再走 lx 的 `POST /deploy`（:8600）触发真编译；
- **反馈包拼装（程序自动，不靠 LLM 现场发挥）**：失败准则 + 证据原文、相关 trace ±1s 窗口（csk 侧工具截取）、上轮产物 diff、迭代记忆（历史"改了什么 → 哪条准则翻转"，**禁止回退已通过的修改**）——按优先级组织的完整规范见本档 §3.2；
- **产物与迭代管理（权威定义）**：每轮落盘 `runs/<task>/iter_NNN/`，全量入 git，结论可复现：

```
runs/<date>_<task>/
  request.json                 # 用户原始指令 + 结构化需求规格
  iter_001/
    plcopen.xml  plc.st        # 生成的代码（XML 唯一源码，.st 为转换产物）
    scene.spec.json  scene.usda  io_map.json
    build/plc_logic.dll        # 链路 A 编译产物
    trace.parquet  events.json  exit.json
    verdict.json  report.md    # 判定结果 + LLM 归因报告
  iter_002/ ...
  final/ -> 通过轮次的快照      # 成功后冻结
```

- **终止**：全过 → `final/` 冻结 → 交 lx 链路 B 做 OpenPLC 验收；6 轮未过 → 取通过准则数最多一轮为 best + 失败分析报告（**人工介入点 2/2**）。

## 5. 跨模块契约一致性（io_list 单一源头）

总体方案 §4 难点"三方变量一致性"的实现归属本侧：

```
plc_project.xml --(lx xml2st parse)--> 定位变量表 {name, addr, type, dir}
requirement_spec.io_list ------------------------------------┐
io_map（契约 v1.1：scene.spec 内嵌 ioEntry / csk 契约③ io_map.json）---┤
                    consistency_check：名称/类型/地址/方向逐条对账
```

- 检查点：`io_list` 每条在定位变量表有逐字同名变量（R2 双向）；io_map 每条 ioEntry
  的 plc_var ∈ io_list 且 dir/类型兼容（R5，**子集覆盖**——契约 v1.1 的 io_map 只含
  可绑物理通道，按钮/灯与 NC/诊断通道不在其列，反向覆盖由 ②b 路由覆盖自检保证）；
  地址不冲突、位宽匹配（BOOL↔%QX，INT↔%QW）；方向语义正确（input↔仿真→PLC，
  output↔PLC→仿真）；
- 调用时机：编排器在**生成后、仿真前**调用（对应总体方案 §3.2 前置校验第 3 步）；
- 任何一方修改（改代码变量名 / 改 io_map 绑定）都触发重查——单一源头 + 自动对账，杜绝三方漂移。

## 6. 与两端的接口契约

| 对端 | 接口 | 方向 |
|---|---|---|
| lx（PLC 侧） | `xml2st --check`（本地快速闸门）；`POST /deploy` :8600（真编译+部署，返回 deploy_result.json，errors 原样进反馈包） | 本侧调用 |
| lx（PLC 侧） | 契约文档 §3：ST 子集 / 定位变量位宽 / 显式拒绝清单 —— 生成器 Prompt 的硬约束 | 本侧遵守 |
| 仿真侧 | requirement_spec → SceneSpec 生成与 MJCF 构建；`run_sim_headless(mjcf, io_map, dll)`；`evaluate()` → verdict.json | 本侧调用 |
| 仿真侧 | acceptance 四类准则结构（csk 文档 §7.1）——需求 Schema 与判定引擎逐字对齐 | 双方共守 |
| 跨链路 | 模拟量统一 **INT @ %QW + 定点换算**（lx 链路 B）——生成器负责落码；契约 v1.1 仿真链路 scene.io_map 不再携带 scale/地址（SI 米制 range，换算归桥，float32/INT16 为共同议题） | 本侧落实 |

## 7. 实施计划（对齐总体方案 §5）

| 阶段 | 本侧工作 | 依赖 |
|---|---|---|
| 一（1–2 周） | requirement_spec Schema + acceptance 四类结构定稿，**三方接口契约冻结（联签）** | 无（最先动工） |
| 三（5–9 周） | 需求理解 Agent、PLC 生成器（Prompt + ST 模式库，种子=motion3axis）、一致性检查器 | lx 契约已冻结 ✅；仿真侧 SceneSpec Schema |
| 四（9–13 周） | 编排器串联全链路、反馈包拼装、归因路由、迭代收敛性调优 | 两端引擎打通（阶段二，各自进行） |
| 五（13–16 周） | 典型场景（分拣/顺序控制/搬运）三端联合测试与指标评估 | 全部 |

## 8. 验收指标（本侧 KPI，总体方案 §6 摘录）

- **首次生成编译通过率（matiec）≥ 80%**——生成器质量的核心指标；
- **闭环收敛率：≤6 次迭代通过全部准则 ≥ 70%**——编排与归因质量；
- **需求还原度：验收准则覆盖率 100%**（每条准则有对应自动化检查，Schema 层保证）；
- **端到端耗时：单需求输入到验证通过 ≤ 30 分钟**（两个短路闸门是主要手段）。

## 9. 待办（按优先级）

1. ~~requirement_spec JSON Schema 草案 + 三方评审冻结~~ → 草案 **v1.0.0-draft.3**（draft.2 + lx 收紧建议落实：range 完整落入有符号/无符号域之一），**评审冻结进行中**——lx ✅（建议已闭环）/ 待 csk（RFC 流程，主方案 §8.3）；
2. ~~与仿真侧确认 acceptance 四类准则的最终字段结构（以 csk §7.1 为底稿）~~ → 字段已逐字对齐其 §7.1，待其评审确认（`check_at` 冻结 "end"，扩展走 RFC）；
3. ~~PLC 生成器 v0：模式库整理 + Prompt 骨架 + xml2st 错误回喂通路联调~~ → 已完成（`src/agent/`：pipeline / patternlib / prompts；xml2st+一致性双闸门回灌；client 流式降级支持万 token 长生成；绘图仪种子已入 src/plc/）；
4. ~~一致性检查器原型（可直接复用 lx 的 `xml2st.parse()`）~~ → 已完成（`src/agent/consistency_check.py`，R1 复用 xml2st.parse；R5 io_map 腿由 ②b 产物激活，2026-09-09 按契约 v1.1 改 ioEntry 子集覆盖语义）；
5. ~~编排器骨架：先串"生成→编译闸门→部署→链路 B 验收"的半环（不含仿真侧）~~ → 半环五闸门（+闸门2b scene/R5 全腿、/status 观测、CLI 直通 ⓪→①）；仿真全环待 csk 判定引擎与 ③b 接口；
6. ~~⓪ AutomationML 解析模块（架构 v2.0 新增职责）~~ → 已完成（+ plotter3axis_station.aml：IEC 62714/CAEX 3.0 全结构参考样式）；
7. ~~②b 场景描述生成器 v0（等 csk SceneSpec Schema）~~ → 已落地确定性 v0；**2026-09-09 对齐契约 v1.1 升 v1**（contract/ 驱动：内嵌 io_map、SI range、地址分配移交 csk build-mjcf）；待 csk 闸门补 6 个 MJCF 类型注册后走真闸门回归；
8. ~~需求理解 LLM 澄清回路~~ → **对话式形态已落地**（`src/agent/chat.py`：AML+需求输入 → 规格回显（逐条准则含谓词明细）→ 用户自然语言修正（`refine()` 定向最小修改，上轮 spec 作上下文）→ 确认后自主闭环；闸门环境自动探测；`--request --confirm --seed` 可脚本化）。LLM 对粗需求的**主动反问**未接（现状：回显+人工审，弱项为 forbidden_state 等值谓词健全性——回显已明示谓词供核对）；
9. 归因分析 LLM（消费 verdict.json → report.md，区分代码/场景问题并路由）——待 csk 判定引擎；
10. plotter3axis 在线验收（需 OpenPLC 环境：run_regression.py L3 自动发现场景对）+ lx 复核代拟的 scenario 脚本后纳入场景库。
