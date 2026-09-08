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

## 2026-09-07（下）在线联调：VM 运行时搭建 + 闸门3/4 真实化 + ST 三层深挖

### 环境自建（无管理员、无 Docker Desktop）

- 本机无 Docker/WSL（非管理员装不了），但发现 **E:\ubuntu 与 D:\Ubuntu24.0 是 VMware
  虚拟机**且 24.04 那台正在运行——vmrun/DHCP 租约定位到 NAT IP 192.168.12.131，
  本机 SSH 密钥已被 VM 信任（yushanyue@，免密直连）。
- VM 内装 docker：sudo 密码经用户授权；`apt install docker.io`（Ubuntu 官方源 29.1.3，
  download.docker.com 被 DNS 污染用不了）；daemon.json 配 daocloud/1ms 镜像加速
  （Docker Hub 直连同样被污染；中途 EOF 断流重试即续传）。
- `docker run fdamador/openplc`（-p 8080/502）——lx 文档的标准部署形态原样落地。
- Windows 侧接入：serve.py 起在宿主机（OPENPLC_URL 指 VM）；**modbus_io.connect
  增加 MODBUS_HOST/MODBUS_PORT 环境变量**（与 serve.py 既有 env 约定对齐，缺省
  127.0.0.1 完全向后兼容——lx 文件最小增量，看板知会）；编排器 CLI 加
  --modbus-host/--modbus-port 注入验收子进程。

### ST 三层深挖（本批最有价值的知识，均已看板移交 lx）

**坑9（matiec 语法层）**：CASE 最后一个分支后的 `END_CASE` 必须带分号
（`END_CASE;`）——xml2st 静态层不解析 ST 语法拦不住，matiec 报
"';' missing"。

**坑10（优化器层，避坑7 的变体）**：INTERP_Z（与 INTERP 同模式的第二个 FB）
运行时无输出——按避坑7 处方"共用一个 FB"：**INTERP 参数化**（VMAX/ACCEL/POSWIN/
MAXPOS 提为 VAR_INPUT 按轴传参，Z 轴传 20/60/1/10），删除子类。但随后发现更深的
变体：**PLC_PRG 内的单扫描选通/边沿记忆变量（pl_go/pl_armed/pl_fire*）的赋值
会被优化器静默吞掉**——诊断口（%QW16-18 直出内部状态，lx 排障方法论第4步）
实锤：pl_armed 置位可见、下一扫描 ELSIF 分支的 pl_go:=TRUE 消失、ix.Done 永不
回落、9 步序列 0.3s 空转。手动路径（%QX 线圈信号驱动的 MC 层）一切正常——
**运行时只信任线圈级信号与 FB 内部的边沿，PLC_PRG 级选通不可用**。
解法（即 lx《运动控制代码生成方案》v2 的"连续跟踪模式"设计）：INTERP 增
`ELSIF fire AND NOT Busy AND 目标变化 THEN 起新轨迹`——序列器全程持有 exe
电平、步进只改目标值，零选通零边沿记忆。绘图序列 7.1s 真实执行。

**坑11（Modbus 观测层）**：OpenPLC Modbus 服务对不同寄存器的快照**非原子**
（v 寄存器可能来自上一扫描）——使能/失能切换窗口里 sw=0x33 配 v=120 的"违例"
是读偏斜伪影。验收脚本的跨寄存器组合判定需：切换瞬态排除（模式连续 ≥3 周期）
或改位置增量判定；裸 time.sleep 会冻结电机仿真反馈引发伺服修正脉冲，等待期间
必须持续 cycle()。

### 修复过程中的其他真 bug

- 编排器 deploy_gate 只认小写 "ok"（serve 返回 "OK"）——此前一直离线 skipped
  从未触发；大小写归一修复。
- 编排器 requests 走 Windows 系统代理（127.0.0.1 也被劫持）——本地服务直连
  Session(trust_env=False)（与 client.py 同款根因，两处都修了）。
- LLM 生成版 plotter 产物在闸门3 被 matiec 拒（多处 invalid declaration——静态
  双闸门查不出的语法层问题），错误已留存待作为反馈包回喂（闸门3 的设计场景）。

