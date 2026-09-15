# IEC 61131-10 PLCopen XML 交换格式（域条目 PX-xx）

本侧视角：**生成器的目标格式**与静态闸门的校验对象。结构权威 = lx 契约②
（xml2st 实现，R1 闸门）；以下登记生成时必须成立的形态。

- **PX-01**（文档结构）唯一代码交付格式为 PLCopen XML（`<project>` 根，含
  `<pou>` 列表）；serve 部署端要求 body 以 `<?xml` 声明开头——LLM 产物从
  `<project>` 片段提取后需补回声明（坑库 P15）。出处：pipeline.extract_xml。
- **PX-02**（POU 结构）每个 `<pou>` 含 `<interface>`（inputVars/inOutVars/
  outputVars/localVars 四块，定位变量可出现在任一块）与 `<body><ST>`（xhtml
  CDATA 包裹的 ST 本体）。定位变量=带 `address` 属性的 variable；不带 AT 的
  是 POU 内部状态，不参与对外对账。出处：xml2st.parse；R2 实现。
- **PX-03**（身份）程序必须声明 `prog_id AT %QW20 : INT`（初值=场景号）并在
  ST 本体首扫描周期写入编号——验收脚本/编排器经 require_program 读 %QW20
  校验运行时程序身份。契约② v1.1（changelog「契约② v1.1」行）。
- **PX-04**（元信息豁免）prog_id 属契约元信息变量，R2 对账时豁免（不算
  io_list 未声明的外来变量）。出处：consistency_check（META_VARS）。
- **PX-05**（三方一致）XML 定位变量名 ≡ io_list.name ≡ scene.io_map.plc_var
  逐字一致（R2 双向）；类型位宽匹配 R3（BOOL↔BOOL、INT↔INT/UINT/WORD）；
  地址不冲突 R4；AML 通道地址逐字遵循 R6。出处：consistency_check.py。
- **PX-06**（生成纪律）XML 是唯一源码——`workspace/program.st` 是转换产物
  永不手改；生成产物落 runs/<task>/iter_NNN/。AGENTS.md 硬性规则 2。
- **PX-07**（命名）ST 定位变量名即 io_list 名（全工程唯一、合法标识符
  `^[A-Za-z_][A-Za-z0-9_]*$`）——是三方对账的对齐键，改名即破约。
  出处：spec_validator S1；R2。
