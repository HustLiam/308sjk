# 场景生成 Agent 与 DeviceIR（0.1 草案）

2026-10-27，gc。状态：本地实现与候选验证，待 csk/lx 评审；没有修改四份冻结契约，没有宣称三方批准。

## 1. 范围与入口

2026-10-28 增量：AML 新增独立 `SimulationRequirements` 草案，生成机械/安装/耦合/IO 测试数据脚本与固定 MuJoCo 检查证据；网页可勾选独立测试并切换回放。配置、迁移、未覆盖能力及产物定义集中见 [AML 仿真验证配置](AML-仿真验证配置.md)。检查判据取自 AML/固定程序，Agent 仅增加合法采样。用户选跑子集不替代全量闸门或人工审批，不改变旧四份冻结契约，原 R5/final 接入仍待 csk/lx 评审。

新入口是 `python -m src.agent.scene_agent`。场景生成使用 AML 和自然语言，输出独立的 DeviceIR、组件候选、MJCF、物理 IO 绑定和检查证据。PLC 生成器、PLCopen XML 格式、xml2st、matiec 部署链和原有验收判据不变。

旧 `SceneSpecGenerator` 的三轴路径保持原行为；当 AML 明确描述无伺服轴的离散设备时拒绝回退生成龙门。2026-10-27 已按用户要求把新格式接入网页独立的“设备场景生成”页；后续补充冻结模型自动同步到“PLC 仿真与验证”页的模型预览、只读IO与产物链接。原 PLC 运行/验收及编排器继续消费冻结 SceneSpec，尚未关联 PLC 的新设备明确显示未关联，不调用旧任务的运行接口。新格式接入原 PLC R5/final 流程仍需 csk/lx 完成字段及判据评审；不得把新 `io_map.json` 直接塞入旧 SceneSpec 验证器，也不得跳过原 PLC 闸门。

实现位置：

| 文件 | 职责 |
|---|---|
| `src/agent/scene_agent/device_ir.py` | CAEX 属性、稳定 ID、SI 单位、接口图、机械关系、AML IO 与局部修订 |
| `src/agent/scene_agent/properties.py` | 接口局部帧读取、显式范围内的接口属性修订；原规则与运行时不变 |
| `src/agent/scene_agent/author.py` | LLM 编写候选规则、根据确定性错误修正、自然语言局部修改 |
| `src/agent/scene_agent/requirements.py` | 面向 PLC 验证的模型要求、阻断问题与适用范围说明、已实现的 IO 物理量目录 |
| `src/agent/scene_agent/rules.py` | 规则 DSL、参数范围、候选区、人工审批、版本和字节完整性 |
| `src/agent/scene_agent/blender*.py` | 受限 Python 几何脚本、本机 Blender 后台建模和导出 |
| `runtime/device_scene.py` | 接口变换、确定性 MJCF、实际运动/接触/光线检测/计数 |
| `runtime/device_plc_link.py` | 冻结设备的 Modbus IO 接入；不生成、部署或修改 PLC |
| `src/agent/scene_agent/validation.py` | 独立组件检查、装配检查、参考分拣线行为夹具 |
| `src/agent/scene_agent/workflow.py` | 任务状态、最多三轮候选构建修正、产物保存 |
| `src/agent/scene_agent/web.py` | 网页后台任务、版本记录、人工审批与冻结、模型下载 |
| `src/agent/web_ui/device_scene.js/css` | 新场景页、三维网格、局部修改、实际测试回放 |
| `src/agent/web_ui/device_tree.js` | 组件包含树与同级运动副类；实例引用两端组件，保持设备事实不变 |

## 2. 中间表示与事实来源

格式为 `308sjk.device_ir/0.1-draft`，Schema 在 `schemas/scene_agent/`。Python 验证器还检查 Schema 无法表达的图引用、重复地址、参数域、坐标和 IO 所有权。

