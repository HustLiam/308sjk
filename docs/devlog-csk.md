## 2026-09-09 (2) 落笔 7mm 稳态根治：几何根因（球头半径未补偿）+ 判据语义提案

### 破案过程

e2e 落笔等待超时（Z 稳态 0.007 ≠ 0）→ 假设"限位/接触太软"做硬化实验（solreflimit
0.02/0.01/0.005 × 120/240/360Hz）——**四种配置稳态全部 7.01mm**，排除约束软硬；
真根因：MJCF 胶囊 `fromto` 定义的是**轴线端点**，胶囊几何向两端各延伸一个半径，
球面最低点比轴线端点低 7.5mm（笔半径）——**笔在 qz=7.5mm 就触纸了**，q=0 落笔
只是把球压进纸面 0.5mm（28N 下压力对接触刚度），7.01mm = 7.5 − 0.49。
**是组装器几何缺陷，不是控制/整定问题。**

### 根治三件套

1. **几何补偿**（mujoco_build 龙门 + plotter 两处）：笔轴长 = pen_length − pen_radius，
   q=0 时球面最低点恰在纸面。修复后落笔稳态 **<0.05mm**（接触穿透量级），压纸力
   也从 28N 回落到 ~0.2N（笔自重）——物理上更合理；
2. **poswin 消费**（runtime/acceptance.py 新增）：定位类目标验收容差从 spec 提取
   （linear_axis.poswin×scale；gantry 缺省 0.005），客户端/测试禁止硬编码阈值；
   plotter 测试改为 poswin 驱动（X/Y=20mm，Z=1mm）；
3. **判据语义提案**（csk 文档 §7.1 增补 + 共同议题登记）：定位类
   done=|fb−target|≤poswin；**接触类判"接触建立"**（接触带稳定/接触传感器/功能
   量测），不判位置相等——伺服压紧的力平衡稳态下位置相等在任何柔性接触中都不成立。

### 责任澄清

与 gc 无关：spec 无动力学字段，误差由本侧组装器几何与库值决定；gc 的 poswin=1mm
恰是验收钥匙。修复责任在 csk（组装器几何 + 测试容差硬编码）。

### 验证

- 新回归 `test_pen_down_press_settles_near_zero`（修复前 7.01mm 会红，修复后
  <0.05mm 过）；poswin 单测 + plotter 测试 poswin 化；runtime 28/28 全绿；
- 乌龙记录：实验脚本 f-string `solreflimit="{tc} 1"` 中 tc 已含 " 1"，拼出三值
  报 "too much data"，排查两轮——教训：插值拼参数串先打印。

## 2026-09-09 (1) example1：龙门模型反向导出 spec + io_map（契约包增补）

### 做法

只读 MJCF 模型文件提取：关节 range→travel、执行器 ctrlrange→io range、gantry_base
pos→pose、option→physics_dt/gravity、floor/default→ground、axisSpeed 取自组装注释
（运行时参数在模型数据面的唯一痕迹）。模型不携带的 8 个字段（scene_id/spec_version/
units/solver/lighting/资产 id/script）取 schema 合法缺省并逐项标注——**模型→spec 存在
信息损失边界，正方向才是无损的**。

### 验证

闸门通过（1 资产/6 IO）；规范化整文件往返 diff（忽略 model 名/注释）与原模型
**完全一致**；正向三步命令复跑（validate→build-mjcf→viewer 落笔画方块墨迹完整）。

## 2026-09-08 (3) 契约发布 v1.1 + 工作流机制化：纯命令完成「检查→创建→仿真」

### 动机（对上轮的纠偏）

plotter 实战暴露：gc 生成 spec 时没有组件契约可依（注册表只在 csk 代码里、无机器
可读发布），导致"闸门拒绝→csk 顺手补库"的混流——库登记与组装规则实质是 csk 的
即时设计而非契约产物。本轮把库沉淀为**正式契约包**发布给 gc，并把工作流固化成
纯命令调用（无人工补写环节）。

### 交付

- `cli components`：注册表 → 机器可读契约 JSON / 人类可读表格（与闸门同源，
  `MJCF_TYPES` 由组装器导出，后端支持列真实反映）；
- `cli build-mjcf`：validate → io_map 地址分配 → MJCF 落盘一条命令；
  **io_map 缺失直接拒绝**（实测对 gc 原始文件正确退出，不代拟）；
- `contract/`（v1.1）：components.v1.1.json + 组件契约表.md + scene.spec.example.json
  （参考 spec，io_map 为 csk 代拟范本待 gc 正式版）+ README.md（字段语义七条决议：
  类型封闭/io_map 必填/米制/pose 语义/轴链声明序/paper_area 基准/动力学缺省）；
