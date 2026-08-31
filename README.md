# SIMENS PLC — Agent 生成 61131-10 代码并闭环仿真

课题目标：开发一个 agent，理解用户工艺需求 → 生成 **IEC 61131-10（PLCopen XML）** 代码 →
自动部署到软 PLC 运行 → 变量接入 Isaac Sim 虚拟仿真 → 通过仿真反馈迭代优化代码。

本仓库当前里程碑（`lx` 分支）：**XML → CODESYS 部署链路** 已打通，附一个
每秒自增 1 的计数器冒烟测试，用 UaExpert 通过 OPC UA 观察验证。

## 链路

```
src/plc/counter.xml (61131-10)
        │
        ▼
src/pipeline/run_deploy.py ── 无头拉起 ──► CODESYS.exe --noUI --runscript
        │                                     │
        │                          src/codesys/deploy_project.py
        │                          建工程→导入XML→编译→下载→运行
        ▼                                     ▼
workspace/deploy_result.json          CODESYS Control Win V3 (软PLC)
（步骤成败+错误，回喂 agent）                │ OPC UA :4840
                                            ▼
                                     UaExpert / 未来的 Isaac Sim 桥接
```

## 快速开始

```bash
pip install -r requirements.txt          # 仅测试依赖 pytest
python -m pytest tests/ -v               # ① XML 校验器单测（本机即可跑）
python src/pipeline/validate_xml.py      # ② 校验交付物并查看提取出的 ST
python src/pipeline/run_deploy.py        # ③ 部署到 CODESYS 并运行（需先装 CODESYS）
```

③ 成功后，UaExpert 连接 `opc.tcp://localhost:4840`，观察 `PLC_PRG.cnt` 每秒 +1。

详细安装与排查见 **[docs/部署手册.md](docs/部署手册.md)**。

## 目录结构

```
src/
  plc/counter.xml        # PLCopen XML 交付物（agent 未来产出的形态）
  codesys/deploy_project.py  # CODESYS 内部执行的 IronPython 部署脚本
  pipeline/validate_xml.py   # XML 结构校验 + ST 提取（标准库实现）
  pipeline/run_deploy.py     # 部署编排器：定位 CODESYS、无头执行、汇总结果
tests/                   # pytest 单测（含反例）
docs/                    # 部署手册、总体实施方案
workspace/               # 本地生成物（.project / deploy_result.json，不入库）
```

## 设计约定

- **XML 是唯一源码**：agent 只产出 PLCopen XML；工程装配（设备/任务/符号配置）由部署脚本负责
- **ST 本体子集**：POU 一律用 ST 语言本体（合法 61131-10 子集），避开图形本体的转换难题
- **POU 名固定 `PLC_PRG`**：与 CODESYS 设备模板的任务调用保持一致，导入即被调度
- **结果可回喂**：`deploy_result.json` 的 `errors` 数组设计为可直接作为 agent 的纠错输入
