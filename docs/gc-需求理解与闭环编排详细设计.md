# 需求理解与闭环编排详细设计（gc 负责部分）

> 本文档是《总体实施方案》中 **① 需求理解模块**、**②a/②b 生成智能体的 LLM 本体**、**端到端闭环编排器（solve 循环、迭代管理、归因反馈的权威定义在本档 §3.2 / §4，csk 文档 §5.2–5.4 指向此处）**与**跨模块契约一致性**的详细设计，负责人：gc（智能体与闭环侧）。
>
> 三人分工全景：**PLC 执行侧（lx，见《lx-PLC代码生成与执行引擎详细设计》）** 负责代码契约与链路 B；**仿真验证侧（csk，见《csk-仿真环境与IO闭环详细设计》）** 负责场景生成、Isaac 运行时、判定引擎与链路 A；**本侧（gc）** 负责让这两端被同一个大脑串成闭环——听懂需求、生成代码、判定后归因、定向重生成、管住迭代直至收敛。

---

## 0. 职责范围（TL;DR）

| 总体方案模块 | 本侧职责 | 关键产物 | 状态 |
|---|---|---|---|
| 用户交互层 | 自然语言输入、多轮澄清、规格回显确认、结果展示 | 对话协议 / CLI | 🚧 未启动（编排 CLI 已有，澄清协议未接） |
| ① 需求理解模块 | 自然语言 → 结构化需求规格；**requirement_spec.json Schema 的定义权** | `requirement_spec.json` + JSON Schema | 🟨 Schema 草案 v1.0.0-draft.1 已出（`schemas/requirement_spec.schema.json` + `src/agent/spec_validator.py`），**待三方评审冻结**；LLM 澄清未接 |
| ②a/②b 的 LLM 生成本体 | PLCopen XML 生成器（在 lx 契约上）、失败归因分析 LLM | 生成器 Prompt 工程 + ST 模式库 | 🟨 生成器 v0 已实现（`src/agent/pipeline.py` + `patternlib.py` + `prompts/plcgen_skill.md`，种子=已验收 6 场景，xml2st+一致性双闸门回灌）；归因 LLM 未启动 |
| 闭环编排器 | solve 循环、两个编译/校验短路、迭代记忆、终止与 best-effort、反馈包拼装与路由 | `orchestrator/` | 🟨 半环骨架已实现（`src/agent/orchestrator.py`：生成→xml2st 闸门→一致性→部署可选；runs/ 落盘与 final 冻结已跑通；仿真全环等 csk 接口） |
| 跨模块一致性 | io_list 单一源头的落地：**三方一致性检查器**（定位变量 ≡ io_list ≡ io_map） | `consistency_check.py` | ✅ 原型完成（`src/agent/consistency_check.py`，R1~R5；io_map 腿接口就绪，csk 产出后自动生效） |

不归本侧的：xml2st 校验/部署/Modbus 验收（PLC 侧）；SceneSpec→USD 构建、Isaac lockstep 运行、trace 采集、**确定性判定引擎**（仿真验证侧）。判定引擎给出 PASS/FAIL，本侧消费它并决定下一步。

## 1. 在总体架构中的位置

```
用户（自然语言）
   │
   ▼
【本侧】① 需求理解 ──► requirement_spec.json（io_list 是三方共同源头）
   │
   ▼
【本侧】编排器 solve() 循环 ──────────────────────────────────┐
   │  ②a PLC 代码生成（本侧 LLM，产出受 lx 契约约束）          │
   │     └► 编译闸门：lx 的 xml2st / POST /deploy（失败即短路） │
   │  ②b 场景生成（仿真侧 SceneSpec LLM）                      │
   │     └► 校验闸门：仿真侧 Schema/物理校验（失败即短路）      │
   │  ③ 仿真执行：③a DLL + ③b json→USD→run_isaac_headless    │
   │  ④ 判定：仿真侧确定性规则引擎 ──► verdict.json             │
   │  归因（本侧 LLM）：区分代码问题 / 场景问题，路由重生成      │
   └── 未通过：带迭代记忆进入下一轮（≤6 轮）◄──────────────────┘
        通过：冻结 final/ → 转 lx 的链路 B（OpenPLC）做软 PLC 验收
```

本侧是环上唯一"懂语义"的节点：两端提供确定性的校验/编译/判定/执行能力，本侧决定**生成什么、往哪返工、何时停**。

## 2. ① 需求理解模块

**功能**：把用户自然语言指令变成机器可执行、可验证的结构化需求规格；歧义主动追问。

**requirement_spec.json（本侧拥有 Schema 定义权，冻结前需三方联签）**：

> **落地状态**：草案 v1.0.0-draft.1 已实现——评审视图 `schemas/requirement_spec.schema.json`，
> 运行权威 `src/agent/spec_validator.py`（两者同一 RFC 内同步修改），基准示例
> `examples/specs/sorting.spec.json`（io_list 与 sorting.xml 定位变量逐字对齐）。
> Schema 文件表达不了的语义规则：S1 唯一性（io 名/AC id/C id）、S2 量程（INT 必带
> range、BOOL 禁带）、S3 信号引用必须落在 io_list、S4 时间阈值 ≥100ms——均在校验器实现。

