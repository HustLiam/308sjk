# csk 本地开发日志

> 仅技术说明（改了什么 / 为什么 / 如何验证 / 技术坑）。进度协调内容一律写 `docs/协作看板.md`。本文件在 master 合入前移除，永不进 master。

## 2026-09-07 (3) 链路 A v0（shim 生成/构建编排/ctypes 绑定）+ 契约③草案

### 改了什么

- `toolchain/shim_gen.py`：io_map（契约③）→ `plc_shim.c/.h`——extern 符号表（__QX0_0/
  __QW0 风格，matiec 版本差异的隔离点 SYMBOL_RULES）+ 紧凑镜像 di/ai/dq/aq + 稳定接口
  plc_init/run/write_image/read_image；纯函数无 IO；
- `toolchain/build_dll.py`：xml→st（子进程复用 lx xml2st，转换点唯一）→ iec2c → shim →
  gcc 共享库；工具链发现顺序 环境变量(MATEC/CC)→PATH；缺失时 ok=False + 可操作提示
  （不抛异常不半成品），build_result.json 供编排器消费；
- `runtime/plc_binding.py`：IOLayout（与 shim 同规则的镜像索引 + 定点换算 scale=
  每 LSB 工程量 + 打包/解包，纯逻辑）+ SoftPLC（ctypes 薄封装）；
- `schemas/io_map.schema.json`：契约③ v1.0.0-draft.1（见 changelog）；
- `toolchain/tests/test_link_a.py`：L2（golden/双实现一致性/换算/Schema/降级）+ L3
  （minimal.st→DLL→写读回环，无 matiec+gcc 自动 SKIP）。

### 技术要点

1. **镜像索引单一规则双实现**：shim 的 C 侧赋值下标与 Python 侧打包下标必须一致，
   否则注入错位——两端各自实现 + golden 测试断言逐变量相等（test_layout_matches_shim_gen），
   测试是唯一仲裁，注释互相指向；
2. **紧凑镜像**按方向独立编号（di/dq bool 字节、ai/aq int16 字），不用原始地址做下标——
   %QW10/11/12 这类稀疏地址不浪费镜像空间，shim 生成时逐变量显式赋值；
3. **word 类型**（CiA402 状态字）不做定点换算，位型透传：to_raw 掩码 &0xFFFF、
   to_eng 无符号解释（raw -1 → 65535）——INT16 有符号容器承载无符号域的坑在绑定层消化；
4. **L3 前置条件探测**：find_toolchain() + MATEC_ROOT；本机无 gcc/matiec/WSL/docker，
   L3 在具备工具链的机器上跑（lx 侧工具链最全，看板已请求协跑）；
5. 测试踩坑：pack 的 100.5/0.1=1005 而非 1000（断言笔误）；aq 镜像 3 字（move_done
   在 dq）——镜像下标按方向独立计，测试造数时别把各镜像长度想混。

### 验证

pytest tests/ + toolchain/tests/ + runtime/tests/ = **115 passed**；scenegen 22+4 绿。
L3（真编译）SKIP——待 lx 工具链机协跑。

## 2026-09-07 (2) 首次许可制合并走查（csk → master）

### 合并策略与冲突解决

- master 在本分支开发期间前进了 88 个提交（架构 v1.1→v2.0：编号改 ①②a②b③a③b④、
  新增 ⓪ AML、csk 文档重排为标准格式 v1.3、看板重置、场景库收敛 motion3axis）；
- **关键发现**：master 90476dd（架构 v1.2）已由负责人独立落地与本分支一致的分工调整
  （②b 场景描述生成归 gc、评审归 csk，组件库随 USD 构建器归 ③b）——本分支的五份
  文档修订全部被官方版本覆盖，冲突解决统一 `git checkout origin/master -- docs/`，
  本分支保留的增量仅为 scenegen/ 与 runtime/；
- 看板以 master 重置版为基线，手动刷新 csk 区块（真实进度）+ 变更记录登记合入条目；
- devlog 按规则合入前 git rm（9c0d857）、合并后重建（本提交）。

### 合入前 DoD 走查（本机）

- 合并 origin/master 进 csk 解决冲突后：master 侧 `pytest tests/` **100 passed**
  （venv：pymodbus 3.8.6 + usd-core + requests + pytest；全局 python 缺 pytest，主仓
  测试统一走 venv 跑）；
- scenegen 22 组 + agent 4 组 + runtime 9 项全绿；
- master 合并后树与 csk 完全一致（`git diff csk master` 为空）+ pytest 复跑 100 passed。

### 遗留/注意

- **float32/INT16 换算归属**（lx 登记的共同议题）：当前 iomap.py 与 gantry_bridge.py
  均为 float32 大端；倾向采纳 lx 建议（换算归桥侧、PLC 保持 16 位字域），定稿后需
  同步改两处并按 §8.3 走 RFC + changelog——这是下一个契约动作，别忘；
