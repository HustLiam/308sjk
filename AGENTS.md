# 308sjk 工作区指令（ZCode Agent 必读）

## 项目与分工

AI 驱动的 PLC 控制代码自动生成与仿真验证闭环系统：AML 设备描述 + 自然语言需求 →
requirement_spec → PLCopen XML（唯一代码交付格式）+ 场景描述 JSON → 多层闸门验证 → final 冻结。
三人协作，常驻个人分支：**lx=PLC 执行侧 / csk=仿真验证侧 / gc=智能体与闭环侧**；master 合入走许可制。

改代码前先读：`docs/协作开发指南.md`（操作手册）；架构决策看 `docs/总体实施方案.md` §8（治理权威）；
各侧细节在 `docs/{lx,csk,gc}-*.md`；实时状态在 `docs/协作看板.md`（只改本人区块）。

## 目录与职责边界

| 目录 | 职责 | 归属 |
|---|---|---|
| `src/agent/` | ⓪ AML 解析 / ① 需求理解 / ②a 生成器 / ②b scene_gen / 编排器 orchestrator / 记忆与归因 | gc |
| `src/pipeline/` | xml2st 契约转换 / run_deploy 部署 / serve.py(:8600) / scenario_*.py 在线验收 | lx |
| `src/plc/` | 已验收场景库（prog_id：motion3axis=1、plotter3axis=2、plotter_circle=3，顺延） | lx |
| `src/agent/knowledge/` | 故障库 pitfalls.json + 模式库注册表（契约性质，改动走 RFC） | gc |
| `schemas/` `examples/` | 四份冻结契约的 Schema 与基准示例 | 三方 |
| `runs/` | 编排器产物（入 git）；`workspace/` 本地工作区（gitignore，勿提交） | — |

## 常用命令（仓库根目录）

```bash
python -m pytest tests/ -q                              # 全部单测（当前 152，改动必须全绿）
python src/pipeline/xml2st.py src/plc/<场景>.xml --check   # 静态契约校验（毫秒级）
# 在线链路（需 serve.py 与 OpenPLC 运行时在线，见"环境"）：
python src/pipeline/run_regression.py                   # L1 静态+L2 单测+L3 在线验收
python -m src.agent.chat --modbus-host 192.168.12.131   # 对话式闭环（用户入口）
python -m src.agent.orchestrator <spec.json> --seed src/plc/<场景>.xml \
    --deploy --acceptance --scenario <场景> --modbus-host 192.168.12.131   # 编排器
```

## 硬性规则

1. **io_list 是三方唯一源头**（ST 定位变量 ≡ io_list ≡ io_map）；地址以 AML 通道表为准（R6 + 自动纠偏）。契约①~④的修改必须走 RFC（三方知情 + 四文档同步 + changelog）。
2. **XML 是唯一源码**：`src/plc/*.xml` 可改，`workspace/program.st` 是转换产物，**永远不要手改**；生成的 XML 落在 `runs/<task>/iter_NNN/`。
3. **闸门顺序不可跳**：xml2st → 一致性(R1~R6) → scene(R5) → matiec 真编译 → Modbus 在线验收；能在便宜层拦截的问题绝不到贵层。
4. **matiec 故障库优先**：编译/运行时错误先查 `src/agent/knowledge/pitfalls.json`（P01~P17），命中即引用修法；新坑要签名化入库。
5. Git：在个人分支工作；**未经用户明确指示不得 push**；devlog（docs/devlog-<成员>.md）永不进 master。

## 已知坑（Windows 本机）

- **系统代理劫持 localhost**：所有本地 HTTP（127.0.0.1:8600、API 调用）必须 `Session(trust_env=False)`（client.py / orchestrator 均已内置，新代码沿用）。
- **OpenPLC 运行时**：VMware VM Ubuntu 24.04 @192.168.12.131，容器 `fdamador/openplc`（Web :8080 / Modbus :502，`ssh yushanyue@192.168.12.131` 密钥直连；docker 操作需 sudo）。serve.py 部署服务在宿主机 :8600（带 `OPENPLC_URL` 环境变量启动）。
- **Modbus 跨寄存器快照非原子**：验收脚本对 v/sw 组合判定需防读偏斜（瞬态排除或位置增量判定）。
- **PLC_PRG 内禁写单扫描选通/边沿记忆逻辑**（优化器静默吞赋值，坑 P10）：多段轨迹用连续跟踪模式（skill prompt 内有模板）。

## 收工清单（每次改动）

pytest 全绿 → 相关场景验收过 → 看板本人区块刷新 → changelog 登记 → devlog 写技术说明（个人分支）。