* `components`：保留 CAEX ID、名称、类型、父级、参数、局部位姿、接口 ID 和来源。
* `parameters`：保留 SI `value/unit`、AML `original_value/original_unit`、数据类型、嵌套属性和来源。字段名保持 AML 原名，因此 `incline_deg` 的规范化值仍为弧度，不能再次乘角度换算。
* `interfaces/connections`：完整端口图，保留 MaterialFlow、IO 与机械连接的不同语义。不会以最近距离代替已声明的连接。
* `joints`：父/子刚体、子坐标轴、两侧锚点、范围、初值和来源。支持 slide/hinge，ball/free 可表达为被动关系；自由工件另用 freejoint。新增驱动语义必须有已实现的确定性行为内核。
* `assemblies`：固定安装的两侧组件/接口、偏置与 `check/solve` 模式。
* `io`：保留 AML 名称、方向、BOOL/INT、%Q 地址、范围、初值、单位比例、物理组件/quantity 和写入者。不存在自动重新分配或补造通道。
* `pending`：未知单位、缺稳定 ID、未确定坐标系或未绑定 IO 等问题。存在待确认项时停止正式构建。

约定右手系 Z 向上，长度 m、角度 rad、时间 s、质量 kg。原始文件 SHA、规范事实 SHA 和修订历史随任务保存。IO 继续复用现有 AML 解析器的权威通道列表；新增物理属性解析不影响 PLC 设备模型。

### 2.1 固定安装接口

AML 两个 `ExternalInterface` 的 `RefBaseClassPath` 尾名为 `MechanicalMount`；各自声明 `assembly_role=parent/child`。接口局部坐标使用 `position_x/y/z_m` 和可选 `rotation_x/y/z_deg`（CAEX Unit 明确）。也可以由已审批规则的 `model.interfaces` 按同名端口提供局部坐标。

`InternalLink` 连接两个接口；链接属性 `placement_mode` 默认为 `check`，可显式选 `solve`。可选 `offset_x/y/z_m`、`offset_x/y/z_rad` 表达安装偏置：

```
T_world_child = T_world_parent × T_parent_mount × T_offset × inverse(T_child_mount)
```

`check` 保留 AML 已给位姿并验证对齐；`solve` 是显式请求从接口推导位姿。重复安装父节点、安装/运动副冲突、变换循环、锚点不对齐均阻断。MaterialFlow 连接不会被误解释成固定装配。

## 3. 规则、Blender 与审批

规则为 `308sjk.component_rule/0.1-draft`，使用 semver。每条规则声明几何、刚体树、内部运动副/equality、安装坐标、物理参数、行为内核、参数适用域、建模近似与未解决问题。

LLM 决定候选分解和几何规则；确定性程序执行单位转换、装配、MJCF 和反馈。LLM 不能审批、改变 AML IO 或降低判据。未知的驱动/反馈方式应列为未解决项。当前行为内核包含输送、单命令直线推料、光电射线、工件源/出口、面板、指示灯、刚性工件，以及空载关节空间位置伺服与使能/复位状态；关节空间范围和编码见 [AML 仿真验证配置](AML-仿真验证配置.md)。逆运动学、碰撞避障、气动压力动态等其他行为仍需独立内核与测试。

简单几何使用 MuJoCo 基本形状。复杂几何的候选脚本只定义 `build(params, api)`，通过受限 GeometryAPI 调用 box/cylinder/mesh/difference/body/clearance_probe。后台 worker 在 Blender 自带 Python 中实际调用 `bpy/bmesh/mathutils`；候选无文件、网络或任意 Python API。运行使用 `--background --factory-startup --disable-autoexec`、独立目录/进程、超时和几何预算；这不是操作系统级安全沙箱。

默认 Blender 是 `E:/Blender/blender.exe`，本机实测 5.2.2 LTS。每个刚体分别导出 OBJ，保存 `.blend`、脚本、参数、刚体原点、碰撞近似、组件清单、预览、版本与日志。可运动部分不能合并为一个视觉网格。当前碰撞近似使用基本形状；复杂凸分解没有自动实现。通道检查使用显式 clearance probes，当前自动探针支持无旋转 box 代理；其他代理需要补充验证，不能带未解决项审批。

**参数化规则不复用某次样本的固定网格**。每个正式实例都按自身参数重跑脚本，检查尺寸、流形、刚体映射、碰撞代理一致性、空腔探针，再复制哈希命名网格到场景资产。Windows 中文路径通过 MuJoCo 虚拟文件系统加载。

