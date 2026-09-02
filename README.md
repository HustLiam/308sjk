# SIMENS PLC — Agent 生成 61131-10 代码并闭环仿真（OpenPLC 运行时）

课题目标：开发一个 agent，理解用户工艺需求 → 生成 **IEC 61131-10（PLCopen XML）** 代码 →
自动部署到软 PLC 运行 → 变量接入 Isaac Sim 虚拟仿真 → 通过仿真反馈迭代优化代码。

本仓库（`lx` 分支）为 **OpenPLC 运行时版本**：全链路纯 API、零 GUI、无许可证限制。
CODESYS 版本的历史实现见 git 历史（保留作标准符合性验收参考）。

## 链路

```
src/plc/motion3axis.xml (61131-10, 唯一源码)
        │ ① xml2st 校验+转换（不合法直接拒，错误回喂 agent）
        ▼
workspace/program.st
        │ ② openplc_client (HTTP) 上传 → matiec 编译 → 启动
        ▼
OpenPLC v3 运行时（Docker / WSL2 / 远程 Linux）
        │ ③ Modbus TCP :502（%QX→线圈 %QW→保持寄存器）
        ▼
scenario_motion3axis.py（验收）/ 未来的 Isaac Sim 桥接
```

## 快速开始

```bash
pip install -r requirements.txt
python -m pytest tests/ -v                # ① 转换器单测（无需运行时）
python src/pipeline/xml2st.py src/plc/motion3axis.xml   # ② 看转换出的 .st
# ③ 启动运行时后（docker run -d --name openplc -p 8080:8080 -p 502:502 fdamador/openplc）：
python src/pipeline/run_deploy.py                  # 部署+编译+启动
python src/pipeline/scenario_motion3axis.py         # 三轴定位闭环验收
```

HTTP API 方式（agent 闭环的部署端点）：

```bash
python src/pipeline/serve.py &            # 起服务
curl -X POST http://127.0.0.1:8600/deploy --data-binary @src/plc/motion3axis.xml
```

## 目录结构

```
src/
  plc/motion3axis.xml       # 61131-10 交付物（agent 未来产出的形态）
  pipeline/xml2st.py        # 校验 + XML→ST 转换（纯标准库）
  pipeline/openplc_client.py# OpenPLC v3 HTTP 客户端
  pipeline/run_deploy.py    # 部署编排器（结果 JSON 供回喂）
  pipeline/serve.py         # POST /deploy HTTP 服务
  agent/                    # gc 智能体与闭环侧（spec 校验/生成器/一致性/编排）
schemas/requirement_spec.schema.json  # 契约① Schema 草案（gc 拥有，待三方评审冻结）
examples/specs/             # requirement_spec 基准示例（sorting，对齐已验收 XML）
runs/                       # 编排器每轮产物（iter_NNN/final，全量入 git）
tests/                      # pytest 单测
docs/                       # 方案与详细设计文档
workspace/                  # 本地生成物（不入库）
```

## 协作开发

分支模型（各成员常驻分支 lx/csk/gc）、本地开发日志、master 合入许可制、契约变更（RFC）与按侧速查约定见 **[docs/协作开发指南.md](docs/协作开发指南.md)**；三人实时进度与待配合事项见 `docs/协作看板.md`。

## 设计约定（agent 生成契约，摘要）

- **XML 是唯一源码**：只产出 PLCopen XML，`.st` 由 xml2st 机械推导；对外接口 = `AT %QX/%QW` 定位变量（统一 %Q 区），编译错误回喂 agent 纠错
- 完整契约（ST 子集、位宽表、显式拒绝清单）的权威定义见 `docs/lx-PLC代码生成与执行引擎详细设计.md` §3
