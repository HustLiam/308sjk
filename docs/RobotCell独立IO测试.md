# 010RB_100 机械臂独立 IO 测试

当前对象是 `ST010/010RB_100/010RB_100_KR240R2700prime_`，组件 ID 为
`AF138E59-0000-17AC-556E-B124000031B5`。使用当前资源导入生成的 MuJoCo 场景。
原始 AML 尚未定义这些通道，本次地址是独立测试配置，不写回 AML，也不改变四份冻结契约。

2026-11-17 按参考场景移除 ST020 的多余机械臂、020GES191 导轨、随臂夹爪和独立控制柜，清理空置 ST020。
PLC 变量名、寄存器地址和编码保持，映射迁移到 010 机械臂；初始位置及限位以 MuJoCo 模型为准。历史验收条目中的
020 机械臂及旧 qpos 索引只记录迁移前结果。

唯一 IO 源文件是 `examples/robotcell/010RB_100.io.json` 中的 `io_list`。
适配器位于 `src/agent/scene_agent/robotcell_io.py`，PLCopen XML 由同一配置生成。
本阶段验证位置量通信；适配器采用有限速度位置跟随和 MuJoCo 正向运动学，未执行力矩控制、碰撞或动力学验收。

## 配置职责

| 文件 | 内容 |
|---|---|
| `examples/robotcell/010RB_100.io.json` | 仅 `io_list`：PLC 变量名称、类型、地址、方向、用途、角度换算、设备 ID 及源关节标识 |
| `examples/robotcell/010RB_100.adapter.json` | 更新频率、指令超时、仿真场景路径、PLC Runtime 连接 |
| `examples/robotcell/010RB_100.motion.json` | 该模型的位置运动速度上限；实际关节限位及初始姿态仍来自编译后的 MuJoCo 模型 |

IO 文件不再含 format、顶层设备对象、嵌套 binding、初值、限位及适配器/模型设置。
每条 PLC 变量直接携带 `component_id`；角度变量同时携带 `source_file`、`source_id` 和 `deg_per_count`。
当前适配器实例处理同一设备的六轴，所有变量的设备 ID 必须一致。

```json
{
  "io_list": [
    {
      "name": "rb100_j1_target",
      "role": "target",
      "dir": "output",
      "type": "INT",
      "address": "%QW100",
      "deg_per_count": 0.02,
      "component_id": "AF138E59-0000-17AC-556E-B124000031B5",
      "source_file": "dae_lib/KR240R2700prime_Product.dae",
      "source_id": "KR240R2700prime_010RB_100_KR240R2700prime__Joint_1"
    }
  ]
}
```

此片段展示一条变量；实际文件保留六对目标/实际位置、使能和反馈序号共 14 条映射。
使能/序号的协议范围由各自用途确定，角度的 INT16 编码范围由类型确定；机械限位不复制到 IO 文件。
适配器解析 `model.jnt_range` 后换算可用整数范围，超限命令整组拒绝；无法覆盖模型行程的编码也拒绝。
测试程序启动值为禁用状态的零值，控制前先由模型实际 qpos 下发当前姿态，再启用运动，不把零值当作机械初态。

## 通道与数值

方向以 PLC 为基准；所有地址都是 OpenPLC 的 holding register，Modbus 协议地址从 0 开始。
反馈使用可从外部注入的 `%QW` 区，`dir=input` 表示 PLC 消费该值，不代表 `%IW` 硬件区。

| 源关节 | PLC 目标变量 / 地址 | PLC 实际位置变量 / 地址 | 角度限位 |
|---|---|---|---|
| Joint_1 | rb100_j1_target / %QW100 | rb100_j1_actual / %QW110 | −185～185° |
| Joint_2 | rb100_j2_target / %QW101 | rb100_j2_actual / %QW111 | −140～−5° |
| Joint_3 | rb100_j3_target / %QW102 | rb100_j3_actual / %QW112 | −120～155° |
| Joint_4 | rb100_j4_target / %QW103 | rb100_j4_actual / %QW113 | −350～350° |
| Joint_5 | rb100_j5_target / %QW104 | rb100_j5_actual / %QW114 | −122.5～122.5° |
| Joint_6 | rb100_j6_target / %QW105 | rb100_j6_actual / %QW115 | −350～350° |

另有 `rb100_enable / %QW106`（PLC 输出，0 保持、1 跟随）和
`rb100_feedback_seq / %QW116`（仿真输入，反馈发布序号）。