候选状态与审批状态分开：

```
DeviceIR → 缺组件 → LLM 候选 → 确定性构建/检查 → 错误反馈给同一组件（最多3轮）
                                      ↓
                        检查通过 + 人工核对预览/近似/参数来源
                                      ↓
                             明确人工审批 → approved
```

`candidates/` 与 `approved/` 隔离。审批 CLI 必须有 reviewer、note 和当前 review SHA；网页支持单项与批量核对，审批说明选填，留空时记录用户对当前版本的勾选确认，不伪装成用户手写意见。批量操作沿用单项版本校验并分别保存记录，中断时明确列出已完成项。review SHA 绑定规则、全部资产、检查报告及机械/IO 实现源码的哈希。通过检查不会自动审批；候选不能连接 PLC 或冻结。审批后修改规则需新版本；实现变化也要求重新验证/评审。审批是本地操作者记录，当前没有多用户身份认证服务。

### 3.1 面向 PLC 验证的模型要求（2026-10-28）

生成目标为**面向 PLC 验证的可执行设备模型**，要求记录在任务的 `model_requirements`，配置标识为 `plc_executable_device/1`。它不代表已完成 PLC 验收，也不增加旧四份冻结契约的字段。

| 必须核对 | 模型要求 |
|---|---|
| 组件与连接 | 刚体划分、父子关系、安装接口、局部坐标和连接一致 |
| 机械约束 | 运动副类型、轴向、锚点、限位与行程正确，避免重复约束 |
| 可执行驱动 | 指令引起实际运动；按 AML 和验证目标实现故障、复位及联锁行为 |
| PLC 映射 | 名称、逻辑方向、类型、地址、单位、写入者与物理量绑定保持 AML 原定义 |
| 传感反馈 | 根据实际位置、接触、射线和出口进入状态计算，不复制指令或预设答案 |
| 验证证据 | 组件、装配、独立行为和 PLC 在线验收分别记录，未完成不能标记通过 |

简单几何足以使用基本形状；保留会影响接触、通道、限位和检测的尺寸。不影响本次验证的外壳外观、纹理、材质细节和电气内部构造，不因未提供而直接阻断。接受的等效质量、惯量或响应近似写入 `assumptions`，未建模细节与适用范围写入已有可扩展字段 `provenance.limitations`，生成配置标识写入 `provenance.validation_profile`；不改规则 DSL 或已审批规则的字节。`unresolved` 只记录影响当前验证的缺失能力或工程事实，并写清受影响行为与所需补充信息。未实现的必要驱动、缺反馈、错误约束或 IO 冲突仍必须阻断，不能改名为适用范围说明来通过检查。旧候选的缺项不自动重新分类，需重新生成或经候选修订后核对。

操作面板可只实现 AML 的逻辑触点与设定值，不要求额外的外壳模型或按钮电路图。项目中的 `dir` 是 PLC 数据逻辑方向，`%QX/%QW` 是共享 Modbus 映射；`dir=input, writer=operator, address=%QX...` 本身不矛盾。作者在组件上下文中核对实际支持的 IO 物理量；无运行时支持且未声明阻断问题的候选会被拒绝。运行时、冻结和 PLC 接入仍使用原有检查，不放宽通过条件。

传送带示例 AML 与离线参考候选已按该范围更新，参考规则版本为 `0.2.0`；保留稳定 ID、19 路 IO 和原有物理检查。旧示例缓存清理使用 `tools/archive_sorting_scene_cache.py --root <工作台根目录>/device-scenes` 预览，只有 `--apply` 才移动文件。仅匹配 FactoryForgeSortingCell 或原示例文件名，归档其任务及未被其他设备使用的规则类型；其他输送设备、六轴历史和共享组件保留并核对 SHA256。归档位于所选缓存根目录的 `archive/`；`cache_reset.json` 只列已移出的任务 ID，前端刷新时仅清理这些任务的来源指针，保留其他设备与 PLC 会话选择。执行前须确认服务和任务空闲。

