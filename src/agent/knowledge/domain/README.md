# 领域知识库（domain KB）——gc 生成侧的标准语义打底

> 登记出处：协作看板 gc 待配合【负责人指令 2026-09-09 lx 代登记】。
> 分工：pitfalls=失败知识（打补丁）/ patterns=已验收范式（选卡）/ **本目录=标准语义打底（防幻觉）**。
> 原则：不复写契约（lx/csk 文档只引 §节号）；每条有可运行出处（已验收 XML / 实现代码 / 实证战役）；
> 条目带编号（`ST-xx`/`PX-xx`/`AML-xx`/`MC-xx`），供 skill 节选、attribution 引用、评审定位。
> 消费：build_messages 按任务类型选域节选注入；归因 diagnosis 可标注条目号。

| 文件 | 域 | 权威交叉引用 |
|---|---|---|
| st.md | IEC 61131-3 ST 子集与边界行为 | lx 契约②（lx 文档 §3）；故障库 P01~P23 |
| plcopen_xml.md | IEC 61131-10 交换格式 | src/pipeline/xml2st.py（R1 实现）；已验收 XML |
| automationml.md | AutomationML / IEC 62714 | src/agent/aml_parser.py；examples/aml 双基准 |
| motion_control.md | PLCopen Motion Control | 《运动控制代码生成方案》§2（v4.0）；motion3axis.xml |
