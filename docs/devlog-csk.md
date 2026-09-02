# csk 本地开发日志

> 仅技术说明（改了什么 / 为什么 / 如何验证 / 技术坑）。进度协调内容一律写 `docs/协作看板.md`。本文件在 master 合入前移除，永不进 master。

## 2026-09-02 (2) 龙门场景三缺陷修复 + 运行时回环切 Modbus

### 缺陷与根因

1. **部件"飘移"、手改 translate 弹回**：`_build_gantry` 的 `joint_x` body0 指向
   `x_rail`，而它只有 CollisionAPI 没有 RigidBodyAPI——PhysX 拒用 body 非刚体的关节，
   整条链（y_bridge/z_carriage/pen）成自由体，重力下悬挂倾斜（截图：pen 下坠 0.14m
   且带 9° 倾斜）。播放中物理每帧覆写位姿，手动改 translate 自然弹回。
   `_build_cylinder` 的 base 同病。
2. **X 轴"只是物体"**：运动链没有显式 x_carriage（导轨是纯静态件、桥身叫 y_bridge
   却沿 X 动），命名与结构都不能自解释。
3. **Z 轴语义反 + 参数不稳**：q∈[0,tz] 沿 +Z 只能上抬，笔尖永远够不到纸面；
   k=6000/m=0.3 在 60Hz 下 dt·ω≈2.36>2（违反本文件自家整定准则），会抖。
4. **jog 环路断连报错 + Isaac 卡死**（problem.txt）：asyncua sync 包装在 Isaac 进程内
   的会话/超时语义（3600000→600000ms 截断、CloseSession 超时、Unhandled exception），
   与 GUI/物理线程争用 GIL，断连时互等。主路线已定 Modbus，直接结构性消灭该类故障。

### 修法（scenegen）

- 新增 `_kinematic_body`：关节固定端 = kinematic 锚刚体（USD 的固定锚标准做法），
  静态几何（base_plate/paper/立柱/x_rail）挂其下成为 kinematic 形状；
- `_prismatic_joint` 增加 `target` 参数（开场驱动目标）；
- `_build_gantry` 重构为显式三轴链 `base→joint_x→x_carriage→joint_y→y_carriage→
  joint_z→z_carriage`；Z 轴语义翻转：**q=0 落笔（笔尖距台面 2mm）、q=tz 抬笔**，
  开场 target=tz 保持抬笔（视觉上笔插在主轴头/滑块里，全行程不脱接）；
- joint_z 整定：m=0.4、k=4000、d=80、F=300 → dt·ω=1.67<2、ζ=1、重力下坠 0.98mm<1mm；
- pen 无碰撞（避免与台面接触抖动，绘图验收走 trace 坐标不受影响）；
- 新增 `simio:posBody`（StringArray）+ `simio:posRest`（FloatArray）：运行时桥按
  "刚体 translate 分量 − 关节零位坐标"回读关节坐标 q，不依赖 PhysX 专有 state API；
- io_map 绑定的 usd_prim（joint_x/y/z）路径不变，旧 io_map/spec 契约兼容。

### 防复发

- `smoke.structural_check` 增加黄金规则：关节 body0/body1 指向的 prim 必须有
  RigidBodyAPI（本次缺陷正是"只查 rel 存在、不查目标是刚体"漏掉的）；
- `test_scenegen.py` 新增 7 组断言：关节两端全刚体、base kinematic、Z 轴语义
  （limits/target/落笔位笔尖 2mm）、三轴刚体齐全、io_map 绑定、posBody/posRest；
  全套 22 组断言绿，`out/gantry`、`out/example` 已重生成。

### 运行时切 Modbus（runtime/）

- 新核心 `gantry_bridge.py`：布局从 io_map.json 推导（位置区=契约 server_register，
  指令区紧随传感区块），float32 大端与 %QW REAL 编码一致；服务端线程独立事件循环
  （Windows 用 SelectorEventLoop，Proactor 关停噪声大）；就绪判据 = TCP 探活 +
  0.25s 稳定（TCP 背板队列会先于应用层可服务，纯探活会假就绪）；
- `isaac_modbus_server.py` 替代 opcua 服务端：20Hz 指令变化才写
  `drive:trans*:physics:targetPosition`（沿用已验证的机制），每帧回写位置反馈；
- `gantry_jog_gui.py` 重写为 pymodbus 客户端：拖画笔写 X/Y、Z 抬/落笔按钮、
  5Hz 读反馈寄存器让画笔跟随**实际位置**（跟随误差直观可见）；
- 旧 asyncua 三件套移入 `runtime/legacy_opcua/`（v4 备选链路评估时再参考）；
- **版本坑**：pymodbus 3.13+ 移除 ModbusSlaveContext.get/setValues，3.8 的
  get/setValues 内部固定地址 +1（服务端请求与本类数据面走同一方法故一致，datastore
  多留 1 字覆盖）；`requirements.txt` 锁服务端 `>=3.7,<3.9`，GUI 客户端任意 3.x；
- 回环测试 `tests/test_modbus_loop.py`（独立脚本+pytest 双模式，6 项）：地址推导、
  FC16→桥、超程钳位、FC03 反馈回读、**断开重连**（asyncua 痛点回归）、迷你闭环。
  专用 venv（pymodbus 3.8.6）全绿。

### 验证汇总

- `python scenegen/scenegen/tests/test_scenegen.py` → 22 组全绿；
- `python scenegen/scenegen/tests/test_agent.py`（离线 MockLLM）→ 4 组全绿；
- `venv(pymodbus3.8.6) runtime/tests/test_modbus_loop.py` → 6 项全绿（pytest 模式亦绿）；
- 重生成产物关节核验：6 个 body 引用全部 rigid=True，base kinematic=True。
- 待真机：Linux Isaac 6.0 上重开 scene.usda，Play 后确认部件不再漂移、
  jog GUI 走 Modbus 全程无卡死（本机无 Isaac，无法替代）。

## 2026-09-02 (1) 模块 ③ 职责收窄：SceneSpec JSON 生成移交给 gc

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