候选生成的格式反馈会给出具体组件、实例和不支持的字段。作者层仅规范化两种可证明无损的冗余：空的非标准`model.behavior_parameters={}`，以及单个基本形状碰撞体与刚体总质量完全相同的重复质量声明；原回复和每次规范化路径均保存。非空行为参数、未知字段和冲突质量不静默删除，也不扩展执行器行为枚举。

几何检查全部通过但存在`unresolved`时，网页可构建`geometry_only`静态候选预览。组件验证状态仍为失败、不可审批；不执行MuJoCo步进/整机行为夹具，不生成回放，不冻结。几何构建使用单独的渲染投影；保存的DeviceIR、其SHA、原IO名称/方向/类型/地址/绑定完整保留，IO绑定显式标记`declaration_only_not_executable`，报告只列几何加载、装配及声明保留，不报告物理运行或IO行为通过。参数/几何失败、版本过期仍阻断预览。2026-10-28 已补充空载关节空间驱动与固定 IO 检查；超出其声明范围的机械臂能力继续阻断，正式构建和审批条件不放宽。

## 4. 仿真、冻结与 IO

MJCF 从机械关系确定性生成 body/geom/joint/equality/position actuator。内部刚体树支持被动 slide/hinge 与 joint/connect/weld 约束；未实现的驱动不会由 LLM 任意执行。质量可按明确的总质量和基本形状体积划分；视觉网格默认不重复增加质量。MuJoCo 由形状和质量计算惯量，这属于模型近似，不代表材料辨识。

参考输送带通过**实际接触**触发有摩擦上限的切向力（20ms 速度响应近似）。推料器是明确质量/增益/阻尼的伺服近似，反馈读取实际 q；故障使用临时 joint equality 保持实际位置。射线使用 `mj_ray`；出口计数来自实际变换后的工件与出口区重叠，每个生成实例只计一次。出口区目前使用包围盒近似；不声称任意非凸工件的精确交集。源按 AML 物料连接寻找输送带，固定容量池实现发料/回收，持续高电平不重复发料。具体近似在每条候选规则中供人工审查。

外部只能写 operator/fault_harness 输入；传感器、到位信号和计数由仿真独占。PLC 只能写 AML 声明的输出。所有 MuJoCo 数值警告均按失败处理，不能把自动复位当成功。

`build` 的装配冒烟不能用于冻结。`check` 运行独立行为夹具：运动范围、伸缩/中途信号、故障保持、发料边沿、接触输送、射线高度、短/高箱真实出口和防重复计数。当前完整覆盖的是**参考按高度分拣线**；其他拓扑可以生成预览，但缺行为夹具时 `behavior_checks_complete=false`，阻止冻结。集成夹具根据实测工件位置给定推料命令，仅验证设备物理模型，**不是 PLC 分拣程序，也不被运行时当作控制策略**。

正式模型须全部组件审批且完整行为检查通过，才能 `check --freeze`。冻结绑定 MJCF、DeviceIR、IO/行为定义、资产、实现版本和检查报告，状态为 `model_frozen`，PLC 验收仍为 `not_run`。

`run` 只接入已有 PLC，按每周期 `%QW20` 校验程序身份、读取输出、推进物理状态、经 `SafeCoilIO` 写回输入。BOOL 使用整组读改写，INT 使用 AML 原地址。本版是带墙钟/仿真时钟记录的 Modbus 轮询，**没有伪称 lockstep**；没有生成新的 PLC 代码或在线验收 verdict。正式 PLC 验收继续遵守 xml2st → R1~R6/R5 → matiec → 在线验收，后续由 lx/csk 将该物理模型纳入验收适配。

## 5. 用户反馈和归因

`edit` 保存新任务，补丁带 `base_sha256/ editable_ids/reason/operations`。支持 set_pose、set_parameter、set_joint、set_mount。Agent 输出不能扩大调用方给的修改范围；运动副/安装变更必须把两侧刚体纳入明确范围。禁止在普通参数编辑里改 IO、类型标识或偷换引用。未影响组件和 IO 保持原记录，过期版本补丁拒绝应用。

