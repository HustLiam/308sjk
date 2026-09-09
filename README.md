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
308sjk/
├── src/
│   ├── agent/                         # 智能体、知识与闭环编排
│   │   ├── aml_parser.py              # AutomationML → device_model
│   │   ├── requirement.py             # 自然语言需求理解与规格修正
│   │   ├── spec_validator.py          # requirement_spec 语义校验
│   │   ├── trajectory.py              # 方形/圆形确定性轨迹规划
│   │   ├── pipeline.py                # PLCopen XML 生成与定向修复
│   │   ├── patternlib.py              # 已验收 PLC 模式卡检索与策展
│   │   ├── scene_gen.py               # SceneSpec 与 IO 映射确定性生成
│   │   ├── consistency_check.py       # XML、io_list、IO 映射一致性检查
│   │   ├── attribution.py             # 失败归因：知识库优先、LLM 兜底
│   │   ├── memory.py                  # 修复记忆与自动学习经验库
│   │   ├── orchestrator.py            # 多闸门生成—验证—反馈循环
│   │   ├── chat.py                    # 对话式 Agent CLI
│   │   ├── client.py                  # 大模型 API 客户端
│   │   ├── config.py                  # 模型、路径与环境配置
│   │   ├── prompts/
│   │   │   ├── specgen_skill.md       # 需求规格生成 Skill
│   │   │   └── plcgen_skill.md        # PLCopen XML 生成 Skill
│   │   └── knowledge/
│   │       ├── pitfalls.json          # 已知错误签名、诊断与修法
│   │       └── patterns.json          # 自动策展的已验收模式注册表
│   ├── pipeline/                      # PLC 转换、部署与在线验收
│   │   ├── xml2st.py                  # PLCopen XML 校验及 XML→ST 转换
│   │   ├── openplc_client.py          # OpenPLC HTTP 客户端
│   │   ├── run_deploy.py              # 上传、matiec 编译及启动
│   │   ├── serve.py                   # /deploy、/status、/health 服务
│   │   ├── modbus_io.py               # Modbus IO 与程序身份检查
│   │   ├── scenario_motion3axis.py    # 三轴运动在线验收
│   │   ├── scenario_plotter3axis.py   # 正方形绘图在线验收
│   │   ├── scenario_plotter_circle.py # 圆形绘图在线验收
│   │   ├── run_regression.py          # 静态、单测、在线三级回归
│   │   └── stop_plc.py                # 停止 OpenPLC 运行程序
│   └── plc/                           # 已验收 PLCopen XML 场景库
│       ├── motion3axis.xml
│       ├── plotter3axis.xml
│       └── plotter_circle.xml
├── contract/                          # SceneSpec/组件契约及对接示例
│   ├── components.v1.1.json
│   ├── scene.spec.example.json
│   ├── example1.json
│   ├── example1_iomap.json
│   ├── 组件契约表.md
│   └── README.md
├── schemas/                           # 机器可校验的数据契约
│   ├── device_model.schema.json
│   ├── requirement_spec.schema.json
│   └── io_map.schema.json
├── examples/
│   ├── aml/                           # AML设备站示例
│   │   ├── motion3axis_station.aml
│   │   └── plotter3axis_station.aml
│   └── specs/                         # 需求规格基准示例
│       ├── motion3axis.spec.json
│       └── plotter3axis.spec.json
├── tools/
│   ├── aml_parser.py                  # AML解析命令行入口
│   ├── gen_scenarios.py               # 场景代码生成辅助工具
│   └── learn_circle.py                # 圆轨迹自动学习实验工具
├── tests/                             # 解析、生成、契约、记忆与编排单测
│   ├── conftest.py
│   └── test_*.py
├── docs/                              # 总体方案、三侧设计、协作及变更记录
├── runs/                              # 每个任务的可追溯迭代产物
│   └── <task>/
│       ├── request.json
│       ├── trajectory.json
│       ├── iter_NNN/                  # XML、ST、SceneSpec、IO映射、闸门证据
│       ├── final/                     # 通过轮次的冻结快照
│       └── summary.md
├── workspace/                         # 本地运行产物，不作为源码
│   ├── program.st / deploy_result.json / *.log
│   ├── memory/fixes.json              # 跨会话修复记忆
│   └── experience/                    # lessons.json、learning_curve.json
├── requirements.txt                   # Python依赖
├── AGENTS.md                          # 工作区协作与操作约束
└── README.md
```

`__pycache__/`、`.pytest_cache/`、`.env` 等解释器缓存和本地配置未列入项目目录。

## 协作开发

分支模型（各成员常驻分支 lx/csk/gc）、本地开发日志、master 合入许可制、契约变更（RFC）与按侧速查约定见 **[docs/协作开发指南.md](docs/协作开发指南.md)**；三人实时进度与待配合事项见 `docs/协作看板.md`。

## 设计约定（agent 生成契约，摘要）

- **XML 是唯一源码**：只产出 PLCopen XML，`.st` 由 xml2st 机械推导；对外接口 = `AT %QX/%QW` 定位变量（统一 %Q 区），编译错误回喂 agent 纠错
- 完整契约（ST 子集、位宽表、显式拒绝清单）的权威定义见 `docs/lx-PLC代码生成与执行引擎详细设计.md` §3
