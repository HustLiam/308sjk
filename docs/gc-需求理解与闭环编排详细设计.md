# 需求理解与闭环编排详细设计（gc 负责部分）

> 本文档是《总体实施方案》中 **① 需求理解模块**、**②a/②b 生成智能体的 LLM 本体**、**端到端闭环编排器（solve 循环、迭代管理、归因反馈的权威定义在本档 §3.2 / §4，csk 文档 §7.2–7.4 指向此处）**与**跨模块契约一致性**的详细设计，负责人：gc（智能体与闭环侧）。
>
> 三人分工全景：**PLC 执行侧（lx，见《lx-PLC代码生成与执行引擎详细设计》）** 负责代码契约与链路 B；**仿真验证侧（csk，见《csk-仿真环境与IO闭环详细设计》）** 负责 SceneSpec 规范/校验器、MJCF 构建与组件库（③b）、MuJoCo 运行时、判定引擎与链路 A 构建，兼 ②b 场景描述的**评审方**；**本侧（gc）** 负责双生成本体（②a PLC 代码 + ②b 场景描述）与闭环编排——听懂需求、生成代码与场景、判定后归因、定向重生成、管住迭代直至收敛。

---

## 0. 职责范围（TL;DR）

| 总体方案模块 | 本侧职责 | 关键产物 | 状态 |
|---|---|---|---|
| ⓪ AutomationML 解析 | IEC 62714 AML → device_model.json（设备/IO/拓扑/运动学） | `src/agent/aml_parser.py` + CLI `tools/aml_parser.py` | ✅ 完成（`schemas/device_model.schema.json` **v1.1.0-draft.1**（2026-09-15 RFC：kinematics.axes 分层 limits/defaults + io 角色绑定，rel_d=rel_{a}_d 前置命名）+ 双示例：motion3axis_station + **plotter3axis_station（CAEX 3.0 全结构新参考样式**：InstanceHierarchy+三类类库、信号方向挂 PLC 通道、InternalLink=电气接线）；确定性解析 + io_list 预填契约测试 ×2） |
| ① 需求理解模块 | 自然语言 → 结构化需求规格；**requirement_spec.json Schema 的定义权** | `requirement_spec.json` + JSON Schema | 🟨 Schema **v1.0.0-draft.3**（draft.2 + lx 收紧建议落实：range 完整落入有符号/无符号域之一），待 csk 评审后冻结；**模块 v0 已落地**（`src/agent/requirement.py`：LLM 模式 io_list 逐字锚定预填+修复回路 / 离线模板模式 / pending 澄清；绘图仪 spec 由 glm-5.3 一轮产出+人工介入点 1 修正后冻结 `examples/specs/plotter3axis.spec.json`）；LLM 多轮澄清未接 |
| ②a/②b 的 LLM 生成本体 | PLCopen XML 生成器（在 lx 契约上）、失败归因分析 LLM | 生成器 Prompt 工程 + ST 模式库 | 🟨 生成器 v0 已实现（`src/agent/pipeline.py` + `patternlib.py` + `prompts/plcgen_skill.md`，种子=motion3axis+plotter3axis，xml2st+一致性双闸门回灌；client 流式降级支持万 token 长生成）；**绘图仪种子已落地**（`src/plc/plotter3axis.xml`：CSP 栈逐字复用 + INTERP_Z 笔轴 + 9 步绘图序列器 + 笔互锁，双闸门绿）；归因 LLM 未启动 |
| 闭环编排器 | solve 循环、两个编译/校验短路、迭代记忆、终止与 best-effort、反馈包拼装与路由 | `orchestrator/` | 🟨 半环五闸门（`src/agent/orchestrator.py`：生成→xml2st→一致性→**scene（②b 产物自检 + R5 检查）**→部署可选→链路 B 验收可选；/status 观测仅记录不裁定；CLI `--aml/--request` 直通 ⓪→①；runs/plotter3axis_demo final 冻结）；仿真全环等 csk 接口 |
| ②b 场景描述生成 | spec → **scene.spec.json（内嵌 io_map，契约 v1.1）**（csk 分支 f39debd 收窄：SceneSpec JSON 生成归 gc） | `src/agent/scene_gen.py` | ✅ **v1 契约对齐已落地**（确定性生成：组件注册表运行时加载 `contract/components.v*.json`；io_map 只含可绑物理通道（fb→pos/cmd→cmd，range SI 米制）；地址分配移交 csk build-mjcf；自检 V0~V4 含路由覆盖；编排器闸门2b 消费——**受阻项：csk master REGISTRY 缺 6 个 MJCF 类型，见看板共同议题**；LLM 布局创意后续仅限 pose/params） |
| 跨模块一致性 | io_list 单一源头的落地：**三方一致性检查器**（定位变量 ≡ io_list ≡ io_map；轴参数 ≡ AML） | `consistency_check.py` | ✅ 完成（R1~R8（**R8**=轴参数三方比对 AML≡XML≡scene.spec，2026-09-15 RFC 落地，v3 形态 XML 记 SKIP）；R5 检查按契约 v1.1 改子集覆盖语义——io_map ⊆ io_list（ioEntry 对账 bool↔BOOL/float↔INT，bind={asset,quantity}），反向覆盖由 ②b 路由覆盖自检保证；io_map 数据由 ②b 产物供给，每轮闸门2b 执行） |

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
2. 多轮澄清协议：规格回显 → 用户确认或修正 → 才进入生成阶段（**人工介入点 1/2**：环前确认、环后人工复核）；
3. `acceptance` 每条准则必须带 `id / desc / type` 及类型专属字段，Schema 校验不过直接退回；
4. 时间类阈值强制 ≥100ms（通信时序约束，写入 Schema 校验规则）。

## 3. 生成智能体

### 3.1 PLC 代码生成器（② 的 LLM 本体）

- **输入**：requirement_spec（task_goal/io_list/constraints）+ 反馈包（迭代时）+ 知识库；
- **输出**：`plc_project.xml`（IEC 61131-10，唯一交付格式）——必须落在 lx 的 xml2st 契约内（ST 本体子集、定位变量 + 位宽契约、显式拒绝清单，见其文档 §3）；
- **知识资产（本侧维护）**：ST 代码模式库（顺序控制、状态机、PID、联锁——六个已验收场景 XML 是首批种子（含 PID 模式））、PLCopen XML 模板、历史项目片段（RAG）；
- **质量目标**：首次生成编译通过率 ≥80%（matiec 前置拦截 + lx 校验器错误文本定向反馈是达标的手段）。

### 3.2 归因分析 LLM（消费判定结果）——✅ v0 已落地

2026-10-20补充：故障知识库不再以纯数字独立匹配，避免路点号误命中编译类型错误。失败上下文包含当前ST（上限24000字符）、未通过判据和各映射信号首值/末值/变化次数；诊断上下文总长上限30000字符，取代原先400字符截断。该改进增加可用证据，不保证LLM必能定位所有控制流问题；画圆恢复实验的根因来自助手源码审查。

2026-10-20后续通用修订：在线行为失败即使命中知识库，也结合工程证据诊断；知识库只作为待验证假设。ST上限提高至48000字符并标记截断，诊断输入上限90000字符；补充规格、计划、未覆盖判据、信号极值和扫描顺序词法检查的行号。诊断记录类别、依据、修复建议和必须保持的行为，不改变确定性结果。`event_delay`支持明确选择first/last真实事件；数值条件支持阈值和设备poswin范围内的容差。用户批准派生状态最多40ms响应，须显式声明purpose、response_s和传感器response_source，从源条件变化计时；安全互锁保持零时延。生成回复触及输出上限时拒绝截断XML，后续允许的重试降低推理强度，不增加调用次数或token上限。实现边界和固定版本20项测试见[通用诊断与批量验收](lx-PLC工程通用诊断与批量验收.md)。

> **落地状态（2026-09-07）**：`src/agent/attribution.py`——**确定性故障库优先、
> 故障库未命中由 LLM 补充诊断**两层：错误文本先签匹配 `knowledge/pitfalls.json`（14 条：lx 避坑
> 1~8 + 联调新增 9~14）与情景记忆修复对（`memory.py`，跨会话）；未命中才 LLM
> 小上下文诊断（输出标 advisory）。红线不变：归因只进反馈包（`_pack_feedback`
> 增强）与 gate.json 留档，**不改变任何闸门裁定**。编排器 7 个失败点统一走
> `_fail()`；final+在线验收 ok 触发 `_consolidate()` 知识入库（修复对 +
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
> pose/params——io_map 骨架要过三方对账（契约③），不交给概率性组件。

## 4. 端到端编排器（solve 循环的权威定义——闭环的核心实现）

> 2026-10-19 严格闭环入口增补：`src/agent/closed_loop.py` 使用真实模型做①需求理解和②a完整 XML，`PLCGenerator` 默认 full_xml，混合形态仅显式选择；`solve(require_online=True)` 要求场景、真部署和在线验收通过后才冻结。模型调用与各轮错误留档，未以种子替代模型。详见《lx-完整XML生成闭环运行指南》。

> 同日自动化更新：①之后新增 LLM 测试规划。图形入口原样传递用户请求，不再追加正方形几何；规划器从规格引用控制信号与轴反馈，输出启动/激励/采样/完成/清理及路径检查，校验后传给 XML 生成器和验收执行器。无效计划最多三次规划修复，不通过时停止；有效计划在全部 PLC 修复轮次保持不变。通过条件按本任务 acceptance、计划路径和 EXECUTION 动态核对，并核对最终计划哈希。

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
  requirement_spec.json        # 结构化需求规格（request 原文已并入 task_goal）
  iter_001/
    plcopen.xml  plc.st        # 生成的代码（XML 唯一源码，.st 为转换产物）
    scene.spec.json  scene.usda  io_map.json
    build/plc_logic.dll        # 链路 A 编译产物
    trace.parquet  events.json  exit.json
    verdict.json  report.md    # 判定结果 + LLM 归因报告
  iter_002/ ...
  final/ -> 通过轮次的快照      # 成功后冻结