组件属性支持 `set_interface_pose(component_id,interface_id,position_m,rpy_rad)` 和 `add_interface(component_id,interface_id,name,position_m,rpy_rad)`，使用独立接口补丁，不与普通操作混写。新增接口限定为未连接的 `MechanicalMount`；连接和运动不会自动创建。已有机械接口要求显式纳入所连两侧；`JointAttachment` 的安装点要求运动副及两侧刚体均在范围内，只改 canonical anchor 位置，方向仍由 joint.axis 定义，不接受无实际行为意义的接口旋转。信号/IO与运动副本身的拓扑引用只读。所有修订新建候选任务、重新检查，不继承父模型冻结。

网页模型树按组件包含关系组织：`sorting_line` 下的 `pusher` 同时包含 `pusher_housing` 和 `pusher_plate`；运动副不拥有组件，也不改变组件的归属。`运动副类（Joint）` 与 `sorting_line` 同级，其下列出 `pusher_slide` 等运动副实例；实例下的父、子组件引用定位原组件节点，不创建重复组件。实例属性标明约束类型（如 slide/滑动副）、两端引用、轴向、范围、初始位置及安装点；组件接口仍在组件属性中维护。旧AML中同时存在于components和joints的运动副声明只显示为运动副实例，组件计数排除该声明；独立joints记录也能显示。虚拟类节点与引用不写回DeviceIR。包含环路、缺失组件引用会提示，约束回路不会误作包含环路。

模型树的显示需同时具备本任务 DeviceIR 和已生成场景；只完成 AML 解析时隐藏右侧树并释放其布局列，避免把设备声明误作模型生成结果。同一任务从未生成到场景就绪也必须重建树。

模型点击会展开组件祖先并定位；选中运动副高亮两端组件及其下属几何，修改范围仍是显式勾选的ID。点击引用可选择原组件，Ctrl/Command支持多选；键盘可展开类节点、导航和激活引用。接口位置修改保存新版本；运动副单独选中时不提供组件位姿修改。规则库审批与设备模型冻结分别记录，修订描述不自动批准新规则或放宽对齐检查；已有AML、DeviceIR、冻结模型和IO不因界面显示调整而改写。

修改后的场景不继承冻结状态，必须重建、检查；参数超出审批域时需要新候选/新版本。现有审批域内可复用规则；规则或设备行为改变重新审批。构建当前重新检查全机，尚未做最小子图增量编译，因此不会遗漏跨组件影响。

错误带 `device_description/component_model/assembly/plc_logic` 层信息和具体证据。候选几何失败只返回该组件修复，不修改事实或判据。实际 PLC 程序身份、通信失败和物理不稳定保留失败记录。尚未实现由 LLM 自动判断 PLC 逻辑根因并调用原 PLC 修复器，避免跨越本次职责边界。

## 6. 使用与证据

### 网页入口（已接入）

运行 `python -m src.agent.web --open`，选择 **设备场景生成**，或直接访问 `http://127.0.0.1:8765/?view=scenes`。

