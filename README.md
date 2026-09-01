# SIMENS PLC — Agent 生成 61131-10 代码并闭环仿真（OpenPLC 运行时）

课题目标：开发一个 agent，理解用户工艺需求 → 生成 **IEC 61131-10（PLCopen XML）** 代码 →
自动部署到软 PLC 运行 → 变量接入 Isaac Sim 虚拟仿真 → 通过仿真反馈迭代优化代码。

本仓库（`lx` 分支）为 **OpenPLC 运行时版本**：全链路纯 API、零 GUI、无许可证限制。
CODESYS 版本的历史实现见 git 历史（保留作标准符合性验收参考）。

## 链路

```
src/plc/counter.xml (61131-10, 唯一源码)
        │ ① xml2st 校验+转换（不合法直接拒，错误回喂 agent）
        ▼
workspace/program.st
        │ ② openplc_client (HTTP) 上传 → matiec 编译 → 启动
        ▼
OpenPLC v3 运行时（Docker / WSL2 / 远程 Linux）
        │ ③ Modbus TCP :502（%QX→线圈 %QW→保持寄存器）
        ▼
verify_modbus.py（冒烟验证）/ 未来的 Isaac Sim 桥接
```

## 快速开始

```bash
pip install -r requirements.txt
python -m pytest tests/ -v                # ① 转换器单测（无需运行时）
python src/pipeline/xml2st.py src/plc/counter.xml   # ② 看转换出的 .st
# ③ 启动运行时后（见 docs/部署手册-OpenPLC.md）：
python src/pipeline/run_deploy.py         # 部署+编译+启动
python src/pipeline/verify_modbus.py      # Modbus 读 cnt，确认每秒 +1
```

HTTP API 方式（agent 闭环的部署端点）：

```bash
python src/pipeline/serve.py &            # 起服务
curl -X POST http://127.0.0.1:8600/deploy --data-binary @src/plc/counter.xml
```

## 目录结构

```
src/
  plc/counter.xml           # 61131-10 交付物（agent 未来产出的形态）
  pipeline/xml2st.py        # 校验 + XML→ST 转换（纯标准库）
  pipeline/openplc_client.py# OpenPLC v3 HTTP 客户端
  pipeline/run_deploy.py    # 部署编排器（结果 JSON 供回喂）
  pipeline/verify_modbus.py # Modbus 冒烟验证
  pipeline/serve.py         # POST /deploy HTTP 服务
tests/                      # pytest 单测
docs/部署手册-OpenPLC.md    # 运行时安装与排障
workspace/                  # 本地生成物（不入库）
```

## 设计约定（agent 生成契约）

- **XML 是唯一源码**：只产出 PLCopen XML；可执行 .st 由 xml2st 机械推导
- **ST 本体子集**：POU 一律 ST 语言本体，类型限 PROGRAM / FUNCTION_BLOCK / FUNCTION
- **主 POU 固定 `PLC_PRG`**：CONFIGURATION 模板按名挂载任务（20ms 周期）
- **对外接口 = AT 地址变量**：`%QX0.1`（线圈）、`%QW0`（保持寄存器，DINT 占 2 个）；
  不带 AT 的变量为内部状态，不进 Modbus 表
- **编译错误回喂**：matiec 日志写入 `workspace/deploy_result.json` 的 errors，可直接进
  agent 纠错 prompt