```

- **终止**：以上为旧接口的概念流程。现役 `closed_loop.py` 已先做 OpenPLC 真编译及 MuJoCo 验收，全部通过后才生成 `final/`。携带 `test_plan` 的新入口按下面§4.1选择最佳版本；不再单凭通过项数量或最近一次静态检查选择下一轮源码。无测试计划的旧接口保留兼容。

### 4.1 保留最佳版本，拒绝退化修复（2026-10-19）

目的：避免一个能完成任务但尚有局部错误的版本，被无法启动的新稿替换。实现为 `src/agent/progress.py`，由 `solve(test_plan=...)` 调用；不引入新的Agent SDK，也不改变完整XML由LLM生成的要求。

这里的“单调改善”指**被保留的最佳版本序列**：在同一规格、计划、场景和判定器下，新版本不能丢失已实际验证的能力，且至少有一项可靠推进，才替换最佳版。LLM提出的候选仍可能变差；系统负责识别并拒绝替换。该规则只覆盖已经采集和验证的行为，不构成对控制程序所有可能状态的数学证明。

2026-10-20补充：同一测试条件还包括编译配置。每轮保存 `compiler.json`，比较镜像和编译脚本哈希，安装状态configured/verified不作为差异；编译软件改变须另建实验。部署前为匹配本机HTTP端口的OpenPLC Docker实例旁路已证实会破坏CASE分支的st_optimizer，原ST仍直接交给matiec。无法识别的本地编译脚本返回configuration_error，停止PLC重写；远端配置标注未检查。验收归因过滤成功身份确认、准备动作和PASS日志，有限上下文优先失败证据。

**版本与修复来源**

每轮有候选版本和当前最佳版本两个独立概念。第一份通过静态检查的XML可成为初始修复基底，随后按真实编译与物理证据选择。每个获采纳版本保存至不可变的 `checkpoints/iter_NNN/`，完整复制后才原子替换 `best.json` 指针。`best/` 是便于浏览的副本，下一轮读取检查点源码。中途文件复制失败，旧指针与旧源码仍可恢复。

候选未获采纳时，下一轮继续从最佳版修改，不能把失败候选当成源码。反馈同时给出最佳版的失败项、最近候选的退化原因和来源轮次，避免拿候选的报错行号直接修最佳版。

**比较规则**

采用逐项约束，不使用一个加权总分抵消能力退步：

| 比较项 | 不允许的退步 | 可接受的推进 |
|---|---|---|
| 检查阶段 | 已能编译的程序变成编译失败 | 通过更后面的检查阶段 |
| 实际通过的判据 | 已通过项变为失败、缺失或未触发 | 增加实际验证通过的判据 |
| 完成与清理 | 已能完成并清理，变为超时或清理失败 | 从未完成变为完成且清理成功 |
| 物理路径 | 任一检查的有序路点进度减少 | 在不损失其他能力时到达更多路点 |
| 未通过的线段 | 已量出的有限偏差增大，或变成无法判定 | 偏差降低，最终达到规定范围 |
| 联锁违规 | 违规比例或违规样本增加，或原触发条件消失 | 以触发条件实际出现的样本为分母，违规比例和违规样本同时降低 |

已经达标的连续量按达标处理，不要求每次测到的秒数或线段偏差都更小。不能用运行时间缩短、触发条件消失或减少动作，来制造“违规样本更少”的进步；也不能靠增加无违规等待稀释违规比例。`forbidden_state` 的前提没有发生时，记录为未观察到，不计入已验证能力；严格最终交付要求所有验收前提有实际覆盖。

先检查全部禁止退步项，再检查是否至少一项推进。相同质量的新稿不替换旧版；连续两份候选未获采纳就停止自动重写，输出当前最佳版和未解决问题，不继续随机重生成。总轮数默认仍为6。

**证据与测试条件**

比较使用本轮 `trace.json`、`verdict.json`、`execution.json`，并核对XML、规格、计划、场景及IO映射哈希。严格模式重新对原始trace执行判定，不能只相信日志中的PASS。规格与计划保存任务开始时的副本；修改容差、场景参数或判定器后，旧结果不再可直接比较，应建立新实验记录。INT联锁值按实际整数比较，不得先转换成BOOL。

选择结果分别为 `promote`（采纳）、`retain`（保留原版）、`reject`（退化）、`incomparable`（测试条件改变或证据不足）。每轮存 `progress.json`；任务存 `progress_history.json`、`best.json` 和 `summary.md` 比较表。候选证据不删除或改写。

**限定本轮修复目标**

生成前保存 `repair_contract.json`，包含基底轮次、源码哈希、本轮首个失败目标、必须保留的已通过项、完成能力和路点进度。模型一次处理一个目标及必要关联修改，仍输出完整XML。范围约束属于生成指导，真正的采纳依据是后续重新编译和验证结果；不把模型“声称最小修改”当作证明。

**配置、环境和接口故障**

依靠非绘图轴的位置切换工艺状态时，如果相邻切换路点在绘图平面上相同，而其他变化轴的位置间距均不大于两倍设备poswin，就以 `PlanFeasibilityError` 在生成PLC前停止。先修正设备、规格与计划，不要求模型通过振荡控制或放宽容差绕过。默认AML已去除“1m行程／1抬笔”的错误描述，明确Z为0..10mm。

此检查只识别当前能确定的误差余量冲突，不代替完整的物理可达性分析。未知根因仍须保留证据待查。已识别的仿真/Modbus环境故障继续单独中止，不当作PLC修复目标。

HTTP401/402分别在需求理解、测试规划及XML修复阶段记录 `api_blocked`，保留现有最佳版和 `api_error.json`，停止重试。同一个模型客户端记录阻断状态，后续调用不再发送HTTP；更新凭据或充值后需新进程。API错误不学习成PLC故障经验。连续无进展停止后不再追加LLM知识提炼调用。

**六边形失败记录的离线验证**

以 `studio_20261019_173003_e4ffc4` 的原始证据重放选择决策：第1轮建立静态基底；第2轮能完成且9个路点到达，替换最佳版；第3、4轮未完成、路点降至0，即使违规次数下降也均拒绝，最佳版保持第2轮。按新版预检，这份原计划因抬/落间距1、poswin1会更早停止。结果见 `workspace/monotonic_hexagon_audit_20261019/audit.json`，模型调用与部署均为0。

**实现边界与后续验证**

拒绝候选后的“回退”指后续修复来源和交付选择，不表示已自动把PLC运行时重部署为最佳版；候选验收结束仍执行输入清理。`best` 可能尚有未通过项，不能作为 `final` 使用。当前CLI不提供无检查的断点恢复；更改设备/计划必须另建任务并重新验证。当前比较已检查本地执行/判定源码与场景，但尚未覆盖所有外部服务的版本漂移。物理噪声可能造成保守拒绝，后续如增加重复验证，必须预先确定重复次数和比较规则，不能挑选最好一次。API恢复后仍需做新模型生成与物理试验，离线选择验证不能替代这一步。

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
- R8（2026-09-15 RFC 落地）：**轴参数三方比对**——AML axis_objects ≡ XML 轴实例参数（POSWIN=INTERP 类型初值 / MC 调用动力学字面量=defaults / 行程=MC 越程界限，多轴行程不一记 SKIP 待 lx 参数化）≡ scene.spec 轴参数（travel=stroke×scale、speed=vmax×scale）；v3 形态 XML（INTERP 带 VMAX）记 SKIP 待种子升 v4.0；闸门2b 传 scene 时增比对 scene 侧；
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

1. ~~requirement_spec JSON Schema 草案 + 三方评审冻结~~ → 草案 **v1.0.0-draft.3**（draft.2 + lx 收紧建议落实：range 完整落入有符号/无符号域之一），**评审冻结进行中**——lx ✅（建议已落实）/ 待 csk（RFC 流程，主方案 §8.3）；
2. ~~与仿真侧确认 acceptance 四类准则的最终字段结构（以 csk §7.1 为底稿）~~ → 字段已逐字对齐其 §7.1，待其评审确认（`check_at` 固定取 "end"，扩展走 RFC）；
3. ~~PLC 生成器 v0：模式库整理 + Prompt 骨架 + xml2st 错误回喂通路联调~~ → 已完成（`src/agent/`：pipeline / patternlib / prompts；xml2st+一致性双闸门回灌；client 流式降级支持万 token 长生成；绘图仪种子已入 src/plc/）；
4. ~~一致性检查器原型（可直接复用 lx 的 `xml2st.parse()`）~~ → 已完成（`src/agent/consistency_check.py`，R1 复用 xml2st.parse；R5 io_map 检查由 ②b 产物激活，2026-09-09 按契约 v1.1 改 ioEntry 子集覆盖语义）；
5. ~~编排器骨架：先串"生成→编译闸门→部署→链路 B 验收"的半环（不含仿真侧）~~ → 半环五闸门（+闸门2b scene/R5 全项检查、/status 观测、CLI 直通 ⓪→①）；仿真全环待 csk 判定引擎与 ③b 接口；
6. ~~⓪ AutomationML 解析模块（架构 v2.0 新增职责）~~ → 已完成（+ plotter3axis_station.aml：IEC 62714/CAEX 3.0 全结构参考样式）；
7. ~~②b 场景描述生成器 v0（等 csk SceneSpec Schema）~~ → 已落地确定性 v0；**2026-09-09 对齐契约 v1.1 升 v1**（contract/ 驱动：内嵌 io_map、SI range、地址分配移交 csk build-mjcf）；待 csk 闸门补 6 个 MJCF 类型注册后走真闸门回归；
8. ~~需求理解 LLM 澄清回路~~ → **对话式形态已落地**（`src/agent/chat.py`：AML+需求输入 → 规格回显（逐条准则含谓词明细）→ 用户自然语言修正（`refine()` 定向最小修改，上轮 spec 作上下文）→ 确认后自主跑完剩余流程（生成→闸门→验收）；闸门环境自动探测；`--request --confirm --seed` 可脚本化）。LLM 对粗需求的**主动反问**未接（现状：回显+人工审，弱项为 forbidden_state 等值谓词健全性——回显已明示谓词供核对）；
9. 归因分析 LLM（消费 verdict.json → report.md，区分代码/场景问题并路由）——待 csk 判定引擎；
10. plotter3axis 在线验收（需 OpenPLC 环境：run_regression.py L3 自动发现场景对）+ lx 复核代拟的 scenario 脚本后纳入场景库；
11. **轴对象映射绑定 RFC 实施中**（device_model Schema v1.1.0-draft.1；lx 2026-09-15 评审通过，三处修订并入生成方案 §6）——**步 1/2/3/4 已落地**（⓪ 分层+io 绑定 / ②a 展开器 axis_expand.py+模板，基准 1 字节级等价 / ②b 改读 limits / 一致性 R8，见 changelog「轴对象绑定 v1」）；**剩步 5**：plotter3axis 种子升 v4.0 + 等价基准 2（lx 配合）；后续把展开器接入生成主路（LLM 只写工艺层 process_body）。

## 10. RFC：轴对象映射绑定（device_model Schema v1.1）——gc 评估与提案（2026-09-15）

> 提案来源：lx 2026-09-09 看板发起、2026-09-15 催办（超 48h 规则）；规范全文在《运动控制代码生成方案》§6（lx 权威），本节不复写，只写 gc 侧的**评估结论、修订意见、迁移方案**。目的：轴参数只在 AML 定义一次——②a 与 ②b 消费 ⓪ 的确定性产物 `kinematics.axes`（axis_objects），一致性检查器比对三方，LLM 只做工艺层（序列/互锁/目标值），不生成也不改写轴参数。

### 10.1 动机

现行轴参数存在三个来源：AML（⓪ 提取，供 ②b 消费）、种子 XML（②a 逐字复用）、skill 骨架（生成 Prompt）。三处数值今天一致（motion3axis：vmax=40.0/accel=80.0/POSWIN=2.0），但一致靠人工维护——skill 骨架就曾落后 master 一个版本（v3.0 vs v4.0，靠指纹单测 c4f27ef 才关住）。本 RFC 把 AML 定为唯一来源：②a 的轴相关生成改为确定性展开（不再依赖 LLM 复用种子），②b 与一致性检查从同一份 axis_objects 取数。

### 10.2 影响面（四项逐项评估，均可接入）

评估基于 2026-09-15 对 master（bb0d5dd）的实际核验：

1. **device_model v1.1（io 绑定 + limits/defaults 分层）——可接入，纯增量**。`aml_parser._extract_axes()`（src/agent/aml_parser.py:243）现产出平铺 vmax/accel/poswin/wrap/stroke，升级为 `limits{vmax,accel}` + `defaults{velocity,acceleration,deceleration}` + `io{fb,sp,sw,v,rel_d,err_id}` 角色绑定。实跑解析器核验两份基准 AML：motion3axis_station 32 点、三轴六角色齐全（fb/sp/sw/v/rel/err_id）；plotter3axis_station 26 点、含 fb/sp/sw/v，rel_d 与 err_id 缺省——按 §6.1 属可选角色，省略合法（展开器不生成 MC_MOVERELATIVE 接线与 ErrorID 诊断出口），与 plotter3axis.xml 现状（无 rel/err_id 定位变量）一致。
2. **②a 按轴对象确定性展开——可接入，工作量最大的一项**。前置已具备：skill 骨架 v4.0 + 指纹防漂移单测（tests/test_skill_skeleton_sync.py，c4f27ef）。数值核验：motion3axis.xml 的 MC 调用动力学 Velocity=40.0/Acceleration=80.0/Deceleration=80.0 与 AML vmax/accel 吻合，defaults 层提案值即现行接线值；INTERP 的 POSWIN 是带初值的局部变量（2.0 ≡ AML poswin，motion3axis.xml:75），初值参数化机械可行。展开器为新增模块（axis_objects → FB 库 POU + 每轴实例组/接线/参数），不改动 LLM 生成工艺层的路径。
3. **②b 组件引用——可接入，改动小**。`scene_gen._axes_info()`（src/agent/scene_gen.py:118）已消费 kinematics.axes 的 stroke/unit/vmax；v1.1 后改读 `limits.vmax`，通道名改从 `io` 绑定取（替代 `_AXIS_RE` 正则推断轴名）。路由规则 `<axis>_fb→pos / <axis>_cmd→cmd` 与 io_map 结构不变。
4. **一致性新增轴参数比对——可接入，建议编号 R8**。R7 已被本侧 CASE 初值提案占用（待 lx 评审）。比对三方：AML axis_objects ≡ XML 轴实例参数（POSWIN 初值 / MC 调用动力学 / 越程界限）≡ scene.spec 轴参数（travel/speed）。device_model 为可选入参（与 R6 同法），缺模型时跳过该检查。

csk 侧零改动：契约包 v1.1 组件参数（linear_axis 的 stroke/speed）现成，②b 消费点全部在 gc 代码内。

### 10.3 对《运动控制代码生成方案》§6 的三处修订意见

1. **rel_d 推断模式应改 `rel_{a}_d`**。§6.1 现文写 `{a}_rel_d`，但已验收 v4.0 的实际命名是 rel 前置：motion3axis.xml 定位变量 `rel_x_d/rel_y_d/rel_z_d`（%QW3~5），AML 通道同名。按原文模式 motion3axis 推断不到 rel_d 角色 → MC_MOVERELATIVE 不展开 → §6.3 等价基准 1 必然失败。
2. **limits 的消费点需按 v4.0 重定义**。§6.2 现文"limits→INTERP 执行上限"，但 v4.0 INTERP 已无 VMAX/MAXPOS 参数（命令级动力学 vel/acc/dec_req，越程拦截改在 MC 层报 ErrorID=1；skill v4.0 骨架同形态）。建议机械消费定义为：展开期校验 defaults ≤ limits（违反记 problems、拒绝生成）+ defaults 作为 MC 调用动力学的来源；INTERP 侧不接线，后续若恢复保护限幅再接。
3. **INTERP 体内越程界限现为字面常量，需参数化**。motion3axis.xml 的 INTERP ST 体内写死 `pos_target > 100 OR pos_target < 0`（MC 层拦截之外的第二道防线）。行程非 [0,100] 的轴（如 plotter z）无法逐字复用；建议改带初值局部变量（与 POSWIN 同法），属 lx 模板域小改，请确认。

另有一条范围界定，建议写进 §6.3：**等价比对限定轴对象派生部分**（FB 库 POU 逐字 + 每轴实例组的参数与接线逐字段）；PLC_PRG 工艺层（序列器/互锁/目标表）不属轴对象派生，由种子提供，不进比对——否则实现时对"全 XML 等价"与"轴部分等价"会产生歧义。

### 10.4 迁移方案与排期

评审通过后按 §6.4 顺序执行，预估 5~6 个工作日：

| 步 | 内容 | 预估 |
|---|---|---|
| 1 | ⓪ 升级：Schema 升 v1.1.0-draft.1（io 绑定按 `rel_{a}_d` 修订版）+ aml_parser 分层与角色推断 + 单测 + 双 AML 基准核验 | 1 天 |
| 2 | ②a 轴对象展开器 + motion3axis 等价契约测试（基准 1） | 2~3 天 |
| 3 | ②b 消费点：`_axes_info` 改读 limits.vmax + 通道名取自 io 绑定 | 0.5 天 |
| 4 | R8 三方轴参数比对（含负测试，缺 model 跳过） | 1 天 |
| 5 | plotter3axis 种子升 v4.0 总线形态（现行仍 v3 形态 INTERP，devlog 2026-09-10 已登记为遗留）+ 等价契约测试（基准 2） | 0.5~1 天，可与步 2 并行 |

**实施进展（2026-09-15）**：lx 评审通过（三处修订并入生成方案 §6，R8 编号确认）后当日落地步 1~4——Schema v1.1.0-draft.1 + aml_parser 分层与角色绑定；②a 展开器 `src/agent/axis_expand.py` + FB 库模板 `templates/motion_stack_v4.xml`，**基准 1 实测字节级等价**（强于 §6.3 树级"逐字段等价"）；②b/一致性 R8 同批。剩步 5（plotter 种子升 v4.0 + 基准 2，lx 配合）与"展开器接入生成主路"。

版本与合入：device_model Schema `1.0.0-draft.1 → 1.1.0-draft.1`（评审通过后定 1.1.0）。aml_parser 是 device_model 唯一生产者，同批升级、不留旧字段兼容读；`runs/` 历史产物不回填。合入批次按 RFC 流程一次做完：改契约 + 本档 §5 增 R8 + lx 生成方案 §6 修订 + 主方案 §3.0 一句 + changelog 登记。

### 10.5 评审请求

- **@lx**：确认 §10.3 三处修订（rel_d 模式 / limits 消费点 / INTERP 界限参数化）与 R8 编号；§6.2 ②a 行的模板依据（§2.2）随展开器实施如需细化由双方在看板迭代。
- **@csk**：知悉——scene.spec 轴参数此后与 PLC 侧同源于 device_model，你侧闸门的组件参数校验不受影响；如无异议即视为同意。
- 48h 未回复按指南 §6 线下拉齐。评审通过前 gc 不实施。


## 11. 主动澄清、轨迹预览与故障回放（2026-10-19）

### 11.1 生成前主动澄清

`clarification.py` 在需求规格生成前识别缺项和歧义。现阶段覆盖单个正方形/圆形的形状、中心、尺寸、圆分段数；多图形意图需先选定本次图形；过于笼统的设备指令会要求补充动作、启动条件与完成状态。先读取显式参数，不重复追问已知值；已有尺寸默认值时展示默认值并记录来源。数值必须有限、尺寸为正、段数为 8..360 的整数；路点同时检查站规划域和 AML 行程。物理长度与设备百分比坐标不一致时，必须提供换算后的设备坐标，不能直接复用数字。

交互入口为 `python -m src.agent.chat`，回答问题后先保存预览，再回显规格供修正。`--confirm` 不等待终端输入：可默认的参数明确留档；无法默认的歧义返回 `needs_clarification` 和问题列表，退出码 2，不进入代码生成。`--answers <JSON>` 提供预先回答，支持 shape/center/size/radius/segments/coordinate_values/process。`RequirementUnderstander.clarify()` 提供只分析问题的接口。规格修正时会同步重新规划并更新预览，避免仍使用上一版坐标。

### 11.2 轨迹预览

`visualization.py` 将最终确认的权威步表展开为独立 HTML（内嵌 SVG/JS，无外部依赖）。展示 XY 落笔实线、抬落笔虚线、参数及来源、XYZ 步表；支持拖动进度、播放/暂停与速度调整。仅展示已知目标之间的运动，不猜测开场位置；XY 路程与 Z 升降分别计量。圆的末段明确闭合到起点后再抬笔。预览不执行 PLC，也不构成验收结论。

命令示例：

```bash
python -m src.agent.chat --request "画一个圆，半径20" --preview-only --confirm
python -m src.agent.chat --request "画一个正方形" --preview-only --open-preview
```

任务目录新增 `clarifications.json`、`trajectory.json`、`trajectory-preview.html`；均为对话/观测附加产物，不改变四份冻结契约。chat 默认生成独立 task_id，显式传 `--task-id` 可指定目录；`--runs-root` 支持改变任务产物根目录。编排器的 `--aml/--request` 路径复用同一澄清与预览实现。

### 11.3 离线故障回放

`replay.py` 消费已有 `runs/<task>/iter_NNN/`，不连接 PLC、不调用模型、不重新判卷。`replay.html` 展示各轮失败阶段、原始报错、已存归因与经验、完整验收日志、逐条 verdict，以及相邻轮次 ST 差异。可选择变量、拖动采样时间、调整播放速度；有 x_fb/y_fb 时展示实际轨迹并叠加保存的规划。支持 trace.json（数组或 samples/trace 容器）、JSONL、CSV；旧 gate 中的诊断文本可恢复已有相对时间线。没有采样的运行只展示日志，缺失通道不补造；跳过在线闸门的结果明确标注。

```bash
python -m src.agent.replay runs/plotter_cell --open
python -m src.agent.chat --replay runs/plotter_cell
```

每轮失败和成功均自动刷新任务根目录回放。验收子进程通过 `AGENT_EVIDENCE_DIR` 接收当前轮目录，编排器保存 acceptance.log；CSP 场景脚本附加保存 trace.json/verdict.json，判定器与 acceptance 字段不变。历史运行只能回放已保存的信息；采样时间不等于故障发生的精确时刻。未来扩展其他场景时可复用 evidence.save_acceptance_evidence。

### 11.4 验证与当前边界

新增交互测试覆盖必需歧义、默认值来源、无效数值/越程、单位不一致、脚本模式无输入、HTML 注入隔离、历史无采样、诊断时间线、CSV、逐条判定、ST 差异，以及验收子进程到回放的完整证据链。当前绘图在线脚本仍按原站标准几何判定；自定义预览不代表已有对应的参数化在线验收。OpenPLC VM 不可达时，仅完成离线功能和模拟验收子进程的端到端验证，不宣称新一轮物理在线验证通过。

## 12. SVG 路径输入（2026-10-19）

### 12.1 输入范围与规划

`svg_input.py` 用标准库解析静态 SVG，确定性地产生与原轨迹规划共用的步表。支持绝对/相对 `M/L/H/V/C/S/Q/T/A/Z`、重复参数与多个子路径；支持 line/polyline/polygon/rect（含圆角）/circle/ellipse；支持分组及 matrix/translate/scale/rotate/skewX/skewY 变换。基础形状的绝对长度支持 px/mm/cm/in/pt/pc/q，根 viewBox 和 preserveAspectRatio=none 的长宽比会参与几何计算。

适配以变换后的曲线解析极值为边界，按指定宽高框等比缩放到 AML 设备坐标；默认中心为 X/Y 行程中点、框宽高各为行程的 60%。SVG 的像素或毫米不直接等于设备坐标。X/Y 单位须一致；默认翻转 Y，以在设备 Y 向上的预览中保持 SVG 朝向，`--svg-keep-y` 可保留原 Y 方向。

贝塞尔使用自适应细分，椭圆弧按变换后的误差上界离散；`--svg-tolerance` 默认为 0.25 个设备坐标。之后按 PLC INT 分辨率量化并合并连续同点；预览显示离散容差和最大 XY 量化误差，两者不代表运行时跟踪精度。每个子路径依次执行抬笔定位、落笔、`polyline_sweep` 路点循环、抬笔，最后抬笔回中心。开放路径不自动闭合，不同路径之间不连落笔线；INT 量化后退化的独立路径明确拒绝。

### 12.2 使用与接线

```bash
python -m src.agent.chat --svg examples/svg/house.svg --preview-only --confirm --open-preview
python -m src.agent.chat --svg drawing.svg --svg-center 50 50 --svg-width 60 --svg-height 40 --svg-tolerance .3 --svg-max-points 1200 --confirm
python -m src.agent.orchestrator --aml examples/aml/plotter3axis_station.aml --svg examples/svg/house.svg --task-id svg_house --no-scene
```

chat 的默认设备为本站绘图仪 AML；也可指定 `--aml`。编排器支持 `--aml/--svg`，或冻结 spec 文件加 `--svg`（`--aml` 此时仅注入设备模型）。配置模型后按现有生成和修复流程消费完整路点表；禁止 `--svg` 与直接复制旧程序的 `--seed` 合用。`--request` 可补充启动、联锁、完成条件等工艺要求；轮廓以 SVG 为准，尺寸以 SVG 参数为准。交互规格修正可调整“宽度30，高度40，中心(40,45)，容差0.3”；改图形应修改 SVG 源文件后重导入。

`--answers` 可提供 `{"svg":{"center":[50,50],"width":60,"height":40,"tolerance":0.25,"max_points":1200,"flip_y":true}}`，覆盖同名命令行设置。`--runs-root` 控制产物位置。除 §11 附加产物外，任务根目录保存原始 `input.svg`；trajectory.source 记录绝对源路径、SHA-256、字节数与源边界。归档前校验文件未被修改；原文件不会直接嵌入 HTML。规划和预览不需要模型或在线 PLC。

### 12.3 输入限制与验证边界

最多 2 MiB、64 层分组、20000 个几何段；展开预算默认 1200 点，可设 2..4096。超预算时拒绝，要求简化文件或调整容差，不截断图形；所有路点检查 AML 行程、INT 域和可表示的 Z 抬落笔高度。速度不超过 AML limits.vmax。

按几何轮廓描画，忽略填充、颜色、笔画宽度和虚线样式；不生成填充扫描线。defs 不作为独立轮廓绘制，隐藏路径跳过。可见 text/image/use、CSS 样式表或 CSS transform、裁剪/蒙版/滤镜、嵌套 viewport 等明确拒绝，需先转换为展开的普通路径；不解析 DTD/实体或执行脚本。

SVG 输入不改变四份冻结契约，不增加标准 FB；`polyline_sweep` 仅为生成提示中的工艺循环。既有闸门顺序与判定语义保持不变，离线回放复用已保存轨迹。当前标准画方/画圆脚本不验证任意 SVG；本次覆盖离线规划、源文件归档、生成提示接线与浏览器交互，尚无任意 SVG 的在线几何验收实证。

## 13. 浏览器 SVG 绘图工作台（2026-10-19）

### 13.1 用户流程

`python -m src.agent.web --open` 启动本地服务并打开 `http://127.0.0.1:8765`。前端在 `src/agent/web_ui/`，原生 HTML/CSS/JS，无 Node 构建或新增依赖。它与 §11 的静态预览不同：上传、选择、对话、生成依赖 Python 后端，使用时应保持服务运行。`--port` 可改端口，`--aml` 指定设备模型，`--max-iters` 控制闭环预算（1..20，默认 6）；默认 `--runs-root E:\chagpt\308sjk-svg-workbench`。