1. 上传 AML、输入需求，前端每次“生成设备场景”执行完整重生成：重新解析当前 AML，所有组件类型都交给 Agent 编写规则，不查询或沿用已审批规则、旧候选、几何资产或 MJCF。已有版本号只作为命名元数据使用；新候选目录必须未存在，重复生成相同规则内容也分配独立版本，重新执行几何构建、实例装配及仿真检查，禁止缺少新规则时回退旧库。页面显示“完整重生成”，任务记录force_regenerate及全部author_required来源。分拣线参考规则仅用于离线示例和检查夹具，网页不提供参考规则生成入口。显式局部修改仍按范围保留未变规则；CLI默认库复用策略独立于此前端生成策略。
2. 页面显示逐阶段进度、实际 MJCF 三维模型、组件/运动副/IO 数量、组件版本与检查报告。复杂网格直接取 MuJoCo 编译后的实际三角面，提供视觉几何/碰撞近似切换。
3. 行为检查完成后可播放、暂停或拖动实际 MuJoCo 采样回放；含发料、输送、推料和出口计数。该回放不是 PLC 控制策略，也不标记 PLC 验收成功。
4. 勾选允许修改的组件，输入自然语言要求；或直接填写单个组件的局部位置。新任务保存父版本、补丁和完整检查结果，原版本及其他组件/IO保留。从当前任务再次生成也使用修订后的 DeviceIR，不退回原始 AML 布局。
5. “查看与审批”展示组件预览、参数域、驱动内核、PLC 信号绑定、建模近似、阻断问题、适用范围与检查证据。支持选择当前可审批项批量核对；实际审批人明确勾选确认后才提交，审批说明选填。检查过期、范围不匹配、阻断问题或未通过检查均不能批准。
6. 全部组件审批后，“构建并冻结设备模型”锁定本任务候选/解析记录的规则 SHA 与审批快照，重新构建正式模型并跑独立整机检查；不会改用组件库中另一个更新版本。缺少完整夹具会阻断冻结。料源引用的工件须对应可生成的 rigid 工件对象池；AML Product 或被料源引用的组件错误使用 none 时，生成阶段须修正或保留阻断项，整机检查也会在动态测试前记录具体缺失工件，禁止以静态外观替代。本页不会生成、部署或启动 PLC。
7. 冻结成功后发布带任务ID和模型SHA的通知，验证页重新检查正式场景/审批/冻结/空闲状态，复用同一MuJoCo模型的view接口，更新只读AML IO、模型XML/物理描述/冻结清单下载，并清除旧任务的HMI、采样和验收展示。未冻结候选不替换已选冻结设备；新的冻结版本自动替换，页面刷新恢复选择，同源多标签页可接收冻结通知。旧模型运行期间只提示待切换，停止后再加载；任务选择器仍可切回旧PLC场景，并再次选择已加载过的冻结设备。版本不符或加载失败时隐藏旧几何，不将旧PLC操作用于新模型。这里是模型展示接入，不表示新设备的网页PLC运行/验收链路已经实现。

任务保存在工作台产物目录的 `device-scenes/tasks/<id>/`；默认根目录为 `E:/chagpt/308sjk-svg-workbench`，跟随 `--runs-root`。后台任务与原 PLC 会话分开，停止生成使用模型连接取消和阶段间检查；正在进行的 Blender 导出结束后退出。重启时将未完成任务标记“已中断”，不补记通过。

产物栏提供 DeviceIR、MJCF、IO 绑定、检查报告和完整模型 ZIP。ZIP 包含 XML 引用的网格、每实例 Blender 源文件和检查证据，保持相对资产路径；直接打开包内 scene.xml 可用 MuJoCo 加载。冻结清单保留原机器的来源路径，用本项目 DeviceRuntime 跨机器运行前需重新构建并冻结。组件详情还可单独下载 `.blend` 和各刚体 OBJ。

HTTP 入口为 `/api/device-scenes` 和 `/api/device-scene/{create,state,view,edit,operate,approve,stop}`；修改接口沿用工作台同源/Host/token 校验，任务操作带版本，下载限定本任务与组件库的白名单文件。Agent 不获得审批 HTTP 工具。

### CLI 与证据

安装可选依赖：`python -m pip install -r requirements-scene.txt`。

以下 PowerShell 命令的候选 JSON 是**人工整理的未审批参考规则**，用于可复现设备测试，不冒充 LLM 产物：

```powershell
$sceneRoot = 'E:/chagpt/scene-agent-sorter'
$componentRoot = 'E:/chagpt/scene-agent-library'
python -m src.agent.scene_agent --library $componentRoot prepare --aml examples/aml/factoryforge_sorting_reference.aml --request '完整按高度分拣线，保留AML机械和IO事实' --candidate-rules examples/scene_agent/factoryforge.candidates.json --offline --out $sceneRoot
python -m src.agent.scene_agent --library $componentRoot build --task $sceneRoot --candidate-validation
python -m src.agent.scene_agent --library $componentRoot check --task $sceneRoot --candidate-validation
```

省略 `--candidate-rules/--offline` 则真实调用现有模型客户端，保存逐轮请求、回复和验证反馈。API key 不入产物。单独真实 LLM + Blender 示例：

```powershell
python -m examples.scene_agent.demo_llm --out E:/chagpt/scene-agent-llm-probe
```

