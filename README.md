# SIMENS PLC — Agent 生成 61131-10 代码并闭环仿真（OpenPLC 运行时）

课题目标：开发一个 agent，理解用户工艺需求 → 生成 **IEC 61131-10（PLCopen XML）** 代码 →
自动部署到软 PLC 运行 → 变量接入 MuJoCo 物理仿真 → 通过仿真反馈迭代优化代码。

本仓库（`lx` 分支）为 **OpenPLC 运行时版本**：执行链使用 API，提供 CLI 与 CSP Studio（PySide6/Qt）两种入口，无许可证限制。

## 完整 XML 生成闭环（2026-10-20）

通用工程诊断、真实事件和数值条件、逐轮计时与多形状多尺寸测试，见[PLC工程通用诊断与批量验收](docs/lx-PLC工程通用诊断与批量验收.md)。最终统计使用独立版本的20个新任务，早期排查记录不混入成功率。

真实模型需求理解与完整 PLCopen XML 生成已通过 OpenPLC→MuJoCo→确定性判定闭环，包含编译失败后的自动反馈修复。入口：`python -m src.agent.closed_loop`，运行条件、严格交付语义与证据见 [完整XML生成闭环运行指南](docs/lx-完整XML生成闭环运行指南.md)。

图形入口：`python runtime/csp_studio.py`。选择三轴 CSP AML、输入“画一个正方形”或其他设备支持的绘图需求、点击“运行完整闭环”。新版按原始需求生成测试计划，再生成完整 XML；计划决定启动仿真、复位/使能/启动动作、采样周期、完成条件及物理路径检查。执行器保留全部映射信号，界面显示同次物理验收实例的画面。计划与执行记录保存在 `test_plan.json`、`execution.json`。图形依赖见 `runtime/gui-requirements.txt`，操作及已验证范围见[运行指南](docs/lx-完整XML生成闭环运行指南.md)。

迭代修复保留当前最佳版本：候选若丢失已验证判据、完成能力或路点进度，就拒绝替换，下一轮仍从最佳版修改；连续两份候选没有可靠推进则停止。`best.json` 指向完整检查点，每轮 `progress.json` 与 `repair_contract.json` 记录选择和目标。HTTP401/402独立停止，不当作PLC修复失败。规则、离线验证与适用边界见[编排设计§4.1](docs/gc-需求理解与闭环编排详细设计.md)。

当前默认模型为 `deepseek-flash`，CLI 与 Qt 共用 `.env` 配置。2026-10-20 测试：347项本地回归、20项USD场景检查及4份XML静态检查通过；Flash生成的完整XML和LLM测试计划原样重部署后，六边形8项物理判定全部通过（9路点、1302样本）。此前中断及一次Modbus断连记录均保留，原生链路A编译测试因缺工具链排除。详见[本次测试报告](workspace/flash_test_20261020_093049/report.md)。已打开的App需重启读取模型配置。

2026-10-20 五边形故障已定位到 OpenPLC 的 `st_optimizer`：它会把不同 CASE 分支中的相似条件体错误合并，编译仍成功但状态机停在原点。部署入口现为匹配本机HTTP端口的Docker运行时旁路该步骤，保留原ST直接交给matiec，并记录镜像/脚本哈希；远端运行时标注未检查。五边形原第一轮XML和原计划未改，复验7项通过（28.55s）；原成功六边形也复验8项通过，均无新增模型调用。归因已排除成功日志干扰，详情见[五边形故障复盘](workspace/pentagon_optimizer_20261020/report.md)。

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
scenario_motion3axis.py（验收）/ 未来的 仿真桥接
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

HTTP API 方式（agent 循环的部署端点）：

```bash
python src/pipeline/serve.py &            # 起服务
curl -X POST http://127.0.0.1:8600/deploy --data-binary @src/plc/motion3axis.xml
```

## 本地 VM 中 OpenPLC 启动速查

VMware VM（Ubuntu 24.04 @192.168.12.131，容器 `fdamador/openplc`：Web :8080 / Modbus :502；`ssh yushanyue@192.168.12.131` 密钥直连，docker 需 sudo）：