角度通道为有符号 `INT16`，`角度[deg] = 寄存器有符号值 × 0.02`，
`qpos[rad] = 角度 × π / 180`。例如 90° 对应 4500，−90° 对应 −4500，
线上用 16 位补码传输。0.02° 分辨率可覆盖 ±350°；0.01° 的 INT16 无法覆盖。
反馈取实际 `data.qpos` 并量化，误差最多 0.01°，不直接回显目标值。
更新目标周期为 30 Hz，预览速度上限 30°/s；非实时 Modbus 通信可能使实际周期变长。

## 重建后的绑定

持久配置保存 `component_id + source_file + source_id`，不保存关节 ID、qpos 索引或内存地址。
启动时通过 `assembly_checks.json` 和 `body_mapping.json` 查找对应 body，
再从本次编译的 MuJoCo 模型读取 joint ID、`jnt_qposadr`、`jnt_dofadr` 和限位。
缺失、重复、组件不符、非受限旋转关节或编码范围无法覆盖模型行程均拒绝启动。
当前绑定已迁移到 `010RB_100` 的 `Joint_1`～`Joint_6`，来源为
`dae_lib/KR240R2700prime_Product.dae`。最新索引以实际重建解析结果
`workspace/2026-11-17-robotcell-010/resolved_bindings.json` 为准。
此次最终模型的六轴依次为 `jb2852/jb2855/jb2858/jb2861/jb2864/jb2867`，
`jnt_qposadr` 依次为 53～58；与旧机械臂曾使用的数值相同只是重排后的结果，绑定并未复用旧 body 或索引。

## PLC 测试协议

沿用 `plc_test.py` 的隔离测试程序。生成的 `plc_test_contract.json` 给出身份、
request/commit/ack、staging 和 observed 寄存器地址；不能手动猜测这些地址。
仅 PLC 变量映射、源关节标识和数值编码参与程序身份摘要。适配器逐周期核对 PLC 身份。
适配器连接/周期/超时和模型运动速度不参与 PLC 身份；改变这些设置无需重新生成 PLC 程序。

1. `TestDriver` 写 staging、最后提交 commit，等待 PLC 扫描确认 ack。
2. PLC 程序把 staging 复制到 `%QW100～106`，这些实际输出由 PLC 写入。
3. 适配器核对读取前后的 request/commit/ack 一致，再读取目标值，更新对应 qpos。
4. 适配器写 `%QW110～116`，PLC 扫描把输入复制到独立 observed 区。
5. 测试读取 observed，核对反馈值与发布序号，证明反馈经过 PLC 扫描。

适配器不写目标输出或 staging。控制端须在 2 秒内发布新的请求序号，即使目标不变也要续发。
身份不匹配、命令快照不一致、越限、Modbus 故障或超时会结束适配器循环，并保持当前 qpos。
该协议用于独立 IO 实验；正式 PLC 业务程序接入时应明确实现对应协议，不能直接用任意已有 PLC 程序替代。

## 运行

仓库根目录执行，依赖现有 Python、MuJoCo 和 pymodbus。默认 Runtime 为本机 VMware 中的独立测试实例：
Web `http://192.168.12.131:8081`，Modbus `192.168.12.131:1502`。
`scene_dir` 相对适配器设置文件所在目录解析；重建或换任务可传 `--scene-dir <imported_scene目录>`。
默认按 IO 文件同目录、同名前缀加载 `.adapter.json` 与 `.motion.json`；CLI `--adapter/--motion` 可指定其他设置文件。
CLI `--host/--port/--web-url` 可覆盖测试端点，不影响绑定身份。设置缺失或无效时 CLI 报错，前端禁用 PLC 控制，不回退到旧配置。

```powershell
# 解析实际完整模型并输出当前索引
python -m src.agent.scene_agent.robotcell_io inspect

# 同一 io_list 生成唯一 PLC 源码和测试协议地址表
python -m src.agent.scene_agent.robotcell_io export --out runs/robotcell_io_20261117/iter_003
python src/pipeline/xml2st.py runs/robotcell_io_20261117/iter_003/io_test.xml --check

# 上传至配置指定的测试 Runtime，真编译并启动；替换该实例当前程序
python -m src.agent.scene_agent.robotcell_io deploy --out runs/robotcell_io_20261117/iter_003

# 使用完整 MuJoCo 模型，发送目标、校验 PLC 输出与 PLC 读取的反馈
python -m src.agent.scene_agent.robotcell_io test
```

`deploy` 仅通过 XML 转换器获得 ST，不手写 ST；Runtime 须沿用项目已验证的编译配置
（旁路有语义缺陷的 st_optimizer，保留 matiec）。以前的独立测试容器已配置过该保护；
如果更换 Runtime，应先核对其编译环境。登录使用 OpenPLC 客户端现有默认账号，HTTP 禁用环境代理。