人工核对候选的 rule、preview、validation、unresolved 后，使用其当前 review.json 中的 SHA：

```powershell
python -m src.agent.scene_agent --library $componentRoot approve --candidate '<候选目录>' --reviewer '<实际审批人>' --review-sha256 '<当前完整SHA>' --note '<审阅结论与接受的近似>'
# 所有依赖均审批后：
python -m src.agent.scene_agent --library $componentRoot build --task $sceneRoot
python -m src.agent.scene_agent --library $componentRoot check --task $sceneRoot --freeze
# 按已通过原闸门部署的程序ID连接；此命令只采集IO，不等于验收PASS：
python -m src.agent.scene_agent run --scene "$sceneRoot/scene" --host 192.168.12.131 --prog-id 9 --seconds 30 --schedule '<操作输入时间表.json>'
```

操作时间表为 `[{"time_s":0.1,"inputs":{"panel_start":true}},{"time_s":0.3,"inputs":{"panel_start":false}}]`，不能写仿真传感器。自然语言编辑使用 `edit --task ... --out <新任务> --request ... --editable-id <ID>`，或用 `--patch` 载入可审查补丁。`prepare --device-ir <修订JSON>` 可从修订继续生成缺少的候选。

本次运行证据集中在 `E:/chagpt/2026-10-27-场景生成Agent开发`；各轮失败和成功分开保存。参考线、人工支架样例、真实 LLM 支架的来源明确区分。所有真实候选仍未审批，未运行现场 PLC 验收。

本次根测试766项通过（新增46项，40.88s），JUnit为 `pytest-all-ascii.xml`。首次把全量旧测试临时目录放在中文路径，旧 `from_xml_path` 出现14项路径读取失败；改用E盘英文临时目录后全绿，没有修改旧PLC/绘图加载代码。新链路在中文任务路径用VFS实际加载通过。`factoryforge_review/REVIEW.md` 包含14类型候选的当前版本、完整审批SHA、参数范围和近似；独立整机行为20项通过。`llm_probe_v4` 为真实模型生成的中空支架，Blender 5.2.2 LTS导出和15项检查通过。`blender_parameter_instances` 真实构建了两组不同参数，分别保留可编辑源文件和网格；同一规则没有错误复用固定尺寸。所有真实组件库审批数为0，测试使用的合成审批仅限清理前的pytest隔离临时库。

2026-10-27 前端已移除分拣线参考建模选项和一键示例，所有普通新建/历史继续生成统一method=agent；既有设备/审批记录与离线回归夹具保留。作者输入补已归一的参数单位及AML接口；内部body必须为对象数组，单刚体root隐含，DeviceIR外部关节不应在规则内部重复，错误精确定位到body数组索引并反馈重试。此改动不新增行为内核，不能据此宣称六轴驱动或PLC验收已通过。

2026-10-27 单位上下文更正：上文不能理解为所有component.parameters数值都已SI。显式CAEX Unit参数由解析器归一；无单项Unit的旧轴字段保留原值，作者按明确的组件轴unit提供语义单位及来源（不改DeviceIR）。pose和joints仍SI，parameter_bounds始终针对原表示数值；越界反馈列实际值/单位/候选界限/同类观测范围。未知单位保持未知，禁止改设备限位或静默修改已审批域使其通过。

2026-10-27 六轴AML v2：示例文件补明确的SI旧轴参数、增益/阻尼及量纲来源、视觉/碰撞代理、惯量近似；受驱动外部副即使仅预览也需位置增益。作者提前要求正有限kp，保持AML声明的kp/damping，规则不得把外部副重复成内部副。独立确定性检查夹具真构建6副6执行器/25IO声明，六次0.1rad/3s动作满足原0.5°窗口；这不是本次Agent输出或PLC闭环。J1碰撞代理较视觉Z尺寸缩短4mm以表示简化轴承间隙，事实明确，不关闭碰撞。v2及核对规则/报告见E:/chagpt/2026-10-27-六轴机械臂AML修订/AML编写核对说明.md；速度/加速度/力矩限制与六轴PLC内核、全工作空间仍不宣称完成。

## 6.1 更换AML后的统一网页生命周期（2026-10-27）

