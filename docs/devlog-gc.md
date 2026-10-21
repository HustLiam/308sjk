# gc 本地开发日志（devlog-gc）

## 2026-10-21 master 发布完成

master 已按用户授权发布为 b50ad03277594cc472394fb4248dc71b4a0df0e1；300个文件指纹与已验证源码清单一致，master树不含个人devlog。此前642通过、6Qt跳过，4份PLC XML静态校验通过。本次合入未修改已验证实现，当前目录保留gc。用户另行明确允许推送gc，个人日志在gc恢复后随该分支同步。

## 2026-10-21 用户授权提交 master

按用户明确要求提交当前工作台及完整依赖、测试、运行证据至master。远端fetch确认master仍为01c489c，与gc无新的上游代码差异；642通过、6Qt跳过的已验证实现保持不变。README修正仅SVG入口、生成不部署和SVG验收未接入的旧说明。提交前保留本日志副本，个人分支记录完整实现；合入提交移除devlog，master发布后在gc恢复。本机Git代理127.0.0.1:10808未运行，远端操作仅在当前命令临时清空http/https代理，未改全局配置。

## 2026-10-21 语言绘图与SVG双来源

新增drawing_input适配原clarification/trajectory的正方形与圆规划器，不生成中间SVG。新会话默认语言描述；无SVG及选区时开放工艺输入，“画一个正方形”可直接产生预览，或在左侧规划后使用默认工艺。支持有标签的中心/边长/半径/直径/圆段数及“边长为40”等常见表达；缺参使用界面明示默认值，按AML检查设备坐标、单位和行程；不支持或无效输入不改变原轨迹。

会话保存drawing_mode、简洁drawing_text与独立language_geometry；切换来源保留原SVG资产、选区和参数，但让规格失效，避免旧SVG要求混入语言任务。当前来源参与冻结输入比较；语言生成归档trajectory及来源，不复制保留的SVG为input.svg。清空上下文保留几何、清掉工艺；资产替换重验当前来源。旧已导入会话仍为SVG来源，旧空会话可直接语言输入。前端按当前来源独立判断按钮状态；右侧直接描述图形、左侧预览、默认工艺统一消费同一轨迹。摘要精简并去掉重复权威步表，嵌入预览收起重复标题；完整生成输入和独立预览文件保留。

验证：根tests/642通过、6Qt跳过；覆盖无SVG直接对话整理规格、两类几何/增量修改、无效输入原子性、默认工艺、来源往返、旧会话、资产更换、生成快照及HTTP令牌/版本/忙碌保护。真实Chrome检查无SVG正方形→默认工艺→生成按钮可用、SVG导入/清空选区→语言恢复→圆形、非法尺寸保护、桌面与390px无横向溢出，JS异常0。Windows禁用8766，浏览器验证使用系统分配的隔离端口。确认用户服务空闲后重启8765，保留原产物目录和模型配置；本次真实模型调用/PLC部署0。截图和摘要E:/chagpt/308sjk-language-drawing；临时服务/Chrome和测试文件收尾清理，未push。

## 2026-10-21 自动修复反馈、重复候选与通信恢复

将尚未接线的repair_feedback接入PLCGenerator.generate/repair内部静态回路，共用归因引擎MemoryStore，错误按族限量回灌且保留全部错误、故障库命中与具体建议到逐调用feedback.json。相同源码与相同静态错误再次出现时抛GenerationStalled，solve保存no_progress，不再调用归因/蒸馏模型。静态通过后的候选也按XML结构/代码指纹去重，在场景构建、部署和物理运行前停止；忽略排版、注释和时间戳，保留字面量及混合XML文本。旧测试中原样返回XML或仅改注释的替身改为确实修改语句，继续检查原有修复路由、退化回退和计划冻结。

历史resolved经验预注入增加diff_hunks；实际采用的repair_base_used参与成功经验差异计算。VerifiedPLCGenerator切回较好候选时合并其失败证据与本轮完整归因，明确二者来源；failed_candidate的时序建议不再覆盖原归因，静态候选重试也恢复逐调用错误和知识修法。

最近b3851eae...中断原文为pymodbus No response received，旧字符串分类漏掉了它。现将其识别为env_down，不交由模型补偿通信故障；通信中断最多对同稿重验一次并归档首轮证据，源码/身份改变或停止未确认不自动重试。再次点击生成且冻结输入相同时，环境中断候选直接过全部闸门，不先调用模型；行为失败候选仍走定向修复，原样返回会立即停止。SVG的明确画方/画圆指令在规格变动或需求调用前拒绝，防止保留SVG与新工艺相互矛盾。

验证：根tests/ 629通过、6Qt跳过；包括静态知识检索/重复熔断、部署前去重、通信恢复/持续失败、旧No response记录续验、回退归因和形状冲突。完整XML旧system与0.3采样回归通过。空闲确认后重启8765，首页与模型配置正常，实际HTTP冲突返回400并验证原会话未变；未调用真实模型或重跑实际PLC运动。服务日志和验证摘要保存在E:/chagpt/308sjk-repair-loop，测试中间目录收尾清理；未push。

## 2026-10-20 相机方向与部署日志

按用户明确确认保持左旋右移并反转两类鼠标增量；垂直平移改用真正的屏幕上方向基向量，避免45度视角下退化为深度移动。部署记录仅在前端映射为中文流程成功/失败，不展示临时文件、内部路径和英文步骤；底层 steps/errors 保留用于诊断。浏览器验证场景页和Runtime页真实左/右键拖动、七步文案及失败分支；无异常，根412通过。静态脚本刷新即可生效，无需重启服务。

## 2026-10-20 绘图精度与显示性能

用独立高精度 AML 保留原地址与轴行程，将 cmd 编码精度提高100倍；SVG 路点0.01、展开容差0.02，输出取 REAL 插补状态，XY 同步、启动先抬笔、空闲保持。P06 阻止 REAL 改型后隐式比较/INT IN_OUT 混用，保留旧 MC 总线 INT 并另设 REAL 目标。实际 PLC 样本做40ms统一时间插值，墙钟补步与阻尼速度前馈，墨迹按物理笔尖采样。新交付e1a2cad真编译/全程运动通过，95%误差11.001→0.247mm、RMS0.139mm。

以原生 WebGL2 渲染后台导出的真实 MJCF 几何，增量位置/笔迹，DPR自适应最高2560×1440；显示约60 FPS，拖动滚轮本地响应，暂停与重启保留快照。去除截图指定统计/标记/重置/状态字样并分隔按钮组。根412与浏览器实操通过（2025×990、59.6 FPS、间隔43px、无页面异常）。旧版本证据与新结果保留在工作台 work/precision-comparison；无 push。

## 2026-10-20 网页模型运动闭环

接入 lx PlcLink/GantryBridge/SafeCoilIO，单线程拥有 PLC Modbus 与 MuJoCo 数据面；启动前校验冻结 XML、部署回执、prog_id、三轴位置通道，停止指令读回确认，页面显示实时位置和物理笔迹。标准 SVG 步序提前读取 Done 导致首点报完成，使用独立新交付修复连续跟踪与完成判定，旧 final 不改。真部署 matiec 通过，完整轨迹 18.83s / 393 墨点 / 最终中心抬笔。测试根409、runtime27；Chrome 按钮及图像通过。服务重启后按当前部署程序与哈希重新判定可启动性。

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

## 2026-09-09 自动化学习机制（经验库 v2：人工提炼 → 自动沉淀）

负责人口径：不使用模式卡（生成侧零卡注入），只靠积累经验——LLM 借鉴类似
错误骨架修复方法，借鉴程度与相似度相关，避免直接套用与无效借鉴；agent 必须
遵守 skill 硬规则不得越界。判据：画圆任务连续 3 个 ≤2 轮 final。

### 四机制落点（A/B/C/D 方案落地）

- **C 指纹统一**：`_SIG_VOLATILE` 归一化迁入 `memory.normalize_errors`
  ——零推进熔断与经验检索共用一套失败指纹（同质失败=仅采样时刻不同）。
- **A 战役内翻转自动记录**（确定性）：solve 循环保存 entry_base（=上轮
  产物）；final 且经 repair 修复 → `_diff_skeleton(base, final)` 提取 ST
  本体骨架级 diff（difflib 统一 diff 增删行，非全文）→ `record_lesson`
  （outcome=resolved，带 diff_hunks）。
- **D 相似度分级借鉴**：`match_lessons`（归一化行集合 Jaccard；同闸门
  优先、跨闸门 ×0.5 降权）：sim≥0.55 → detail 级（修法+变更骨架）；
  0.30~0.55 → 方向级（仅修法）；<0.30 不返回。`_fail` 统一注入反馈，尾注
  skill 硬规则约束（防越界借鉴）；gate.json 留档 lessons（可观测）。
- **B 战役级 LLM 提炼**：final/best_effort 后扫 history 同质失败族
  （连续 ≥2 轮同签名）→ `AttributionEngine.distill_lesson`（复用 _diagnose
  形态：失败证据+结局+骨架 → {diagnosis, fix} 泛化经验）→ 经验库
  kind=distilled；无 LLM 自动跳过（学习退化不阻塞闭环）。
- **存储**：`workspace/experience/lessons.json`（本机持久，跨重启）；
  坑库（契约/RFC）不动——全自动的只有经验库，治理红线保持。