### 验收结论（在线）

- **scenario_plotter3axis：45/45 连续 4 次全过**（含幂等前奏、急停中止+经参考点
  恢复+复跑、笔互锁负测试、越程拒绝、绘图区 containment 采样）。
- **编排器全闭环**：⓪(AML)→①(spec)→②a(种子)→②b(scene+io_map)→闸门1/2(xml2st+
  一致性)→闸门2b(R5 全腿)→闸门3(POST /deploy 真编译+GET /status 观测
  prog_id=2)→闸门4(scenario 45/45)→**final**。
- **run_regression L1+L2+L3 双场景全 PASS**（motion3axis 在 VM 时延下有个别
  边际时序项抖动——lx 本机 35/35，已看板登记供 lx 评估阈值）。
- pytest 129 全绿。
- 已知细节（待 lx 复核时裁决）：连续跟踪分支不做 MAXPOS 检查（当前用法安全：
  序列目标在量程内、手动路径 Busy/exe 时序天然屏蔽，devlog 留档）。


### 对话式 CLI（待办 #8 交互形态落地，src/agent/chat.py）

- 交互流：AML 路径（回车用绘图仪示例）→ 多行自然语言需求（空行结束）→ ① LLM
  规格生成 → **render_spec 回显**（io 统计/constraints/acceptance 逐条含谓词明细
  ——forbidden_state 的"当 X=a 禁止 Y=b"直接可见，等值谓词弱项靠这一步人工拦）
  → 回车确认 / 自然语言修正（循环）→ 生成方式选择 → 闸门环境自动探测 → solve
  自主迭代（事件回调打印）→ 交付物路径。
- **refine() 修正回合**：上轮 spec 作 assistant 上下文 + 用户修正文本的**定向
  最小修改**（prog_id 补丁验证过的模式：远稳于 feedback-only 从头重生成）；
  验证+锚定照常，失败回灌。粗需求实测 AC2~AC6 出现反向等值谓词
  （"pen_down 时禁止 x_v=0"），回显阶段可见——LLM 主动反问仍未接，回显是
  当前防线。
- probe_gate_env：serve /health + Modbus connect 探测（Session trust_env=False，
  Windows 系统代理教训第三次应用）；可达即启用闸门3/4，离线自动降级——用户
  无需关心环境状态。
- 脚本化：--request --confirm --seed 使全流程可管道演示（实测粗需求 iter_1
  final，交付 runs/plotter_cell/final/{plcopen.xml, scene.spec.json, io_map.json}）。


### 标准形态推进：知识库 / 记忆 / 归因 / 自动策展 / 上下文预算

- **知识库**（src/agent/knowledge/pitfalls.json，committed 契约性质）：lx 避坑
  1~8 + 本项目联调新增 9~14 共 14 条，结构 {签名→诊断→修法}；归因按错误文本
  签名计分匹配（短语命中权重高）。
- **记忆**（src/agent/memory.py）：程序性（坑库，committed）+ 情景
  （workspace/memory/fixes.json 修复对，本机）；检索确定性签名匹配，无向量零依赖。
- **归因引擎**（src/agent/attribution.py）：**KB 优先（命中不打 LLM）、LLM 兜底
  （未命中才小上下文诊断，输出标 advisory）**；红线保持——归因只进反馈包
  （_pack_feedback 增强版），gate.json 留档，不改变闸门裁定。实测：LLM 兜底对
  空证据正确回答"证据不足"。
- **编排器接线**：7 个失败点统一收敛到 _fail()（归因→增强反馈→留档）；
  final+在线验收 ok → _consolidate()（修复对入情景记忆 + 种子自动策展，
  skipped 不策展——未在线验证不构成"已验收模式"）；best_effort 记 abandoned。
- **模式库自动策展**（patternlib）：knowledge/patterns.json 注册表与静态
  CATALOG 合并视图；register_pattern 登记前过闸门防坏种子、防 key/文件重复；
  pattern_cards 选卡读合并视图。**实测闭环：final 后绘图类需求自动选到
  plotter3axis 卡（零代码改动）**。上下文预算：render_cards 超限丢卡并注明。