选择新AML立即POST `/api/device-scene/upload`，生成只做解析/查库的独立任务，不调用模型。写入新input.aml/DeviceIR，按实例返回component_resolution（已审批匹配/候选复用/需要编写）、missing_types及source_sha256/device_ir_sha256；不能同时指定新文件与旧source_task。点击生成后重新解析原始字节，只有显式、同源局部修订可以叠加；查询有效审批规则，缺失或明确调参不兼容时由Agent编写规则，再验证/实例化/装配。生产网页不再包含reference/example分支，离线夹具仅从测试作者边界注入。

设备页发布带任务ID、AML/DeviceIR/冻结模型摘要的当前来源通知，并同步URL/本地选择。最终验证页新源到达即清理旧几何、HMI、轨迹和旧验收，读取新DeviceIR且等待冻结；随后仅加载同任务正式冻结模型。运行中的旧设备延后切换并保留明确提示；过期响应、旧冻结事件、错误来源摘要不得覆盖新选择。已有localStorage优先迁移当前设备任务，而不是上一次冻结的无关设备。原历史PLC任务可以显式切回；新设备尚未关联PLC时禁用运行/验收入口并隐藏旧设备服务信息，不把新模型预览视为PLC闭环。

组件库审批字节/实现闸门不变；未知格式或旧AML物理IO绑定不能解析时明确报错，禁止静默退回旧模型。此阶段未新增六轴行为内核或通用PLC验收夹具，仍须真实能力与对应检查到位后才能冻结/验收。

## 6.2 六轴连续采样、干涉与连接可视化（2026-10-28）

机械测试由离散位姿跳变改为从初始角出发、受限速度/加速度的稠密正向运动学采样；真实驱动响应仍由 IO 动态模块验证。六轴 AML 开启非相邻连杆及地面接触，固定检查器逐帧独立计算代理几何有符号距离，首次穿透失败并保存证据；相邻运动副/安装连接及焊接刚体明确豁免。此能力不等于自动避障、连续碰撞检测或实机碰撞验收。接口坐标轴与关节连接使用 AML 两端安装点并跟随 MuJoCo 实际刚体，不依据外形边界猜测接口位置。

配置、豁免和证据位置以 [AML-仿真验证配置](AML-仿真验证配置.md) 为准。旧六轴 31/31 的范围排除了碰撞；修订后真实 DeepSeek 新生成模型为 23/31，j2 地面、j3/j5 连杆干涉及相关 IO 测试失败，不能冻结。四份冻结契约不变。

## 7. 待三方评审的接入项

2026-10-29 通用防碰撞脚本生成：先隔离装配实际模型，再向 Agent 提供编译几何及受检部件对，生成多运动副协同路径；固定预检/动态试跑失败则回传证据，最多3轮，无合法程序则停止。覆盖范围、工艺接触边界及导入/冻结的评估见 [通用仿真测试脚本生成规范](通用仿真测试脚本生成规范.md)。该机制不依赖六轴类型。旧四份冻结契约保持；同一已验证模型包晋级导入的流程仍是待评估项。

六轴 IO 规则和 Agent 动作脚本扩展（2026-10-28）以 [六轴IO绑定校验规范](六轴IO绑定校验规范.md) 为准：允许按通道规则设计局部合法激励、反馈状态注入和控制器状态序列，固定解释器裁定。超限拒绝、工况受阻、响应失败与绑定错误分开，不产生机械关系或 PLC 验收结论；四份冻结契约不变。

* csk：审查 DeviceIR/physical_scene 的机械表示、物理近似、行为夹具接口和模型冻结；确定如何接入既有 SceneSpec/R5、final 及 lockstep。
* lx：确认离散设备 INT/BOOL 地址与程序身份的接入方式；编写或复用该设备的原链路 PLC 验收场景。PLC 生成策略不在本次修改范围。
* gc：评审后接入 Studio 的场景生成/局部反馈入口；扩展组件与行为时增加候选和独立夹具。审批域外参数与新行为继续走候选评审。

四份既有冻结契约的任何正式变更，仍须单独 RFC、四文档同步与 changelog 登记。