页面上传/拖入 SVG 后，显示解析后的轮廓；点击画布或勾选列表切换独立子路径，支持全选、清空、反选。选择粒度为连续子路径，编号在当前文件内稳定，按原文档顺序绘制；不是任意裁剪一条曲线中的局部线段。取消某路径时，剩余路径仍按全图边界适配，位置、尺寸与相对关系不变。画布使用源图朝向，轨迹预览使用设备坐标与 INT 路点，后者包含抬落笔。

参数框可调整适配宽高、中心、容差、点数预算与 Y 朝向；点击“更新轨迹”保存并刷新预览。对话区可输入工艺要求或“宽度40，中心(50,50)”等修改；几何先确定性更新，再用已有 RequirementUnderstander 理解/修正规格，IO 仍锚定 AML。规格显示后用户点击“生成 PLC 代码”，后台调用现有 PLCGenerator + Orchestrator。生成阶段不使用种子复制，不注入模式卡；传入完整选中路点表，复用生成/修复及静态、场景一致性闸门。网页当前固定 deploy=False、acceptance=None，不自动连接运行时。

### 13.2 状态、产物与一致性

`web.py` 使用标准库 ThreadingHTTPServer 和单后台执行器；HTTP 请求及时返回，页面每秒轮询忙碌任务。`/api/import`、`/api/plan`、`/api/chat`、`/api/generate` 分别处理导入、规划、对话和生成，`/api/state` 读取状态。一次只执行一个模型任务；同一会话忙碌时禁止修改输入。页面显示真实进度回调、阶段、规格和文件链接，报错后解除忙碌状态并保留消息。