```bash
# ① VM 未开机时（容器随 VM 自启；vmrun 路径按本机安装位置）：
"/e/VMware/vmrun.exe" start "D:\Ubuntu24.0\Ubuntu 64 位.vmx"

# ② 宿主机 ping 不通 VM 时——VMware 网络服务挂了（VMnet8 掉 169.254 即此症），
#    管理员 PowerShell：
Start-Service VMnetDHCP; Start-Service 'VMware NAT Service'

# ③ PLC runtime 启动（容器 Up 还不够——Modbus :502 要等 runtime 启动才监听）：
python -c "import sys;sys.path.insert(0,'src');from pipeline.openplc_client import OpenPLCClient as C;c=C(base_url='http://192.168.12.131:8080');c.login();print(c.start())"
```

## 全闸门在线验证（生成 + 真编译 + 在线验收）

```bash
# ① 编译服务（闸门3；不在线则该闸门记 skipped 不阻塞）：
OPENPLC_URL=http://192.168.12.131:8080 nohup python src/pipeline/serve.py > workspace/serve.log 2>&1 &

# ② 对话式入口（环境探测到 serve/Modbus 在线自动启用闸门3/4）：
python -m src.agent.chat --modbus-host 192.168.12.131

# ③ 或编排器直连（显式强制全闸门）：
python -m src.agent.orchestrator examples/specs/plotter3axis.spec.json \
    --deploy --acceptance --scenario plotter3axis --modbus-host 192.168.12.131
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
│   │   ├── attribution.py             # 失败归因：知识库优先、未命中由 LLM 补充
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
│   ├── plc/                           # 已验收 PLCopen XML 场景库
│   │   ├── motion3axis.xml
│   │   ├── plotter3axis.xml
│   │   └── plotter_circle.xml
│   ├── scenegen/                      # csk ②b 确定性支撑：SceneSpec 校验/USD 构建/Modbus 地址分配
│   ├── runtime/                       # csk ③b 运行时：Modbus TCP 桥 + 独立运行时 + 示教器
│   └── toolchain/                     # csk 链路 A：io_map→C shim→DLL 工具链
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


## 主动澄清、轨迹预览与故障回放

对话入口会在生成前追问缺失或有歧义的绘图参数，先保存可交互的本地轨迹预览；规格修改时同步更新。现有任务可离线查看逐轮报错、诊断、变量曲线及 ST 差异。用法与证据说明见 `docs/gc-需求理解与闭环编排详细设计.md` §11。

```bash
# 只预览，不生成或部署 PLC；--confirm 为无终端输入模式，采用的默认值会留档
python -m src.agent.chat --request "画一个圆，半径20" --preview-only --confirm
# 交互澄清后，用浏览器打开预览
python -m src.agent.chat --request "画一个正方形" --preview-only --open-preview
# 回放已有运行（不连接模型或 PLC）
python -m src.agent.replay runs/plotter_cell --open
```

预览位置会打印到终端；`--runs-root` 可指定产物根目录，`--answers` 可读取预先回答的 JSON。新任务验收完成或失败后自动生成 `replay.html`；历史运行缺少采样时只回放已保存的日志。

## SVG 路径输入

SVG 可直接作为绘图轮廓，支持路径命令、贝塞尔曲线、椭圆弧、基础形状与分组变换。系统等比适配到设备坐标框，按当前 IO 编码精度量化路点，并为独立路径安排抬落笔；超行程、点数超预算或不支持的可见元素会在生成前报错。范围与参数见 `docs/gc-需求理解与闭环编排详细设计.md` §12。

```bash
# 无需模型或 PLC 即可预览；宽高使用设备坐标，不是 SVG 的毫米/像素
python -m src.agent.chat --svg examples/svg/house.svg --svg-width 60 --svg-height 60 --preview-only --confirm --open-preview
# 配置模型后，去掉 --preview-only 进入现有生成流程；--runs-root 可指定产物目录
```

任务目录同时保存 `input.svg`、含源文件校验值的 `trajectory.json` 和交互预览。网页生成按冻结轨迹检查实际路点与接触笔迹，验收证据绑定同一份 XML、规格、场景和采样；历史产物缺少对应证据时不会补记通过。见 gc 设计 §13.18。

## PLC 与仿真工作台

在项目根目录启动：

```bash
python -m src.agent.web --open
```

打开 `http://127.0.0.1:8765`，在“绘图素材与参数”选择轨迹来源：语言模式可直接输入“画一个正方形，边长40”或“画一个圆，半径20”，无需 SVG 或选区；SVG 模式支持导入、选择路径和调整参数。预览后使用默认工艺或向右侧 Agent 补充要求，核对规格，再点击“生成并验证 PLC”。缺省几何参数在界面明示；切换来源保留绘图输入并使旧规格失效。见 gc 设计 §13.20。