- 推送 master（契约包，负责人指令）与 csk 分支。

### 严格流程实走（发布树上，纯命令）

① `cli validate contract/scene.spec.example.json` → OK（8 资产/6 IO）；
② `cli build-mjcf … -o out/plotter_cell` → scene.xml + io_map 三件套 + 分配表；
③ `mujoco_jog_runtime --scene --io-map` + 客户端方块闭环全绿；
回归 runtime 21 + 根 106 + scenegen 20 全绿。

## 2026-09-08 (2) plotter_cell 实战：gc master:scene.spec.json 走通 MuJoCo 工作流

### 流程实录（组件库新增已标注；组装规则 R1-R4 见 mujoco_build 源码）

- validate（gc 原始文件）FAIL——io_map 缺失（schema 必填）；
- 组件库登记 6 类型（ground/work_table/linear_axis/tool_head/pen/hmi_panel）
  + ParamSpec 扩 vec2/bool/str_list 形态（均【csk 2026-09-08 新增】标注）；
- io_map 补三轴米制草案（工作副本），重验证 OK；
- `_plotter_xml` 多资产装配 + mujoco_jog_runtime 通用化（travel/轴速从
  linear_axis 推导 = stroke×scale / vmax×scale；墨迹笔尖/纸面动态化）；
- `tests/test_plotter_build.py` 6 项（行程换算/纸面几何/qz=0 离纸 2mm/扫掠覆盖/
  闭环/超程钳位）+ e2e 方块绘制全绿。

### 回馈 gc 的发现（9 项，重点）

io_map 缺失（请出正式版）；plot_head.parent=y_axis 与 z_axis 链语义冲突（按轴
声明序成链）；轴 0 点语义（定为 pose=行程中心）；paper_area 基准（按 work_table
尺寸）；hmi_panel 无 IO 通道（本轮未接入）；动力学参数缺失（沿用库值）；ground
资产与顶层字段重复；参数形态扩展 3 种；spec_version 1.0 vs 1.1。

### 技术坑

fresh MjData 的 geom_xpos 全零（取几何前必须 mj_forward；龙门纸在世界原点碰巧
绿，plotter 纸心 (0.5,0.5) 暴露）；静态件 pose z=0 按落位面解释（按中心则台体
半埋、纸面低半台高）。

## 2026-09-08 (1) aml_parser Linux 兼容最小补丁（跨模块，附带给 gc）

### 现象与根因

- 合并 origin/csk 后根目录 pytest 7 例 test_aml_parser 全挂 `OSError(36) ENAMETOOLONG`；
- `parse_aml(source)` 用 `Path(source).is_file()` 探测"是文件还是 XML 文本"——内存
  XML 字符串（8KB、含换行）被当路径 stat，Linux ext4 单文件名上限 255 字节 → 必炸；
- **不是 Python 小版本差异**：实测 3.10.12 / 3.12.14 / 3.13.15（uv 独立构建）行为一致，
  pathlib 的 `is_file` 只吞 ENOENT/ENOTDIR 等少数 errno 后重新抛出；是 **Windows/Linux
  差异**（Windows 的 is_file 对非法名/超长名返回 False）——推测 gc 在 Windows 跑全绿。

### 修复

`_is_file_quiet()`：try/except OSError 包 `is_file`，异常按非文件处理走 `<memory>`
分支（Windows 行为不变）。跨模块改动，看板 →gc 登记请复核合入。

### 验证

根目录 100/100 + toolchain 6 + runtime 15 + scenegen 20 全绿（本机 Python 3.10.12）。

### 环境备忘

- 为对照实验用 uv 另装了独立 Python 3.12.14 / 3.13.15（`~/.local/share/uv/python`，
  系统 python3 未动）；日常工作继续系统 3.10，不需要迁移；
  `uv python uninstall 3.12 3.13` 可清理。

## 2026-09-07 (8) 实操反馈修复：工作区布局错位 + 落笔墨迹保留 + 原生视窗

### 背景（示教器实操反馈两个问题）

① GUI 画笔往右上走，MuJoCo 里笔尖出画板；往左下走，笔尖够不到纸——**坐标系错位**；
② 落笔后看不到画过的轨迹，无法核对 GUI 画布 ↔ 仿真动作是否一致。

### 根因（①）：纸张摆在根原点，笔尖行程却在 [pose, pose+travel]

`mujoco_build.py` 沿用了 USD 场景的摆法：纸张/底板以 gantry 根 body 原点为参考摆放。
但 spec 的 pose 语义是**笔尖行程原点**（左下角），三段滑动的扫掠区间是
[pose, pose+travel]，行程中心在 pose + travel/2——于是纸只盖住行程左下四分之一，
右上出纸、左下永远在纸外。**USD 场景同样存在此缺陷**（Isaac 6.x 从未跑起来过机构，
所以一直没人看见；MuJoCo 首次让笔真的动起来后暴露）。