- 联调暴露并修复 3 个集成 bug：① serve 编译失败返回 HTTP 500 时 deploy_gate
  只留状态码文本丢了 errors——改透传 body；② _fail 未防 errors=None；③
  register_pattern 的相对路径正斜杠被 split("\\") 切不动拼出双重路径——
  归一化 rsplit("/")。
- 测试 133→147（坑库匹配 4 / 情景记忆 2 / 归因 4 / 策展与预算 2 / 编排器接线 2）；
  实效演示：坏 END_CASE → 闸门3 matiec 真失败 → gate.json 归因命中 P09 给出修法。


## 2026-09-07（晚）画圆场景：LLM 全流程实跑 + R6 契约盲区 + 收敛记录

### LLM 闭环实跑（plotter_circle_gen，纯 LLM 生成无种子，6 轮预算）

- iter1：matiec 语法（漏声明/空分支）→ 归因命中 P16/P17（精确）→ 反馈修复 ✓；
- iter2：**真编译通过**（LLM 自学：WORD 桥接 x_sww/WORD_TO_INT、prog_id=3、
  连续跟踪注释、COS/SIN 折线画圆架构）→ 败于验收身份：prog_id=3 vs
  plotter3axis 脚本要求 2——**暴露真契约盲区，见 R6**；
- iter3：回归（重生成丢修复——从头重生成的不稳定性再次实证，定向修复远优）；
- 中止后台循环，转工程收敛路径（下述）。

### R6：⓪ 侧地址腿（本轮最重要产出）

- 病灶：iter2 代码名字/类型全对但**地址自编一套**（cmd_draw@%QX0.7、
  sp@%QW3-5…）——一致性检查从不对账地址值，编译全绿、Modbus 层才露馅
  （探针写线圈 16/读 17 全是空位）。
- 修复：consistency_check 增 **R6**（device_model 可选腿：AML 通道地址 ≡ XML
  定位变量地址，双向对账+占位检测）；编排器闸门 2/2b 传入 device_model；
  2 例测试（含负例）。画圆 XML 抓 10 处分歧、方形 XML 通过。
- LLM 定向修复（地址表+触发前置，52s 收敛）后：编译✓、互锁/使能/落笔全活、
  XY 到达起点——但序列在步 2→3 停滞（其步进块与 ix 调用顺序竞态，归因建议
  P10 类；根因未完全定位，记录为观察）。

### 工程收敛：移植到已验证轨道

- 决策：不再法证式调试 LLM 变体，把画圆步表（7 步：抬笔→(50,25)→落笔→
  ck 0..23 折线 COS/SIN→抬笔→回圆心→完成）移植到方形 45/45 验证过的
  序列器轨道（workspace/derive_circle.py 派生，prog_id=3）。
- 画圆场景脚本 scenario_plotter_circle.py（gc 代拟 @lx 复核）：一次通过
  21/22，随后三类场景层竞态逐个消解：①包围盒 ±3 对折线转角+滞后过苛→
  [20,80]+落座驻留门槛；②重跑幂等（上轮残留）→三段前奏（原位抬笔解锁→
  XY 归零→落笔）；③**前奏手动定位被插补器残留 Busy 吃掉**（脉冲撞上旧弦
  完成→exe 即清→新目标未被接受→插补器/电机位置失同步，[4] 内斜线追赶
  污染轨迹统计，相位标记诊断口实锤 pos=(15.6,15.6) v=(50,50)）→
  manual_goto 加到位校验+重试。**最终 5/5 连续全过**。
- 编排器定版闭环 final（五闸门真实：R5 active/deploy ok/acceptance ok）；
  **plotter_circle 自动策展入模式库**（绘图类需求次卡）。

### 首过率数据（KPI 基线，如实记录）

- 本场景 LLM 独立走完"生成→真编译"用了 **2 个迭代**（iter1 语法修复、
  iter2 编译通过），但**未独立走完在线验收**（地址盲区→R6 拦截后定向修复
  编译版行为停滞；最终程序由已验证轨道派生）。定向修复（上下文最小修改）
  每次均在 1 轮内收敛 vs 从头重生成的反复丢修复——两条路径的效率差异
  已三次实证，生成器架构改进方向：迭代内改"定向修复"为默认。