会话保存于 `sessions/<随机id>/`（session.json、draft 预览、资产引用；旧版 source.svg 升级保留），资产内容按校验值保存于 `assets/<类型>/<SHA-256>/`。浏览器记录会话标识，刷新可恢复选择、参数、资产与进度；服务重启后未完成任务标为中断，不自动恢复模型调用。每次生成分配独立 `svg_web_<随机id>` 目录，冻结当前规格、路点、源 SVG 与 web-request.json（会话/版本/选区/参数/工艺文本）。后续修改不会覆盖此前任务。结果区仅展示最新任务成功时的 `final/scene.spec.json`（内含 `io_map`）和 `final/plcopen.xml`，不显示任务编号/状态行；失败或运行中不展示旧任务文件。历史日志、中间稿与资产快照仍保留在磁盘，旧任务下载列表也按此规则过滤，不单独导出 io_map。

每次输入修改递增 revision；旧页面请求返回 409。修改选区或参数会清空旧规格，必须重新与 Agent 整理需求；尚未应用的参数修改也会禁用生成按钮。空选区保存为不可生成状态。源文件 SHA 校验仍在生成快照归档前执行。CLI 同时支持 `--svg-paths 1 3`；answers.svg.selected_paths 可指定编号，交互尺寸修正保留选区。该字段仅属于附加 trajectory 数据，不改变冻结 requirement_spec/PLCopen/io_map/acceptance 契约。

### 13.3 本地服务与验证边界

服务仅绑定 127.0.0.1；校验 Host/Origin 和页面令牌，API Key 仅由 Python 读取，不发到浏览器。上传 SVG 仍经 §12 的解析器；前端只用点列创建 SVG 元素，不插入原文件 DOM。源文件下载按附件处理；产物读取限定会话拥有的任务和固定文件类型，拒绝任意路径访问。预览、选区及几何调整可离线使用，无模型时明确提示无法整理工艺规格或生成代码。

验证覆盖选区与源图位置一致、多路径抬笔、越界编号、旧版本拒绝、清空选择、规格失效、源文件归档、异步任务成功/失败与解锁、模型配置隔离、HTTP 上传/下载及来源限制、重启恢复。浏览器检查实际 file input、画布/复选框、选区预览、参数、刷新、移动布局；对话→生成→进度→XML 下载使用隔离模型替身验证，未调用真实模型生成本次 PLC，也未执行任意 SVG 在线验收。

### 13.4 基础资产查看与替换

工作台顶部固定展示 AML、组件库、SVG 三个资产区。即使尚未上传 SVG，也能查看和替换设备与组件库；`/api/session` 初始化独立会话，`/api/asset` 负责替换/恢复默认，`/asset/<类型>/<校验值>/<子项>` 提供固定类型资产的附件下载。`assets.py` 管理不可变内容与兼容校验，当前选择保存在会话中，各会话互不影响，不覆盖仓库中的 AML 或发布契约。

| 资产 | 页面内容与操作 | 消费位置 |
|---|---|---|
| AML | 当前文件，展开查看设备、轴行程/速度及 IO 地址；上传 .aml、下载、恢复默认 | 轨迹行程/速度、RequirementUnderstander 的 IO 锚定、PLC 地址表与一致性闸门、场景轴参数 |
| 组件库 | 库名/版本，展开查看组件接口与物理参数；整体上传/导出/恢复默认，单独替换接口契约或物理参数 | 当前会话专属 SceneSpecGenerator 契约注册表与 MuJoCo 构建参数 |
| SVG | 当前文件和校验状态；上传/拖入/替换/下载 | 保留路径选择、确定性规划与预览；替换 SVG 保留当前 AML、组件库及历史任务 |

