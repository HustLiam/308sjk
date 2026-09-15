# AutomationML / IEC 62714（域条目 AML-xx）

本侧视角：⓪ 解析的**输入契约**与 io_list 预填的 ⓪→① 数据流。
结构权威 = src/agent/aml_parser.py 实现 + examples/aml 双基准（确定性解析，非 LLM）。

- **AML-01**（文件结构）CAEX 3.0：`InstanceHierarchy`（站拓扑：PlotterCell→
  设备树）+ 三个类库（SystemUnitClassLib 设备类型 / InterfaceClassLib 接口类型 /
  RoleClassLib 角色语义）；`InternalLink` 表达电气接线（A→B 端子对）。
  出处：plotter3axis_station.aml（IEC 62714 全结构参考样式）。
- **AML-02**（IO 点位）IO 点 = 设备上的 `ExternalInterface`，其属性给出
  name/dir/type/address/range/unit/signal——`build_io_list` 把它们确定性映射为
  io_list 初始值（name/dir/type/range/unit/device=信号语义）。出处：aml_parser。
- **AML-03**（方向语义）通道 dir 按**控制器视角**挂 PLC 通道：input=主站/现场
  写入 PLC（按钮/反馈），output=PLC 写出（指令/指示灯）。出处：plotter3axis
  .aml 重写说明（changelog「场景+模块 v1.0」行）。
- **AML-04**（地址权威）io_points.address（%QX/%QW）是 XML 定位变量与验收脚本
  的共同寻址语言——R6 地址腿逐字对账的对象；名字/类型全对但地址自编一套的
  产物会在 Modbus 层才露馅（画圆战役实证，R6 由此催生）。
- **AML-05**（运动学）轴参数（stroke/unit/vmax/accel/poswin）从运动学属性
  提取为 kinematics.axes——②b 场景生成（gantry travel=stroke×scale、
  speed=首轴 vmax×scale）与 ②a 生成（POSWIN 到位窗口）的 ⓪ 侧来源。
- **AML-06**（降级路径）无 AML 时 ②b 从 io_list 的 `<axis>_fb` 量程/单位推断
  轴信息（scene_gen._axes_info）；地址腿由编排器 fix_addresses 确定性补齐。
- **AML-07**（预填契约）两份基准 AML 的 build_io_list 预填与对应基准 spec 的
  io_list **逐字等价**（契约测试锁定）——io_list 是三方唯一源头，LLM 只允许
  校验/补充语义字段，不得动 name/dir/type/range。