- serve.py PROG_NAMES +{3: plotter_circle}（/status 显示名，需重启 serve 生效）。


## 2026-09-07（夜）LLM 现场生成为标准：8 轮泛化验证战役与能力基线

### 用户口径锁定

"具体任务不依赖模式库；新任务由 AML+用户输入自主生成 PLC XML 与 JSON"——
`--no-curated-patterns` 泛化口径落地（patternlib include_curated 开关贯通
pipeline/orchestrator/chat）：**生成只允许静态 CATALOG（motion3axis 通用运动
原语，等价标准库），自动策展场景卡排除**（含答案性质）。对话 CLI 与编排器
均支持。

### 逐轮验证与系统性改进（每轮暴露一层、修一层）

| 轮次 | 暴露的层 | 系统性改进（非场景补丁） |
|---|---|---|
| llm2 | 编译错误类修复✓ 但序列器行为停 | repair 定向修复为默认策略（PLCGenerator.repair+编排器路由+熔断） |
| llm3 | 编译提速（模板缺失） | **通用序列器模板**（连续跟踪模式，lx 生成方案 v2 知识）入 skill |
| llm4 | 地址回喂低效（LLM 修地址打转） | **地址表前置注入 prompt**（⓪ AML 通道→%Q 契约） |
| llm5 | 反馈只有 FAIL 文本，LLM 推不出运行时病灶 | **验收 trace 时间线**（pos/v/pen/done 每 2s）+ CiA402 空闲态约定 |
| llm6 | trace 显示停在 (51,26,9) 但不知内部状态 | **运行时透视**：验收失败自动注入诊断口（%QW16+跳 prog_id@20）→部署→采集 pl_step/exe/Busy 时间线→回喂（人肉诊断法自动化） |
| llm7 | 地址又在 LLM 手里打转 | **地址自动纠偏**（fix_addresses 机械改写 XML——契约执行不属 LLM 工作；实测 10-16 处一键纠绿） |
| llm8 | 修复回退已 PASS 项（iter7 差 1 项全过！） | repair 增反回退约束（对照 PASS/FAIL 明细禁改已过逻辑） |
| llm9 | 复核确认（10 轮预算） | 收敛有波动，证据回路每轮精确暴露病灶 |

### 能力基线（如实）

- 静态契约（io 锚定+R1~R6）：**1 轮** ✓（地址机械纠偏后必绿）
- matiec 真编译：1~3 轮 ✓ 稳定；prog_id 身份 1~2 轮 ✓
- IO 行为（使能时序/笔互锁负测试/越程拒绝）：稳定通过 ✓
- **序列器全行为（画圆完成+急停复跑）：≤10 轮内未稳定收敛**——llm8 iter7
  距全过仅差 1 项（急停复跑+失能）；病灶证据精确（z 目标误用圆公式→v_z=20
  饱和 / X/Y 触发线未置位 / 步进停滞于起点），修复推进但非线性。
- KPI 对照：项目目标"≤6 轮收敛率≥70%"——当前"编译级"达标，"行为级"
  基线约 0/8 全收敛、最佳差 1 项。差距本质：文字证据→ST 时序语义的翻译
  是当前模型的弱项，非框架缺失。

### 下一步候选（按杠杆排序）

① 验收失败输出结构化（per-check JSON+期望/实际对比表，替代文本行）；
② 场景仿真层前置（本地轻量 ST 求值器，生成后自检行为再上线）；
③ repair 附加上轮 XML diff 高亮变更区（强化最小修改约束）。

## 2026-09-08（下午）驱动状态机初值缺陷修复（方案 v1.1 实施记录）

依据《gc-驱动状态机初值缺陷修复开发方案》v1.1（GC-PLAN-2026-09-08-01）实施 W1/W2/W3/W5；
W4（F1/R7 静态校验规则）按 §9.1 前置条件（lx RFC 评审）**未实施、不设旁路**。

### V0 根因验证（W1，结论已回填方案附录 A）

