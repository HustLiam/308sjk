# AML 仿真验证配置

2026-10-28，gc。此配置扩展独立 DeviceIR 草案；不变更 requirement_spec、PLCopen XML、旧 io_map 与 acceptance 四份冻结契约。架构边界见 [场景 Agent RFC](RFC-场景生成Agent-DeviceIR.md)。

AML 保存设备事实与验证要求；测试动作、实际状态、轨迹、判定和人工审批保存到任务目录。设备信息按六类组织：

| 类别 | 当前表示 |
|---|---|
| 设备身份 | CAEX `ID`、`Name`、`RefBaseSystemUnitPath`、版本及来源属性 |
| 组件关系 | `InternalElement` 包含层级、`MechanicalMount` 安装接口、运动副连接两端；耦合属性见下文 |
| 几何与位置 | 组件形状/尺寸、碰撞几何、位姿、接口局部坐标；CAEX `Unit` 归一为 m/rad/s/kg |
| 物理与运动学 | 质量、惯量/等效惯量、摩擦、关节范围/初值、速度及加速度限制；缺失能力须明确阻断 |
| 功能与 IO | 可写指令/只读反馈的 `quantity`、响应与阈值参数；PLC 通道名称、方向、类型、地址、换算和 `value_writer` |
| 验证 | `InstanceHierarchy/SimulationRequirements`：范围、检查对象、容差、响应超时和允许简化 |

完整可上传示例为 [factoryforge_sorting_reference.aml](../examples/aml/factoryforge_sorting_reference.aml)，保留原 18 个声明对象、1 个运动副及 19 路 IO，新增以下 CAEX 属性结构：

```xml
<Attribute Name="SimulationRequirements">
  <Attribute Name="format" AttributeDataType="xs:string"><Value>308sjk.simulation_requirements/1</Value></Attribute>
  <Attribute Name="description" AttributeDataType="xs:string"><Value>验证机构连接、指令响应及实际反馈</Value></Attribute>
  <Attribute Name="position_tolerance_m" Unit="m"><Value>0.002</Value></Attribute>
  <Attribute Name="angle_tolerance_rad" Unit="rad"><Value>0.001</Value></Attribute>
  <Attribute Name="response_timeout_s" Unit="s"><Value>3</Value></Attribute>
  <Attribute Name="allowed_simplifications">
    <Attribute Name="visual" AttributeDataType="xs:string"><Value>可省略外壳纹理，保留碰撞与检测尺寸</Value></Attribute>
  </Attribute>
  <Attribute Name="checks">
    <Attribute Name="all_io">
      <Attribute Name="kind" AttributeDataType="xs:string"><Value>io_binding</Value></Attribute>
      <Attribute Name="target" AttributeDataType="xs:string"><Value>*</Value></Attribute>
      <Attribute Name="description" AttributeDataType="xs:string"><Value>逐通道验证绑定和实际响应</Value></Attribute>
    </Attribute>
  </Attribute>
</Attribute>
```

`kind` 支持 `mechanical_joint`、`fixed_mount`、`joint_coupling`、`io_binding`。`target` 分别为运动副 ID、安装连接 ID、耦合 ID、IO 名称；`*` 表示此类全部对象。未知字段、错误引用和非法容差在解析时拒绝；`*` 没有匹配对象时留下未覆盖模块，不能据此冻结。旧 AML 仍可读取，但测试页会标明范围与容差来自模板默认值，不能冒充 AML 声明。

运动副组件可用 `coupled_to` 指定主关节 ID，`coupling_ratio` 指定非零比例，`coupling_offset` 指定从关节坐标的偏置（从关节单位，归一为 m 或 rad）。关系为 `q_follower = offset + ratio * q_leader`；初始值必须一致，主关节全行程不得超出从关节范围，重复从关节与耦合环路拒绝。确定性构建器生成 MuJoCo joint equality；检查器独立核对引用、约束系数、启用状态及受力后实际关系。当前支持 slide/hinge 标量关节。

