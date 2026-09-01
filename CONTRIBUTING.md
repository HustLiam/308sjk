# CONTRIBUTING — 协作开发指南

> 本文档回答"作为本项目成员，我该怎么干活、怎么和别人配合"。治理原则见《总体实施方案》§8（工程治理），实时状态见 `docs/协作看板.md`；本文是两者的**操作手册**——规则条文以主方案 §8 为唯一权威，本文不复写，冲突时以主方案为准。
>
> 三人分工：**lx = PLC 执行侧** ｜ **csk = 仿真验证侧** ｜ **gc = 智能体与闭环侧**

---

## 1. 先读什么（文档地图）

| 文档 | 权威域 | 什么时候读 |
|---|---|---|
| `docs/总体实施方案.md` | 全局架构 + 工程治理（§8） | 入项必读；做架构决策前 |
| `docs/lx-PLC代码生成与执行引擎详细设计.md` | PLCopen XML 生成契约、链路 B | 写生成器 / 改 xml2st / 加场景前 |
| `docs/csk-仿真环境与IO闭环详细设计.md` | SceneSpec / USD / 判定引擎 / 链路 A | 写场景 / 改判定 / 涉及 lockstep 前 |
| `docs/gc-需求理解与闭环编排详细设计.md` | requirement_spec / 生成智能体 / 编排器 | 写需求 Schema / 编排逻辑前 |
| `docs/协作看板.md` | 三方进度 / 问题 / 待配合 | **每次开工前**、每次合入前 |

红线：契约细节只在各侧权威文档维护，其他地方只写"文档名 §节"引用，**禁止复写**（防止两处漂移）。

## 2. 环境搭建（首次，约 15 分钟）

```bash
git clone https://github.com/HustLiam/308sj.git && cd 308sj
git checkout lx          # 或你的工作分支
pip install -r requirements.txt

# 验证 ①：转换器单测（无需任何运行时，必须全绿）
python -m pytest tests/ -v

# 验证 ②：看一份 PLCopen XML 转出的 ST
python src/pipeline/xml2st.py src/plc/counter.xml --check

# 验证 ③（可选，lx 侧联调才需要）：起 OpenPLC 运行时
docker run -d --name openplc -p 8080:8080 -p 502:502 fdamador/openplc
python src/pipeline/run_deploy.py && python src/pipeline/verify_modbus.py
```

三个验证分别对应三层能力：纯转换、纯校验、全链路。csk 侧另需 Isaac Sim 4.5 与 matiec 工具链（见其文档 §3.1 / §6）。

## 3. 分支模型与合入流程

```
feat/<侧名>-<主题>   →   PR（至少 1 名相关方评审）   →   master（受保护）
```

1. **从 master 拉功能分支**：`feat/lx-consistency-checker`、`feat/csk-usd-builder`、`feat/gc-spec-schema` 这样命名，一个分支只做一件事；
2. **提交信息**：`模块: 变更摘要`（历史风格，如 `xml2st 防信息丢失：…`、`主方案去 CODESYS 化：…`）；
3. **提 PR 前自检**（DoD 清单为操作视图，规则源为主方案 §8.4）：
   - [ ] `python -m pytest tests/ -v` 全绿；
   - [ ] 涉及场景的：对应 `scenario_*.py` 验收通过（7 个已验收 XML 是契约回归样例，改动契约必须全过）；
   - [ ] 涉及契约/架构的：四份文档同一 PR 内同步修订；
   - [ ] `docs/协作看板.md` 本人区块已更新；
4. **评审规则**（主方案 §8.1 RACI 表）：常规改动 1 名相关方评审即可；**契约文件**的 PR 必须三方知情，评审人须含契约拥有方；
5. **禁止直推 master**；禁止"顺手改契约"——契约变更走 §5 流程。

## 4. 日常协作节奏

- **开工前**：看 `docs/协作看板.md`——有没有人向你提了待配合？共同议题有没有变化？
- **会话中**：卡住超过半天且依赖他人的，立刻在看板"待配合"区 @ 对方并写清依赖内容，不要闷头等；
- **收工前**：更新看板本人区块（进度状态、新问题、新请求），再合入代码；
- **跨方接口对接**：先在共同议题区对齐字段结构（冻结成契约），再各自写代码——避免"两边各写完再对"的返工模式。

## 5. 契约变更流程（RFC）

四份冻结契约（requirement_spec Schema / PLCopen XML 生成契约 / io_map 结构 / acceptance 四类结构，见主方案 §8.3）的任何修改：

```
起草 RFC（动机 / 影响面 / 迁移方案）
  → 看板共同议题区登记，@三方
  → 三方评审通过（48h 内未回复视为需要线下拉齐）
  → 同一 PR 内：改契约 + 改四文档 + 记 docs/changelog.md + 契约版本号升级（规则见主方案 §8.3）
```

## 6. 代码约定（按侧速查）

**通用**：Python ≥3.10；改动带测试；不引入未登记依赖（进 `requirements.txt` 前先在看板提一句）。

**lx 侧（PLC）**：
- 新增场景 = `src/plc/<场景>.xml`（61131-10，符合 lx 文档 §3 契约）+ `src/pipeline/scenario_<场景>.py`（验收脚本）+ 单测如涉及转换器分支；
- XML 是唯一源码，**永远不要手改 `workspace/program.st`**；
- Modbus 线圈写入必须经 `SafeCoilIO`，禁止裸 `write_coil`。

**csk 侧（仿真）**：SceneSpec 是封闭类型枚举，新组件先入组件库再对外；判定引擎保持确定性（不引入 LLM 判定）。

**gc 侧（智能体）**：生成器产出的 XML 必须先过 `xml2st --check` 本地闸门再进流水线；判定结论只消费 csk 的 verdict，不自行复判。

## 7. 出问题了找谁

| 问题类型 | 找谁 | 通道 |
|---|---|---|
| XML 校验/编译/OpenPLC/Modbus | lx | 看板待配合 @lx |
| Isaac/USD/SceneSpec/判定引擎 | csk | 看板待配合 @csk |
| Schema/生成器/编排器 | gc | 看板待配合 @gc |
| 契约争议 | 三方 | 共同议题区 + 线下拉齐 |

---

## 附：一次典型协作的完整走查（示例：新增"气缸顺序控制"场景）

1. gc 出该场景的 requirement_spec（含 io_list 与验收准则）；
2. lx 依契约写 `cylinder_seq.xml` + `scenario_cylinder.py`，链路 B 验收通过，看板更新；
3. csk 依同一 io_list 做 SceneSpec→USD，链路 A lockstep 跑通；
4. 双链路 trace 比对（lx 与 csk 在看板共同议题登记结果）；
5. gc 的编排器把该场景纳入闭环回归，全绿后冻结。

每一步的产出都落在 git 里，任何结论可回溯——这是本项目对"可复现"的基本要求。
