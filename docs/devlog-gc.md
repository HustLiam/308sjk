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