### 改了什么

- **布局修复**：纸/底板/导轨/立柱全部以行程中心（pose + travel/2）摆放，纸面尺寸
  = travel 全行程（0.6×0.4）；GANTRY_CONST 中 x/y/z_offset 三个补正量删除
  （布局对齐后不再需要）；
- **墨迹（user_scn 渲染层，不进物理）**：笔尖离纸高度 < 12mm 时按 4mm 间距滴墨点
  （`mjv_initGeom` 球体，深蓝 0.1/0.1/0.55），固定落在纸面上表面 +1.5mm；
  环形缓冲 2000 点，抬笔自动断线。三个坑：`mat` 参数要 9 元旋转矩阵展平
  （`np.eye(3).flatten()`）不是四元数；所有参数必须 numpy 数组且 rgba 要 float32；
  落笔判断不能看 qz（伺服静差让 qz 停在 0.007，卡阈值）而要看**笔尖世界 z**；
- **`--viewer` 原生视窗**：`mujoco.viewer.launch_passive` + 周期 `sync()`（每 2 物理帧），
  首次 sync 后把相机设为 3/4 俯视（az 135° / el -42° / 距离 1.9m / 注视工作区中心），
  可正常拖转缩放；无 GUI 环境自动回退无头模式（`--watch` 路径不变）。

### 验证

- 新增回归 `test_pen_sweep_covers_paper`：四角行程极值 (0,0)/(0.6,0)/(0,0.4)/(0.6,0.4)
  各 mj_forward 后，z_carriage 的 xy 必须落在 paper geom 包围盒内——布局再错位
  会直接红；
- 6/6 全绿；根目录 75 项无回归；进程级：GUI 折线 (0.05,0.05)→(0.5,0.33)→(0.12,0.35)
  经 Modbus 画完，视窗截图经视觉模型确认**深蓝轨迹线保留在纸面中部**。
- 合并 origin/csk（链路A/分工对齐）后复跑：toolchain 6 + runtime 15（含本组 6）+
  scenegen 20 全绿；根目录 93/100——7 例 test_aml_parser 为远端固有的 Python 3.10
  pathlib 兼容问题（`Path(超长str).is_file()` 在 3.10 抛 OSError36、3.11+ 吞掉），
  干净检出 origin/csk 同样复现，非合并引入，已反馈 gc。

## 2026-09-07 (7) MuJoCo 轻量仿真面：Isaac 6.x 关节回归的备选后端

### 背景（真机联调结论，Isaac 链路的现状）

真机（RTX 4080SUPER / Isaac 6.0.0-rc.59 离线包与 6.0.1.0 pip 正式版双双实测）
上龙门机构**不动**。经 tensor API（PhysX 真值）逐轴排查：同场景内自由刚体（探针
立方体）下落/碰撞/静止全部正常，但棱柱关节链行为系统性错乱——X 轴驱动方向反转
（目标 +0.3m 实际走到 -0.3m）、Y 轴被拖到限位外、Z 轴"冻结"实为反向驱动力被笔-
纸接触挡住。交换 body0/body1、加 ArticulationRootAPI、静态基座、CPU dynamics
（PhysxSceneAPI.EnableGPUDynamics=False）逐一对照均无效——**Isaac 6.x PhysX
(110.1.11/110.1.13) 对 maximal-coordinate 棱柱关节链的解算回归**，非本侧代码或
场景问题（USD 语义按 4.5 时代规范书写，自由刚体路径同一进程正常）。

### 改了什么

新增 MuJoCo 后端（`runtime/mujoco_build.py` + `mujoco_jog_runtime.py`），与
Isaac 工作流 A 同构、与 USD 链路消费同一份 scene.spec.json：

- **组装器**：gantry_xyz 组件 → MJCF（三段滑动关节链 base→x→y→z + position
  执行器 + 行程限位）。质量 4/3/0.4kg、kp 6000/6000/4000、阻尼 250/250/80 与
  USD DriveAPI 同源；关节 q=0=作者位姿、Z 0=落笔/travel=抬笔语义不变；
- **运行时**：主循环 = 指令寄存器 →（axisSpeed 速率限制，与 StageLink 同语义）
  → `data.ctrl`；`mj_step` 后 `data.qpos` → 反馈寄存器。Modbus 层完整复用
  `gantry_bridge`（布局/钳位/开场抬笔约定不变），PLC 侧与示教器零改动；
- **依赖**：`mujoco>=3.2`（pip 几 MB，无 GPU/许可证依赖），已登记
  `runtime/requirements.txt`。