页面显示后台进度；新产物须通过静态校验、真实编译部署及物理仿真验收，才提供场景描述 `scene.spec.json`（内含 `io_map`）与最终 `plcopen.xml`。当前任务未成功时不显示旧结果。服务读取项目现有模型配置；未配置模型时仍可规划、预览和使用默认工艺。

页面顶部提供 **AML、组件库、SVG** 三个基础资产区：查看当前文件与内容，上传替换或下载。组件库包含接口契约和物理参数，可导出整个 JSON 包修改后替换，也可单独替换契约或物理参数 JSON。替换先校验，失败保留当前资产；成功使旧规格失效，生成保存不可变资产快照。范围及文件格式见 gc 设计 §13.4。

页面按两个业务部分切换：

1. **资产管理与 PLC 代码生成**：左侧管理资产、绘图素材与折叠参数，右侧固定 PLC 生成栏；可使用默认工艺直接准备生成，或对话补充要求。支持清空会话上下文并保留资产、轨迹和历史交付；对话仅回显简洁工艺目标。交付场景描述 JSON 和最终 PLC XML。
2. **仿真与验证**：整合场景构建、PLC 部署、Runtime 启停、模型运行与验收。参考 CSP Studio，左侧是运行配置、HMI 按钮/状态灯和分轴数值，右侧显示实际模型与笔迹；下方提供验收结果、信号趋势、运行日志与历史记录。自动运行时面板只读；连接手动调试后可操作 BOOL 输入，触发按钮使用至少 150ms 脉冲。网页显示实际 MuJoCo 几何与运动，支持旋转、平移、滚轮缩放和高清显示。

通过“导入场景与 PLC”配对上传外部场景 JSON 与 PLC XML，按当前 AML/组件库校验；可选上传需求规格 JSON 以配置验收。未提供规格时仅允许手动调试，不报告工艺验收通过。任务选择器可切回原工作台产物，导入不覆盖已有交付。
仿真与验证经 `DEPLOY_URL`（默认 `http://127.0.0.1:8600/deploy`）接入 lx 部署服务。服务使用 `OPENPLC_URL`、`MODBUS_HOST`、`MODBUS_PORT` 连接 Runtime；非默认登录可设置 `OPENPLC_USERNAME` / `OPENPLC_PASSWORD`，无需在浏览器填写。部署仍由 `run_deploy.py` 执行 XML 转换、真编译和启动；新产物生成期间会自动部署并验收，已有交付或外部导入也可在第二页显式部署运行。启动模型前核验任务、程序身份与部署回执；停止后保留真实笔迹，后端重启也可查看上次快照。见 gc 设计 §13.8~13.13、§13.18。

SVG 工作台新会话默认使用 `plotter3axis_precision_station.aml`。位置输出 `x_cmd/y_cmd/z_cmd` 保留 `%QW3/4/5` 地址，以 0.01 个轴工程单位编码，量程放大100倍后仍在 INT16 域；反馈和设备行程保持原单位，桥按各自量程换算。生成器直接使用 REAL 插补状态编码，SVG 小数路点量化为0.01，默认曲线展开容差0.02。原 `x_v/y_v/z_v` 保留诊断，`prog_id` 定位 `%QW20`。旧 `plotter3axis_csp_station.aml` 与交付仍可读取；资产量程变化后须重新生成并部署相应 XML。详见 gc 设计 §13.10~13.12。

这是带 Python 后端的交互页面，需要保持服务运行；原有独立 HTML 预览仍可直接打开。默认产物保存到 `E:\chagpt\308sjk-svg-workbench`，可用 `--runs-root` 覆盖；`--port` 修改端口，`--aml` 指定设备模型。生成失败时保留候选和证据，按故障库与归因修复；重复候选及时停止，通信中断与代码失败分开处理。详细机制见 gc 设计 §13。