`test` 包含禁用保持、两组反向目标和返回量化初始位置，逐周期核对其他关节不变，最后清除使能。
`run --seconds 30` 只运行适配器，需要另一个控制端按协议持续提供命令。
运行结果在 `workspace/2026-11-17-robotcell-modbus/`，失败记录为 failed，不记为通过。

CLI 适配器独立加载模型文件，拥有自己的 `MjData`。前端通过
`src/agent/scene_agent/plc_preview.py` 将同一适配器接到当前页面的关节预览会话，
在 PLC 确认反馈后发布实际 qpos 和姿态，浏览器显示该状态。

## 前端手动测试

在组件树中选择 `010RB_100_KR240R2700prime_`，勾选要测试的关节、填写目标角度和运动时长，
点击“执行所选关节”右侧的“PLC控制”。目标经 TestDriver 提交给真实 OpenPLC，
适配器读取 PLC 输出、更新 MuJoCo，再把实际位置送回 PLC observed 区确认。
控制线程顺序执行驱动端与适配器，二者共用本次运动的一条 Modbus TCP 连接。
未勾选的关节保持原 qpos；六轴实际位置均反馈给 PLC。

按钮依据当前模型是否完整解析 IO 配置绑定启用；未绑定组件、结构父节点或混合选择显示灰色禁用，
后端同样拒绝这些 PLC 请求。地址读取适配器设置的 `runtime`，当前为 `192.168.12.131:1502`。
三个文件均参与前端预览缓存校验；修改后重新加载场景，模型重建时依据源标识重新解析实际索引。

PLC 运行期间禁用普通位置执行及复位，仍可点击“停止”。停止或结束清除 PLC 使能并保持最后确认的位置；
通信故障和未确认清零在现有错误区域显示。切换组件停止旧会话，页面超过 5 秒未轮询也会停止；
同一 Runtime 同时只允许一个浏览器预览会话控制。预览速度上限仍为 30°/s，运动时长不足时自动延长。
这些操作不写回源模型文件，仍属于位置 IO 测试。

## 验证状态（2026-11-17）

- 前端接入后的最终全量回归：1047 项通过（283.97 秒），日志 `workspace/2026-11-17-plc-control/pytest-final.log`。
- 前端接入真实 OpenPLC：40 项相关测试及 Chrome 16 项检查通过。关节53由约72.471°到75.480°，34个不同位置帧，qpos/画面/PLC observed反馈一致；未勾选关节保持，停止清使能、PLC恢复初态、未绑定组件禁用及HTTP拒绝均通过。10份源任务/模型记录摘要保持，独立Runtime最终RUNNING、enable=0。证据 `workspace/2026-11-17-plc-control/acceptance.json` 和 `browser-report.json`。
- 全量回归 1036 项通过；随后新增 TCP 用例，最终专项 16 项通过。覆盖索引重建重绑、重复/错误绑定、越限、补码、反馈读取、超时保持、错误 PLC 身份，以及真实 localhost Modbus TCP 收发；TCP 服务端使用明确标记的扫描替身。
- 完整 RobotCell：84 关节、4330 body、1828 geom；实际模型绑定和双向数据路径离线检查通过，非绑定 qpos 保持。同一完整模型的真实 localhost TCP 收发也通过。证据为 `actual_model_offline_report.json` 和 `actual_model_tcp_report.json`，后者明确标注 `MODBUS_TCP_SCAN_SUBSTITUTE_NOT_OPENPLC`。
- 真实 OpenPLC：网络恢复后联调通过。使用独立容器 `openplc-io-test-20261103`（8081/1502），核对编译保护后上传本配置生成的 XML 转换产物，真实 matiec 编译成功，Runtime 为 RUNNING。六轴两组反向目标和返回量化初始位置全部通过，14 个通信周期的实际输出、MuJoCo qpos、PLC observed 反馈一致；禁用保持及其他关节不变检查通过。最终使能 `%QW106=0`。
- 证据：`workspace/2026-11-17-robotcell-modbus/live/acceptance.json`、`live/test_report.json`；编译/部署记录在 `runs/robotcell_io_20261117/iter_002/compilation.log` 和 `deployment.json`。16 项专项复测通过。本轮无实现代码修改，沿用此前全量回归结果。
- 原 8080/502 实例仍为 STOPPED，活动程序及源码摘要保持；原始 AML/导入模型未修改，场景未冻结。此前网络阻断记录保留在 runtime_status.json 的 previous_probe 中。

### 010 机械臂迁移验收（2026-11-17）