### 技术坑（MJCF 组装的两处自碰撞顶死）

- MuJoCo 只过滤**父子** body 间的碰撞，隔代（如 x_carriage 的 saddle ↔ z_carriage
  的 slider）照样碰撞；而龙门几何本来就是"滑座骑导轨/滑块穿床头"的视觉互穿形态，
  开场即 -15mm 穿深 → 600N 驱动力全被接触力顶死（acc=0、关节以 4mm/s 蠕动）。
  排查法：`mjDSBL_CONTACT` 关接触对照 + 遍历 `data.contact` 打印几何对；
- 修法：机构件（立柱/导轨/滑座/床头/滑块体）`contype=0 conaffinity=0`，只保留
  **笔尖↔纸面**与整机↔地面两类功能接触——机构运动由关节限位约束，不靠碰撞。

### 验证

- `tests/test_mujoco_loop.py` 5 项全绿：MJCF 可编译且限位与 io_map 布局一致、
  指令经斜坡驱动真实 qpos（非指令回声）、超程 9.9m 钳位到 0.6、开场 Z 0.4s 抬到
  0.2、阶跃成斜坡（0.2s 时 X≈0.1）；
- 进程级端到端：runtime 启动 ~4s（Isaac 20s~3min），spy 客户端拖 (0.3,0.2)+落笔
  0.8s 到位、超程钳位 ✓；
- 示教器全闭环：GUI 拖动 → Modbus → 斜坡 → MuJoCo → qpos 反馈 → 画笔回读，
  读数栏显示真实位置（X=0.300 Y=0.200 Z=0.000）；
- 根目录 pytest 75 项 + runtime 桥回环 6 + StageLink 3 全绿（无回归）。

### 附带修复（真机联调期间发现，一并入库）

- `gantry_jog_gui.py` 写失败路径两个 bug：① `except ... as exc` 的 `exc` 在块外
  被 Python 删除而延迟 lambda 引用 → NameError，状态栏永远不显示"写入失败"；
  ② 失败后未断开死连接，`connected` 仍 True，重连需点两次"连接"。修法：先捕获
  `msg=str(exc)` 再调度 after 回调，失败时补 `client.disconnect()`；
- `isaac_jog_runtime.py` import 双路兼容（离线包 `isaacsim.simulation_app` /
  pip 元包 `isaacsim` 顶层导出）。

## 2026-09-07 (6) 第三次许可制合并走查（分工与场景对齐）

- 合并内容：删除 scenegen/agent（分工越界自纠）+ 滚筒分拣线废弃场景；场景现役集 =
  motion3axis + 三轴绘图仪；master 合并后 pytest 115 全绿；
- 流程第三次走查，devlog 移除/重建同款；网络间歇中断下推送均重试成功。

## 2026-09-07 (5) 分工与场景对齐（负责人指令）

### 指令

① gc/csk 分工按项目文档（架构 v1.2/v2.0）执行而非按当前工作——csk 不含 agent；
② 不符合项目文档的废弃项目直接删除——滚筒/传送带分拣线例子；
③ 现役场景只有运动控制（motion3axis）+ 三轴绘图仪（gantry_xyz）。

### 执行

- 删除 `scenegen/scenegen/agent/`（7 文件，含 MockLLM 生成-校验-重试闭环）——我此前
  越界实现了 gc 的 ②b LLM 本体，属分工越界；gc 的场景描述生成器今后以
  `validate(spec)` 闸门 + 契约③ schema 为唯一对接点；
- 删除 `examples/conveyor_sort.json` + `out/{example,agent,glm}`；规范示例改为
  `examples/gantry_plotter.json`（与 out/gantry 同源，git 识别为 rename）；
- test_scenegen 重锚：8 组校验器断言全部改用绘图仪/微型夹具（气缸 axis 枚举作为
  组件级校验覆盖，非场景），构建/Modbus/关节黄金规则断言 gantry 化——20 组绿；
- 文档：csk 文档 §0/§4.1/§4.2（示例换绘图仪 JSON）/§4.3（现役场景说明）/§8/§9、
  scenegen README 重写、看板 csk 区块与变更记录、changelog 场景对齐 v0.2。

### 验证

scenegen 20 组 + pytest 115（master 100 + toolchain 6 + runtime 9）全绿。

## 2026-09-07 (4) 第二次许可制合并走查（链路 A v0 + 契约③ draft.1）

- 合并前 devlog 移除（9c0d857 同款流程）、master 合并后 pytest 115 全绿（100+6+9）；
- 契约③ draft.1 已挂共同议题待 lx/gc 评审；L3 待 lx 工具链机协跑（MATEC_ROOT）；
- 本日志按规则合入前移除、合并后重建（第二次走查，流程已熟）。

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
