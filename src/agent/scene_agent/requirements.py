"""Scope of the device model; independent model checks are not PLC acceptance."""

MODEL_PROFILE='plc_executable_device/1'
DEFAULT_REQUEST='根据 AML 生成面向 PLC 验证的可执行设备模型，保留组件连接、运动约束和 IO，使用简化几何，并依据实际仿真状态生成反馈。'
MODEL_REQUIREMENTS={
    'profile':MODEL_PROFILE,
    'purpose':'面向 PLC 验证的可执行设备模型',
    'required':[
        '组件与刚体划分、父子关系、安装接口和坐标系明确，连接与装配一致。',
        '运动副类型、轴向、安装点、行程和限位正确，不重复定义约束。',
        'PLC 指令有可执行的驱动响应；复位、故障和联锁按 AML 与本次验证目标建模。',
        'IO 名称、方向、类型、地址、单位、写入者及物理量绑定与 AML 一致。',
        '位置、到位、检测和计数来自实际运动、接触或检测状态，不能复制指令或预设测试答案。',
        '保留影响接触、通道和检测的几何；组件检查、装配检查、行为检查与 PLC 在线验收分别记录。'
    ],
    'simplification':'允许使用基本形状和明确记录的等效参数；未要求验证的外观、纹理、外壳材质和电气内部细节不作为阻断项。',
    'blocking_policy':'unresolved 仅记录影响本次 PLC 验证的问题，并说明受影响行为与所需补充信息；未实现驱动、错误约束、缺失反馈或 IO 冲突必须阻断。',
    'limitations_policy':'provenance.limitations 记录不影响本次验证的未建模细节和适用范围；不得把功能缺失改名为 limitations 以通过审批。',
    'acceptance_boundary':'组件审批与设备独立检查通过，不表示 PLC 闭环验收通过。'
}

# These are implemented quantities, not capabilities an author may invent.
IO_KERNEL_QUANTITIES={
    'robot_control':{'enable_cmd','reset_cmd','ready','fault'},
    'joint_position':{'joint_position_cmd','joint_position_fb','in_position'},
    'conveyor':{'run_cmd','fault'},'source':{'emit_cmd'},
    'binary_prismatic':{'extend_cmd','extended','retracted','fault'},
    'ray':{'detect'},'sink':{'count'},'indicator':{'green_cmd','red_cmd'},
    'panel':{'green_cmd','red_cmd','start','stop','reset','estop_ok','delay_setpoint'}
}

# Authoring context for implemented code in runtime/device_scene.py. These are
# host capabilities, not fields to be invented inside component modeling rules.
RUNTIME_KERNEL_CONTRACTS={
    'robot_control':{'implemented':True,'rule_selector':'model.behavior=robot_control',
        'parameters':['estop_io_name','self_collision_policy','environment_contact_policy'],
        'behavior':'enable_cmd and estop_ok and no latched fault -> ready; estop false latches fault; reset_cmd clears only after estop restored. Disable decelerates joint reference and holds.'},
    'joint_position':{'implemented':True,'aml_behavior_alias':'revolute_position_servo',
        'rule_selector':'model.behavior=joint_position','parameters':['driven_joint_id','max_velocity_rad_s','max_acceleration_rad_s2','position_tolerance_rad','gravity_compensation'],
        'drive':'bounded acceleration and speed joint-space target -> actual external joint position actuator; fixed runtime owns behavior; no additional rule code or internal joints',
        'feedback':'joint_position_fb = rounded actual MuJoCo q / AML physical_per_plc_unit, saturated to declared encoder range; in_position uses actual q error; not copied command',
        'physical':'positive explicit mass_kg/position_kp/damping on the moving link; zero-position geometry uses AML geometry_center_* and half dimensions; preserve declared gains and gravity compensation',
        'scope':'joint space unloaded servo approximation, no inverse kinematics; collision simplifications require explicit AML policy'},
    'binary_prismatic':{
        'implemented':True,'aml_behavior_alias':'binary_prismatic_cushion_v1',
        'rule_selector':'model.behavior=binary_prismatic',
        'parameter_source':'DeviceIR component.parameters, copied into physical scene components.parameters; NOT model.physical_parameters',
        'joint_binding':'component.parameters.driven_joint_id references the external DeviceIR joint; the deterministic builder creates its actuator and hold equality on the child body',
        'parameters':['driven_joint_id','travel_m','nominal_speed_m_s','command_true_target_m','command_false_target_m',
                      'extended_threshold_m','retracted_threshold_m','cushion_fraction','minimum_speed_factor','minimum_cushion_m'],
        'drive':'bounded joint target updated from actual q and cushion speed factor each step; position actuator follows that target',
        'feedback':'extended/retracted derive from actual joint q and AML thresholds; fault holds actual q through joint equality',
        'io_binding':'AML component_id + quantity extend_cmd/fault/extended/retracted; names, direction, addresses and writer remain AML facts',
        'physical_parameters_location':'mass_kg/position_kp/damping belong to the external moving child rule; a geometry_enabled=false controller has physical_parameters={}',
        'schema_note':'controlled_joints is author INPUT context only; it is not an output rule field. No extra kernel parameter block is needed.'},
    'conveyor':{
        'implemented':True,'parameter_source':'DeviceIR component.parameters',
        'drive':'actual contact gates tangential force; limit=surface_friction*item_mass*9.81, velocity response time=0.02s',
        'collision_contract':'when AML declares collision_friction/collision_priority, preserve them on geom.friction/geom.priority; retain surface_friction as the drive force limit'},
    'common':{'io_binding':'runtime looks up AML component_id and quantity, not guessed PLC variable names',
              'unsupported_behavior_policy':'A missing required kernel, parameter, joint or IO binding remains unresolved. Do not infer a missing kernel merely because its parameters are not inline in the rule.'}
}