组件库包为 UTF-8 JSON，`format` 固定 `308sjk.component-library.v1`，另含字符串 `name`/`version`、`contract`、`physics`。先点击“导出当前库”取得完整模板，再修改并导入；`contract` 为现有 components.v1.1.json 的机器可读结构，`physics` 为 `runtime/physics_defaults.json` 的结构（schema_version=1.0、units=SI、gantry 参数表）。也可分别下载/替换这两个子项。每个上传文件上限 2 MiB；拒绝重复 JSON 字段、非有限数值、未知物理字段、非正质量/增益/几何尺寸等。

支持当前后端已实现组件的兼容数据替换，不上传或执行 Python 插件、任意 CAD 或新组件实现。契约保持 v1.1 的组件类型、quantity、方向、数据类型及参数签名，允许在已实现字段内调整数值边界/默认数据；当前场景生成器必须有 gantry_xyz。新类型、新接口或契约版本仍需配套后端实现与既有治理流程，未改变四份冻结接口。候选 AML 必须能解析且适配本站 X/Y/Z 三轴、单位 % 或 mm；候选资产组合须通过场景自检和 MJCF 构建后才能替换。失败保留原选择与版本。

物理默认值由 `runtime/physics_assets.py` 从 JSON 加载，`build_mjcf(spec, physics=...)` 按调用消费独立资产；不修改模块全局常量。质量、位置执行器增益、阻尼及龙门几何参数用于现有参数化模型；默认构建行为保持不变。独立构建器也支持 `python runtime/mujoco_build.py --spec <scene.spec.json> --physics <physics.json> --out <scene.xml>`。

成功替换任一资产均递增 revision、清空旧规格并刷新预览；AML 替换后适配框和中心按新设备行程重置。会话忙碌时禁止替换；页面未提交尺寸仍不得生成。每次生成另外冻结 `assets/device.aml`、`assets/component-library.json`、`assets/components.json`、`assets/physics.json` 和 `assets-manifest.json`，以及已有 input.svg。MJCF 构建已独立为第二业务部分（§13.5），使用同一任务的冻结输入，不再附带于 PLC 生成结束回调。历史任务仍保留自己的资产组合。

验证：根 345 项、runtime 27 项通过；资产专项覆盖组合兼容、会话隔离、非法上传回滚、刷新、真实模型参数变化和生成快照不可变。默认 gantry/plotter_cell 两种 MJCF 与改动前逐字节相同；替换后的执行器增益经 MuJoCo 模型加载验证。浏览器实际上传替换 AML、整库、契约、物理参数与 SVG，下载、恢复默认、刷新和移动布局通过；真实模型生成/在线验收未执行，Agent 生成快照链路使用隔离替身测试。

### 13.5 三业务分区与网页场景预览（2026-10-20）

页面顺序为“资产管理与 PLC 代码生成 → 仿真场景生成 → OpenPLC Runtime 控制”。前两部分接通已有生成/构建代码；负责人确认第三部分本次只划分页面并展示状态，控制后续接入，仿真画面留在网页中。

第一部分仍通过 `RequirementUnderstander`、`PLCGenerator` 和 `Orchestrator` 生成两份最终交付，SceneSpec 内嵌 io_map。第二部分 `POST /api/simulation/build` 只允许当前会话最新成功任务；校验请求 revision，并对账任务冻结的资产、工艺、选区和参数；读取其 `final/scene.spec.json`、`request.json` 和 `assets/physics.json`，复用 `runtime.mujoco_build.build_mjcf` 并经 MuJoCo 实际加载。缺少冻结资产即失败，不回退当前库；输入不同须先重新生成 PLC；切换后恢复全部相同输入时可复用既有交付。产物位于任务 `simulation/`：scene.xml、运行用 scene.spec.json/io_map.json、build.json；内部映射文件不作为第一部分的独立下载项，final 冻结文件保持不变。

仿真侧寄存器布局复用现有 `scenegen/iomap.py`，直接加载纯 Python 模块以避开旧包入口的 pxr 依赖；PLC 面地址以最终 XML 定位变量为准。缺失的合成 x/y/z_cmd 不猜地址，缺少工程量范围或轴通道则记录接线问题。当前 SVG 默认生成的速度输出程序与 `PlcLink` 的位置指令接口存在边界，界面显示“通道待适配”，不宣称已具备运动闭环。

`GET /api/simulation/frame` 限制会话产物归属并校验相机参数；复用现有 CSP Studio 的 MuJoCo 离屏渲染方式，网页可旋转、缩放和复位视角。每次预览只做 `mj_forward`，不运行物理步进或连接 PLC；GL 上下文在同一线程创建/销毁，渲染串行执行。第二、第三部分均可查看同一已构建模型。

初版 `GET /api/runtime/status` 只读现有部署服务的 `/status`，使用 trust_env=False；`DEPLOY_URL` 可覆盖默认 `http://127.0.0.1:8600/deploy`。初版仅显示状态；后续按负责人指令接入部署/启停/日志，当前实现与验证边界见 §13.8，仿真运动仍未接入。

验证：根 362 项通过；覆盖冻结资产消费、最终文件不变、XML 实际地址对账、合成通道阻断、旧 revision 拒绝、相机边界、会话读取隔离、只读状态和无启动路由。浏览器使用隔离服务和已有交付文件验证三业务导航、真实场景构建/加载、网页内渲染、视角旋转、下载、刷新及 390px 布局，未调用模型或运行 PLC。

### 13.6 第一业务页紧凑布局（2026-10-20）

第一业务页为三个模块：左上资产管理保留 AML 与组件库；左下绘图素材与参数合并 SVG 上传、路径选择、轨迹预览和默认折叠的参数表单；右侧 PLC 代码生成栏集中工艺对话、执行进度和生成产物，按标签切换，生成按钮保留在栏底，成功后切到产物标签。所有原有资产/参数/对话与生成接口保持不变。

桌面宽度 ≥1000px 时，第一业务页占满剩余视口，左侧内容独立滚动，右栏位置不随左侧滚动改变；长对话与日志在各自区域滚动，避免整页不断加长。小屏幕按资产、绘图、PLC 顺序堆叠。只调整第一业务页布局，场景生成和 Runtime 状态页继续独立显示。

验证：根 362 项通过；Chrome 在 1440×900、1366×768、1024×768、1280×600 验证无整页纵向溢出、右栏与生成按钮持续可见，左侧展开详情/参数时独立滚动。隔离生成替身验证进度/完成产物切换、下载、对话草稿保留，实际上传 SVG、参数失效保护与轨迹预览正常；390px 无横向溢出，零页面异常。

### 13.7 默认工艺、清空上下文与鼠标场景操作（2026-10-20）

轨迹预览仅保留 XY 图形和按顺序播放，不再生成规划参数/步骤表卡片；轨迹参数仍完整保存在嵌入数据与工件中，回放页的参数/证据区不变。工作台路径标签统一为“路径 N”，移除 path/rect 类型、路点数/单位摘要、路径顺序说明和发送提示。

`POST /api/context/clear` 清除本会话消息、累积需求、规格、报告及日志；按当前资产/选区/几何重新创建干净的准备数据，防止旧工艺通过 request_text 或 refine 再次注入。资产、轨迹与历史任务产物保留，revision 递增；任务忙碌或页面版本过期时拒绝，刷新后仍保持清空状态，不清理项目经验库。

`POST /api/process/default` 读取当前 AML 和轨迹，复用 RequirementUnderstander 的无客户端模板模式构建默认工艺规格，不调用模型；默认目标是启动后依序绘图、轨迹间抬笔、结束抬笔回中心、急停停止。点击“使用默认工艺”后可直接生成 PLC，也可继续对话修改；PLC XML 生成仍使用现有模型与闸门。完整 io_list 锚定设备，不增加或改名 IO。

`presentation.process_summary` 只处理用户显示，精简目标并将变量引用转换为中文，去掉任务编号、IO 表和逐条机器准则；完整 requirement_spec 与生成请求保持不变。旧版冗长目标在公开状态返回时转换为摘要，不覆盖原始历史证据；自定义目标的方向、停止等语义保留。

第二、三部分的三维预览去掉底部按钮：左键拖动旋转，右键/中键或 Shift+左键拖动平移，滚轮缩放；键盘方向键、加减号和 Home 亦可操作。相机接口补充有界 pan_x/pan_y，渲染串行、合并快速鼠标事件，只请求最新视角，避免按事件堆积渲染；场景仍只做预览，不推进物理或控制 PLC。

验证：根 374 项通过；新增上下文持久化、干净请求/规格、资产与历史保留、默认工艺 IO 锚定、无 LLM 调用、直接生成、忙碌/版本/token 边界、摘要不改契约及平移参数边界。Chrome 实际验证清空→刷新→默认工艺→生成、预览去参数、简洁路径与目标、鼠标旋转/平移/滚轮、键盘复位和移动布局，零页面异常；生成使用隔离替身，场景使用实际 MuJoCo 渲染。9 个既有预览重新应用模板，嵌入数据逐一保持等价。

### 13.8 前端接入 lx 执行侧（2026-10-20）

第三业务页新增“部署并启动 PLC”“启动 Runtime”“停止 Runtime”、状态轮询和日志读取。浏览器仍只访问工作台带 token 的写接口；`runtime_control.LXRuntime` 转发到 lx `serve.py`，部署继续使用 `POST /deploy` → `run_deploy.py` → `xml2st` → `OpenPLCClient` 上传、matiec 真编译、启动，不另建 PLC 执行链路，不修改最终 XML 或生成 ST。启停作用于 Runtime 当前加载的程序，不代表触发绘图。

部署仅接受本会话最新成功且输入仍匹配的任务；后台重读冻结 AML、request.json、组件契约和最终场景，按 XML/一致性/场景顺序重新校验后上传。版本、任务归属、忙碌与跨会话后台并发均在写入前拦截。操作记录持久化，服务重启时把未完成记录标为结果待确认。最终交付仍只有场景 JSON 和 PLC XML。

lx 部署服务新增 `POST /runtime/start`、`POST /runtime/stop`、`GET /runtime/logs`，复用现有 OpenPLCClient；`GET /status` 补充 capabilities 和成功上传 XML 的 SHA-256 回执（不作为 Modbus 运行身份的替代）。部署/启停共享非阻塞锁，部署删除旧结果后才执行子进程，避免旧成功被误报；失败步骤与错误回传前端，非预期启停状态裁定失败。HTTP 客户端统一 trust_env=False。部署服务以 `OPENPLC_URL`、`MODBUS_HOST` / `MODBUS_PORT` 指定目标，登录凭据只留后端环境变量；旧部署服务缺少新能力时，前端禁用新操作并提示更新。

