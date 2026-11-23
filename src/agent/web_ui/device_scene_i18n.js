/* Chinese presentation only: never mutate rule, report or approval data. */
'use strict';
(() => {
  const normalize=value=>String(value??'').replace(/\s+/g,' ').trim();
  const translations=new Map([
  [
    "1 kg equivalent moving mass and position servo kp=50000, damping=400 approximate the reference kinematic pusher. Not measured pneumatic dynamics. Fault uses a temporary joint equality hold; feedback uses actual joint q.",
    "采用 1 kg 等效运动质量、位置增益 kp=50000 和阻尼 damping=400，近似参考推料器的运动；这些不是实测气动参数。故障时用临时关节等式约束保持位置，反馈取实际关节位置 q。"
  ],
  [
    "AML mass_kg used as the total body mass of the link.",
    "使用 AML 的 mass_kg 作为该连杆的总质量。"
  ],
  [
    "AML mass_kg used as the total body mass of the single base link.",
    "使用 AML 的 mass_kg 作为单个底座刚体的总质量。"
  ],
  [
    "AML specifies only logical contacts (start, stop, estop_ok) with no physical dimensions; no decorative panel shape is invented.",
    "AML 仅描述 start、stop、estop_ok 等逻辑触点，没有物理尺寸，因此未添加装饰性面板外形。"
  ],
  [
    "AML stroke_min/stroke_max are in degrees; joint range in rad is converted as deg*pi/180 and must satisfy stroke_min<=stroke_max.",
    "AML 的 stroke_min/stroke_max 使用度；关节范围通过 deg*pi/180 转换为弧度，并要求 stroke_min<=stroke_max。"
  ],
  [
    "Acceptance not run; parameters are illustrative, not manufacturer data.",
    "尚未执行验收；参数仅为示例，不是厂商数据。"
  ],
  [
    "Base modeled as a single box of half-sizes size_x_m/2, size_y_m/2, size_z_m/2 centered at geometry_center offsets.",
    "底座用单个盒体近似，半尺寸为 size_x_m/2、size_y_m/2、size_z_m/2，中心位于 geometry_center 指定的偏移处。"
  ],
  [
    "Base modeled as a single rigid box body using AML size_x_m/size_y_m/size_z_m as full side lengths and geometry_center_* as the box center in the component frame.",
    "底座建模为单个刚性盒体；AML 的 size_x_m/size_y_m/size_z_m 是完整边长，geometry_center_* 是组件局部坐标中的盒体中心。"
  ],
  [
    "Box base with nominal dimensions from AML; mass taken from AML mass_kg.",
    "底座采用 AML 的名义尺寸建成盒体，质量取自 mass_kg。"
  ],
  [
    "Channel axis is through Y with wall thickness wall_m on all four walls; top and bottom remain solid.",
    "通道沿 Y 轴贯穿，四壁厚度均为 wall_m；顶壁和底壁保持实体。"
  ],
  [
    "Coarse position_x/y/z and rotation_x/y/z at assembly level preserved as instance pose; not re-encoded in the component rule.",
    "装配层的 position_x/y/z 和 rotation_x/y/z 保留为实例位姿，不在组件规则中重复编码。"
  ],
  [
    "Collision approximated by four side-wall boxes; the channel is preserved and verified by a central clearance probe.",
    "用四个侧壁盒体近似碰撞几何；保留通道，并用中心间隙探针验证其畅通。"
  ],
  [
    "Command/feedback INT encoding at 0.01 degree per count and IO mapping are proposed, not field verified.",
    "指令/反馈采用 INT，编码为每计数 0.01°；该编码与 IO 映射均为示例方案，未经现场验证。"
  ],
  [
    "Damping not supplied in AML.",
    "AML 未提供阻尼参数。"
  ],
  [
    "E-stop channel redundancy/safety integrity level not specified.",
    "尚未指定急停通道冗余或安全完整性等级。"
  ],
  [
    "Entry test uses actual transformed collision-box AABB overlap; not exact oriented-box intersection for arbitrary rotated non-box products.",
    "入口检查使用变换后碰撞盒的轴对齐包围盒（AABB）重叠判定；对于任意旋转的非盒形工件，不等同于精确的有向盒相交判定。"
  ],
  [
    "Exact manufacturer base geometry is illustrative, not measured.",
    "底座的具体厂商外形仅为示例，未经测量。"
  ],
  [
    "External DeviceIR hinge joint drives this link; no additional internal hinge is added.",
    "该连杆由 DeviceIR 中的外部转动副驱动，不再添加内部转动副。"
  ],
  [
    "External DeviceIR joint range/axis is authoritative; per-link stroke limits not enforced by this component rule.",
    "外部 DeviceIR 关节范围和轴向为准；此组件规则本身不执行连杆行程限位。"
  ],
  [
    "Finite preallocated product pool is an implementation of spawn/retire; capacity overflow retains one pending edge. Source marker is illustrative.",
    "通过有限的预分配工件池实现生成和回收；容量不足时保留一个待处理触发沿。料源标记仅为示意。"
  ],
  [
    "Fixed_to_world=true treated as world-anchored; DeviceIR supplies no internal articulation.",
    "fixed_to_world=true 表示固定到世界；DeviceIR 未提供内部关节结构。"
  ],
  [
    "Geometry centered at AML geometry_center_* in body frame.",
    "几何中心采用 AML 中的 geometry_center_*，坐标相对于刚体局部坐标系。"
  ],
  [
    "Geometry origin is the proximal joint center; the limb box is offset by the AML geometry_center (given in body-local coordinates).",
    "几何原点位于近端关节中心；连杆盒体按 AML 的 geometry_center 偏移，该偏移使用刚体局部坐标。"
  ],
  [
    "Hollow channel is modeled as a through-cavity along +Y spanning the full depth; four box collision proxies approximate the walls and preserve the central channel.",
    "中空通道沿 +Y 方向贯穿全部深度；四个盒形碰撞代理近似通道壁，并保留中心通道。"
  ],
  [
    "IO address mapping is 'proposed_example_mapping_not_field_verified'.",
    "IO 地址映射标记为 proposed_example_mapping_not_field_verified，即示例映射，未经现场验证。"
  ],
  [
    "IO address mapping is proposed_example_mapping_not_field_verified; acceptance not_run.",
    "IO 地址映射为示例方案，未经现场验证；验收状态为 not_run（未运行）。"
  ],
  [
    "IO mapping (reserved_program_id_address %QW20, enable/reset/ready/fault) is proposed example mapping, not field verified.",
    "保留 reserved_program_id_address=%QW20 及 enable/reset/ready/fault 映射；该映射仅为建议示例，未经现场验证。"
  ],
  [
    "Indicator housing dimensions are visual approximations.",
    "指示灯壳体尺寸为视觉近似。"
  ],
  [
    "Inertia tensor, friction, and actuator tuning (damping, torque limits) not provided in AML.",
    "AML 未提供惯量张量、摩擦或执行器调参数据，包括阻尼和力矩限制。"
  ],
  [
    "Interface frames are taken at the assembly origin because AML does not give explicit Robot6R-level interface positions.",
    "由于 AML 未明确给出 Robot6R 层级的接口位置，接口坐标系取装配原点。"
  ],
  [
    "Interface frames are taken at the component origin since AML supplies no explicit interface positions.",
    "由于 AML 未明确给出接口位置，接口坐标系取组件原点。"
  ],
  [
    "Legacy axis parameters (stroke_min/max, vmax, accel, velocity, acceleration, deceleration, poswin) are in deg/deg-s/deg-s^2 per parameter_units and are kept as provided; only converted inside expressions where needed.",
    "根据 parameter_units，旧轴字段 stroke_min/max、vmax、accel、velocity、acceleration、deceleration、poswin 使用度及对应的角速度/角加速度单位；保留传入数值，仅在需要的表达式中转换。"
  ],
  [
    "Link modeled as a single box of half-sizes size_x_m/2, size_y_m/2, size_z_m/2 centered at geometry_center offsets relative to the body origin at the proximal joint.",
    "连杆用单个盒体近似，半尺寸为 size_x_m/2、size_y_m/2、size_z_m/2；以近端关节为刚体原点，盒体中心按 geometry_center 偏移。"
  ],
  [
    "Link modeled as a single rigid box using AML size_x_m/size_y_m/size_z_m as full side lengths and geometry_center_* as the box center in the link frame.",
    "连杆建模为单个刚性盒体；AML 的 size_x_m/size_y_m/size_z_m 是完整边长，geometry_center_* 是连杆局部坐标中的盒体中心。"
  ],
  [
    "Manufacturer base geometry and inertia tensor not provided (illustrative only).",
    "尚未提供厂商底座几何和惯量张量；当前仅为示例。"
  ],
  [
    "Manufacturer link geometry is illustrative, not measured.",
    "连杆的厂商外形仅为示例，未经测量。"
  ],
  [
    "Mass inertia tensor not supplied in AML; MuJoCo will derive inertia from box geometry and mass_kg.",
    "AML 未提供惯量张量；MuJoCo 将根据盒体几何和 mass_kg 推导惯量。"
  ],
  [
    "Mass inertia tensor not supplied in AML; MuJoCo will derive it from the box geometry and mass_kg.",
    "AML 未提供惯量张量；MuJoCo 将根据该盒体几何和 mass_kg 推导惯量。"
  ],
  [
    "Mass taken from AML mass_kg as total body mass for the link.",
    "连杆的刚体总质量取自 AML 的 mass_kg。"
  ],
  [
    "Mass taken from AML mass_kg as total body mass.",
    "刚体总质量取自 AML 的 mass_kg。"
  ],
  [
    "Mass taken from AML mass_kg as total link mass.",
    "连杆总质量取自 AML 的 mass_kg。"
  ],
  [
    "Material selection and structural strength/stiffness analysis (static approximation only).",
    "尚未完成材料选型及结构强度、刚度分析；当前仅为静态近似。"
  ],
  [
    "Mounting/interface detail geometry (bolt patterns, fillets) not specified in AML.",
    "AML 未指定安装接口的细节几何，例如螺栓孔位和圆角。"
  ],
  [
    "Moving surface approximated by contact-gated tangential force, capped at AML mu*m*g; 20 ms velocity response. Collider friction 0.01 is numerical regularization, not the AML belt coefficient; AML mu governs the explicit tangential-force cap. Not a belt elasticity or motor-torque model.",
    "运动带面采用接触触发的切向力近似，力上限为 AML 的 mu*m*g，速度响应时间为 20 ms。碰撞摩擦系数 0.01 仅用于数值正则化，并非 AML 的传送带摩擦系数；切向力上限由 AML 的 mu 决定。此模型未描述皮带弹性或电机力矩。"
  ],
  [
    "No additional geometry is generated at the assembly level; the assembly pose is the posed placement of the contained link chain.",
    "装配层不额外生成几何；装配位姿用于放置其包含的连杆链。"
  ],
  [
    "No bolt patterns, mounting holes or fillets are modeled, as none were requested.",
    "由于需求未指定，未建立螺栓孔位、安装孔或圆角。"
  ],
  [
    "No geometry is synthesized for the assembly itself.",
    "不为装配节点本身生成几何。"
  ],
  [
    "No inertia tensor or center-of-mass offset given in AML; box-approximation inertia used.",
    "AML 未提供惯量张量或质心偏移，使用盒体近似惯量。"
  ],
  [
    "No material strength/stiffness analysis was provided; geometry is a rigid static approximation of the hollow support.",
    "尚未提供材料强度或刚度分析；中空支架几何采用刚性静态近似。"
  ],
  [
    "No mechanical dimensions, material or mounting data for the operator panel in AML; physical geometry intentionally omitted.",
    "AML 未提供操作面板的机械尺寸、材料或安装数据，因此省略实体几何。"
  ],
  [
    "No mounting bolt pattern or interface flange geometry provided.",
    "尚未提供安装螺栓孔位或接口法兰几何。"
  ],
  [
    "No physical dimensions supplied in AML; panel modeled as a thin structural slab sized to fit operator signal positions only, per minimal geometry policy.",
    "AML 未提供实体尺寸；按最小几何策略，将面板建为仅容纳操作信号位置的薄结构板。"
  ],
  [
    "No physical inertia tensor, damping, friction or actuator tuning data provided.",
    "尚未提供物理惯量张量、阻尼、摩擦或执行器调参数据。"
  ],
  [
    "Operator pushbutton/estop behavior kernels not implemented; logical IO roles are preserved by the assembly.",
    "尚未实现操作按钮和急停的行为内核；装配保留原逻辑 IO 角色。"
  ],
  [
    "OperatorPanel input signal processing / debounce semantics are not defined.",
    "尚未定义 OperatorPanel 的输入信号处理和消抖语义。"
  ],
  [
    "PLC angle encoding is signed_INT 0.01 deg/count; the simulation-side conversion and feedback (measured, never copied command) require runtime implementation not present in AML.",
    "PLC 角度采用有符号 INT，编码为每计数 0.01°；仿真侧换算与反馈需要运行时实现，AML 本身不提供该实现。反馈必须取测量值，不能复制指令。"
  ],
  [
    "Panel carries only logical signals (start, stop, estop_ok); decorative panel shapes are not invented.",
    "面板仅包含 start、stop、estop_ok 等逻辑信号，未添加装饰性外形。"
  ],
  [
    "Panel envelope is an illustrative flat enclosure; AML provides no panel dimensions, so a fixed 0.30 x 0.20 x 0.08 m box is used (not from AML).",
    "面板外形采用示意性扁平壳体；由于 AML 未提供尺寸，使用固定 0.30 × 0.20 × 0.08 m 盒体，该尺寸并非来自 AML。"
  ],
  [
    "Panel geometry not specified in AML; treated as a logical operator panel with no physical geometry, preserving start, stop and estop_ok signals without inventing decorative shapes.",
    "AML 未指定面板几何；将其视为无实体几何的逻辑操作面板，保留 start、stop、estop_ok 信号，未添加装饰性外形。"
  ],
  [
    "Panel housing is visual only; contacts are external operator inputs. Timing knob purpose still requires a separate process requirement, not inferred here.",
    "面板壳体仅用于视觉展示，触点属于外部操作输入。定时旋钮的用途仍需独立工艺需求说明，此处不作推断。"
  ],
  [
    "Panel is modeled as a logical/structural container with no geometry.",
    "面板建模为不含几何的逻辑/结构容器。"
  ],
  [
    "Panel physical dimensions, mounting, button layout and indicator set are not specified in AML.",
    "AML 未指定面板实体尺寸、安装方式、按钮布局或指示灯配置。"
  ],
  [
    "Panel signal wiring to PLC IO retained as-is; no behavior kernel is claimed.",
    "面板到 PLC IO 的信号接线保持原样；未声明已实现相应行为内核。"
  ],
  [
    "Physical panel dimensions, mounting position and enclosure geometry are not specified in AML.",
    "AML 未指定面板实体尺寸、安装位置或壳体几何。"
  ],
  [
    "Physical panel dimensions, mounting, and button/indicator geometry not provided in AML.",
    "AML 未提供面板实体尺寸、安装方式以及按钮/指示灯几何。"
  ],
  [
    "Position gain chosen explicitly as a modeling assumption since AML provides only tolerance and velocity/acceleration limits; position_kp set to 200 N*m/rad for the hinge (torque per radian), adequate for the illustrative link masses.",
    "由于 AML 仅提供容差及速度/加速度限制，位置增益作为显式建模假设选取：转动副 position_kp=200 N·m/rad，即每弧度的力矩增益，原规则认为其适用于这些示例连杆质量。"
  ],
  [
    "Post is illustrative only; ray origin, direction and range are AML facts.",
    "立柱仅为示意；射线原点、方向和量程来自 AML。"
  ],
  [
    "Requested behavior revolute_position_servo (six-axis drive and fixture) is not implemented; PLC runtime support, enable/reset semantics and acceptance are unresolved.",
    "尚未实现所需的 revolute_position_servo 行为及六轴驱动检查夹具；PLC 运行时支持、使能/复位语义和验收仍待解决。"
  ],
  [
    "Required behavior six_revolute_joint_position_commands_and_measured_feedback (six-axis drive and fixture) is not implemented; runtime support and acceptance not run.",
    "尚未实现 six_revolute_joint_position_commands_and_measured_feedback 所要求的六轴驱动及检查夹具；运行时支持与验收尚未执行。"
  ],
  [
    "Revolute position servo behavior kernel (revolute_position_servo) is not among the built-in behavior kernels; behavior is left as 'none' with the servo requirement recorded.",
    "内置行为内核不包含 revolute_position_servo；当前 behavior 保持 none，并记录位置伺服需求。"
  ],
  [
    "Rigid static fixture: omitted mass/inertia because behavior is none and the part is static.",
    "此部件是刚性静态夹具，behavior=none，因此省略质量和惯量。"
  ],
  [
    "Robot6R is an assembly/container; its links (base, j1..j6) are separate components with their own rules and external hinges in DeviceIR.",
    "Robot6R 是装配容器；底座及 j1～j6 连杆各自是独立组件，使用各自规则及 DeviceIR 中的外部转动副。"
  ],
  [
    "Robot6R is modeled as an assembly root (structural container); link geometry, joints and IO are contributed by child RobotFixedBase/RobotRevoluteLink and the AML joints block, not duplicated here.",
    "Robot6R 建模为装配根节点（结构容器）；连杆几何、关节和 IO 由子组件 RobotFixedBase/RobotRevoluteLink 及 AML 关节声明提供，此处不重复建立。"
  ],
  [
    "Robot6R is represented as a structural container (behavior=none); the six revolute links and their joints are supplied by separate component rules and DeviceIR joints, so no internal bodies or joints are declared here.",
    "Robot6R 表示结构容器，behavior=none；六个转动连杆及其关节由独立组件规则和 DeviceIR 提供，因此这里不声明内部刚体或关节。"
  ],
  [
    "Robot6R is treated as the assembly/container node; its six revolute links and fixed base are separate components with their own rules and DeviceIR joints.",
    "Robot6R 作为装配容器节点；六个转动连杆和固定底座均为独立组件，使用各自的规则及 DeviceIR 关节。"
  ],
  [
    "Servo position_kp / actuator tuning not supplied in AML.",
    "AML 未提供伺服 position_kp 或执行器调参数据。"
  ],
  [
    "Single rigid box link; box centered at AML geometry_center_* in body frame.",
    "连杆为单个刚性盒体，中心位于 AML 的 geometry_center_* 指定的刚体局部坐标处。"
  ],
  [
    "Six-axis drive, fixture, runtime enable/estop/reset semantics, PLC INT 0.01-degree encoding, and in-position logic are not implemented by any built-in behavior.",
    "内置行为尚未实现六轴驱动、检查夹具、运行时使能/急停/复位语义、PLC INT 每计数 0.01° 编码及到位逻辑。"
  ],
  [
    "Six-axis drive, position commands, measured feedback, enable/reset semantics and runtime support are not implemented by any built-in behavior kernel.",
    "内置行为内核尚未实现六轴驱动、位置指令、测量反馈、使能/复位语义和对应运行时支持。"
  ],
  [
    "Six-axis revolute position servo with measured feedback is not an available built-in behavior; behavior=none only preserves geometry and external joint.",
    "尚无带测量反馈的六轴转动位置伺服内核；behavior=none 仅保留几何和外部关节。"
  ],
  [
    "Static geometry demonstration only; no material or strength simulation.",
    "仅演示静态几何，不进行材料或强度仿真。"
  ],
  [
    "Static structural container: no actuators, no moving bodies, no IO added.",
    "静态结构容器，不添加执行器、运动刚体或 IO。"
  ],
  [
    "Stroke limits remain in legacy degrees; behavior=none models only the passive rigid link geometry.",
    "行程限位仍使用旧格式的度数；behavior=none 仅描述被动刚性连杆几何。"
  ],
  [
    "Stroke_min/stroke_max are in legacy axis units = deg and are converted to radians only in expressions for range limit checks; joint range itself is taken from the DeviceIR joint.",
    "stroke_min/stroke_max 使用旧轴单位 deg，仅在行程检查表达式中换算为弧度；实际关节范围取自 DeviceIR。"
  ],
  [
    "The AML kinematic_chain base->j1..j6 is honored through the joint definitions of the child bodies; no inverse kinematics is implemented (control_mode=joint_space_position_only_no_inverse_kinematics).",
    "AML 的 base→j1～j6 运动链通过子刚体的关节定义实现；未实现逆运动学，control_mode=joint_space_position_only_no_inverse_kinematics。"
  ],
  [
    "The fixed base is modeled as a rigid box with uniform mass density distribution; inertia is derived from the box mass and half-sizes, not from AML (not provided).",
    "固定底座采用均匀密度的刚性盒体；惯量根据盒体质量及半尺寸计算，AML 未提供该惯量。"
  ],
  [
    "The link is modeled as a rigid rectangular box; inertia is box-derived from mass_kg and body half-sizes (none supplied in AML).",
    "连杆采用刚性长方体；惯量由 mass_kg 及刚体半尺寸推导，AML 未提供惯量。"
  ],
  [
    "The revolute joint connecting this link to its parent is external (DeviceIR); no internal hinge is declared.",
    "连杆与父体之间的转动副由外部 DeviceIR 定义，不声明内部转动副。"
  ],
  [
    "The revolute joint itself is declared at scene level from the AML joints block; this rule only provides body geometry and declared joint limit range.",
    "转动副由 AML 关节声明在场景层建立；本规则仅提供刚体几何及声明的关节限位范围。"
  ],
  [
    "Velocity kernel limits are max_velocity_rad_s and max_acceleration_rad_s2; AML vmax/accel are command-unit-rate metadata only (deg-based) and are NOT used as SI kernel limits.",
    "速度内核采用 max_velocity_rad_s 和 max_acceleration_rad_s2；AML 的 vmax/accel 仅为基于度的指令速率元数据，不作为 SI 内核限值。"
  ],
  [
    "Wall thickness assumed uniform on all four sides given single wall_m parameter.",
    "由于仅有一个 wall_m 参数，假设四侧壁厚均匀一致。"
  ],
  [
    "acceptance_status is not_run; source_status example_parameters_not_measured.",
    "acceptance_status=not_run，表示未运行验收；source_status=example_parameters_not_measured，表示示例参数，未经测量。"
  ],
  [
    "binary_prismatic kernel is the only supported single-DOF agent; a revolute joint-space position servo with measured feedback, in_position tolerance and fault/reset semantics needs a new runtime kernel.",
    "当前仅支持 binary_prismatic 单自由度行为；带测量反馈、到位容差及故障/复位语义的转动关节位置伺服需要新增运行时内核。"
  ],
  [
    "enable_false_semantics (controlled stop then hold) and reset_semantics (clear latched fault) require runtime implementation that is not present.",
    "enable_false_semantics 所要求的受控停止后保持，以及 reset_semantics 所要求的清除锁存故障，均需尚未提供的运行时实现。"
  ],
  [
    "fixed_to_world flag not represented as a rule field; world anchoring is handled by the assembly/DeviceIR.",
    "fixed_to_world 不作为规则字段重复声明；固定到世界的关系由装配和 DeviceIR 处理。"
  ],
  [
    "fixed_to_world kinematics anchoring is not represented beyond the implicit static root body.",
    "除隐含的静态根刚体之外，尚未进一步表示 fixed_to_world 的运动学固定关系。"
  ],
  [
    "fixed_to_world=true is realized by DeviceIR connection to world, not duplicated inside this component rule.",
    "fixed_to_world=true 通过 DeviceIR 与世界的连接实现，不在组件规则中重复建立。"
  ],
  [
    "fixed_to_world=true maps to a weld joint to the world body at the component pose.",
    "原规则将 fixed_to_world=true 表示为在组件位姿处与世界刚体建立固定连接（weld）。"
  ],
  [
    "limit_policy 'reject_out_of_range_commands_no_silent_clamping' needs runtime enforcement not implemented.",
    "limit_policy=reject_out_of_range_commands_no_silent_clamping，要求拒绝越界指令而非静默截断；相应运行时约束尚未实现。"
  ],
  [
    "required_behavior 'six_revolute_joint_position_commands_and_measured_feedback': no built-in kernel for a 6R revolute position servo chain with measured feedback; runtime_support_status states six_axis_drive_and_fixture_not_implemented.",
    "required_behavior=six_revolute_joint_position_commands_and_measured_feedback；当前没有带测量反馈的 6R 位置伺服链内核，runtime_support_status 标记为六轴驱动和检查夹具尚未实现。"
  ],
  [
    "reserved_program_id_address %QW20 and IO address status 'proposed_example_mapping_not_field_verified' are preserved but not validated.",
    "保留 reserved_program_id_address=%QW20，以及 IO 地址状态 proposed_example_mapping_not_field_verified，但尚未完成验证。"
  ],
  [
    "revolute_position_servo drive (drive_model=csp, requested_behavior, command/feedback quantities) is not implemented by any built-in behavior kernel.",
    "内置行为内核尚未实现 revolute_position_servo 驱动，包括 drive_model=csp、请求行为和指令/反馈物理量。"
  ],
  [
    "start/stop/estop_ok are operator-written BOOL inputs; panel behavior kernel = panel. This revision only restates the panel envelope consistently with the assembly; IO quantity/writer roles are unchanged from 0.1.0.",
    "start/stop/estop_ok 是由操作员写入的 BOOL 输入，面板使用 panel 行为内核。本版本仅统一面板外形与装配的表述，IO 物理量和写入者角色相对 0.1.0 保持不变。"
  ]
]);
  function describe(value){
    const original=String(value??''),key=normalize(original);
    if(!key||/[\u3400-\u9fff]/u.test(key))return {text:original,original:null,translated:false};
    const text=translations.get(key);
    return text?{text,original,translated:true}:{text:'该说明尚无中文译文，请展开核对原文。',original,translated:false};
  }
  const errorTranslations=new Map([
    ['Approved rule versions are immutable; increment the version','已审批的组件规则版本不能修改；请为变更后的规则使用新版本号。'],
    ['Approved rule and asset versions are immutable; increment the version','已审批的组件规则和资产版本不能修改；请为变更后的内容使用新版本号。'],
    ['Review is stale: candidate, assets or checks changed','候选规则、资产或检查结果已更新，请重新核对后审批。'],
    ['Model implementation changed since validation; revalidate before approval','建模程序在检查后发生了变化，请重新检查组件后再审批。'],
    ['Approved modeling implementation changed; a new candidate review is required','已审批规则对应的建模程序已变化，需要重新提交候选组件进行审批。'],
    ['Unresolved/failed candidate cannot be approved','候选组件仍有未解决问题或未通过检查，不能批准。'],
    ['Reviewer and decision note required','请填写审批人。'],
    ['Only candidates can be approved','只能对候选组件执行审批。'],
    ['This exact approval already exists','该组件版本已有相同的审批记录。'],
    ['Approved rule changed','已审批的组件规则内容发生了变化。'],
    ['Candidate content-address collision','候选组件的内容与其摘要不一致。'],
    ['Validation target is not an isolated candidate','检查对象不是隔离环境中的候选组件。'],
    ['Authoring output truncated','模型生成内容被截断，候选规则不完整。'],
    ['Rules must cover exactly the missing types','候选规则必须完整覆盖缺少的组件类型，不能遗漏或增加类型。'],
    ['Duplicate rule type','候选规则中存在重复的组件类型。'],
    ['Blender candidate timed out','Blender 候选组件构建超时。'],
    ['Blender export is incomplete','Blender 导出的组件资产不完整。'],
    ['Missing rigid-body mesh','缺少刚体网格文件。'],
    ['Missing InstanceHierarchy','AML 缺少设备实例层级 InstanceHierarchy。'],
    ['Unsupported DeviceIR version','不支持此 DeviceIR 版本。'],
    ['Duplicate component ID','组件 ID 重复。'],
    ['Invalid/cyclic component parent','组件的父级引用无效或存在循环。'],
    ['Joint has unresolved/same bodies','运动副关联的刚体不存在，或两端指向同一刚体。'],
    ['Multiple kinematic parents','同一组件存在多个运动学父级。'],
    ['Zero joint axis','运动副轴向向量不能为零。'],
    ['Invalid joint range','运动副的运动范围无效。'],
    ['Joint initial outside range','运动副的初始位置超出运动范围。'],
    ['Stale scene edit','设备场景版本已更新，请刷新后重新修改。'],
    ['Edit outside explicit user scope','修改超出了用户明确选定的组件范围。'],
    ['LLM expanded edit scope','模型返回的修改超出了用户明确选定的范围。'],
    ['Failed to fetch','无法连接设备场景服务，请检查服务是否已启动。'],
    ['NetworkError when attempting to fetch resource.','读取设备场景服务失败，请检查网络连接。'],
    ['Load failed','读取设备场景服务失败，请检查网络连接。']
  ]);
  const errorPrefixes=[
    ['Moving body mass must be explicit in candidate rule: ','运动组件缺少明确的质量或密度，须修正规则后重新生成：'],
    ['Candidate authoring failed: ','候选组件规则生成失败：'],
    ['Local edit could not be validated: ','局部修改未通过校验：'],
    ['Missing approved component rules: ','缺少可用的已审批组件规则：'],
    ['Blender executable missing: ','未找到 Blender 程序：'],
    ['Blender candidate failed; see ','Blender 候选组件构建失败，详见日志：'],
    ['Approved bundle was modified: ','已审批的组件资产包被修改：'],
    ['Missing parameter ','缺少参数：']
  ];
  function errorMessage(value){
    const original=String(value??''),key=normalize(original);
    if(!key)return original;
    const translated=errorTranslations.get(key);if(translated)return translated;
    for(const [prefix,label] of errorPrefixes){
      if(!key.startsWith(prefix))continue;
      const detail=key.slice(prefix.length);
      return label+(['Candidate authoring failed: ','Local edit could not be validated: '].includes(prefix)?errorMessage(detail):detail);
    }
    // Local UI prefixes must not prevent translating the server's error.
    const wrapped=original.match(/^([^：\n]+：)([\s\S]+)$/u);
    if(wrapped&&/[\u3400-\u9fff]/u.test(wrapped[1]))return wrapped[1]+errorMessage(wrapped[2]);
    if(/[\u3400-\u9fff]/u.test(key))return original;
    // The original remains in the banner title and in the task's error record.
    return '操作失败，请将鼠标停在提示上查看原始错误。';
  }
  const checks={interface_alignment:'关节接口对齐',mount_alignment:'安装接口对齐',mjcf_compile:'MuJoCo 模型编译',physics_smoke:'物理仿真基本检查',joint_range:'关节运动范围',io_coverage:'IO 绑定覆盖',io_declarations_preserved:'IO 声明保留',static_geometry_preview:'初始几何预览',actuator_extended:'执行器伸出到位',actuator_retracted:'执行器缩回到位',in_transit_feedback:'运动途中反馈',fault_holds_actual_position:'故障时保持实际位置',one_item_per_edge:'每个触发沿生成一个工件',contact_driven_transport:'接触驱动输送',conveyor_fault_stops_transport:'输送故障停止运输',ray_geometry:'传感射线几何',parameter_domain:'参数适用范围',declared_interfaces:'接口声明',dimensions:'几何尺寸',isolated_mjcf_smoke:'组件独立仿真基本检查',component_preview:'组件预览',structural_container_without_geometry:'无实体几何的结构容器',candidate_build:'候选组件构建',manifold:'网格封闭性',positive_volume:'正体积检查',collision_manifest:'碰撞代理一致性',true_scale_bounds:'实际尺寸检查',clearance:'通道间隙检查',blend_and_preview:'Blender 源文件与预览',export_body_mapping:'导出刚体映射'};
    Object.assign(checks,{functional_fixture_coverage:'功能检查夹具覆盖',collision_proxy_matches_build:'碰撞代理与构建结果一致',source_flow_binding:'料源流向绑定',material_product_pool:'料源工件实例绑定',actual_short_outlet:'矮工件实际出口',actual_tall_outlet:'高工件实际出口',one_count_per_item:'每个工件仅计数一次'});
  checks.collision_free_path='运动路径部件干涉检查';
  checks.motion_plan_preflight='全行程测试路径预检';checks.local_motion_preflight='局部连接关系路径预检';checks.motion_scenario_precondition='机械测试工况前提';
  Object.assign(checks,{io_scenario_precondition:'IO 测试工况前提',io_port_target:'IO 端口作用对象',actuator_route:'指令执行器与关节绑定',controller_owner:'控制器归属',command_property:'指令角度换算与目标属性',command_target_isolation:'指令目标通道隔离',local_joint_response:'局部合法指令的实际响应',measured_channel_isolation:'其他关节的实际响应',measured_encoder:'实测关节角反馈编码',measured_in_position:'实际误差与到位阈值',invalid_command_rejected:'非法指令拒绝',controller_truth_table:'使能急停复位与状态反馈',contact_transition:'外部触点状态传递'});
  checks.controller_drive_gate='控制器指令与关节使能绑定';
  function checkName(value){const raw=String(value??''),parts=raw.split(':');return checks[parts[0]]?checks[parts.shift()]+(parts.length?' · '+parts.join(':'):''):raw;}
  const directions={input:'PLC 输入',output:'PLC 输出'},writers={plc:'PLC 程序',simulation:'仿真反馈',operator:'操作员',fault_harness:'故障注入'};
  globalThis.DeviceSceneText=Object.freeze({describe,errorMessage,checkName,direction:value=>directions[value]||value,writer:value=>writers[value]||value});
})();
