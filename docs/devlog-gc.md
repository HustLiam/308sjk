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