验证：根 383 项通过，新增真实隔离 HTTP 的启停/日志、并发拦截、旧结果清理、精确 XML 回执、R6 地址错配拒绝、冻结产物和会话版本保护、token 与异步失败步骤测试。Chrome 使用真实工作台与 lx HTTP 服务、替身 OpenPLC/编译器，验证按钮依次启动→停止→部署、状态轮询、步骤与日志、第一业务页固定布局及移动端无横向溢出；MuJoCo 画面真实离屏渲染，未推进运动。当前 VM :8080/:502 不可达，未实际部署、启停或在线验收；速度 SVG 与 lx 位置桥之间的运动适配范围已向负责人提问，尚待确认。

### 13.9 部署入口与实际在线验证修复（2026-10-20）

根目录 `python -m src.agent.web` 启动时，`deployment_preflight` 的顶层 `from pipeline import xml2st` 无法找到 `src/pipeline`；此前测试主动加入 src 搜索路径，掩盖了实际入口问题。现改为复用一致性闸门已加载的 lx 转换器。新增两个真正独立的解释器分别以 src.agent.web 与 agent.web 启动临时端口，并经 HTTP 部署冻结文件到替身服务；回归先复现 root 入口失败，再验证修复。

真实重试时运行态上传在 Runtime 内部状态调用处超时。lx 服务新增 Runtime 访问锁：部署/启停仍单独互斥，状态/日志不穿插写操作；在途状态读结束后才执行写操作，不把正常读请求误报成重复部署。忙碌状态返回 BUSY。run_deploy 在上传前停止当前程序，上传后仍走真编译与启动；requests 超时也写 FAILED 结果并保留已完成步骤，客户端会话在 finally 关闭。按容器源码核对，日志路由修正为 `/runtime_logs`，非 200 明确失败。

根 393 项通过，覆盖双入口、读写并发、停止后上传、HTTP 超时留档、未知 Runtime 状态拒绝与实际日志路由。在线验证暴露旧经验测试会在假验收失败时部署诊断种子；已在该测试隔离 _runtime_probe，重跑全套期间真实部署请求数量保持不变，最后重新部署用户当前 XML。任务 `svg_web_a4b0f72c36e5ea28045e999cebc11012` 实际上传、matiec 编译、启动均成功，Runtime=RUNNING，上传回执与 final XML SHA-256 一致；证据位于任务内部 runtime-deploy.json，不增加前端最终下载项。

该任务生成 XML 的 prog_id 是无地址的局部变量，故 %QW20 仍为 0，程序名显示 unknown；本次没有改写冻结 XML。部署成功不等同于程序身份与绘图验收通过；速度/位置运动桥接仍未接入。工作台 :8765 PID16076、部署服务 :8600 PID1992，用户刷新页面加载新进程 token 和已成功的操作记录。

### 13.10 SVG 位置指令（2026-10-20）

工作台默认 AML 改用独立示例 plotter3axis_csp_station.aml。保留原26条 IO 及地址，新增 x_cmd/y_cmd/z_cmd 位置输出（默认 %QW3/4/5，量程/单位取对应位置反馈）；驱动参考连接到新位置输出，旧速度输出仅保留诊断。旧会话通过 POST /api/motion/position 显式转换，新 AML 是独立资产，按实际空闲寄存器分配且避开 %QW20，不复用速度通道承载位置。四份冻结契约及 axis_object.io 的字段结构不变，新位置量通过现有 io_points 与既有命名规则接入。

若已有当前有效的 SVG 交付，转换器从标准 dv_x/y/z.Setpoint 接线提取原插补位置（ix_x/y/z.Setpoint 或经过点动选择的 x_interp/y_interp/z_interp）。INT 直接赋值，REAL 显式量化；在 run/all_oe、急停及故障门控下输出位置，未使能/急停/故障时写当前位置反馈。原 FB 库、步序、路点、MC 参数与互锁原文保留，不调用 LLM。转换结果作为新种子走全部离线闸门，生成独立任务及资产快照；原 final 不变。prog_id 在新交付中定位 %QW20 并按原规格身份写入。非标准轴布局/地址冲突拒绝自动转换，用户可使用位置 AML 重新生成。

新 LLM 生成与修复按当前位置 AML 注入位置输出要求及实际地址；生成闸门和部署预检都检查真实位置源，拒绝将速度或 HMI 目标作为驱动输出，检查身份定位。chat/orchestrator CLI 传入位置 AML 时启用同样检查。新会话默认位置资产，旧会话不自动迁移；几何参数、工艺约束与旧任务保留，转换操作走原 token/revision/busy 保护。最终下载仍仅场景 JSON 和 PLC XML。

验证：根402项通过；覆盖 AML 地址不重排/空闲分配/实际接线/幂等、FB与工艺保留、速度及 sp 误接拒绝、未知布局和地址冲突、提示词、无模型转换、新冻结交付及位置场景/部署检查。当前用户新任务 svg_web_3ee73213073324be934cd138d24d6b56，来源 a4b0f72c，29 IO、6条真实物理通道、runtime_ready=true。实际939行ST转换、上传453356.st、matiec编译、启动均成功，Runtime RUNNING、prog_id=2且XML回执匹配；原任务 XML 哈希不变。现有 runtime/plc_link.py 完成一次真实 Modbus 收发（输入 %QW0-2、位置输出 %QW3-5），反馈原值写回，无绘图命令。证据为任务内 position-deploy.json 与 position-bridge-check.json。

Chrome 验证真实页面的位置模式、两项交付、映射就绪、身份与部署完成、三维预览及390px布局。工作台 PID8784。网页物理步进、实时运动画面与模型运动启停仍未接入，本次没有执行完整 SVG 运动/几何验收。
### 13.11 网页 OpenPLC 控制 MuJoCo 运动（2026-10-20）

第三页从只读扩展为“启动模型运动 / 停止模型运动”。启动重验冻结 XML/AML/场景与部署回执 SHA-256、prog_id，检查三轴位置指令和反馈的实际地址；部署目标由 lx 状态给出。运行线程复用 lx GantryBridge 和 PlcLink，以约 15 ms 位置轮询驱动 MuJoCo 120 Hz 物理步进，位置反馈按冻结 IO 量程回写。线圈使用 lx SafeCoilIO 整组读改写；停止清运行和绘图触发、置急停并确认读回。运动期间锁定资产、代码生成和 Runtime 操作；一台机器只允许一个运动任务。

网页 `/api/simulation/frame` 渲染运行线程的关节位置快照与实际笔尖墨迹，保留鼠标视角操作；停止后保留最后画面。笔尖接触判断包含胶囊端帽半径。任务内部保存 `simulation/motion.json` 供复查，前端最终交付仍为场景 JSON 与 PLC XML。

在线联调发现旧位置版 SVG 程序在三轴插补调用前读取上一扫描 Done，首个起点后即报完成。标准模板修法是保持 draw_active 期间的插补触发，先调用三轴插补器，再用当前 Done 和位置反馈推进步序；修复作为独立新任务冻结，不覆盖旧 final。新任务 `svg_web_5b1424be628026079cca2b6fe9377d3e` 真编译并部署，约 18.83 秒完成所选两段轨迹、留下 393 个物理墨点，回到中心并抬笔。根 pytest 409 项、runtime 27 项通过；Chrome 实际点击启动/停止，960px 动态画面与状态更新通过。尚未添加任意 SVG 的数值几何误差判定。
### 13.12 高精度 SVG 与浏览器本地三维显示（2026-10-20）

默认设备资产改用独立 `plotter3axis_precision_station.aml`：位置输出沿用原地址，range 放大100倍、unit显式标注0.01倍工程单位；反馈与设备行程不变。io_list、XML 定位变量和 io_map 仍按冻结结构对账，桥用各通道量程做 SI 换算；不扩展 Schema。位置闸门检查 REAL 插补状态乘倍率后编码，拒绝先取整 Setpoint 再放大。SVG 默认展开容差0.02、坐标量化0.01；两轴线段按位移比例缩放速度/加减速，首次横移前原地抬笔，空闲使能保持反馈。REAL/INT 与 MC IN_OUT 边界按 P06 显式转换；生成独立新任务，不改旧 final。

Motion 对实际 PLC 指令样本做40ms时间插值，以墙钟补齐120Hz固定物理步，增加阻尼速度前馈与偏置力补偿。实际笔尖墨迹按0.5mm距离采样、记录笔划编号，首次抬笔后才启用描画，防止初始化接触点误入轨迹。MuJoCo 仍是唯一物理求解端；浏览器只显示后台提供的真实几何和位置，不用规划步表代替实际运动。

新增 `/api/simulation/view` 导出 MJCF 形状/尺寸/颜色/变换，`/api/motion/frame` 按run标识和墨迹偏移增量返回。浏览器原生WebGL2本地绘制，约25Hz拉取快照并按显示时钟插值到约60 FPS；相机操作本地响应。画布根据窗口与像素密度适配，最多2560×1440。停止及重启服务后可读取上次真实快照，不重新运行模型。原PNG接口保留兼容。移除场景统计卡片、AML/LIB/SVG字样、恢复默认、位置模式提示和选择数量；Runtime 与模型按钮两组间隔加大，旧任务部署记录不冒充当前任务结果。

验证：根412通过；新任务 `svg_web_e1a2cadb67571012b3d151c5585fa872` 真编译/部署、完整两笔轨迹、先抬笔与回中心通过。以同一所选SVG参考折线（容差0.02工程单位）计算，原样例95%采样偏差11.001mm，新版0.247mm、RMS0.139mm、最大1.451mm；这是当前模型/样例实测，不是任意轨迹误差保证。Chrome 1600×1100、DPR1.5下画布2025×990，最终显示均值59.6 FPS；启停、重新完成、滚轮、删除项和43px按钮间隔通过，无页面异常。证据在工作台 `work/precision-comparison`；未增加前端交付下载项。


### 13.13 仿真与验证 Studio 集成（2026-10-20）

master `01c489c` 合入 gc；恢复本地 SVG、精度编码、场景显示和 lx 执行适配。任务读取统一为 `task_inputs.load_task_spec`，优先 `requirements/requirement_spec.json`，兼容旧 `request.json`；旧规格地址及轴参数仅从同任务 XML/AML 补全。保留远端完整 XML、测试计划、进度跟踪和编译环境检查，PLC 生成功能继续使用原工作台流程。

