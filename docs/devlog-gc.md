# gc 本地开发日志（devlog-gc）

> 技术说明专用（改了什么/为什么/如何验证/踩坑），永不进 master；进度与协调写协作看板。

## 2026-09-01 gc 侧首批实现（Schema 草案 / 一致性检查器 / 生成器 v0 / 编排器半环）

### requirement_spec Schema + 校验器

- 交付 `schemas/requirement_spec.schema.json`（draft-07，供三方评审与工具链）+
  `src/agent/spec_validator.py`（纯 Python 可执行校验，零新依赖——jsonschema 不在
  requirements.txt，为避免未登记依赖，结构规则双写：Schema 文件为评审视图，
  校验器为运行权威，两者必须同一 RFC 内同步改，已在两处文件头注明）。
- acceptance 四类字段逐字取自 csk 文档 §5.1 的 JSON 示例，未增删字段名；
  `check_at` 冻结为 `"end"`（csk 示例只出现该值，扩展走 RFC）。
- JSON Schema 表达不了的跨字段语义落为 S1~S4（唯一性 / INT 必带量程 / 信号引用
  必须落在 io_list / 时间阈值 ≥100ms），实现在校验器并写入 Schema description。
- io_list.type 封闭集定为 {BOOL, INT}：依据 lx 位宽契约（%QX↔BOOL、%QW↔INT/UINT/WORD）
  与"模拟量统一 INT@%QW 定点换算"的双链路约定收窄到两值；UINT/WORD 留在
  ST 层兼容集但需求层不开放，避免三方各写各的。range 挂在 io_list（raw 区间），
  换算系数按主方案 §3.3 归 io_map——量程语义与换算实现分离。
- 验证：tests/test_spec_validator.py 19 例（正例 = examples/specs/sorting.spec.json，
  该示例的 io_list 与 sorting.xml 定位变量逐字对齐，兼作一致性对照样例）。

### 三方一致性检查器

- `src/agent/consistency_check.py`，规则 R1~R5：R1 先过 lx 的 xml2st.parse（位宽/
  拒绝清单短路，文本形态落临时文件走同一代码路径，保证与 CLI 裁定一致）；
  R2 名称双向一致（XML 里多出的定位变量也算违规——单一源头）；R3 类型宽度匹配；
  R4 地址查重；R5 io_map 腿（plc_var/dir/type/bind 结构对账，主方案 §3.3）。
- io_map=None 时输出 SKIP 行不算失败——csk 侧尚未产出，半环只对账两方，
  接口已就位，对方就绪后自动生效。
- 坑：想单测"R3 类型不匹配"时发现，spec=INT 而 XML=REAL@%QW 会被 R1 先拦
  （REAL 不在 WORD_TYPES），R3 真正独立的失败面只剩"io_list 类型出封闭集"。
  这是防御纵深而非冗余，保留。
- 验证：tests/test_consistency_check.py 15 例，含路径/文本两种输入等价性。

### 生成器 v0（智能体框架迁移）

- 框架从 308sjk_history/agent 迁移：client.py/config.py 原样沿用（BigModel
  OpenAI 兼容封装 + .env 密钥加载）；pipeline.py 的生成-校验回灌循环保留，
  校验端从历史上的 plc.validator 换成本仓库真实闸门（xml2st + 一致性）。