### 生成侧配套

- `PLCGenerator.no_cards=True`：system prompt 零模式卡注入（轨迹路线
  默认启用）；skill 文档内联 INTERP 权威骨架 42 行（P22 经验物化——
  去卡后的骨架权威来源）+ 既有硬规则（FB 冻结/序列器模板/初值/急停/
  笔态/402 约定）构成全部约束面。
- 学习 runner `tools/learn_circle.py`：独立子进程连跑画圆（每任务=重启
  =经验跨重启消费验证），自动判定连续 3 任务 ≤2 轮 final。

### 测试

pytest 176 → **182 全绿**（经验库记录/同质命中/分级门控/abandoned 不检索/
指纹同源/翻转自动记录/diff 骨架/零卡注入；移除依赖已清理 runs 的 iter_005
重放用例——P18 覆盖由内联文本用例等价承担）。

### 学习战役（plotter_ci_learn 系列，判据：连续 3 任务 ≤2 轮 final）

（结果回填：workspace/experience/learning_curve.json）

### 学习战役结果（判据达成，2026-09-09）

三段推进（tools/learn_circle.py，每任务独立进程=重启汲取经验的验证形态）：

- **阶段一（learn1~10，10 轮预算）**：learn1/2 未收敛（经验库零 resolved，
  借鉴回路空转的死结）；从 git 历史回灌 traj4/sq_traj 三条真实翻转对后
  learn3 起 6/8 收敛（轮次 4~10）。瓶颈数据：10 役 9 役首轮烧在
  generate/deploy——经验只作用于"失败后修复"，首轮 fresh 无经验加持。
- **阶段二（杠杆补强）**：skill 内联 DRIVE402/MC 层权威骨架（INTERP 已有
  ——三轮骨架物化后 learn6 降到 4 轮）；开发**经验预注入**（同 shape 任务
  的 resolved 教训注入首轮 system prompt，recent_lessons 按 shape 过滤——
  借鉴前置到生成时）。插曲：_distill_family 漏传 shape 的 NameError 损失
  learn11（实际已 final，经验手工重建）；监控命令取消的进程组风暴致
  learn14~20 空跑（环境瞬时故障，恢复后重启）。
- **阶段三（learn21~23，预注入+12 条 resolved）**：**2 轮 / 2 轮 / 1 轮
  连续三个 fast——判据达成**（learn23 首轮直接全过）。最终经验库 21 条
  （12 resolved）。runner 判定输出见 workspace/experience/learning_curve.json。

**关键机制缺陷修复（过程中实证）**：pytest 默认归因引擎曾向真实经验库写
入假经验（21 条污染）——MemoryStore 默认路径改运行时解析 + tests/conftest.py
autouse 隔离（教训入库：学习系统的存储必须有测试隔离层）。

## 2026-09-09（下）②b 场景生成对齐 csk 契约包 v1.1（contract/）

### 背景与输入

master 新增 `contract/`（csk→gc 契约包 v1.1，2026-09-08；2026-09-09 增补 example1），
声明为 gc 生成 scene.spec.json 的**唯一权威**：components.v1.1.json（15 类型机器可读
契约）/ 组件契约表.md（同源人类可读）/ scene.spec.example.json（绘图工位范本，io_map
为 csk 代拟）/ example1.json + example1_iomap.json（龙门 MJCF 反向导出 + 地址分配产物）。
已 `git checkout origin/master -- contract schemas/io_map.schema.json` 引入 gc 分支。

### 关键裁决（与 v0 的差异全部以契约为准）

1. **io_map 内嵌 scene.spec.json**（Schema required 字段），独立 io_map.json 工件废止。
   v0 的 mappings/io_channel{plc_addr,modbus}/scale{offset,factor} 字段全部移除——
   **地址分配移交 csk build-mjcf**（iomap.assign_modbus 按 io_map 声明顺序确定性产出
   %QX 逐位/%QW float32 大端 2 寄存器/%IW 传感区块，即 example1_iomap.json 形态）。
   ⓪ 侧 AML 地址对账职责不受影响（R6 仍查 XML≡AML），只是 gc 不再在 scene 产物里
   代 csk 分配仿真侧地址。