生成器会为所有运动副、安装连接、耦合及 IO 编译 `simulation_tests.json` 数据脚本。Agent 可以在强制边界与中间位姿之外增加合法采样点；不得修改目标、判据、容差、IO 绑定或删减覆盖。脚本通过固定解释器执行，不使用 Python `exec/eval`。模型作者同时收到 AML 的验证范围、容差与简化要求。

检查器以原 DeviceIR 为期望源，读取实际 MuJoCo 编译参数、刚体变换、关节坐标、颜色、模型属性、接触、射线和事件。机械位姿采样属于运动学检查；驱动响应与故障保持另由 IO 动态模块测试。当前物理 IO 检查器覆盖输送、推料、料源、射线、出口计数、面板/指示灯及关节空间位置伺服；面板设定值支持 PLC 原始值到秒的换算，逻辑灯不要求外壳几何。非支持物理量、编码及 ball/free 检查显示未覆盖；六轴增加离散采样的部件干涉检查，逆运动学、碰撞避障、连续碰撞检测和真实负载/力矩仍不在当前范围。

前端“设备场景生成”上传或替换 AML 后会生成新的计划与版本摘要。测试模块收在下方“检查结果”弹窗；勾选运行后，可用“查看所选测试效果”回到模型。效果选择、播放/暂停、时间拖动、倍速和折叠 IO 状态位于仿真视图左侧。每个模块从独立物理状态开始。失败回放保留；几何预览不能运行测试，旧模型/旧计划的回放不能用于新版本。

“场景记录 → 清空历史 / 管理”可按名称或任务 ID 搜索、勾选、全选当前结果并批量删除记录及模型/回放缓存。后端先核对所有选择的任务 ID、版本与空闲状态，再将精确目录移入当前工作台的临时清理区并删除；移动失败时恢复已移动项。共享组件库及 PLC 会话保留，删除当前场景会清空三维视图和浏览器来源指针，并通知同源标签页。Windows 文件占用导致缓存未完全删除时，明确返回 pending，不报告缓存已清完。此操作仅在用户提交所选记录后执行。

六轴采用 `robot_control` 与 `joint_position` 固定内核：PLC 角指令按 AML `physical_per_plc_unit` 换成目标角，以声明的速度和加速度推进位置参考，位置执行器作用于真实 MuJoCo hinge。反馈由实际 q 编码，边界饱和按 AML 仿真配置声明；实际 q 同时保留，不能用编码饱和掩盖误差。到位由实际目标误差判定，急停故障锁存；原因消失后复位才可重新就绪，失能以受限参考减速后保持。当前示例采用显式等效增益、阻尼和重力补偿，不声称实机参数。未声明触点保持时间的纯逻辑面板无额外延迟。

六轴碰撞与连续回放修正（2026-10-28）：原示例明确排除全部内部自碰撞与环境接触，旧 31/31 不包含碰撞结论。指定六轴 AML 现改为 `self_collision_policy=enabled_non_adjacent`、`environment_contact_policy=enabled`；运行时只为 AML 声明的直接运动副两端排除接触，非相邻部件及地面保留物理碰撞。关节和 IO 模块另用固定的 `mj_geomDistance` 逐帧检查实际代理几何的有符号距离，即使旧模型禁用了 solver 接触，也不能掩盖非相邻几何干涉。直接关节/安装邻居与焊接刚体不参与此干涉判据，地面是否参与按实际环境接触策略记录；数值穿透阈值为 0.01 mm。首次穿透即失败并保存干涉部件、几何、深度、位置和回放，未完成的边界行程不能记为通过；不自动改 AML 尺寸、行程或移走障碍。