- 模式库 patternlib.py：不复制代码，直接从 src/plc/*.xml 提取 ST 本体做模式卡，
  关键词选卡（task_goal + io_list.device 参与 matching），命中不足用通用卡补齐。
  lx 侧维护场景后卡片内容自动跟随，无第二份拷贝可漂移。
- prompts/plcgen_skill.md：lx 契约 §3 的操作摘要（生成器运行时材料）。红线
  "契约不复写"针对文档间漂移；prompt 属实现（如 xml2st.py 本身就是契约的代码
  化），且机械裁定权始终在 xml2st——prompt 写偏只损首过率，不会放过违规产物。
- thinking disabled 参数带上 400 兜底重试（历史框架同款）。
- 种子模式：PLCGenerator(client=None, seed_xml=…) 不调 LLM 直接以种子 XML 过
  双闸门——编排器联调/回归的门禁通道，种子不是免检通道。

### 编排器半环

- `src/agent/orchestrator.py`：solve() 骨架对齐 gc 文档 §4 伪代码。闸门顺序
  生成 → xml2st（本地毫秒级）→ 一致性 → 部署（可选）。全环（SceneSpec/USD/
  Isaac/verdict）留挂点未实现——csk 接口未就绪，接了也是死代码。
- 部署闸门语义：POST /deploy :8600 连不上记 skipped 不记 failed（半环约定，
  服务不在线不该阻塞 final）；真编译失败才回喂 errors 重试。
- 迭代记忆 _pack_feedback：失败证据原文 + 已通过闸门清单（"禁止回退已通过
  的修改"的落地），best_effort 取闸门推进最远一轮。
- 产物落盘 runs/<task_id>/{request.json, iter_NNN/{plcopen.xml, plc.st, gate.json},
  final/, summary.md}，CLI 冒烟已跑（runs/sorting_demo，种子模式）。
- 验证：tests/test_orchestrator.py 9 例 + tests/test_patternlib.py 8 例。
  全仓 pytest 75 例全绿（含 lx 侧 test_xml2st 24 例回归）。

### 遗留 / 下步

- LLM 真实生成联调需要 API Key（ZHIPUAI_API_KEY / .env），种子模式已覆盖
  全部编排逻辑，LLM 路径只差 _call 端到端跑通；
- Schema 冻结等三方评审（RFC 流程，主方案 §8.3）；评审通过后 schema_version
  从 1.0.0-draft.1 升 1.0.0；
- 推送远端失败：当前 GitHub 凭据（1433223-ysy）对 HustLiam/308sjk 无写权限，
  本地 gc 分支就绪，待权限后 push。

## 2026-09-03 第二批：⓪ AML 解析 + draft.2 + 闸门4（pytest 74→100）

背景：合并 origin/master（场景库重组为 motion3axis 单场景 + 架构 v2.0 新增 ⓪）。
本批三件事：⓪ AML 解析模块（架构 v2.0 新职责）、Schema draft.2（lx INT16 域建议）、
编排器闸门4（链路 B 验收）。

### ⓪ AML 解析器（src/agent/aml_parser.py + tools/aml_parser.py CLI）

- **关键词误判坑**：接口识别最初对 RefBaseClassPath 整串做子串匹配，
  "308sjkInterfaceClassLib" 里的 "interface" 含 "int" 子串 → 所有 DigitalInput 被
  判成"数字/模拟并存"。修正：只按类路径**末段**（接口类名本身）判
  input/output/digital/analog——库名永远不该参与信号语义判定。
- **xmlns 不敏感**：CAEX 按标签 localname 匹配（剥 `{ns}` 前缀）。真实 AML 工具
  导出带 xmlns="http://www.dke.de/CAEX"，手写示例常不带，两种形态必须等价解析
  （单测 test_xmlns_invariant 保证）。
- **确定性**：文档先序遍历、JSON 输出无时间戳/路径无关字段——同输入同输出逐字节
  相同（单测保证）。这是主方案 §3.0 "确定性代码（非 LLM）"的可测试化表达。
- **错误语义分层**：结构错误（非 CAEXFile/无 InstanceHierarchy/坏 XML）抛
  AMLParseError（CLI exit 2）；内容问题（IO 重名/地址冲突/方向不可判定/%I 区/
  BOOL 带量程/axis_type 非法/断链）收集进 problems 返回（exit 1），模型 best-effort
  产出——闸门语义与生成器双闸门一致，问题文本可直接进反馈包。
- **%I 区地址**：记 problem 而非抛错——lx 位宽契约统一 %Q 区，⓪ 作为第一道闸门
  尽早暴露工程侧映射问题，但不该崩掉整个解析。
- **axis_type 必须显式声明**才入 kinematics.axes（有 stroke/vmax 无 axis_type 记
  问题不猜测）——轴类型（linear/rotary_modulo/rotary_finite）决定 lx INTERP 的
  WRAP 参数语义，猜错会静默生成错误回绕行为。
- **地址 ↔ 类型交叉校验**：%QX↔BOOL、%QW↔INT，接口关键词与地址矛盾记 problem
  （比单源校验强，且不加任何猜测）。
- **预填契约测试**（test_prefill_equals_spec_io_list）：示例 AML
  examples/aml/motion3axis_station.aml 的 build_io_list 预填与
  motion3axis.spec.json 的 io_list **逐字等价**（name/dir/type/range/device/unit，
  顺序无关——AML 按设备分组、spec 按功能分组）。AML 的 description 属性即 spec
  的 device 语义串来源。这条测试把 ⓪→① 数据流钉死：任何一侧漂移立刻红。

### Schema draft.2：lx INT16 域建议的落实口径

- lx 建议字面是"INT16 域"，但 motion3axis 状态字 x_sw=[0,65535] 是**在用事实**
  （UINT16 满量程），按字面 [-32768,32767] 收紧会打红基准示例。
- 落实口径：**%QW 字并集域 [-32768,65535]**——依据 lx 自己的位宽表（%QW 承载
  INT/UINT/WORD，同一 16 位寄存器），INT16 下界 + UINT16 上界。已在看板向 lx
  说明并请确认（如要收紧到纯 INT16 需先改 motion3axis 状态字语义，属契约联动）。
- 校验顺序：min<max → 域检查（elif 链，一次只报最具体的错）。

### 编排器闸门4（链路 B 在线验收）

- 场景名→脚本映射：src/pipeline/scenario_<名>.py；CLI --acceptance + --scenario
  （缺省取 --seed 文件名茎，否则 task_id——task_id 是 runs 目录名带 _demo 后缀，
  不天然等于场景名，不猜）。
- 离线判定：验收脚本连不上 Modbus 时输出含"无法连接"/"ConnectionError"
  （lx modbus_io.connect 的 ConnectionError 语义），闸门读输出特征记 skipped——
  与闸门3 的半环约定一致：环境缺失不阻塞 final，真失败（exit 1 的 PASS/FAIL
  明细尾部 40 行）才回喂重试。
- 验收不必先过闸门3：serve :8600（deploy）与 OpenPLC Modbus :502（验收）是两个
  服务，serve 不在线不代表运行时没有已部署程序；require_program 读 %QW20 兜底
  程序身份，陈旧程序不会被误验收。
- 可注入性：subprocess 调用收敛到 _run_acceptance 单方法，monkeypatch 它即可
  模拟离线/成功/失败三态，不碰真子进程。

### 验证

- pytest 100/100（新增 19 AML + 5 闸门4 + 2 S2 域）；
- CLI 冒烟：--deploy --acceptance 双离线 → deploy/acceptance 双 skipped、final
  达成，gate.json 语义正确；
- 本机无 OpenPLC/serve（502/8600 均关），在线链路的真验收留待运行时环境
  （lx 的 run_regression.py L3 同源场景脚本）。

## 2026-09-03 补记：master 合入走查

- 推送 master 时被拒：lx 几乎同时合入了工具增量（run_regression.py + GET /status，
  origin/master b97025c→08e8e91）——fetch 后二次合并，冲突两处（changelog 表头行 +
  看板变更记录行），均为同日平行新增行，双边保留（lx 行在前、gc 行在后）。
- master 合入链：b3117b8（移除日志）→ 8a2709d（--no-ff 合并 gc）→ a7e4bcc（并入
  lx 工具增量）。master 树无 devlog（ls-tree 验证 0），pytest 100/100。
- lx 的 GET /status 已上线：返回 {runtime.status, prog_id, program}——下批接入
  编排器（部署前一站式确认运行时状态，比裸 POST /deploy 的超时失败信息友好）。
- run_regression.py 是 lx 侧 L1-L3 门禁，与 gc 编排器闸门互补不重叠：前者管合入
  前回归，后者管生成闭环。

## 2026-09-07 第三批：三轴绘图仪场景贯通 ⓪→①→②a→②b + 闸门链全绿（pytest 100→129）

背景：负责人指令——生成三轴绘图仪 .aml、完善 ⓪①②a②b 四模块、与 lx 侧全闭环联调。
合并 origin/master（95bd923：lx 评审响应 + float32/INT16 议题登记）后开工。

### ⓪ 三轴绘图仪 AML（examples/aml/plotter3axis_station.aml）

- **负责人纠偏**：首版 AML 镜像了 motion3axis 的 PLC 变量表（描述里全是 MC_*
  语义、测试专用的 inject_fault 也进了设备描述）——被指出 AML 是独立的工程侧
  设备描述标准（IEC 62714/CAEX），不应反向参照 lx 的 PLC 代码。推倒重写为
  **CAEX 3.0 全结构**：InstanceHierarchy（PlotterCell 单元：绘图机本体+操作台+
  控制柜/PLC/IO 模块）+ SystemUnitClassLib（设备类型库）+ InterfaceClassLib
  （信号类）+ RoleClassLib（角色）。
- **信号方向的建模口径**（IEC 62424 惯例，控制器视角）：DigitalInput/Output/
  AnalogInput/Output 四个接口类**只挂在 PLC 通道上**（PLC 的 IO 模块通道
  ExternalInterface，携带 address/range/description 工程属性）；现场侧元件用
  中性连接器类（ButtonContact/LampCircuit/Encoder/VelocityReference/StatusWord/
  PositionReference），不含方向语义。解析器的 io_list 唯一来源因此天然=PLC 通道，
  现场侧不会重复计入。
- **InternalLink=电气接线**：26 条 wire_*（现场连接器↔PLC 通道）+4 条 mount_*
  （机械法兰）。device_model.topology 首次携带真实电气语义（此前只有机械 flange）。
- **io_list 从设备视图重推导（27→26）**：删 inject_fault——测试专用注入不属于
  设备（越程测试改由主站直接写 x_sp=150 实现，语义更真）；其余 26 点全部保留
  （8 DI 按钮/6 DO 指示灯/6 AI 反馈+设定值/6 AO 状态字+速度指令），地址沿用
  %Q 区约定（契约②，跨团队契约而非 lx 代码）。
- **XML 注释坑**：分隔注释 `<!-- ---- 标题 ---- -->` 含 `--` 序列不合法（XML
  规定注释内不得出现双连字符），改用 `====`。
- 解析验证：problems=[]，26 io 全带地址、3 轴（z=[0,10]mm 笔轴）、30 链接
  （26 电气 + 4 机械）；class lib 不污染 io_list（专测保证）。

### ① 需求理解模块（src/agent/requirement.py + prompts/specgen_skill.md）

- 三形态：**LLM 模式**（spec 主体由 LLM 组织、io_list 逐字锚定预填——比主方案
  "校验和补充"更收紧：name/dir/type/range 四元组不得动，只许润色 device 语义）、
  **模板模式**（无 client 离线回归：预填+goal+最小 sim_health）、澄清问题
  （build_io_list 的 pending 随报告返回，人工介入点 1 的机器侧形态）。
- 锚定检查 _anchor_problems：改名/发明 IO/删减点位三类均拒；LLM 修复回路
  max_rounds=3（与 ②a 生成器同构）。
- extract_json：围栏优先 + 裸 JSON 花括号配平（字符串内 } 转义感知）。
- task_id 派生坑：驼峰切分 Plotter3Axis→plotter3_axis，数字后下划线需二次去除。
- **真实 LLM 产出**（glm-5.3，rounds=1 通过锚定+校验）：constraints C1~C7、
  acceptance 初版 9 条。**人工介入点 1 修正三处**（复核 LLM 产物的必要性实证）：
  ① AC4/AC5/AC7 的 forbidden_state 用 {x_v equals 40} 等值谓词判"不得运动"——
  只能命中单一魔数速度值，x_v=39 即漏判，不健全 → 删除，笔互锁改由验收脚本
  行为级负测试承担（位置不变+零速采样）；② 新增"急停受控减速时限"
  （quickstop↑→any_moving↓ ≤3s，event_delay）——原缺运动停止判据，且纯
  forbidden_state 会在减速窗口内误报（any_moving 在 QSA 减速期间合法为真）；
  ③ 三条按轴 region_containment 合并为单一 plot_head 终态 (50,50,10)。
  修正依据记入 workspace/plotter_spec_meta.json（provenance，不入 git——契约
  Schema additionalProperties:false，_meta 不能留在冻结 spec 里）。
- 冻结 examples/specs/plotter3axis.spec.json（26 io / C1~C7 / AC1~AC7）。

### ②a 种子 src/plc/plotter3axis.xml（CSP 栈派生 + 绘图序列器）

- 派生脚本（workspace/derive_plotter.py，一次性，workspace 不入 git）机械复用
  motion3axis 已验收 FB 本体：INTERP/DRIVE402/MC_POWER/MC_RESET/MC_STOP/
  MC_MOVEABSOLUTE/MC_MOVEJOG/MC_HOME 逐字拷贝（ElementTree 深拷贝），XML 为
  唯一源码。
- **INTERP_Z**（笔轴专用插补器）：行程检查 0..10、VMAX 20/ACCEL 60/POSWIN 1。
  **派生坑**：edge_ir→edge_irz 只改变量声明不改本体引用 → matiec 未声明变量；
  改名必须声明+引用同步（坑8 的另一面）。
- **PLC_PRG 应用层**三块新逻辑：
  1. **9 步 CASE 绘图序列器**：cmd_draw 上升沿启动；步目标进步时锁存 pl_t*，
     fire 线保持到对应 INTERP Done 再清；步进条件=三轴 Done 且触发线全清。
     步序：1 抬笔（当前位置）→2 定位(20,20)→3 落笔→4~7 四边→8 抬笔→9 回中心
     (50,50)→plot_done。快停/故障中止序列（pl_step:=0，释放后不自动续跑）。
  2. **笔互锁**：pen_up := z_fb>=8；手动 cmd_go 的 X/Y 腿与 jog、hm_x/hm_y 都
     AND pen_up（Z 自由——先抬笔永远是出路，不会死锁）；序列期间（pl_step<>0）
     禁止一切手动。move_done 汇总 AND (pl_step=0)——序列中熄灭，语义=纯手动
     定位完成。
  3. **插补目标路由复用**：pl_fire > 回零 > 手动三优先级；Z 回零位=抬笔安全位
     10（x/y 回 0）。
- **触发线清除顺序**（关键时序，想清楚才敢写）：entry 置 pl_fire → ix 调用
  （上升沿内 INTERP 置 Done:=FALSE）→ 之后才允许"Done 则清 fire"。若清除块
  放在 ix 调用之前，会拿上一轮的陈旧 Done 立刻清掉本轮回零触发——序列器一次
  都不会动。m3a 的 go/hm 路径天然安全（MC FB 在 ix 调用前刚置 exe），序列器
  直驱 fire 线后这个顺序变成必须显式论证的约束。
- prog_id=2；定位变量 26+prog_id，与冻结 spec io_list 逐字对齐。
- 双闸门：xml2st --check 通过；一致性 R1~R4 通过。

### ②b 场景描述生成器（src/agent/scene_gen.py）

- 职责依据 csk 分支 f39debd 收窄结论：SceneSpec JSON 生成归 gc，csk 自
  scene.spec.json 起负责仿真全链路。v0 **确定性生成**（同输入逐字节同输出，
  便于评审与回归）；LLM 布局创意后续只许改 pose/params，io_map 骨架（对账契约）
  不交给概率性组件。
- 产物：scene.spec.json（8 资产：ground/work_table/3×linear_axis/plot_head/pen/
  hmi_panel；物理参数、终止条件）+ io_map.json（mappings：plc_var/io_channel
  {plc_addr + modbus{area,address}}/bind{prim,quantity}/dir/type/range/unit/scale）。
  **io_channel 地址直接取 ⓪ 侧 AML 通道地址**（%QX2.0→线圈16 等）——看板登记过
  的"io_points.address 是 io_map 对账 ⓪ 侧来源"首次成为实现事实。
- 降级路径：无 device_model 时轴资产从 io_list 的 <axis>_fb 量程推断、地址按
  声明顺序确定性分配（登记看板：⓪ 模型缺位时地址腿降级）。
- 自检 V1~V4（资产封闭集/结构、io_map 结构、双向覆盖、bind.prim 落在场景资产
  内）；编排闸门消费，ValueError 即闸门失败。
- bind 路由规则：axis 后缀（fb→joint_pos / v→vel_cmd / sw→status_word /
  sp→panel <axis>_target）；BOOL input→panel <name>_btn、BOOL output→<name>_lamp。

### 编排器集成（闸门2b + /status 观测 + CLI ⓪→① 直通）

- solve() 增 scene_generator/device_model 参数（默认 None 向后兼容）：一致性
  两方过后生成 ②b 产物落盘 iter 目录 → consistency_check 传 io_map 复跑
  （R5 全腿激活，gate.json 记 r5:"active"）→ 失败短路回喂。best-effort 闸门
  序插入 scene=3。
- status_probe()：deploy 后 GET /status 记录 runtime_status 进 gate.json——
  **仅观测不裁定**（require_program 兜底位置是 lx 确认过的语义，编排器不重复
  校验程序身份）。
- CLI：--aml+--request 直通 ⓪→①（LLM/模板）；--aml 单独用=仅注入设备模型、
  spec 走冻结文件（可复现联调路径）；--no-scene 关闭 ②b。

### 全闭环联调（本机无 Docker/WSL，OpenPLC 不可启动）

- 种子路径全绿：spec+AML+seed → ①闸门 xml2st ✓ → ②闸门 R1~R4 ✓ → ②b scene
  闸门 R5 全腿 ✓（8 资产/26 映射）→ 闸门3 deploy **skipped**（deploy 服务
  不在线）→ 闸门4 acceptance **skipped**（OpenPLC/Modbus 不在线）→ final 冻结
  （runs/plotter3axis_demo，含 scene.spec.json+io_map.json）。skipped 语义即
  lx 确认过的半环约定：环境缺失不阻塞 final、真失败才回喂。
- scenario_plotter3axis.py（gc 代拟 @lx 复核，prog_id=2）：11 节覆盖 7 条 AC +
  笔互锁行为负测试（[3] 落笔态手动定位拒绝：位置不变断言）、越程拒绝（[6]
  x_sp=150）、绘图区 containment 采样、急停中止+复跑、AC2 自落笔态回参考点。
- serve.py PROG_NAMES 登记 {2: plotter3axis}（lx 文件 +1 行，看板知会）。
- **LLM 真实生成（②a 无种子）**：见下节联调记录。

### LLM 长生成的网络坑（client.py 流式降级）

- 万 token 级完整工程输出在**非流式**请求下，服务端生成期间连接零字节往返，
  稳定触发 SSLEOFError / Read timed out（120s/300s/600s 三档全灭，小/中请求
  正常——与鉴权/ payload 大小无关，是长生成+空闲连接的组合）。
- **根因修正**：流式降级仍报 ProxyError("Unable to connect to proxy")——
  shell 无 proxy 环境变量，但 requests 在 Windows 读**注册表系统代理**；
  代理对长生成连接（非流式空闲 / 流式 CONNECT）都会掐。`trust_env=False`
  直连 200 OK。最终解法（client.py 两处加固，保留）：
  ① Session(trust_env=False) 默认直连（确需代理的环境显式传参开启）；
  ② 非流式失败特征（Read timed out/SSLEOFError/UNEXPECTED_EOF/Connection
  reset）自动降级 stream=True 流式重试，SSE 分片聚合为非流式同构响应——
  对上层（pipeline/requirement）零侵入。

### 验证

- pytest 100→129（AML 绘图仪 4 + requirement 7 + scene_gen 9 + orchestrator 5
  + S2 收紧 2 改 1 增）；S2 draft.3 收紧后 cross-domain [-100,65535] 负测试红。
- CLI 冒烟：种子路径 final（双 skipped）；--aml/--request 直通路径见联调记录。

### ②a LLM 真实生成实验（glm-5.3，motion3axis 模式卡 → 绘图仪场景）

- **首轮生成 3 轮收敛**（498s）：round1 回复无 ```xml 围栏（extract 失败）→ round2
  XML 命名空间前缀未绑定（unbound prefix）→ round3 **双闸门全过**（xml2st 契约 +
  26 定位变量一致性）。修复回路按设计工作。
- **LLM 的架构自主性**（与 gc 种子不同但同等合法）：6 POU（省去
  MC_MOVEABSOLUTE/MC_HOME/MC_MOVEJOG，自建 edge+exe 状态机直驱 INTERP）；
  **把 INTERP 参数化**（VMAX/ACCEL/MinP/MaxP 作为 FB 输入按轴传入，Z 轴传
  20/60/0/10）——比 gc 种子的 INTERP_Z 子类方案更优雅，模式卡泛化能力的实证。
- **发现契约缺口**：产物缺 prog_id（契约② v1.1 程序身份）——静态闸门不查
  （xml2st/一致性均不强制 prog_id 存在，require_program 在线才暴露）。
  根因是 plcgen_skill.md 没写这条约定（prompt 缺口，非模型问题）。
  两步处置：① prompt 补"程序身份（契约② v1.1，必做）"条目（后续生成免疫）；
  ② 对已产出物做**定向最小修复**——上轮产物作 assistant 上下文 + 单点修正指令
  （50s 收敛，逐字最小 diff，双闸门复验绿）。经验：feedback-only 从头重生成
  会重新踩格式/命名空间坑且可能丢内容，**带完整上轮产物的定向修复**远稳于
  重新生成。
- **语义分歧记录（诚实边界）**：LLM 版对 C1/C5/C6 的读法比 gc 种子严
  （cmd_go 需先 homed、手动 Z 走 z_sp 电平而非 cmd_go 沿、cmd_draw 前置抬笔）。
  其中"draw 前置抬笔"与冻结 spec C6 一致——**scenario 脚本 [7] 已对齐**
  （抬笔态启动 + 原地抬笔后复跑）；homed 门控与手动 Z 语义分歧属规格未定面，
  在线闸门4 的 FAIL 回喂正是为收敛此类分歧设计的闭环机制——本机无 OpenPLC，
  留待在线环境（登记看板）。
- 产物链：runs/plotter3axis_llm（final 冻结，闸门3/4 skipped）；
  种子对照 runs/plotter3axis_demo。两份 final 的 scene 闸门均 r5=active/26 映射。