前端收敛为“资产管理与 PLC 代码生成”和“仿真与验证”两个业务页。参考 `runtime/csp_studio.py`，第二页左侧集中场景构建、PLC 部署/Runtime 启停、自动运动、手动调试和 HMI，右侧复用本地 WebGL 实际模型，下方按标签展示验收、趋势、日志和记录。`studio_web.panel` 复用 `hmi_gen.build_hmi`，BOOL 输入按钮、BOOL 输出灯、INT 分轴表；自动运行只读，手动操作在 Motion 拥有的线程队列中执行，每次写前查程序身份，沿用 SafeCoilIO 完整分组读改写，触发自动回落150ms。停止复位输入并核对读回，错误不伪装为零值。HTTP/1.1 连接复用减少高频快照轮询反复建立 TCP 连接；错误响应关闭连接，避免未读请求体污染下一请求。

“运行验收”只执行已交付 PLC：身份/回执/静态闸门→实际运动/采样→现有 csk `evaluate` 与 `verify_trajectory`，不调用模型或重写 XML。原始 PLC trace 与120Hz实际关节反馈分别保存；路径判定使用实际关节位置按同任务量程换算，避免拿整数 PLC 反馈验证小数路点；原始寄存器证据不覆盖。`simulation/verifications/<run>/` 保存 `trace.json`、`physical_trace.json`、`verdict.json` 和 `report.md`，记录 XML/场景/IO 哈希、规范化规格哈希、采样数和来源。停止、故障、手动运行或未配置工艺准则均为 UNVERIFIED，不能标 PASS。通用反馈与路点是否通过仍由确定性判定器决定，完成灯不代表验收通过。

`/api/simulation/import` 配对导入 SceneSpec JSON 与 PLCopen XML，可选 requirement_spec；只使用当前 AML/组件库冻结快照，先转换/一致性/场景闸门，再构建模型，不自动部署。可选规格必须通过 Schema 与 AML io_list 锚定。无规格时仅生成有效的 IO 量程检查描述供调试，但通过任务元数据禁用自动工艺验收。导入建立新任务，原交付不覆盖；`/api/simulation/select` 在本会话任务中切换，旧输入的任务不允许控制。文件与记录下载保持会话所有权和路径白名单。

历史内置契约 travel_z 开区间下界从0.01到0.005的修正，需要单独兼容：只识别与旧内置契约完全一致的资产，在新内容地址资产与派生仿真任务中改为10mm行程；原 XML 逐字节复用，原任务 final/ 与资产不修改，migration.json记来源。自定义契约和外部导入文件不自动改写。Runtime BUSY 状态短暂重试，不误判为停机；后台失败在仿真页直接显示。

验证：根tests与runtime/tests合计591通过，6项桌面Qt测试因未安装PySide6跳过；四份场景XML静态转换通过。新增11项Studio测试覆盖新旧规格、队列脉冲/保持、输出/跨会话拒写、原始证据与实际物理反馈分离、真实事件边沿、断链失败、文件导入/回滚/切换、历史契约新任务迁移、BUSY重试及HTTP连接复用。

实际会话旧任务e1a2cad保留，派生 `svg_web_4de8bc1d04a245d898fa0807f4f76eb8`；XML SHA-256仍为1ee0ef85870abfa846e1703fb7c4ee864348ac2ddfe9d1178363837fe66f1bba，与现有部署回执相同。手动使能/启动/停止读回通过；完整绘图21.375s，228个PLC样本、2533个物理样本，实际笔迹完成，量程判据通过，但原规格首路点[58.62,28.93,10]不满足到位窗口，PATH判定FAIL，未放宽准则或更改PLC。Qt式“绘图完成”和“验收通过”分别展示。外部同源文件经浏览器文件输入导入、模型可加载、未配置规格时自动验收禁用、切回原任务通过。最终1440×1000/DPR1.5下画布1472×825、约60.2FPS；390px无横向溢出，150次快照HTTP/1.1请求2.39s，无浏览器异常；记录下载通过。浏览器证据在 `E:/chagpt/308sjk-studio-integration`。后台最终运行于:8765；未push。


### 13.14 SVG 重新生成后的空白与假完成修复（2026-10-20）

用户任务 `svg_web_b4ee8f850db41627cb6aefeaba0a717a`（三路径，98路点）生成稿将 pl_t* 声明为INT并取整坐标，在调用INTERP之前读取前一扫描的Done推进步号，随后每轴Done即清go_exe，目标连续跟踪被切断。实测只到首定位点即plot_done，4.55s/0笔迹。故障在新生成的绘图程序；前次Studio验证覆盖旧交付复跑，未覆盖重新生成。这属于P22/P10记录的连续跟踪/完成语义问题，应保留当前实采证据修复工艺层。

`repair_generated_svg`仅接受已识别的标准三轴命名和插补器结构，完整匹配嵌套IF/CASE，在独立任务保留源交付。重新将当前冻结步表落位到REAL目标，正确采用每段waypoints长度，先原地抬笔，再定位、落笔、连续跟踪；Done清理仅在非绘制期间执行，步进放到插补调用之后，校验当前新目标位置。复用既有precision_svg_program并补POSWIN输入无初值、SCAN_TZ积分常量及可选rel变量结构支持；未知形态拒绝自动修改、重复转换拒绝。当前原程序和资产不覆盖。

新增LLM SVG检查 svg_program_problems，拦截标准轴栈在插补调用前用Done步进、带小数步表却声明INT目标；错误进入现有生成重试。新生成/修复LLM带入当前轨迹；显式种子转换维持已有非生成语义。轨迹提示和goal_text按coordinate_quantum区分REAL/INT，纠正原先对0.01路点仍声称INT的矛盾提示，强调持续触发与扫描顺序。Motion在SVG完成但无实际笔迹时明确失败，不能只凭plot_done报绘制完成。

验证中新出现的Windows10053定位为POST请求体未读即关闭socket引发RST：工作台令牌拒绝与lx启停/错误响应均先消费有大小限制的请求体，再鉴权/操作；保持错误Connection:close，并按原body解析部署XML，未对写操作自动重试。完整tests/runtime595通过，6项Qt缺PySide6跳过；10次拒绝响应/持久连接与原先失败的HTTP路径通过。

修复任务 `svg_web_ba785341bf112adff9d1dbeeba95449d`：源XML哈希0555dc6d...不变，新XML ebcf71ec...两次实际matiec编译部署成功，完整绘图31.0s、1503实际笔迹点、3段独立笔迹并回中心；331PLC/3697物理样本。与冻结参考折线比较，笔迹95%偏差0.248859mm、RMS0.129509mm，参考覆盖95%距离0.256255mm、最大0.412368mm，属于当前模型与样例实测。浏览器1472×866画布、三条笔迹可见、状态正确、无异常；原final保留，新的Studio和模型模块保留，无需回滚。严格PATH仍未通过第2步到位窗口，原始判据与报告保留，没有调宽验收容差。证据 E:/chagpt/308sjk-svg-recovery。最终后台:8765及部署:8600已重启，修复XML回执一致，模型停止。

### 13.15 部署循环、失败证据与自动使能顺序（2026-10-20）

当前任务747f9d3a同时声明内部prog_id和%QW20定位prog_id，本地转换未拦截，真实matiec在定位声明处失败。部署失败已停止原Runtime但未安装新程序；随后手动启动恢复旧程序，其prog_id仍为2，不能证明是当前XML，精确SHA回执检查正确拒绝运动。问题不是反复缺少启动操作，不能以绕过XML身份检查修复。

xml2st新增POU级大小写不敏感变量重名检查，覆盖同块及跨local/input/output变量块；不同POU同名仍允许，生成提示明确prog_id只能声明一次。工作台runtime_deployment独立保存部署进度/错误，Runtime启停不再覆盖失败证据；启动模型在检查运行状态之前核对精确XML回执，失败直接展示原编译原因。runtime/status返回deployment_matches及当前任务部署失败提示，页面只有当前XML已部署且Runtime运行时才能启动模型/手动/验收。未放宽回执或程序身份约束。

联测另发现自动模型复位与使能同时保持会打断新MC_POWER的控制字握手。改为先保持150ms复位且run=false，再释放复位并使能，随后等待all_oe发绘图脉冲。标准SVG修复扩展到同型INTERP用于Z、REAL的*_tgt及外层步号/内层Busy判据；REAL目标不套INT_TO_REAL或REAL_TO_INT。当前生成稿还存在自引用边沿、Done无条件清持续触发及小路点阈值问题，在新任务修复；生成检查补拦截自引用边沿与无条件清触发，不覆盖原final。

原747f9d3a XML SHA 86db1afc...保留；修复任务svg_web_5e32c7d7b35e1c24cbb0ba18391c1466，XML SHA 2c49e3bd...。真实matiec编译后一次“部署并启动PLC”直接进入RUNNING，不额外调用runtime/start，随后直接启动模型32.27s完成、1821实际笔迹点、345PLC/3851物理样本并回到中心。严格PATH到位窗口仍FAIL，原准则与证据保留，未宣称全部精度验收通过。浏览器实际页面回执一致、Runtime启动按钮禁用、模型启动按钮可用；注入“编译失败后启动Runtime成功”的页面状态仍显示原部署错误并禁用模型，无页面异常。完整tests/runtime603通过，6Qt因无PySide6跳过，既有pymodbus服务退出提示不影响结果。证据E:/chagpt/308sjk-deployment-cycle，未push。

### 13.16 按用户要求恢复更新前的 PLC 生成策略（2026-10-20）

用户反馈新生成PLC质量下降，明确要求将生成部分恢复到本次拉取远端之前。基线不是只取91f7dc6提交，而是合入01c489c前保存的本地stash ad1876bf101ea43a55f4587d3245cd608bbdc270（父91f7dc6），包含当时尚未提交的SVG/位置指令生成支持。恢复前的pipeline、skill和骨架同步测试已备份，来源/前后哈希见E:/chagpt/308sjk-plc-generation-rollback/rollback.json。

恢复范围：plcgen_skill.md逐字恢复至快照，SHA-256=4f3d3034a7f6280423a7da96879e5dceb05ecd1903b865245de55b3e988ba571；恢复motion_stack_v4.xml同快照纯FB库作为旧骨架指纹。PLCGenerator.build_messages、generate、repair恢复旧装配、完整XML输出和错误回灌/最小修改流程，temperature恢复0.3；取消按CSP轴改用plcgen_csp_skill的路径、额外扫描策略追加与hybrid生成分支。当前所有入口均生成完整XML。

仅保留当前工作台兼容所需的MODEL/API配置、请求/响应证据与进度、输出截断拒绝、静态闸门与SVG检查、test_plan可选参数（提供时仅追加计划数据，不改system策略）。不回退orchestrator产物布局、HMI、前端、场景生成、Runtime部署/控制、物理运行，也不更改旧final或自动部署。CSP短提示文件仍在仓库但不再被生成器选择；当前执行侧场景XML不变。