iter_005 副本补 `<initialValue><simpleValue value="1"/></initialValue>` 一处 →
部署 → 画圆在线验收：步 1 冻结消失、序列器前进至步 3、驱动器使能链路复活
（"释放后重新使能" PASS）。根因链（§2.2）实证闭环。运行时已恢复 plotter3axis。

### 实施明细

- **F2a 反馈全量落盘**（orchestrator.py）：`acceptance_gate` 去掉 `[-40:]` 截断，
  返回全量行；`_pack_feedback` 统一截尾（常量 `FEEDBACK_TAIL_LINES=40`）并在
  反馈文本注明"（输出已截断至尾部 40 行，全量见 gate.json）"。措辞与方案 §5.3
  的"验收输出"略有偏差（截断点在通用 `_pack_feedback`，非验收专用），语义等价。
- **F2b 坑库 P18**：按方案 §5.3 原文入库；实测对 iter_005 真实验收证据以
  `all_oe=FALSE` 子串命中（3 分，与 P10"Done"同分但均进 top3，P18 列表序在后
  不影响命中）。归因引擎零代码改动。
- **F2c skill 硬规则**："状态机变量必须显式初值"条目入 plcgen_skill.md 的
  matiec 硬规则节（CiA402 空闲态约定同族），含 402 从 1=SOD 起步与 ELSE 兜底。
- **F3 零推进熔断**（orchestrator.py）：`_fail_signature` 归一化哈希 + solve 循环
  `last_fail_sig`/`force_fresh`。**归一化规则**（本节即方案 §5.4 第 4 条的登记）：
  ① `t=数字s`（trace/diag 采样时刻）→ `t=?s`；② `时:分:秒`→TS、`日期`→DATE；
  ③ `文件:行(列)` 冒号行号→`:N`（lookbehind 单词字符，不误伤"R2: 变量"类）；
  ④ `line 数字`→`line N`；⑤ `*.st` 文件路径→`F.st`；⑥ `iter_数字`→`iter_N`。
  **状态数值（pl_step=1→2）刻意保留**——那是推进证据不是噪声；急停时长
  （3.04s→2.9s）同理保留。签名 = (gate, md5 前 16 位)；连续两轮同签名 → 下轮
  强制 fresh + narrate `switch_fresh` 播报。触发不限于 repair 轮（略宽于方案
  §5.4 的 repair 语境）：fresh 连续同质失败同样提前熔断，误判后果仍为进入既有
  fresh 兜底路径，误伤面与方案 §5.4 分析一致。
- **测试环境加固（计划外）**：serve.py 在线时 acceptance 失败类单测会经
  `_runtime_probe` 真部署 OpenPLC（每轮 20~40s，且占用共享运行时）——相关单测
  统一 `deploy_url="http://127.0.0.1:1/deploy"` 隔离，被测语义不变。

### 测试

新增 7 例（test_orchestrator.py 5 + test_agent_memory.py 2，方案 TC-UNIT-3/4/5/6
落地，TC-INT-3 转为 iter_005 gate.json 重放单测）：pytest **152 → 159 全绿**
（39s）。TC-UNIT-1/2、TC-INT-1/2 随 W4 待 RFC 后实施。

## 2026-09-08（晚）轨迹参数化路线落地（流程跑通为最高优先级）

负责人方向裁决：生成工艺目标太"繁琐"是走不通的根因——实际 PLC 生成主要靠
轨迹规划。新路线：用户简单输入 → ① 理解并**交互确认必要参数** → 确定性
轨迹参数 → LLM 按权威步表**自主生成**（IEC 61131-10 契约，不套用策展场景
卡：圆/方同构卡=把答案放进 few-shot）→ XML + 场景 JSON 双交付。

### 架构（新增 src/agent/trajectory.py，其余接线）

- **trajectory.py**（确定性核心，零 LLM）：`plan_square/plan_circle` →
  序列器步表（动作+坐标+笔态）；站标准几何与验收脚本逐字对齐（方 20..80
  中心 (50,50)；圆心 (50,50) r25 起点 (50,25) 24 段折线+COS/SIN 公式）；
  `plan_with_confirm` 缺参确认（confirm 回调交互 / None 自动采纳默认值）；
  行程域校验（越界即拒）。
