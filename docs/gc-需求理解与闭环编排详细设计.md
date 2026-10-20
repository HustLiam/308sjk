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
