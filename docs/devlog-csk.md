# csk 本地开发日志

> 仅技术说明（改了什么 / 为什么 / 如何验证 / 技术坑）。进度协调内容一律写 `docs/协作看板.md`。本文件在 master 合入前移除，永不进 master。

## 2026-09-02 模块 ③ 职责收窄：SceneSpec JSON 生成移交给 gc

### 改了什么

`csk-仿真环境与IO闭环详细设计.md` 的职责表述全面收窄，**技术方案本体（Schema、校验器、USD 构建器、Isaac 运行时、IO 桥、判定引擎）未动**：

- 文档头部新增"职责边界"声明：上游接口 = `scene.spec.json`（gc 侧 LLM 生成），本侧自 JSON 起接手；
- §0 TL;DR 新增"职责起点"行，"LLM 直接生成什么"行改为 gc 归属；
- §1.2 三层资产策略图标注三层归属（第 1 层 gc 生成 / 第 2、3 层本侧）；
- §2 章首新增职责划分说明；§2.1 流水线四步逐一标注归属（① gc，②③④ 本侧）；
- §2.2 更名为"gc⇄csk 接口契约"，声明 Schema 变更走主方案 §8.3 RFC；
- §2.5 校验失败从"拼进反馈 Prompt"改为"结构化列表返回编排器，gc 负责拼装"（与 §5.2 归因归 gc 的既有边界对齐）；
- §5.4 接口表中移除 `gen_scene_spec`（归 gc），保留 `validate_scene / build_usd / run_isaac_headless / evaluate` 四个本侧接口；
- §6 目录注释、§7 落地路线（D5–7 / D8–10 / D11–14）同步改为 gc 归属表述。

### 为什么

原 §8.1 将模块 ③（SceneSpec/USD/组件库）整体划给仿真侧，导致本侧要同时维护"LLM 生成"与"确定性仿真"两类性质完全不同的工作。收敛为与模块 ② 对称的分工模式（lx 拥有 XML 契约+闸门、gc 做 LLM 生成本体）：**本侧拥有 SceneSpec Schema 契约与校验闸门，gc 拥有 SceneSpec 的 LLM 生成本体**。LLM Prompt 工程从本侧职责中剥离后，本侧全部交付物都是确定性代码，可单测、可回归。

### 同步范围（主方案 §8.7：受影响文档同一合入内修订）

| 文档 | 修改点 |
|---|---|
| 总体实施方案 | §8.1 RACI"③ SceneSpec / USD 构建 / 组件库"拆为两行（LLM 生成本体→智能体侧；Schema/校验/USD/组件库→仿真侧）；§8.7 权威域表 csk 行更新 |
| gc 文档 | §0 工作分解表"② ③ 的 LLM 生成本体"行补 SceneSpec 生成器；§1 架构图 ③ 行归属；§4 solve() 伪代码注释；§6 接口契约表拆分为"遵守（Schema）"+"调用（闸门/构建/仿真/判定）"两行 |
| 协作开发指南 | §1 文档地图 csk 行权威域；§6 gc 侧速查补"SceneSpec 先过 csk 校验闸门" |
| 协作看板 | csk 区块本人刷新；共同议题区登记"模块 ③ 分工细化"待三方确认；变更记录留痕 |

### 验证

- 全文检索旧表述无残留：`grep -n "LLM 接入\|仿真侧 SceneSpec LLM\|LLM 生成 SceneSpec" docs/*.md` 仅剩 gc 归属语境；
- 四文档交叉引用的章节号（gc 文档 §3/§4、主方案 §8.3、csk 文档 §2.2/§2.5）逐一核对存在；
- 未触碰任何契约字段（io_map 结构、acceptance 四类、requirement_spec、PLCopen XML 契约均未改），无需 changelog/版本升级。

### 技术备忘

- 本机另有一份 09-01 上午的旧文档草稿（OpenPLC Modbus 主链路 + Isaac Sim 6.0 方向），与 master 现行双链路架构（matiec lockstep 主链路 A + Isaac Sim 4.5）不一致，已 `git stash`（含原文件名 `仿真环境与IO闭环详细设计.md`），待与现行架构对齐后再决定是否合流；
- 本地未跟踪的 `scenegen/`（schema/components/validate/build_usd/iomap/cli + agent 半环）与 `runtime/`（isaac_opcua_server、gantry_jog_gui）为本侧早期实现，其目录结构与 master 的 `src/` 布局归并方案 = 共同议题区"仓库策略确认"，待三方决议后再决定迁入路径。