| 字段 | 内容 | 约束 |
|---|---|---|
| `task_goal` | 被对象与工艺动作序列描述 | 自然语言，供②a/②b共享 |
| `io_list` | IO 清单：`name / dir(input\|output) / type / range / device` | **三方一致性唯一源头**（②ST 定位变量、③io_map、④地址映射）；跨链路类型规则见 §6 |
| `constraints` | 时序约束、互锁条件、异常处理策略 | 供②a生成逻辑与④映射为 forbidden_state 类准则 |
| `acceptance` | 可量化验收准则，**封闭四类**：`event_delay` / `region_containment` / `forbidden_state` / `sim_health` | 结构与仿真侧判定引擎逐字对齐（其 §5.1 的 JSON 即权威结构）；落不进四类的一律退回重新组织 |

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

### 3.2 归因分析 LLM（消费判定结果）

- 输入：仿真侧 `verdict.json`（确定性证据，如"t=4.286s PE1 上升沿 → t=5.431s 气缸推出，延迟 1.145s > 0.5s"）+ 相关 trace 窗口 + 当前代码/场景；
- 输出：`report.md` 归因报告，**区分代码问题（→②）与场景问题（→③）**并路由；
- 红线：归因不改变 PASS/FAIL 结论；判定永远由仿真侧确定性引擎做出，LLM 不判卷。

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
    trace = run_isaac_headless(usd, io_map, dll)      # ③ 仿真执行（③a DLL × ③b）
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
io_map.json（仿真侧产出）------------------------------------┤
                    consistency_check：名称/类型/地址/方向逐条对账
```

- 检查点：`io_list` 每条在定位变量表与 io_map 中均有对应且名称逐字一致；地址不冲突、位宽匹配（BOOL↔%QX，INT↔%QW）；方向语义正确（input↔Isaac→PLC，output↔PLC→Isaac）；
- 调用时机：编排器在**生成后、仿真前**调用（对应总体方案 §3.2 前置校验第 3 步）；
- 任何一方修改（改代码变量名 / 改 io_map 绑定）都触发重查——单一源头 + 自动对账，杜绝三方漂移。

## 6. 与两端的接口契约

| 对端 | 接口 | 方向 |
|---|---|---|
| lx（PLC 侧） | `xml2st --check`（本地快速闸门）；`POST /deploy` :8600（真编译+部署，返回 deploy_result.json，errors 原样进反馈包） | 本侧调用 |
| lx（PLC 侧） | 契约文档 §3：ST 子集 / 定位变量位宽 / 显式拒绝清单 —— 生成器 Prompt 的硬约束 | 本侧遵守 |
| 仿真侧 | requirement_spec → SceneSpec 生成与 USD 构建；`run_isaac_headless(usd, io_map, dll)`；`evaluate()` → verdict.json | 本侧调用 |
| 仿真侧 | acceptance 四类准则结构（其 §5.1）——需求 Schema 与判定引擎逐字对齐 | 双方共守 |
| 跨链路 | 模拟量统一 **INT @ %QW + 定点换算**（双链路一致约定）——生成器负责落码，换算系数写入 io_map（量程换算字段） | 本侧落实 |

## 7. 实施计划（对齐总体方案 §5）

| 阶段 | 本侧工作 | 依赖 |
|---|---|---|
| 一（1–2 周） | requirement_spec Schema + acceptance 四类结构定稿，**三方接口契约冻结（联签）** | 无（最先动工） |
| 三（5–9 周） | 需求理解 Agent、PLC 生成器（Prompt + ST 模式库，种子=已验收 6 场景）、一致性检查器 | lx 契约已冻结 ✅；仿真侧 SceneSpec Schema |
| 四（9–13 周） | 编排器串联全链路、反馈包拼装、归因路由、迭代收敛性调优 | 两端引擎打通（阶段二，各自进行） |
| 五（13–16 周） | 典型场景（分拣/顺序控制/搬运）三端联合测试与指标评估 | 全部 |

## 8. 验收指标（本侧 KPI，总体方案 §6 摘录）

- **首次生成编译通过率（matiec）≥ 80%**——生成器质量的核心指标；
- **闭环收敛率：≤6 次迭代通过全部准则 ≥ 70%**——编排与归因质量；
- **需求还原度：验收准则覆盖率 100%**（每条准则有对应自动化检查，Schema 层保证）；
- **端到端耗时：单需求输入到验证通过 ≤ 30 分钟**（两个短路闸门是主要手段）。

## 9. 待办（按优先级）

1. ~~requirement_spec JSON Schema 草案 + 三方评审冻结~~ → 草案已出（见 §2 落地状态），**评审冻结进行中**（RFC 流程，主方案 §8.3）；
2. ~~与仿真侧确认 acceptance 四类准则的最终字段结构（以其 §5.1 为底稿）~~ → 字段已逐字对齐其 §5.1，待其评审确认（`check_at` 冻结 "end"，扩展走 RFC）；
3. ~~PLC 生成器 v0：模式库整理 + Prompt 骨架 + xml2st 错误回喂通路联调~~ → 已完成（`src/agent/`：pipeline / patternlib / prompts；xml2st+一致性双闸门回灌；LLM 真实调用待 API Key 端到端联调）；
4. ~~一致性检查器原型（可直接复用 lx 的 `xml2st.parse()`）~~ → 已完成（`src/agent/consistency_check.py`，R1 复用 xml2st.parse；io_map 腿接口就绪待 csk 产出）；
5. ~~编排器骨架：先串"生成→编译闸门→部署→链路 B 验收"的半环（不含 Isaac）~~ → 半环已跑通（`src/agent/orchestrator.py`；部署闸门连不上记 skipped；链路 B 验收脚本接入与仿真全环待两端就绪）。