骨架同步测试改为检查同快照FB库的接口，不能继续拿更新后POSWIN入参形态要求旧提示词再次跟随主干；纯FB库不伪装为可部署PROGRAM。增加精确旧提示哈希、CSP仍使用旧完整骨架、生成/定向修复采样与证据、可选计划接口及禁止hybrid回归。现有完整XML测试按语义断言输出格式，保留截断/身份/精度等防护。tests/runtime共606通过、6Qt缺PySide6跳过。未进行真实LLM再生成或在线部署，质量效果需后续生成产物验证，不能由回归通过推断所有新PLC都可运行。

### 13.17 PROGRAM 定位 IO 声明导致部署失败的修复（2026-10-20）

用户当前产物svg_web_7ea9d48cc3c91ce75d5319478a0180c4编译日志141274.st:381..394报invalid input variable(s) declaration，VAR_OUTPUT处亦报声明错误。实际ST为PROGRAM PLC_PRG / VAR_INPUT / run AT %QX0.0等，包含30个定位变量（含prog_id）放在inputVars/outputVars。连接、停止和上传均成功，问题在matiec不接受定位形参；回退生成策略不会自动修复已有XML的这个布局错误。

新增xml_normalization.normalize_program_io，仅在模型输出提取后、闸门之前将PROGRAM的inputVars/outputVars/inOutVars中带address变量移入localVars；保留名称、类型、地址、初值及块属性，FB接口和ST工艺本体不修改，无改动则逐字返回输入。generate/repair都接入并播报纠正数量，原始response证据保持原样。种子不静默纠正，外部错误XML由xml2st在便宜层拒绝，报出变量名、错误分组与合法分组，避免等到远端编译才发现。恢复的旧skill/prompt策略与0.3采样不变，prompt哈希仍为4f3d3034a7f6280423a7da96879e5dceb05ecd1903b865245de55b3e988ba571。

针对当前冻结原稿派生svg_web_841616588b1c820da0c7a9d0aee93a07，仅调整30个声明分组，所有POU的ST本体逐字相同。原XML SHA1657c997...不变，新XML SHA213982e4...，offline闸门/场景加载/真实matiec编译/部署/自动启动均成功；精确XML回执一致且Modbus prog_id=2。此轮未启动模型，不据此宣称运动或精度验收通过。增加6项声明纠偏/FB不改/幂等/初值保留/生成修复与raw证据回归，以及3项AT形参静态反例；完整615通过，6Qt因无PySide6跳过。证据E:/chagpt/308sjk-program-io-deploy-fix，原交付及回退基线保留，未push。

### 13.18 新产物的实际编译、物理验收与失败修复（2026-10-21）

网页生成改为必经闭环：生成完整XML → 原静态契约/一致性检查 → 使用本任务冻结资产构建MuJoCo场景 → lx真实编译/部署/启动 → 本轮XML回执核对 → 实际Modbus位置桥和MuJoCo验收 → 全部PASS才冻结任务final。失败保留候选、编译源码上下文、PLC与物理采样和报告，回原修复循环；Runtime离线、回执变化等环境故障停止，不让模型修改程序来掩盖环境问题。候选迭代目录中的final只是现有运行器的输入适配，不能作为任务交付；顶层final及两项下载仅在本轮物理证据通过后发布。种子转换也必须经过真实验收。

`workbench_validation.WorkbenchOrchestrator`复用原编排器、lx接口、场景构建和Motion，不另建执行链。物理报告绑定XML、场景、io_map、冻结规格、轨迹和实际采样哈希，网页生成完成再独立核验。历史产物不追加“已验收”标签。SVG附加检查展开每个waypoint，要求真实落笔接触；实际笔迹到参考线段的最大距离按各轴冻结poswin归一化，最大值须≤1，不使用百分位掩盖局部过冲。原PATH及规格判据同时执行。

保持用户要求回退的旧system提示词、FB骨架资产与0.3采样；只在网页生成的user上下文补验收和现有接口要求。将R8参数检查、可确定的REAL→INT_TO_REAL错误前移到同轮反馈，识别共享非绘图IF保护避免误报。模型生成的标准INTERP在高精度位置输出下增加离散跨目标收尾及半量化步长的重新定位阈值；保留原变量、路点、到位窗口和halt减速分支，未知结构不自动修改。原始模型回复仍留档，变换后重新过静态闸门和真实编译。

实际联测发现仅有阻尼/重力前馈会造成转角过冲。`servo_feedforward.PositionFeedforward`从真实PLC指令的采样速度、加速度和已加载MuJoCo质量矩阵计算惯性/阻尼/偏置前馈，同时补偿下一位置采样在当前P环求值的时序偏差。只设置ctrl及控制力，绝不设置qpos/qvel，也不读取规划轨迹生成运动；资产质量、增益、阻尼及验收容差不变。真实求解器加减速/停止回归及同一实际PLC指令离线回放均验证误差下降，最终仍以在线全流程报告裁定。

同一冻结输入重试时复用失败候选作为模型修复上下文，不复用其通过结论。改变资产、工艺、参数或选区即拒绝继承；优先保留通过物理检查更多的候选，避免后续改坏步序仍从退化稿继续。源码、失败来源、迭代、报告均留档。

真实联测证据：E:/chagpt/308sjk-new-generation-validation/run_20261021_000657。新折线任务svg_web_a68e595f4e0813a9d12f92ff1c9e7954全部PASS，871物理采样、350接触采样、6个展开路点；最大偏离为冻结轴到位容差0.1047倍。新双路径任务svg_web_f91631c48ab75bea6c25ebffa935ec63全部PASS，1522物理采样、531接触采样、9路点，12.938s完成，最大偏离0.0771倍。实际失败还暴露XML ST重复POU结束标记，生成回复仅移除冗余页脚，原始证据保留；非draw_active步号序列也检查连续触发。几何通过而路点失败时反馈明确要求到位后才稳定计时，避免回改已通过插补器。完整639通过、6Qt跳过。两份均经历实际生成、失败与修复，没有宣称任意SVG都会在有限轮次收敛；未通过者不交付final。

### 13.19 修复知识、重复候选和通信中断续验（2026-10-21）

内部静态修复统一消费`repair_feedback.static_feedback`：先检索故障库、历史成功修复和同类经验，将具体修法与各错误族的代表证据一起回灌，逐调用保存完整错误、知识命中和建议。首轮resolved经验带`diff_hunks`，不再只注入“定向修复后全过”等抽象总结；规格、AML地址、轨迹和验收仍以当前冻结输入为准。切换至较好物理候选时，分别保留基底失败证据与本轮完整归因，后者明确标注来源，避免误当作基底也有的缺陷；学习差异使用实际修复基底。静态失败候选续修恢复其逐调用错误与知识建议。

同一生成任务内，相同源码再次出现相同静态错误即停止后续模型调用；静态通过的源码若与已测候选相同，则在再次构建场景、编译和物理验收前停止。源码指纹忽略排版、注释和时间戳，保留声明、指令、字符串/数值与混合XML正文。停因记录为`no_progress`，保存候选及证据，停止后不再做失败族LLM蒸馏。重复候选不能冻结final，旧交付不变。

通信分类覆盖pymodbus的`No response received`、Modbus通信失败/超时及连接错误。可恢复通信中断最多对同一源码重验一次，不生成新稿，首轮报告/采样另存；再次运行前重新核对Runtime和源码回执。身份或源码变化、停止未确认不自动重试；持续故障以`env_down`停止，网页显示环境原因。手动再次生成时，只有冻结输入相同才继承候选；环境中断可直接重新走全部验证闸门，不先调用模型，不继承旧PASS。新静态错误或后续行为失败仍按证据进入正常修复回路。

SVG页面的“画正方形”“画一个圆形”等明确形状指令在修改规格和需求模型调用前拒绝，并提示更换SVG，防止旧选区/旧轮廓与新文字要求冲突；调整宽高/中心等原功能保留。仍使用用户恢复的完整XML生成system策略与0.3采样，不改冻结契约。

验证：根`tests/`629通过、6项Qt环境跳过；包含知识命中、内部静态熔断、部署前去重、回退归因、通信单次恢复/持续失败、旧No response续验和SVG冲突。确认无运行任务后已重启8765；首页、模型配置和实际HTTP冲突400检查通过，冲突请求前后会话输入/规格/历史不变。最近b3851eae...的原始通信故障记录被新分类识别为可直接续验。本次未调用真实模型或重跑实际PLC物理运动，不能据此宣称该旧候选行为已通过。证据目录：`E:/chagpt/308sjk-repair-loop`。

### 13.20 语言绘图与SVG两种轨迹来源（2026-10-21）

绘图来源由会话`drawing_mode`显式区分为`language`或`svg`。新会话默认语言描述，已有SVG会话迁移后保持SVG来源。语言模式不要求SVG资产或选区，左侧可描述并预览，也可在右侧直接输入“画一个正方形”等任务；预览后可使用默认工艺或继续补充对话。当前支持正方形与圆，复用原确定性规划器和澄清模块，解析中心、边长、半径/直径和圆段数；缺参默认值在界面明示，规格生成前可审查，物理单位不兼容或超出AML行程则前置拒绝。

`drawing_input`只适配输入与规划，不把文字转成SVG。会话保存独立`language_geometry`和简洁描述；SVG资产、选区及参数保留，切换来源时重新规划当前输入并使旧规格失效。工艺对话、默认工艺、清上下文和资产更换都按当前来源处理。规格理解读取当前权威几何，PLC生成继续消费冻结的`trajectory.json`与规格。生成快照增加来源和语言几何；来源不同不能继承失败候选或旧交付。语言任务的归档不含保留SVG的`input.svg`，原SVG资产仍可切回使用；无效规划保持之前的会话和轨迹。

前端按来源判断可用条件：语言模式初始就可发送对话，默认工艺要求当前有效预览，生成要求规格与预览匹配；修改尚未应用的描述会禁用默认工艺/生成，防止按旧轨迹执行。SVG模式继续要求有效选区和参数一致。统一展示两类轨迹预览，嵌入时收起重复标题，规格摘要不重复权威步表。除通用绘图提示文字外，原完整XML生成system策略、冻结契约和原部署/物理验收闸门不变。

验证：根`tests/`642通过、6项Qt环境跳过。新增无SVG直接对话到规格、默认工艺、圆/正方形及修改、来源切换/保留、旧会话、资产重验、语言生成快照和HTTP保护测试。真实Chrome桌面与390px移动端验证预览、默认工艺、生成按钮、SVG导入/空选区/语言恢复、圆形及非法尺寸保护，无横向溢出和JS异常。8765已在空闲确认后重启；本次未调用真实模型、部署或运行PLC。截图、浏览器记录和服务检查保存在`E:/chagpt/308sjk-language-drawing`。