2. **bind={asset, quantity}**（v0 为 {prim="/World/x_axis", quantity=joint_pos}）。
   quantity 词汇表按契约注册表：linear_axis 只有 cmd(in,float)/pos(out,float)——
   v0 的 joint_pos/vel_cmd/status_word 与 panel 的 *_btn/*_lamp/*_target 全部废止。
   路由规则改为 `<axis>_fb`→`<axis>_axis`.pos、`<axis>_cmd`→cmd（生成与自检同一
   函数 route_physical_channels，防两处规则漂移）。
3. **按钮/灯不进 io_map**（hmi_panel 无注册 quantity）：落 panel.params.buttons/lamps，
   csk 运行时按名接线（范本实证）。NC 设定值(sp)/状态字(sw)/速度指令(v)为非物理通道
   不进 io_map——速度指令轴要仿真支持须 RFC 给 linear_axis 加 vel quantity。
4. **range 一律 SI 米制** = stroke × scale_m_per_unit（x: [0,100]%×0.01→[0,1]m；
   z: [0,10]mm×0.001→[0,0.01]m；round(,9) 规避浮点尾差保证逐字节确定性）。
5. **注册表运行时加载**：load_contract() 扫 contract/components.v*.json 取最高版本，
   spec_version 随契约版本（当前 1.1）。契约换版只换文件不改代码；参数校验规则
   （enum/float 界/vec2/vec3/str/str_list/必填/未知参数）全部从 JSON 约束元数据泛化
   实现，与 csk validate._check_params 同源同语义——**vmax:null 类缺陷**（v0 无模型
   时落 null，契约闸门拒数字类型）由 _clean_params 剔除缺省解决。

### R5 语义变更（contract 引发的检查器适配）

覆盖检查从双向改为**单向 io_map ⊆ io_list**：契约 v1.1 的 io_map 只含可绑物理通道，
按钮/灯/NC/诊断通道合法缺位。反向安全性（可绑通道不得静默丢失）转移到 ②b 自检的
**路由覆盖规则**（V3：route_physical_channels 判定可绑的通道必须在 io_map 且绑定一致）。
类型对账 bool↔BOOL、float↔INT（ioEntry enum {bool,float}；契约③ io_map.json 的
{bool,analog,word} 一并兼容）。R5 接受 list（内嵌形态）与 {entries:[...]}（契约③
build 产物）两种形态，旧 {mappings} 形态废止。

### 交叉验证（pxr 未装的替代法）

本机无 USD Core（pxr），csk scenegen.components 顶层 import pxr。用 types.ModuleType
桩掉 pxr 子模块后运行其 validate.py 本体（registry/规则代码原样执行）。结果：
- contract/example1.json（gantry_xyz，9 USD 类型内）**通过**——ioEntry 形态获真闸门背书；
- contract/scene.spec.example.json（绘图范本，6 MJCF 类型）**被拒**："未知组件类型
  linear_axis/...，封闭枚举: [9 个 USD 类型]"——**csk master 的 REGISTRY 落后于其
  契约 JSON**（6 个 MJCF 类型已声明未合入）；gc 生成物受阻项与其自家范本完全同集
  （仅类型缺口+其级联 bind 报错，无 Schema/结构/参数/量纲类错误）。
- 结论：gc 按契约包（声明权威）生成，登记看板共同议题待 csk 合入；本侧自检（契约
  JSON 驱动，同规则）已覆盖参数/绑定合法性。

### 其他适配

- plot_head/pen 挂**链尾轴**（v0 硬编码 parent=y_axis，单轴场景自检失败）；无轴不生成。
- 编排器闸门2b / chat 交付清单同步（scene.spec.json 单工件，gates.scene.io_map_vars
  计内嵌条目）；final 冻结 copytree 自动跟随。
- 测试重写：test_scene_gen（契约加载/生成形态/cmd 路由/降级/V0~V4 负例/契约范本
  自检回归——范本以合成 io_list 喂入 V0~V4 全过）、test_consistency_check R5 新语义
  （子集合法/类型不匹配/bind 形态/契约③ wrapper）、test_orchestrator（无独立
  io_map.json、io_map_vars=3）。pytest 182→190 全绿。

## 2026-09-09（夜）②b 切换 gantry_xyz 单资产路线 + 驱动通道合成（本地实验，未提交）

### 决策背景

plotter 多资产 spec 在 csk mujoco_build 的 `_plotter_xml` 简化路径下机械结构不完整
（无底座/立柱/导轨，滑座为无碰撞小方块）；gantry_xyz 原生分支才有完整机构
（四级 body 链+三位置执行器+球头补偿笔）。负责人裁决走 gantry 路线保模型准确性。

### 实现（src/agent/scene_gen.py + consistency_check.py）

1. **gantry 装配**：三轴可解析（运动学 ∪ `<axis>_fb` 推断 ⊇ {x,y,z}）→ 单资产
   gantry_xyz；travel=stroke×scale、speed=首轴 vmax×scale、pose=笔尖行程原点
   （扫掠区中心继承 v0 工位 (0.5,0.5)→pose (0,0,0)）。非三轴降级 legacy
   linear_axis 族（v0 装配原样保留）。
2. **契约下界钳位**：travel_z 开区间下界 >0.01 恰好排除 10mm 笔行程（10×0.001=0.01）
   ——`_clamp_to_contract` 按契约元数据钳到下界+10%（0.011），语义不变；
   **RFC 议题：travel_z 下界 vs 绘图笔行程**。
3. **驱动通道合成**：io_list 现为速度指令型（x_v），无位置指令变量可绑 → 模型
   不可驱动。按 gantry quantity 合成 `<axis>_cmd`（dir=output，range=travel）三条；
   io_list 已有真实 `<axis>_cmd` 时不重复。`is_driver_channel()` 判定豁免形态，
   R5/V3 对其定向豁免 ⊆ 检查（待 RFC 把位置指令通道并入契约②后撤销豁免）。
4. **路由覆盖改按绑定判存在**（asset+quantity），plc_var 命名自由——csk example1
   的 AxisX_cmd 与合成 x_cmd 同绑 gantry.x_cmd，覆盖即满足。
5. 交叉验证（origin/csk 真实现）：validate PASS；mujoco_build 组装出
   gantry_base→x/y/z_carriage 四级机构 + drive_x/y/z 三执行器（travel 1/1/0.011）；
   iomap.assign_modbus 产出 fb→%IW0-4 / cmd→%QW0-4（模型可驱动）。

### 遗留（RFC 议题，登记待推进）

- io_list 位置指令化（x_v→x_cmd 或并存）：动契约②（AML 通道表/XML/②a skill/
  R5 撤豁免），三方知情；
- travel_z 下界 0.01 vs 绘图笔 10mm 行程；
- 观察：gantry_bridge.derive_layout 的 cmd 寄存器推位（sensor_end+2i）与
  assign_modbus 的 %QW0-4 编址不一致（csk 侧内部缝，jog GUI 可用、OpenPLC 桥
  轮询需对齐——移交 csk）。

pytest 190→192 全绿。**未提交未推送（本地实验态）。**

## 2026-09-09（深夜）[2b] 抬笔反复失败战役的根因闭环与知识沉淀（P23）

### 现象与战役

plotter_cell（画正方形，LLM 战役）iter_002~005 反复死于验收 [2b]：cmd_home 后
笔抬离超时（3.3s > 3s）且 z_fb 停 0。经验库检索实证：iter_002 无命中（<0.30）、
iter_003 仅 0.32 弱命中且偏题——**该失败族此前无任何可复用经验**，坑库 Top 命中
（P21/P18）也指向错误方向。

### 根因（三组对照实验在线实证）

逐周期采样发现三轴 sw **同步**在 0031↔0033↔0437 间循环（all_oe 闪烁）：
- 补丁 A（删 MC_POWER pstep=4 自愈分支）：仍振荡 → 非自愈分支；
- 补丁 B（Enable 硬接 TRUE）：仍振荡 → 非使能输入；
- 补丁 C（**仅删 DRIVE402 的 `ELSE state := 1` 与 `ELSE sw := 0` 两个 CASE
  兜底 + pstep 显式初值**）：三轴 sw 立即恒稳 0437，[2b] 通过，完整验收推进到
  后续段（iter_003 剩余 X/Y 互锁缺陷属另一族）。

定性：LLM 给 CASE 状态机加的 ELSE 复位兜底（旧 skill 规则"一律带 ELSE"误导）
在三轴同款 FB 多实例并行下被 matiec 优化器合并缺陷周期性触发——驱动被反复拽回
重握手；cmd_home 脉冲时刻 all_oe=FALSE 则回参考点直接拒动（z_sw=0437 停原位
形成假到位）。与 iter_006（自愈稳定版）diff 交叉印证：其修复恰是删 ELSE。

### 沉淀（本轮改动，未提交）

- 坑库 **P23**（runtime 域）：签名含"笔抬离纸面/Z 到参考位 10（实际 0）/
  Z 轴 bit2=1..."验收原文，诊断含三组对照实验结论，修法=封闭值域+显式初值时
  CASE 不加 ELSE 复位兜底、多实例同款 FB 禁止 ELSE 复位到重握手态；
- skill 硬规则修订（plcgen_skill.md）：废除"CASE 一律带 ELSE"旧表述，改为
  P23 边界规则（多实例 FB 无 ELSE / 空兜底；单实例兜安全态且禁触发全握手重跑）；
- 重放单测 test_p23_else_fallback_oscillation_replay（iter_003 真实 gate.json
  错误 → P23 必须榜首命中）。pytest 193 全绿。

诊断工具留 workspace/（probe_2b.py / probe_tl / iter003_* 补丁对照件，不入库）。
运行时已重部署 plotter3axis 种子复位。**未提交未推送。**

## 2026-09-10 领域知识库 v0 落地（看板待配合【负责人指令 lx 代登记】响应，未提交）

按方案三段实施（P1 还账 → P2 打底 → P3 接线）：

- **P1 v4.0 骨架同步**：plcgen_skill「基础 FB 骨架冻结」从 v3.0 升 v4.0——INTERP
  换命令级动力学接口（vel/acc/dec_req 总线 + Aborted 中止；无 VMAX/MAXPOS 参数，
  越程改 MC 层 ErrorID=1）+序列器连续跟踪分支标注；DRIVE402 常量修订（KP 8→25、
  QS_DECEL 200→120，对齐 motion3axis v4.0）；MC 层补全（MC_POWER v4.0 全文补回、
  MC_MOVEABSOLUTE 校验+ErrorID+中止沿检测全文、MC_HALT 全文、MOVERELATIVE/
  READSTATUS/READACTUALPOSITION 签名与要点）+总线接线形态（手工 AXIS_REF：
  tgt/vel/acc/dec 总线 + 触发线 OR）；**P23 联动核对：v4.0 骨架无 ELSE 复位兜底 ✓**。
- **P1 防漂移护栏**：tests/test_skill_skeleton_sync.py——从已验收种子
  （motion3axis.xml）提取 FB 接口指纹与 skill 内联骨架逐块比对（全内联块签名
  相等 + 摘要块端口齐备 + v4.0 关键语义词在场 + P23 无兜底形态），master 骨架
  演进而 skill 未跟随时变红。本次"落后一个版本"的根因（无同步校验）就此关闭。
- **P2 四域知识文档 v0**：src/agent/knowledge/domain/{st,plcopen_xml,
  automationml,motion_control}.md——38 条条目（ST-12/PX-07/AML-07/MC-12），
  每条附可运行出处（已验收 XML/实现代码/坑库实证），契约内容只引 §节号不复写。
- **P3 消费接线**：knowledge/domain/loader.py（按任务关键词选域 → 条目索引摘要，
  token 预算内一行一条）；pipeline.build_messages 注入 system prompt（异常不阻塞
  生成）；attribution 蒸馏 prompt 挂全量条目索引（diagnosis 可标注条目号溯源）。
- 工具坑：Bash 工具 heredoc 会吞反斜杠（\n 变 \n），含转义序列的代码补丁必须
  走 Edit 工具；pipeline/attribution 源文件为 CRLF 行尾。
- 遗留：plotter3axis.xml（gc 种子）仍 v3 形态 INTERP（VMAX 参数+跟踪分支），
  升 v4.0 总线形态属后续任务；skill 序列器模板已标注两种形态兼容。

pytest 193→197 全绿。**未提交未推送。**

## 2026-09-15 轴对象映射绑定 RFC 评估回区（响应 lx 09-09 提案与 09-15 催办）

master 同步（bb0d5dd 合入 gc：motion3axis 种子战役 runs 入库 + 看板催办）后，
按催办要求对《运动控制代码生成方案》§6 做接入性评估，提案文本落 gc 文档 §10。

评估用的事实核验（全部只读操作，2026-09-15 于 master bb0d5dd）：

- **两基准 AML 实跑**（`agent.aml_parser.parse_aml`）：motion3axis_station
  32 io_points、x/y/z 六角色齐全（fb/sp/sw/v + `rel_x_d` 等 rel 三条 + err_id 三条），
  problems=0；plotter3axis_station 26 io_points、仅 fb/sp/sw/v，rel_d/err_id 缺省
  ——按 §6.1 可选角色省略合法，且与 plotter3axis.xml 现状（无 rel/err_id 定位
  变量、26 点）一致。
- **发现规范与实现的命名分歧**：§6.1 role 推断写 `{a}_rel_d`，v4.0 实际命名是
  `rel_{a}_d`（motion3axis.xml:916-922 `rel_x_d/rel_y_d/rel_z_d` @ %QW3~5；AML
  通道同名）。按原文模式 motion3axis 推断不到 rel_d → MC_MOVERELATIVE 不展开
  → 等价基准 1 必失败。列为修订意见①。
- **limits 消费点与 v4.0 模板的矛盾**：§6.2 "limits→INTERP 执行上限"，但 v4.0
  INTERP 已无 VMAX/MAXPOS 参数（命令级动力学 vel/acc/dec_req，越程改 MC 层
  ErrorID=1，devlog 2026-09-10 骨架同步时已核实）。改为"展开期校验
  defaults ≤ limits + defaults 为 MC 动力学来源"。列为修订意见②。
- **INTERP 体内越程常量**：motion3axis.xml INTERP ST 体写死
  `pos_target > 100 OR pos_target < 0`（注释自称 MC 层之外第二道防线）——
  非 [0,100] 行程轴（plotter z）不能逐字复用，建议参数化为带初值局部变量
  （POSWIN 同法，motion3axis.xml:75 有初值 2.0 先例）。列为修订意见③，lx 域。
- **数值吻合核验**：motion3axis.xml MC 调用 Velocity=40.0/Acceleration=80.0/
  Deceleration=80.0（go_x/rel_x 等接线）与 AML vmax=40.0/accel=80.0 吻合——
  §6.1 defaults 层提案值即现行接线值，等价基准 1 的参数面无障碍。
- **plotter 基准前置**：plotter3axis.xml 仍 v3 形态 INTERP（VMAX 参数版，
  2026-09-10 devlog 已登记），等价基准 2 需先升 v4.0——排期步 5 同批关闭。

评估结论：四项（⓪ / ②a / ②b / R8）均可接入，无架构性障碍；排期 5~6 个工作日
（评审通过后）。R8 编号理由：R7 已被 CASE 初值提案占用（待 lx 评审）。

改动文件：gc 文档（§10 新增 + §9 待办 #11）、协作看板（gc 区块三处 + 共同议题
一行 + 变更记录一行）。纯文档改动，无代码变更；pytest 跑全量确认与改前一致。

**未提交未推送。**

## 2026-09-10 情景记忆与模式库清除（本地，未提交推送）

- fixes.json（116 对）删除：action 字段全为收尾套话（95 abandoned"迭代上限未收敛"+
  21 final"种子通过全部闸门"），无一条真实验证对——归因 match_fixes 消费的
  similar_fixes/repair_hints 拼接实为噪音；删后 match_fixes 返空、repair_hints
  回归纯坑库修法。写入点未停（orchestrator:719/781），下次战役收尾会重建文件。
- patterns.json（2 张卡）git rm：plotter3axis/plotter_circle 两卡标签域（绘图族）
  与零卡路线完全重叠、不可达；非绘图任务兜底=内置 CATALOG motion3axis。
  _catalog 缺文件优雅降级（patternlib.py:50 is_file 判断）。下次种子战役
  final+在线验收 ok 时 _consolidate 会重新策展登记（机制未停）。
- 遗留决策点：① record_fix 停写或改写"坑库命中→修复→结局"真实验证对（反哺
  坑库修法治愈率统计——情景记忆的独特价值）；② _consolidate 自动策展是否
  保留（与"非绘图任务扩充卡源"演进一并决策）。
- pytest 209 全绿；changelog 已登记。

## 2026-09-15（下）轴对象绑定 RFC 开工：步 1~4 落地（lx 回执后当日，未提交）

lx 15:31 回执通过后按 gc 文档 §10.4 排期实施。四个代码改动 + 一个顺手修：

- **步 1 ⓪（aml_parser + Schema v1.1.0-draft.1）**：`_extract_axes` 重写——
  limits{vmax,accel} / defaults{velocity,acceleration,deceleration}（AML 显式
  声明优先，缺省回退 limits 同值、deceleration 回退 accel——lx §6.1 示例值
  40/80/80 即回退语义，与种子 MC 调用逐字吻合）/ io 角色绑定（`AXIS_IO_ROLES`
  表驱动：必需 fb/sp/sw/v 缺失记 problems 且 io=None；可选 rel_d 用 **rel 前置
  模式 rel_{a}_d**（RFC 修订①）、err_id 缺省省略）；短名冲突检测（x_axis 与 x
  同短名 → problems）。消费方同步：requirement._device_summary 与
  scene_gen._axes_info 改读 limits。
- **步 3 ②b**：`_axes_info` 读 limits.vmax；`route_physical_channels` 增
  device_model 参数——有 axis io 绑定时走 `_route_gantry_bound`（通道名取
  绑定），否则回退原正则路由（同产物：role 推断本就按 `<short>_fb` 匹配，
  差异仅在命名偏离范本时以绑定点为准）；generate() 传入 device_model。
- **步 4 一致性 R8**：`_check_axis_params`——POSWIN 取 INTERP 类型初值
  （共享 FB，须全轴一致）；MC 动力学从 PLC_PRG 体 go/rel 调用字面量提取比对
  defaults；行程从 MC_MOVEABSOLUTE 体内字面常量提取（全轴同行程才可比，多轴
  行程不一记 SKIP——待 lx 参数化）；scene 侧 travel≡stroke×scale、
  speed≡vmax×scale。v3 形态 XML（INTERP 带 VMAX）整体记 SKIP（plotter 种子
  现状）。**踩坑：ElementTree 的 iter() 不支持 `{*}` 通配**（那是
  find/findall 的 ElementPath 语法）——种子是 tc6_0201 命名空间文档，
  `root.iter("pou")` 静默返回空；写 `_iter_local` 按 localname 迭代修复。
  主入口增 scene 参数；编排器闸门2b 传 scene_out["scene"]。
- **步 2 ②a 展开器（最大件）**：`src/agent/axis_expand.py` +
  `src/agent/templates/motion_stack_v4.xml`（种子 FB 库+骨架逐字副本，PLC_PRG
  段挖空为 @@PLC_PRG@@ 标记）。PLC_PRG 完整生成：七个 localVars 块（分组与
  顺序与种子逐字一致——G1 站 BOOL 输入 / G2 fb+rel / G3 sp / G4 sw+v+err+聚合
  +prog_id / G5 cw+interp+jog_pos+tgt+exe 触发线 / G6 动力学总线（初值=
  defaults）/ G7 十二类 FB 实例）；ST 体全部轴派生接线（扫描头/目标总线/
  使能-复位-快停/读块/命令层/插补/清触发/点动路由/驱动/聚合/ErrorID 路由）。
  **关键发现：motion3axis 的 PLC_PRG 无工艺层序列**（纯轴派生），故基准 1
  可做到全文档等价——实测**字节级一致**（44334 字节），强于 §6.3 树级判据。
  poswin/行程全轴一致时**代入 FB 体字面常量**（转义形态 `&gt;`/`&lt;`；ST 字面
  量用整数字形 100/0、POSWIN 初值用小数形 2.0——种子两种惯例并存）；
  defaults≤limits 展开期校验（RFC 修订②）；jog_axis/jog_velocity/reset_axes
  为应用配置参数（点动挂 x、50.0、复位挂 x——种子惯例）。
  工艺层接口：process_vars/process_body 尾接（plotter 的 9 步序列器走此口，
  LLM 只写工艺层的目标达成）。
- **顺手修**：plotter_circle.xml 头注释 prog_id = 2 → 3（lx 2026-09-09 复核
  意见；xml2st --check 复验通过，纯注释无行为变化）。

测试：test_aml_parser 20→26（v1.1 结构断言 + plotter 可选角色 + 必需角色缺失
负测试 + Schema 校验）、test_consistency_check 20→27（R8 基准绿 + 四负测试 +
v3 SKIP）、tests/test_axis_expand.py 新增 6 项。**pytest 198→215 全绿**。

遗留：步 5（plotter 种子升 v4.0 + 基准 2，lx 配合——建议直接用展开器产种子，
工艺层走 process 注入）；展开器接入生成主路（pipeline LLM 只写 process_body）；
lx 改种子 FB（INTERP 越程常量参数化）时等价测试变红 → 刷模板 + 代入逻辑改
参数位 + R8 行程比对改读实例参数。

**未提交未推送。**

## 2026-09-10 术语更名：坑库 → 故障库

文件名 pitfalls.json / 条目编号 P01~P23 / 代码 API 均不变——仅中文称呼更换
（口语"坑库"→工业语境"故障库"，对齐 FMEA 的 failure mode 术语）。活文档与代码
注释替换 25 处（memory/orchestrator/domain 三份/gc 两文档/看板/术语手册/周报
模板/单测）；本日志与 changelog 历史行保留原词存档，changelog 新增更名登记行
作为新旧术语的映射依据。术语手册词条已更新（原"坑库"定义行改注更名）。

## 2026-09-22 画方战役 P24~P27 修复链：st_optimizer 根因拔除 + skill 模板缺行族

### 起点与交接核对

上会话（2026-09-16/17，其 devlog 两节未写盘即交接，本节覆盖其要点）完成 7 局
战役，前沿推进到在线验收 [2b] 段（抬笔超时）。工作区未提交改动核对齐全：
R8 三处修复（POSWIN 异构 SKIP / 提取器兼容 html:p / travel_z 契约钳位豁免）+
skill 两条硬规则（每轴接线必抄骨架 / 触发输入禁常量封死）+ P23 修法扩充，
pytest 216 绿。今晨又一局（11:49~13:55）六轮未收敛，iter2/4/6 三轮部署编译
失败且报错行号与本地产物语义对不上——由此入手。

### P24：OpenPLC st_optimizer 原地改写上传件（环境级根因）

取证链（全部可复现）：

1. 容器 `/workdir/webserver/st_files/941763.st` 与本地产物 plc.st diff：
   INTERP_Z 的 21 行 `IF hold_req` 块被搬进 INTERP 体内、`IF hold_req THEN`
   与配对 `END_IF;` 两壳被删——本地 ST 完全合法，变形发生在上传之后；
2. 上传还原实验：把 17945 字节合法 ST 原样上传，OpenPLC 落盘 17921 字节
   错序件（md5 与战役件一致）——排除本侧管线；
3. `webserver/scripts/compile_program.sh` 读源：matiec 之前跑
   `./st_optimizer st_files/x.st st_files/x.st` **原地改写**；
4. `utils/st_optimizer_src/st_optimizer.cpp`（214 行）核读机制：首个 IF 块
   之后向全文件扫描，把与其**头行文本完全相同**的后续 IF 块的**函数体**
   （剥壳）拼进首个块内，随后同名块整块丢弃。INTERP 与 INTERP_Z 各有一行
   字节相同的 `IF hold_req THEN`（z 轴 poswin 异构迫使 LLM 拆双 FB）即触发。

**修复**：容器内 compile_program.sh 注释 `./st_optimizer` 行（标记
`#DISABLED-GC-P24`）。**影响面实证**：对现行两个种子（plotter3axis /
motion3axis 转换产物）跑优化器，唯一差异是文件尾追加一个空行（diff 547a548
/ 735a736）——禁用对 lx 侧种子部署与 run_regression L3 零影响；历史件
993061.st md5 与种子优化输出一致，交叉印证。P10（选通赋值被吞）与 P23 的
"优化器"实为同一二进制，两条目已加交叉引用；P23 的"matiec 优化器"表述已
更正为 st_optimizer。

### 模板缺行族 P25~P27（skill 骨架不完整 → LLM 自创死线）

优化器拔除后真实错误浮出，三局战役（sq8/9/10）逐层剥离出 skill 模板三处
缺行，全部"种子有、模板漏"：

- **P25**：接线节缺 `x_sw := dv_x.sw; x_v := dv_x.v_cmd;` 回拷行——x_sw 是
  定位变量且唯一赋值点就是这行，漏后恒 0，[1] 上电段三轴 sw.bit0 判定全挂
  （sq8 四轮同款；LLM 自创 MC_READSTATUS 写镜像路由，但该块 sw 是输入无
  输出）。补行后 [1][2] 即过。
- **P26**：DRIVE402 骨架截尾于 `sp_prev := Setpoint;`——LLM 补写丢
  `v_cmd := REAL_TO_INT(v_out);`，v_cmd 声明后永无赋值，三轴速度输出恒 0、
  场景伺服不动（sq9 四轮 [2b]；probe_2b 探针实证 hm/z_interp 正常而 z_v=0；
  一行补写后 z_v=+120、Z 0.1s 抬到位）。骨架补全尾部两段（v_cmd 行 + sw
  输出 CASE + state4 到位 OR 16#0400）并写明接口类型（v_cmd 是 INT——LLM
  曾自声明 REAL 引发类型连锁错）。
- **P27**：MC_HOME 本体只有摘要无内联——LLM 自创"到位位 0x0400 判完成"，
  但 0x0400 空闲时已置位（轴停目标上即 pos_err≤2 且 v_ff=0，z_sw=0437），
  home_exe 置位同扫描被清零，cmd_home 请求被无声吞掉（sq10 两轮 [2b]，
  探针实证 cmd_home 后 hm_z_exe/pl_step/z_interp 全 0）。补 MC_HOME 权威
  本体内联（plotter 种子逐字）+ 硬规则：完成判定只允许故障位 0x0008，
  运动完成由 PLC_PRG 的 ix.Done 清 exe 行承担，0x0400 禁入任何 FB 的
  完成/启动判定。

### R8 误报修复（consistency_check.py）

`_call_args` 原用 `\(([^)]*)\)` 提取实参，遇嵌套括号（`fire := cmd_go AND
(NOT go_x_exe)`）在首个右括号截断 → "go_x 调用缺 Velocity 实参"误报
（三局各烧 1~2 轮；iter 实物核验 Velocity=40.0 全在）。改括号配平提取，
负测试 test_nested_paren_call_args_not_truncated 保证。

### 防漂移与测试

- tests/test_skill_skeleton_sync.py 增 test_drive402_tail_present
  （v_cmd 行与 sw CASE 三行必须在场——截尾类漂移的针对性单测）；
- 故障库 P01~P27（净增 P24/P25/P26/P27 四条，P10/P19/P23 增补）；
- pytest 216→218 全绿。

### 战役记录

sq8：前沿 [1]（P25 死线）；sq9：前沿 [2b]（P26）；sq10：前沿 [2b]
（P27）；**sq11：final 收敛**——六轮：iter1 generate（API 质量类）、
iter2 [3] 段场景脚本 pymodbus 写异常（基础设施瞬态，非程序错）、iter3 [3]
落笔互锁缺、iter4 [5] move_done 聚合、iter5 [8] 恢复回参考点、
**iter6 [1]~[10] 全 PASS**（含急停受控减速 0.28s、越区 0 次、终态 (51,51,10)），
final 冻结 + runs/latest 更新 + 验收"全部通过 ✅"。落笔互锁/move_done/恢复
回零三处修复均由验收回喂自行完成（知识库未新增条目——修法属规格 C/AC 判据
范畴，回喂文本已足够定向）。2 轮内收敛指标未达（第 6 轮过），sq12 以齐备
知识栈再冲：**iter3 收敛**（iter1 generate 空转=API 质量类、iter2 [2b]
修复轮、iter3 全 PASS，18:01 final 冻结 + runs/latest 更新，复验 218 绿）。
两次 final 的首轮稳定缺口同集：落笔互锁（C2）/move_done 聚合/急停后恢复
回零三处规格级语义靠验收回喂一轮修复——若要压到 ≤2 轮，下一步应把这三处
写进 skill 工艺层模板（当前仅验收文本描述）。

### 环境与工具备注

- VM 容器变更已登记看板（lx 知会：run_regression L3 同环境，种子零影响）；
- 探针脚本 workspace/probe_2b.py（追加 dbg 寄存器 + Modbus 逐周期采样 +
  伺服模拟，可改 iter 路径复用）；诊断件 workspace/vm_941763.st、
  iter006_inout_fix.st、iter002_vcmd_fix.st 留档；
- Bash 后台起战役必须用 run_in_background：普通 `&` 会随会话回收（sq10
  首启实证）。


## 2026-10-19 master 同步与 Agent 交互功能

- 在 gc 分支先保存 `gc-before-master-sync-20261019` stash，合入 origin/master b2d06f6，再恢复原本地改动。文档冲突保留双方记录；runs/latest 采用 master 的 CSP XML/scene/io_map，旧本地 latest 原件另存 workspace/backups/gc-before-master-sync-20261019。没有 push。
- 新增 clarification.py：显式几何解析与缺项问题、有限正数/分段数/AML 行程校验、百分比坐标与毫米的歧义拦截、交互重答和脚本化答案。chat 的 --confirm 不再触发旧 _terminal_confirm 阻塞；修改规格后同步重新规划，默认独立 task_id 保留历史。
- 新增 visualization.py：纯内嵌 SVG/JS 页面，参数、步表、抬落笔路径、拖动与播放。用户内容仅经安全 JSON 和 textContent 展示。修正 goal_text 对小数的整型截断及圆步表末段闭合；未知开场位置不作假定。
- 新增 replay.py/evidence.py：回放已有 gate/trace/verdict/log，保留失败阶段、诊断、ST 差异和样本来源。编排器每轮刷新回放并将当前目录传给验收子进程；CSP 场景只增加证据记录，不改 evaluate/attribute。没有采样的旧运行明确只展示已有日志，skipped 不显示为在线全过。
- 验证：修改前基线 218；修改后根 253 + runtime 27 全绿。35 项新增测试包含模拟验收子进程到 trace/log/replay 的完整链路。Chrome 独立 headless 配置检查方/圆预览、旧日志模式、变量曲线、滑块及轮次切换，零页面异常；人工检查预览截图。
- 环境：默认 Git 代理 127.0.0.1:10808 不在线；临时禁用代理并切 Windows schannel 完成 fetch。pytest 系统临时目录权限受限，改用 workspace 下 basetemp；不修改系统配置。scenegen 旧 USD 测试缺 pxr，未安装新依赖。192.168.12.131:22 连接超时，在线验收未重跑。

## 2026-10-19 SVG 路径输入

- 新增 `svg_input.py`，标准库实现路径扫描器与椭圆端点参数转换；支持 M/L/H/V/C/S/Q/T/A/Z 相对/绝对/隐式重复、基础形状和分组仿射变换。根 preserveAspectRatio=none 保留非等比变换；不支持的可见内容明确拒绝，避免悄悄少画。
- 适配边界用二/三次贝塞尔导数根与椭圆弧扫角内极值，避免控制点边界或完整椭圆边界把局部曲线缩得过小。贝塞尔按控制点到线段（非无限直线）的距离细分，能保留共线回折；弧用仿射椭圆半径上界控制折线误差。有限数值、源文件大小/深度/段数/点数预算前置校验，极端弧数值转换为可读报错。
- 按 AML X/Y 同单位坐标适配，默认翻转 SVG Y；分离离散容差与 INT 量化偏移。量化前后检查行程，检查 INT 域与可表示 Z 抬笔高度；连续重合点合并，退化子路径拒绝。每个独立路径按输入顺序抬笔定位、落笔按序描画、抬笔，开放路径不擅自闭合。
- chat 与 orchestrator 共用 --svg 系列参数，直接传完整步表给生成/修复，不让模型重新猜轮廓；拒绝原样种子复制模式。交互修正规格支持适配框/中心/容差调整。保存 input.svg 原字节和 SHA-256，归档前检测文件变化；复用已有预览/回放，不改冻结 Schema 或验收语义。
- 验证：根 pytest **311 passed**；SVG 专项 **58 passed**（含路径命令、变换、极值、回折、曲线/弧、抬笔、预算、越程、量化退化、源文件变更、离线 CLI、生成提示路点完整性、编排器参数接线）。Chrome 独立 headless 检查 4 路径/42 点/17 步/50 路段的示例，滑块、播放、390px 布局通过，零页面异常，截图人工检查通过。
- 示例 `examples/svg/house.svg`；预览与截图保存 `E:\chagpt\308sjk-svg-20261019`。未安装依赖，未调用真实模型生成新 PLC，未执行任意 SVG 在线几何验收；标准方/圆脚本不可代替任意 SVG 判定。代码在 gc 本地，未 push。

## 2026-10-19 浏览器 SVG 绘图工作台

- 新增 `src/agent/web.py` 与 `web_ui/{index.html,app.js,style.css}`。标准库本地服务 + 原生页面，不新增依赖；上传源文件只转换成点列，不注入原 SVG DOM。路径支持画布点击、复选框、全选/清空/反选；参数框、抬落笔预览、Agent 对话、规格核对、异步生成、进度与下载组成完整入口。
- `parse_svg` 可附加路径编号/元素标签；svg_catalog 用屏幕容差生成点列，限制总预览预算并适配画布。plan_svg 新增 selected_paths；按源文档顺序执行子集，适配仍按全图边界，取消某路径不改变其余图形的位置比例。CLI 增 --svg-paths，尺寸对话修正保留选区。
- 会话持久化 session.json + draft；revision 检查拒绝旧页面写入。选区/参数变更使规格失效，未提交参数亦禁止生成；空选区持久化并阻断生成。单后台任务串行使用模型；失败恢复可编辑状态，异常隐藏 Key，刷新恢复，重启标记中断。生成使用独立随机 task_id 冻结 spec/trajectory/input.svg/web-request.json。
- 真实生成接线复用 RequirementUnderstander 的 understand/refine、PLCGenerator(no_cards=True) 与 Orchestrator.solve，固定 deploy=False/acceptance=None；不跳过现有离线闸门。限制来源、请求大小、令牌和会话产物读取；Key 仅留在 Python。
- 验证：根 **327 passed**；工作台相关 16 项（包括参数化边界）+ 原 SVG 58 项均通过。Chrome 独立配置实际文件导入、画布与复选框、选区预览、参数更新、刷新恢复、空选择、390px 布局和旧规格阻断通过，零页面异常。隔离模型替身验证对话→生成进度→XML 下载，不把替身 XML 当成所选图形的真实 PLC 实证。
- 已启动本地 127.0.0.1:8765 服务，产物默认 `E:\chagpt\308sjk-svg-workbench`；截图同目录。服务读取现有模型配置，本次未发真实生成请求，未部署运行时，未修改冻结契约，未 push。

## 2026-10-19 基础资产分区与可替换组件库

- 工作台升级为 AML / 组件库 / SVG 三分区。未上传 SVG 时也能管理设备与组件；详情显示轴与 IO、组件接口、质量/增益/阻尼和几何参数，支持下载、整包或子项替换及恢复默认。候选文件与当前设备组合先校验，失败保留旧状态；旧规格失效与 revision 机制延续。
- 新增 assets.py：内容寻址资产库、AML 解析/数值检查、兼容组件契约及 JSON 包验证。资产按会话引用，SVG 替换保留 AML/组件库；老会话升级保留历史。路径规划、理解、PLC 地址表和场景生成均改为消费会话 device_model，禁止混用启动时全局默认模型。SceneSpecGenerator 新增可选实例契约，不修改全局 CONTRACT_TYPES。
- runtime 物理默认值迁至 physics_defaults.json，physics_assets.py 负责有限数值、类型、维数和几何校验；build_mjcf 与命令行均可传物理参数，gantry 与 plotter 分支按调用消费，不污染全局默认值。当前只接受已实现组件接口的数据，不执行上传脚本或假装能加载任意新组件。
- 每次生成保存 assets/device.aml、component-library.json、components.json、physics.json 与 assets-manifest.json；输入 SVG 原字节照旧冻结。生成完成后用快照物理参数构建 scene.xml 并提供下载，历史结果不受后续替换影响；本次未启动真实设备或自动仿真运行。
- 验证：根 **345 passed**，runtime **27 passed**（pymodbus 服务端测试退出时有遗留异步任务提示，测试均通过，本批未修改桥线程）；18 项新增资产测试，含当前 AML 传入理解/生成适配器、契约约束实际生效、物理增益经 MuJoCo 模型加载验证、原值不污染、历史快照独立。默认 gantry/plotter_cell 构建输出与 HEAD 原构建器**逐字节一致**。
- Chrome 隔离配置实际文件上传覆盖 AML/整库/契约/物理参数/SVG、失败保留、下载、默认恢复、选区、刷新及 390px 布局，零页面异常；生成与历史下载用隔离模型替身验证。截图保存在 E:\chagpt\308sjk-svg-workbench/{assets-overview,assets-selected,assets-mobile}.png。未改仓库冻结契约，无新依赖，未 push。

## 2026-10-19 工作台展示精简

按用户截图移除圈选节点及对应 JS 写入：品牌、副标题、模型配置/站台信息、资产快照说明、组件库说明标签、AML 与组件库摘要/哈希行。资产详情和操作保留，后端资产校验及快照逻辑不变。根 345 项通过；Chrome 检查节点移除、资产详情、轨迹选择、390px 布局，零页面异常，截图 E:\chagpt\308sjk-svg-workbench\ui-cleanup.png；未 push。

## 2026-10-19 SVG 摘要与生成结果精简

移除 SVG 路径数/大小、SHA 行及更新说明；结果区取消任务编号/状态，前后端仅提供最新成功任务的 final/scene.spec.json 和 final/plcopen.xml。io_map 保持内嵌于 SceneSpec，不单独导出，不改变映射或冻结文件；历史列表读取时同步过滤，中间轮次、闸门记录与资产快照留在磁盘。最新任务运行或失败时隐藏旧结果，避免误下载此前 PLC。

验证：根 349 项通过；新增四组历史任务状态回归，覆盖最终文件过滤、映射保留、无额外导出、原场景字节不变、下载归属和 HTTP 附件。Chrome 检查真实已完成任务的两个下载、SVG 节点删除、失败状态隐藏结果、过滤中间稿及 390px 布局；截图 E:\chagpt\308sjk-svg-workbench\results-cleanup.png。已重启本地后台，未触发模型生成/设备部署，未 push。

## 2026-10-20 轨迹预览说明精简

`visualization.write_view` 按页面类型生成页眉和提示区；轨迹预览去除品牌、副标题及顶部说明块，JS 同步取消对这些节点的写入。回放页仍使用原说明，轨迹和参数等内嵌数据保留。已用新版模板重渲染本地工作台 7 个已有预览，逐一检查内嵌 JSON 等价，并重启后台。

根 349 项通过；Chrome 检查节点移除、轨迹与参数呈现、播放、滑块和 390px 布局，零页面异常。截图 E:\chagpt\308sjk-svg-workbench\preview-cleanup.png。本次仅调整展示，未运行模型生成或设备验收，未 push。

## 2026-10-20 三业务工作台与网页仿真场景

按负责人确认拆为三部分：资产管理与 PLC 生成、独立仿真场景生成、OpenPLC Runtime 状态。仿真画面嵌入网页，第三部分本次仅展示状态，部署和运动控制留后续。保留已有资产/对话/生成界面及仅两份最终下载；MJCF 构建从 `_generate` 回调移至独立异步动作，失败不影响已经完成的 PLC 交付。

新增 simulation.py 复用现有 MJCF 和 Modbus 布局代码，读取同一任务冻结的场景/需求/物理参数，经 MuJoCo 加载校验后写 simulation/。PLC 地址取最终 XML，不采用顺序分配出的 PLC 地址替代真实接线；合成 cmd 或范围缺失只记为待适配，不绑定猜测地址。final 交付不变，io_map 内部副本不对外独立导出。请求 revision 必须最新；对账实际冻结输入，拒绝不同资产/需求的旧任务构建，全部恢复相同输入时允许复用已有交付。

网页预览沿用 CSP Studio 的 MuJoCo Renderer，每个请求在同一线程创建/关闭 GL 上下文，只 mj_forward、不 mj_step；无需 PySide6，也未引入 CDN 或新依赖。第二、三部分均可显示模型并旋转/缩放视角。Runtime 页只 GET 既有 :8600/status，trust_env=False；不可达时部署服务离线、Runtime 未获取，始终区分当前页面未接入运动控制。没有新增控制路由或执行在线部署。

验证：根 362 项通过，新增场景/HTTP/状态覆盖；Chrome 独立服务复用已有 SVG 交付验证实际 MJCF 构建、渲染、相机、状态、刷新、下载与 390px 布局，零页面异常。截图 E:\chagpt\308sjk-svg-workbench\business-{scene,runtime}.png。当前已有 SVG 的 x/y/z_v 与合成 x/y/z_cmd 不匹配既有位置桥，界面如实显示通道待适配；未将场景预览视为闭环运行。已重启 :8765，并复用现有会话交付完成实际场景构建与 PNG 渲染冒烟；两份 final 文件 SHA-256 均保持一致。未调用模型生成、未运行 PLC、未 push。


## 2026-10-20 PLC 生成页紧凑布局

第一业务页收拢为资产管理、绘图素材与参数、PLC 代码生成三个区域。AML/组件库在左上，SVG 上传移入绘图区，参数入口在画布前默认折叠；桌面视口内左侧独立滚动，右侧栏保持位置，生成按钮固定在栏底。右栏新增工艺对话/执行进度/生成产物标签，生成时打开进度、成功后切换产物，保留两份最终下载及原交互节点；小屏幕自动堆叠。未改后端/API 或生成行为，静态页面刷新即生效。

根 362 项通过；Chrome 检查四种桌面尺寸无整页滚动、左侧展开详情后右栏不移动、生成按钮持续可见。隔离生成替身验证进度→产物及文件下载、对话草稿保留；SVG 文件实际上传、参数修改使规格失效、iframe 轨迹预览和 390px 布局通过，零页面异常。截图 E:/chagpt/308sjk-svg-workbench/compact-generation{,-mobile}.png。未调用模型或运行 PLC，未 push。


## 2026-10-20 默认工艺、清空上下文与鼠标场景操作

移除预览的规划参数卡片（回放保留）、选区的 path/rect、路点/单位和顺序说明、对话发送提示。新增服务端清空上下文动作，重建干净准备请求并清除旧规格/报告/消息/日志，保留资产、轨迹和任务交付；请求版本与忙碌保护延续。默认工艺复用无客户端 RequirementUnderstander 模板模式，按当前 AML/轨迹构建可直接交给生成器的规格，无额外需求 LLM 调用。工艺摘要独立 presentation.py，中文化变量引用并保留自定义目标语义，完整契约不变；旧冗长消息只在返回视图时转换。

三维预览取消底部按钮，改为指针捕获拖动旋转、右键/中键平移、滚轮缩放，支持方向键/加减号/Home；鼠标事件合并、单帧请求串行，以最新目标视角跟进。后端增加有界相机平移，不调用物理步进或 PLC 控制。

根 374 项通过；Chrome 使用隔离服务验证清空后刷新仍为空、默认工艺准备→生成替身→真实 MJCF 构建/渲染、鼠标拖动/平移/滚轮和键盘复位、无按钮及 390px 布局，零页面异常。截图 E:/chagpt/308sjk-svg-workbench/interaction-{dialogue,scene}.png。已更新 9 个已有预览并逐一核对嵌入数据不变，重启 :8765；实际服务的新控件、既有预览和相机平移/越界拒绝冒烟通过。未清空用户真实会话、未调用真实模型或运行 PLC，未 push。

## 2026-10-20 前端接入 lx OpenPLC 执行链路

增加 runtime_control 薄适配，第三页通过同一 lx 部署服务调用最终 XML 部署并启动、当前 Runtime 程序启停、状态轮询和日志。部署前基于冻结 AML/规格/组件契约/最终场景重验便宜闸门，保留原 XML 和产物列表；后台异步执行、跨会话串行、版本与归属保护，操作记录可刷新恢复，中断结果不冒充成功。lx serve 补充启停与日志端点，共享操作锁；清理上轮 deploy_result 后再部署，失败步骤原样回传，成功结果附精确 XML hash；OpenPLCClient 屏蔽系统代理并沿用现有执行方法，非默认登录通过后端环境指定。

根 383 项通过；隔离 HTTP 覆盖 lx 端点/锁/错误与回执、实际冻结交付 R6 检查、工作台权限/版本/当前性和异步失败。Chrome 实际点击启动→停止→部署并读取日志，验证状态与步骤、桌面固定侧栏及移动布局，零页面异常；执行器和 OpenPLC 为替身，MJCF 真实渲染。截图 E:/chagpt/308sjk-svg-workbench/runtime-control.png。VM :8080/:502 不可达，未实际部署或启停 PLC；SVG 速度指令与现有位置桥接的适配范围待负责人确认，仿真仍显示未启动。未 push。

已核实所有真实会话空闲，重启工作台 :8765（PID 29804），启动 lx 部署服务 :8600（PID 30280，后端目标沿用 VM 192.168.12.131）。实际冒烟：/health 正常、页面四个操作控件已加载、工作台状态回传 service=online / runtime=UNREACHABLE，两个 stderr 日志为空。未发送任何真实部署/启停请求，用户资产、会话和模型配置保留。

## 2026-10-20 修复部署入口并完成实际部署

用户报告 No module named pipeline。独立解释器以 python -m src.agent.web 启动可复现，agent.web 测试入口因 sys.path 加 src 没暴露；deployment_preflight 改用 consistency_check 已加载的 xml2st，不引入新搜索路径。双入口子进程经真实临时 HTTP 服务部署到替身，覆盖实际启动路径；当前用户任务冻结输入离线校验通过。

真实重试在 /upload-program 超时，文件已到容器但状态/RPC 调用未完成；同期间前端轮询仍在访问 Runtime。新增独立 Runtime 访问锁，部署/启停互斥且等待在途只读请求，期间状态返回 BUSY、日志拒绝插入。run_deploy 登录后先停现有程序再上传，保留后续真编译/启动；RequestException 也形成结构化 FAILED 结果，finally 关闭会话。容器源码证实运行日志路由为 /runtime_logs，客户端同步修正并检查 HTTP 状态。

回归中发现 tests/test_experience.py 的假验收失败会经 _runtime_probe 实际部署诊断程序并恢复种子，曾将在线 Runtime 切回 plotter3axis 种子；已向用户说明并显式隔离此测试的探针。最终根 393 项通过（58.94s），期间真实 /deploy 请求数保持 3 次不变，之后单独重新部署用户 XML。该文件未改写：任务 svg_web_a4b0f72c36e5ea28045e999cebc11012，XML→920 行 ST、上传757835.st 的首次成功后再恢复当前任务，最终实际停止/上传/matiec 编译/启动通过、Runtime RUNNING、回执 SHA 与原 XML f23d14a0106b663b62c2a9a7f547775c065ac294cda2905485bc0f9a106f9bec 一致。最终证据保存该任务 runtime-deploy.json（以证据中的最终上传文件号为准）。

后续边界：当前生成 XML 的 prog_id 为未定位局部变量，%QW20=0，未修改冻结交付；未发送绘图按钮命令，未启动 MuJoCo 运动、未做行为验收。工作台重启 PID16076、部署服务重启 PID1992，API 配置和会话保留；未 push。

## 2026-10-20 SVG 转换为位置指令

新增 position_control.py：AML 原26通道、地址和量程保留，添加三轴位置输出，驱动参考连接从速度切到位置，速度仅诊断；添加独立默认 CSP AML。未扩展四份冻结契约或 axis_object.io。自动转换读取原 dv.Setpoint 接线，复用同一位置量（含已有点动选择），INT 直连/REAL量化，追加使能/急停/故障位置保持门控，保留原 FB、SVG 步序与 MC 参数；身份在新 XML 绑定 %QW20。拒绝未知布局/冲突，旧 final 不修改。enable_position 只在会话版本有效、空闲且原任务输入匹配时转换已有交付，否则仅准备新资产/规格；独立新任务记录来源。转换用种子走编排器，无 LLM 调用；后续新生成及修复增加CSP提示/闸门，部署前同样复核。CLI 随位置 AML 启用。

根402通过，新增9项覆盖资产不重排、位置源类型/门控、旧逻辑保持、错误速度/HMI源拒绝、地址冲突、幂等、无模型转换与新场景映射。默认资产测试更新，执行接口回归继续用显式旧 AML 的26通道种子，避免改变测试含义。当前真实会话 c6d7ce... 由 a4b0f72c 转出新任务3ee73213073324be934cd138d24d6b56，旧 XML f23d14a... 哈希未变；新 AML29 IO、位置%QW3/4/5、反馈%QW0/1/2，MJCF真加载、runtime_ready=true。真实939行ST、上传453356.st、matiec与启动PASS、程序身份2、回执匹配。LX原生PlcLink单周期读取位置输出并将原反馈值写回成功，无启动/绘图线圈写入；证据position-deploy.json和position-bridge-check.json留任务内。

实际Chrome验证位置模式按钮/两项交付、中文阶段、Runtime运行中身份2、映射就绪、真实场景图和移动布局，截图 E:/chagpt/308sjk-svg-workbench/position-workbench.png。工作台重启PID8784；未接网页物理运动控制、未跑完整SVG几何验收，未push。


## 2026-10-20 master 更新与仿真验证 Studio

先stash保护本地全部修改和未跟踪工作，再将origin/master01c489c合入gc（merge2f43211），恢复stash并逐处合并11份冲突；备份stash保留。采用远端的完整XML/进度跟踪/轴实例POSWIN检查/编译环境检查，保留本地位置编码闸门、证据与回放、代理规避及非默认Runtime凭证。旧“异构POSWIN跳过”测试调整为逐轴参数不匹配应拒绝。task_inputs统一读取requirements规格并兼容旧路径，新增任务规格冻结AML、地址、轴参数。

UI收敛两页，生成流程保留；Studio复用hmi_gen和csk判定器，手动BOOL输入由Motion线程队列串行执行，触发150ms、使能保持、操作前身份校验，停止复位并回读。其他会话/输出信号/自动运行写请求被拒绝。原始PLC采样与实际MuJoCo关节采样分离保存，PATH使用真实物理反馈，不能用整数寄存器检验0.01精度。验收记录绑定XML/scene/io/spec哈希，手动/中断不标PASS。配对导入外部SceneSpec和PLCXML，可选原规格，校验后建立独立任务；无规格仅调试；失败目录清理、既有产物不动。任务选择器可返回生成交付。

实际旧任务Z场景11mm与新主干10mm要求冲突；只对完全匹配旧内置契约的资产派生新任务/快照，保持XML字节及旧final，记录migration来源。解决状态轮询返回BUSY导致错误拒绝启动；失败提示直接出现在仿真页。长时间轮询浏览器出现ERR_NO_BUFFER_SPACE，改为HTTP/1.1复用并在错误时关闭连接；150次快照2.39s，最终浏览器无异常。

测试591通过、6Qt因缺PySide6跳过，四XML静态通过。runtime测试退出仍有既有pymodbus异步服务任务销毁提示，未影响测试结果或网页Motion（其不启动桥服务端）。实际手动及全程绘图、228PLC/2533物理样本、导入与切换、1472×825约60FPS和390px均通过。当前PLC完成绘图但PATH首路点未满足规格，保留FAIL与原始证据，没有调宽容差或修改交付。证据E:/chagpt/308sjk-studio-integration；后台:8765；未push。


## 2026-10-20 重生成SVG空白绘图恢复

对照用户新任务ba0a717a与旧可绘图交付、stash中的旧Motion，数据桥仍按实际PLC命令运行。新PLC的pl_tx/y/z是INT，步进在三轴调用前读取Done，并每轴Done清持续触发；迹线只到35/53/10，4.55s假完成且ink0。提示summarize_for_prompt/goal_text仍将0.01路点说成INT，与高精度生成要求冲突；修正提示、扫描顺序说明，并在LLM生成/修复闸门拦截旧Done和整数目标，保留显式种子转换语义。加实际无笔迹完成检查。已知P22/P10优先对照。

标准生成稿在新任务修复：完整匹配嵌套IF，Done清理受draw_active保护，逐字使用当前冻结REAL步表与各段实际点数、先抬笔、调用后到位推进，复用原FB定义和高精度编码；补POSWIN无初值、SCAN_TZ、非必有rel变量支持，未知布局和重复转换拒绝。原final未改，来源记conversion.json。新ba95449d静态与实际matiec/部署通过、31秒/1503点/三笔完整绘图、回中心，精度/覆盖独立对参考计算约0.25mm(P95)。本次未调用模型，原0555...哈希不变、新ebcf...，不回退已有Studio/模型逻辑。

回归两次遇到不同HTTP测试10053；定位工作台拒绝POST及lx成功start路径都可能未读请求体即关闭Windows连接，RST使调用方收不到响应。修成有界读取再鉴权/路由，部署读取同一body；没有重试非幂等操作。对应局部50项与最终完整595项通过，6Qt缺PySide6跳过，既有pymodbus服务退出任务提示仍存在。新增4项覆盖重生成拦截、真实标准结构恢复变体、假完成失败和REAL提示，连接拒绝测试扩大到10次。实际浏览器3笔轨迹/状态/画布与错误检查通过。严格PATH第2步仍因实际到位窗口未通过，报告未改、容差未放宽。证据E:/chagpt/308sjk-svg-recovery；最后重启后台8184、lx26844并重部署同稿，模型停止。未push。

## 2026-10-20 部署循环与新稿启动恢复

根因记录在gc设计§13.15。workspace/deploy_result.json与VM实际ST确认prog_id普通声明和AT声明重复，matiec失败后手动start只复活旧二进制，LAST_DEPLOYMENT为空；保留精确回执检查。xml2st按POU跨块/casefold检查重复名，生成提示补唯一声明。runtime_deployment单独留存失败，不因start/stop覆盖，UI禁用未匹配当前XML的模型运行按钮；失败优先于STOPPED提示，避免继续引导启动旧程序。

联测在自动reset与run同时开启时卡MC_POWER，改成150ms复位后再run，使能不再被复位控制字打断。当前LLM标准变体同型Z/REAL目标/嵌套Busy步进得到明确适配，首次转换的REAL_TO_INT赋给REAL被真实matiec拦截，修复为按声明类型保留REAL、旧INT目标仍显式转换。新增回归对应覆盖此错误；边沿自引用与Done清持续触发也加生成检查。按当前冻结轨迹派生修复任务，原final不改，不改生成ST。391c1466实际一次部署自动启动后直接模型运行32.27s完成1821点，PATH严格精度仍FAIL，报告保留。

603通过、6Qt跳过；浏览器验证真实按钮/回执以及启停不覆盖失败日志，无页面异常，证据E:/chagpt/308sjk-deployment-cycle。未push。

## 2026-10-20 按要求回退 PLC 生成部分

通过reflog和stash定位最新拉取前快照ad1876b/父91f7dc6，不能仅用父提交而丢掉gc本地SVG/CSP工作。恢复旧skill原文、纯FB库、prompt装配以及generate/repair错误回灌；旧temperature=0.3。移除新CSP提示选择、额外扫描策略和hybrid分支，保留API/model配置、证据、截断保护、验证闸门与test_plan兼容。Studio、orchestrator新产物布局、部署与物理运动不修改，既有产物保留。

备份与前后哈希在E:/chagpt/308sjk-plc-generation-rollback。同步测试改与同一冻结FB库比对接口，库没有PROGRAM，直接解析接口。新增3项回退回归，恢复前prompt hash 4f3d3034...被锁定，CSP也不切换短提示；真实返回替身覆盖生成/修复与证据。全tests/runtime606通过、6Qt跳过，既有pymodbus任务退出提示仍在。未真实调用模型生成或部署，不宣称PLC质量已在线验收。

## 2026-10-20 PROGRAM 定位变量布局部署修复

错误签名：matiec invalid input variable(s) declaration / unexpected token after VAR_INPUT，实际行包含AT；VAR_OUTPUT同类。不同于P02的FB内CONSTANT，原因为PROGRAM定位IO放在input/output形参列表。新增独立normalizer，在模型generate/repair提取后将PROGRAM的带address变量移至localVars，FB形参及ST逻辑不改，保留变量元数据和块属性，无改动返回原字串；raw response照常留存。xml2st静态拒绝所有非localVars中的AT，错误源名与合法改法进入反馈，种子不自动改写。旧prompt/生成策略未改。

当前30个声明机械迁移，新任务aee93a07；源8a0180c4不改，新旧所有POU本体相同。真实compile116786.st通过，deploy/start成功，Runtime RUNNING、hash213982e4...一致、Modbus prog_id=2。未启动模型运动。615通过，6Qt跳过；证据E:/chagpt/308sjk-program-io-deploy-fix。未push。

## 2026-10-21 新生成产物的物理验收闭环

不再以离线生成绿当作交付成功。网页采用WorkbenchOrchestrator接现有lx部署与Motion，失败回原修复循环，只有同源码实际物理PASS才final。环境错误不归因成PLC故障。证据按XML/场景/映射/规格/轨迹/采样绑定；SVG逐展开路点及实际接触线段的最大偏差判定，poswin不改。原system提示哈希仍4f3d3034...，完整XML及0.3策略保留。

实际新SVG暴露共享NOT draw_active分支被正则只认首轴、R8检查太晚丢失修复上下文、matiec只返回行号无法准确修类型、标准INTERP小窗口离散越过目标后不Done、伺服转角过冲等问题。修正对应检查、补源码行反馈和标准插补收尾；伺服根据实际位置命令差分与模型质量矩阵做惯性及采样时序补偿，未改资产/未直接赋物理位置。真实求解器测试与实际指令回放验证，线上仍逐轮严格裁定。

显式重试保持相同冻结输入才继承失败候选并重新全部验收；物理检查回退时保留较好源码继续修复。首份新折线a68e595f...全部PASS，871物理/350接触采样、6路点，最大偏差0.1047倍容差；双路径f91631c4...全部PASS，1522物理/531接触采样、9路点、12.938s，最大偏差0.0771倍容差。两份均经历真实失败与修复，保留各轮原始证据。

后续实测补齐：XML body重复END_FUNCTION_BLOCK/END_PROGRAM仅移除冗余页脚，不改工艺，源模型response保留；完整POU/VAR框架在便宜层拒绝。仅pl_step无draw_active的序列也拦截无条件清触发。几何PASS而PATH失败的候选不应继续改已通过插补器，反馈改成到位后才计时、未到位归零、保持旧目标至少150ms再换步；原先运动开始即累加pl_dwell，即使上限8改15也无实际稳定等待，导致抬笔点未到位即横移。此处修复由LLM根据实际报告完成，最终全PASS。

完整639通过、6Qt跳过；Node语法及git diff --check通过（已有pymodbus退出提示不影响结果）。证据E:/chagpt/308sjk-new-generation-validation/run_20261021_000657，旧system哈希未变，既有交付不覆盖，未push。任意SVG有限轮次收敛仍不作保证，未通过产物不发布final。