- 最终源 AML 为整组删除 ST020，299 组件、4 台机械臂，重建模型含 3602 bodies、1543 geoms、69 joints。
- `runs/robotcell_io_20261117/iter_002/io_test.xml` 静态检查和专用 Runtime matiec 真编译通过，程序 `629766.st`，身份 `a7d6e9a78a7d48632c0e4f54296c14e2bd9bcc73c2feff62a0db9a5e9a0982e5`。
- 真实 Modbus 六轴双向验收通过：14 周期覆盖禁用保持、两组反向目标和初态恢复；其他 qpos 不变，PLC observed 与实际反馈一致，结束使能为 0。证据 `workspace/2026-11-17-robotcell-010/acceptance.json`。
- 全量 1050 项通过（280.84 秒），最终配置专项 40 项通过。
- 实际Chrome20项检查通过：020机械臂/导轨/控制柜消失、原始网格保持、010 PLC按钮启用、实际运动与PLC observed反馈一致、停止保持/清使能、恢复初态、未绑定组件按钮及HTTP均拒绝；无JS异常。截图与报告见同目录full-scene.png、plc-controlled.png、browser-report.json。

### 配置职责拆分验收（2026-11-17）

IO仅映射列表，adapter与motion设置分开，模型初态/限位动态读取。全量1060项（268.79秒）、专项50项、真实Chrome20项通过；iter_003 XML静态及matiec通过，程序929194.st，六轴真实双向14周期通过，最终Runtime RUNNING、enable=0。源AML/模型摘要保持。证据workspace/2026-11-17-io-separation。

2026-11-17 后续更新：用户明确要求更新服务后，8765已重启为PID27636，读取拆分后的三份配置。旧进程仍检查顶层component_id，导致新IO列表被判定未绑定；Python服务不自动重载模块，必须重启。8765实际Chrome20项通过，PLC目标→MuJoCo→PLC反馈、停止清使能及恢复均通过，最终enable=0；证据workspace/2026-11-17-service-update。8766为先前验证服务，日常入口使用8765。


### Agent 控制台（2026-11-18）

原局部修改入口改为“agent控制”，按钮为“下发命令”；移除原位置修改按钮及示例描述。用户必须在命令中明确设备，后端锁定唯一的可动组件，前端自动选中；结构父节点、名称歧义和未检验设备不执行。错误/澄清复用现有错误区域，未新增常驻说明。

- IO 检验：`robotcell_io test` 的实际双向验收成功后写相邻 `.verified.json`，绑定配置、模型 XML、body_mapping、joint_records 与验收报告摘要。Agent 发现 `examples/**/*.io.json` 及当前场景任务 `io/*.io.json`，要求适配器 scene_dir 匹配当前场景；配置、模型、证明文件变更或重复设备配置使资格失效。IO 文件本身继续只含 io_list。
- 理解和生成：复用 RequirementUnderstander 的模型调用与 PLCGenerator 的静态/一致性闸门，新增已绑定运动计划分支；模型输出受约束计划，固定生成器将它转换为 PLCopen XML。计划落为独立 motion_plan.json，不修改冻结 requirement_spec 契约。
- 当前能力：任意数量已验证 limited hinge 的位置映射；绝对/相对运动、多个关节同步、顺序动作、到位后停留、有限重复和返回指定姿态。名称、源关节、IO 地址、比例、实际限位均来自配置与当前模型，未固化010或六轴索引。笛卡尔路径、缺失夹爪/传感器IO等超出当前能力的请求返回澄清，不省略动作后执行。
- 执行：XML 静态→IO一致性→模型绑定→OpenPLC matiec 编译→程序身份核对→Modbus执行。PLC自身通过TON计时、目标插补和实测反馈推进步骤；主机仅发送使能/心跳、读取PLC目标和回写真实qpos反馈，未指定关节保持。任务、XML、编译/部署/执行证据保存到 runs/scene_command_<id>/iter_001。
- 会话：同一服务内与手动PLC控制共用端点互斥；运行期间拒绝直接位置命令。已有停止按钮取消任务并清使能；浏览器断联停止，结束后生成程序mode=0兼容原手动PLC按钮。当前为运动学位置预览，未增加夹爪行为、逆运动学或碰撞避障能力。
- 当前场景已切换到 imported_scene_2aeefc93ede14732a519e819a3b44a28；适配器已同步，并针对当前目录重新通过六轴14周期双向IO检验。
- 验收：全量1093项通过（257.37秒）；专项75项。真实模型调用与浏览器/PLC验证：单轴相对+停留+返回，双轴同步往返重复两次，执行中停止/清使能，未指定关节保持，未绑定/歧义名称拒绝，执行后手动PLC恢复可用。证据 workspace/2026-11-18-agent-control/{browser-report.json,extended-report.json,current-io-qualification/test_report.json}。