- venv-modbus 现在承担主仓 pytest + runtime 测试双职责（requirements：pymodbus<3.9、
  usd-core、requests、pytest）。

## 2026-09-07 真机回归修复：joint_z 开场饱和弹射穿纸

### 现象（真机 Play 复现）

开场位姿正确；Play 后笔直接掉到纸下面，"z 坐标像没固定住"。X/Y 链稳定（bridge 在
导轨上、carriage 在桥上），只有 z 组件掉落——滑块停在纸面高度、笔穿透纸面。

### 根因

joint_z 是唯一"开场驱动目标 ≠ 作者位姿"的关节：场景从 q=0（落笔位）启动，而
drive targetPosition authored 为 tz=0.2。Play 瞬间 0.2m 误差 → 力饱和（min(k·e,
maxForce)=300N）→ 0.4kg 滑块以 ~750m/s² 弹射，60Hz 下单步位移厘米级，直接隧穿
q≥0 限位与纸面，混沌后卡在纸下。X/Y 目标均为 0（零初始误差）故安然无恙。

### 修法（三层防御，任一层单独即可避免）

1. **场景层**（components._build_gantry）：joint_z 开场 target=0（三轴统一零初始
   误差），场景开场静置于落笔位（笔尖距纸 2mm）；pen 改为有碰撞——任何残余故障下
   笔停在纸面而非穿透；抬笔改由运行时完成；
2. **运行时层**（stage_link.StageLink）：所有指令经 axisSpeed（simio:axisSpeed，
   默认 0.5m/s）**速率限制**后写驱动目标，_last_cmd 以作者位姿 0 为起点——寄存器
   阶跃（GUI 抬笔按钮、将来 OpenPLC 一次写 0.2m）都变成 ≤0.5m/s 的斜坡，物理上
   不可能再弹射；编辑器 20Hz/独立运行时物理帧各自传真实 dt；
3. **回归层**：test_scenegen 断言三轴开场 target 全 0 + 笔有碰撞；test_stage_link
   新增抬笔斜坡测试（单帧增量 ≤ axisSpeed·dt、0.2m 恰好 ~24 帧抬完）。

### 技术坑

- 斜坡断言被 USD float32 舍入（~1e-8）击穿 1e-9 容差——stage 回读值做增量断言时
  容差至少放宽到 1e-6；
- 0.2m/0.5m/s 抬笔耗时 0.4s ≈ 24 帧（60Hz），测试帧数窗口按此卡。

### 验证

scenegen 22 组 + agent 4 组全绿；venv runtime 测试 9 项全绿（桥回环 6 + StageLink 3，
含新斜坡测试）；out/gantry、out/agent 已重生成。待真机：Play 后笔应静止于纸面上方
2mm，GUI 连接后 ~0.4s 平滑抬起，拖动全程无穿透。

## 2026-09-02 (3) 独立运行时：脱离 Script Editor 的 Modbus 工作流

### 改了什么

- 新增 `runtime/isaac_jog_runtime.py`：一个 Isaac Python 进程同时承载「SimulationApp +
  World 物理主循环 + Modbus 服务端」，`--window` 可选带渲染窗口，`Ctrl+C` 干净退出
  ——外部示教器/OpenPLC 桥照旧连 :5020，**全程不需要打开 GUI 编辑器、不需要粘贴脚本**；
- 抽出 `runtime/stage_link.py`（StageLink）：simio 标记 + io_map → 关节驱动/位置回读
  的接线逻辑，编辑器脚本与独立运行时共用一份（此前接线逻辑内联在编辑器脚本里）；
- 两个入口均补"开场指令 = X/Y 原点 + Z 抬笔"（与场景初始位一致，客户端接管前不跳变）；
- 新增 `tests/test_stage_link.py`：用 usd-core 打开仓库真实 scene.usda + 真 GantryBridge，
  验证「FC16 指令 → 关节驱动属性」「刚体位置 → 反馈寄存器 → FC03 读回」「超程钳位」
  ——StageLink 全链路无需 Isaac 即可回归（无 usd-core 时自动跳过）。

### 为什么

外部 Modbus 客户端无法伸进运行中的 Isaac 进程改关节驱动，进程内必须有承接者；
但承接者不必是 Script Editor 粘贴脚本——让承载 Isaac 的进程自己当服务端即可，
这正是闭环 run_sim 骨架（csk 文档 §3.3）的形态，OpenPLC 桥接入时该脚本不变。

### 验证

venv(pymodbus 3.8.6 + usd-core)：`pytest runtime/tests/ -q` → 8 passed
（桥回环 6 + StageLink 2）。isaac_jog_runtime.py 本体无法在本机验证（无 Isaac），
待真机跑 `--window` 工作流。

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