- **requirement.py**：`extract_shape`——LLM 唯一职责=识别形状+抽用户显式
  数字（null 剔除、垃圾输出兜底 unknown），几何数字不进 LLM。
- **pipeline.py**：`build_messages` 注入 `summarize_for_prompt(traj)` 作
  权威约束（坐标不得改动/不得增删路点）；generate/repair 均带 trajectory。
- **orchestrator.py**：solve 增 trajectory（落盘 run_dir/trajectory.json
  任务级留档）；CLI `--request` 路径接完整流程 + `--confirm-params`（终端
  逐项确认，缺省自动采纳）+ `--task-id/--prog-id`（同站多役区分+程序身份）。
- **chat.py**：同一流程对话形态接线。

### 验收体验修复（负责人要求）

1. **实时流式**：`_run_acceptance` 子进程注入 `PYTHONUNBUFFERED=1`——
   根因是 scenario 脚本 print 在管道模式下全缓冲，结果最后一次性涌出。
2. **段级 fail-fast**：逐行解析段标题 `[N]` 与 FAIL 行；失败段检查完毕
   （下一段标题出现）即 kill 子进程，合成说明行（"段 [N] 失败已暂停；
   已通过段：…先修复本段"）进直播+gate.json+LLM 反馈——repair 拿到
   "已过段禁回退 + 本段明细"的聚焦证据；重跑从头（脚本幂等，lx 已验证）。
3. **字数播报移除**：删 `_chunk_reporter`（"已输出约 N 字"逐步打印），
   保留阶段级播报与流式通道（保活功能不变）。

### 实测（真 LLM + 真部署 + 在线验收）

- **画方（plotter_sq_traj）：第 5 轮 final，45 项全 PASS**。轮次轨迹：
  iter1 静态闸门一次过（地址纠偏 16 处）→ matiec 新坑"FB 内部变量当调用
  形参"（`go_x(interp_exe := ...)` 类，Invalid assignment syntax）→ iter2
  修复部署过 → 验收段 [1]~[4] 全过（**上一战役全挂的笔互锁负测试 [3]
  三项全过**）、段 [5] move_done+bit10 两项 FAIL → fail-fast 暂停 →
  iter3/4/5 定向修复 → 45/45：绘图 11.0s、终态 (50,50,10)、落笔联动 53
  采样、越区 0、急停减速 0.21s、复跑完成、失能零速。
- **画圆（plotter_ci_traj4）：第 5 轮 final，全项 PASS**。四役迭代史
  （每役暴露一类语义缺陷 → 签名化入库 → 下役生效，闭环知识回路的实证）：
  traj1（8 轮 best，差 1 项急停复跑）→ 暴露 **P20**（qs_latch 需 cmd_reset
  才清，剧本释放后直接复跑 → 死锁）；traj2（7 轮卡段 [1]）→ 暴露 **P21**
  （pen_down 被写成含 run/all_oe 的复合信号，未使能恒 FALSE）；traj3
  （8 轮 best：定位/落笔全对但落笔后 XY 死）→ 对照黄金轨道闭合诊断暴露
  **P22**（INTERP 骨架被自由发挥：自加 pos_fb 同步/改 fire 沿门槛 →
  三轴联合步进永假，序列卡死）+ skill"基础 FB 骨架冻结"硬规则；traj4
  第 3 轮起 XY 联动复活（113 采样完整画圈）、第 4 轮差终态回圆心 1 项、
  **第 5 轮全过**：plot_done 19.3s、终态 (51,49,9)、联动 110、越界 0、
  急停减速 0.06s、**复跑 18.0s**（P20 修法生效）、失能零速。
  （traj3 期间运行时环境故障一次：VM 挂起+serve 掉线 → vmrun 恢复 +
  docker restart + serve 重启；假 final 产物已清除。）
- **match_pitfalls top 3→5**：P22/P20 症状与 P18 相近时挤占真根因，
  放宽容量（多参考无害，归因不裁定红线不变）。

### 测试

pytest 159 → **176 全绿**（trajectory 9 例 + extract_shape 4 例 + 段级
fail-fast/全过路径 2 例 + trajectory 落盘透传 1 例 + 零推进熔断回归 1 例）。