机械测试从初始姿态出发，按排序后的强制/补充采样点推进并返回初始姿态；使用余弦分段轨迹限制声明速度与加速度，每秒至少 30 帧，单帧不超过 1° 或 2 mm。每帧由 MuJoCo 正向运动学重新计算整条链，保留刚体变换、关节坐标及碰撞证据；这属于运动学采样，不伪称驱动力学过程。前端直接播放这些稠密采样，在首次干涉帧标红相关几何。接口坐标轴、关节轴与两端连接跟随同帧实际刚体；模型树列出机械接口，运动副引用具体的组件/接口，六轴初始镜头可容纳完整模型。

2026-10-28 已按用户指定更新 `D:/RSsources/Project_competition/西门子AI/博途/input.aml` 和 `simple_6axis_robot.aml`，保留原机械关系、位姿及 19/25 路 IO。第一轮原文件在 `E:/chagpt/2026-10-28-scene-layout-real/aml-originals`；该轮分拣 20/20、六轴不含碰撞范围 31/31。六轴原回复的明确参数引用对象 `{"$parameter":"name"}` 只转换为 DSL 等价字符串 `$name`，记录原值与转换路径，不改变数值、几何或判据。碰撞修订前备份、用户明确允许向 DeepSeek 发送后重新生成的原始回复、MJCF 与失败证据保存在 `E:/chagpt/2026-10-28-robot-collision`：新六轴 23/31，j2 碰地面、j3/j5 连杆干涉及相关 IO 动态测试真实失败；未审批或冻结。

全量闸门结果在 `candidate_scene|scene/simulation_test_results.json`，模块回放在 `test_replays/`；用户所选运行结果另存 `selected_tests/`，不会覆盖全量验证或人工审批。整机检查和冻结必须执行全计划，未覆盖/失败/只跑子集不能代替全量通过。产物及模型包包含脚本、结果和回放；记录绑定 AML/DeviceIR、MJCF、物理定义、脚本与检查器摘要。模型独立测试不产生 PLC 在线验收结论。

CLI 可复现所选模块：

```powershell
python -m src.agent.scene_agent tests --task <task-directory> --candidate-validation --test-id <test-id>
```

省略 `--test-id` 执行全部模块；失败或未覆盖退出码为 2。组件审批仍为人工操作；本次检查器及运行时升级会使旧实现摘要过期，旧任务需复查/重建，不能自动继承原通过结论。

模型树仅在本任务的模型已生成、实例化并装配后显示。导入 AML 后先保存和解析 DeviceIR，页面显示“待生成装配”，不把设备声明树提前展示为已生成模型。替换文件或切换至尚未生成的任务会清空旧树及选择；同一任务生成完成后重新建立树。

新设备测试脚本采用 [通用仿真测试脚本生成规范](通用仿真测试脚本生成规范.md)：隔离整机装配后再生成脚本，使用实际碰撞几何，支持多轴让位及失败证据回传重写。`test_safety.json` 保存当前模型的预检范围及来源；模型/脚本/检查器变化后旧报告不能直接使用。

机器人测试路径现在先预检再执行。原全行程计划存在干涉时不执行该动作，只在另行预检通过的局部路径检查连接关系；未覆盖的全行程仍阻止完成/冻结。具体分类与覆盖边界见 [六轴IO绑定校验规范 §7](六轴IO绑定校验规范.md#7-机械测试路径与-io-结论的隔离)，历史失败记录保持原样。

六轴 IO 绑定校验现以 [六轴IO绑定校验规范 v1](六轴IO绑定校验规范.md) 为独立权威。原全行程 IO 激励改为局部合法指令、非法值预期拒绝及反馈状态注入；真实 Agent 按固定规则编排全部 IO 动作程序，宿主解释器保留静态声明/类型/权限检查并独立裁定。`robot_io_rules.json` 可下载规则与窗口，`simulation_tests.json` 标记实际脚本来源；IO 绑定、响应未达标与工况受阻分别记录，不由 IO 测试推断机械关系错误。既有机械工作空间测试及其碰撞证据保留，不能用局部 IO 通过替代整机验收。
