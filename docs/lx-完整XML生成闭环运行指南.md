# 完整 XML 生成闭环运行指南

负责人：lx。更新：2026-10-20。CLI：`src/agent/closed_loop.py`；图形入口：`runtime/csp_studio.py`（PySide6/Qt）。

## 1. 已验证的流程

`AML → 真实 LLM 需求理解 → 自足 spec → LLM 测试计划 → 计划校验 → LLM 完整 PLCopen XML → xml2st/一致性 → 本轮 SceneSpec/MJCF/IO 地址表 → OpenPLC 真编译部署 → 按计划启动 MuJoCo、激励和采集 → 确定性判定 → 失败证据回喂 LLM 修复 → final`

本入口不接受种子替代生成，强制 `generation_mode=full_xml`。所有 PLC 控制代码来自模型回复；程序不调用 `stack_gen` 来补写运动栈。XML→ST、场景→MJCF 是确定性转换。CSP 位置从站形态遵循《运动控制代码生成方案》§7：物理从站跟随位置指令，最简 run 门控不要求把 internal402 速度形态的功能块塞入 PLC。模型可自行决定必要的 POU 结构，交付必须是完整 XML。

当前 SceneSpec 生成器采用现有确定性组件装配，不宣称场景布局也由 LLM 创作。默认设备是三轴 CSP 绘图仪；新版测试计划与 PLC 代码均由 LLM 生成，图形入口保留用户的原始形状需求。

2026-10-20 已切换 DeepSeek Flash，并完成六边形验证：真实需求理解、LLM测试计划及完整XML均有原始回复；修复CDATA检查误报后，原样工程与计划重新编译部署，8项物理判定全部通过（1302样本、采集27.515s、9路点、最大线段偏差0.900）。此前日志编码中断和一次Modbus断连均保留，成功来自原样复跑，不代表通信问题已修复。347项本地回归、20项USD场景检查、4份XML静态检查通过；原生链路A编译因缺工具链未执行。详见[Flash测试报告](../workspace/flash_test_20261020_093049/report.md)。

2026-10-20 五边形故障已定位到 OpenPLC 的 `st_optimizer`：它会把不同 CASE 分支中的相似条件体错误合并，编译仍成功但状态机停在原点。部署入口现为匹配本机HTTP端口的Docker运行时旁路该步骤，保留原ST直接交给matiec，并记录镜像/脚本哈希；远端运行时标注未检查。五边形原第一轮XML和原计划未改，复验7项通过（28.55s）；原成功六边形也复验8项通过，均无新增模型调用。归因已排除成功日志干扰，详情见[五边形故障复盘](../workspace/pentagon_optimizer_20261020/report.md)。

## 2. 环境和启动

希望在原来的 Qt App 中操作时，完成 2.1 的配置检查后直接使用 2.4；2.2、2.3 是命令行入口。

以下命令适用于本机 Windows PowerShell。项目目录为 `D:\Study\SIMENS PLC`，开发分支为 `lx`。

### 2.1 本机配置检查

打开 PowerShell，执行：

```powershell
Set-Location 'D:\Study\SIMENS PLC'
git branch --show-current
& '.\.venv\Scripts\python.exe' -c "from src.agent.config import API_URL, MODEL, has_api_key; print('API:', API_URL); print('MODEL:', MODEL); print('KEY configured:', has_api_key())"
```

预期分支为 `lx`、API 为 `https://api.deepseek.com`、模型为 `deepseek-flash`、密钥状态为 `True`。该检查不会打印密钥。

本机虚拟环境、密钥与模型配置已经准备好。只有环境缺少依赖时才安装仓库两组依赖：

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt -r runtime/requirements.txt
```

以后更换配置时，参考 `.env.example` 编辑已有 `.env` 中的 `LLM_API_URL`、`LLM_MODEL`、`DEEPSEEK_API_KEY`、`LLM_THINKING`、`LLM_REASONING_EFFORT`；保留现有密钥，不要直接用示例文件覆盖 `.env`。配置修改后重新启动 Agent 进程。当前默认模型为 DeepSeek Flash，保留 thinking enabled / high；下文 Pro 记录属于历史实验。配置依据见 [DeepSeek 官方模型文档](https://api-docs.deepseek.com/quick_start/pricing/)与[推理参数文档](https://api-docs.deepseek.com/guides/thinking_mode/)。

### 2.2 终端 A：运行环境和部署服务

先打开 Docker Desktop，待引擎就绪，再执行：

```powershell
Set-Location 'D:\Study\SIMENS PLC'
docker start openplc
docker ps --filter 'name=openplc'
Invoke-RestMethod -Uri 'http://127.0.0.1:8600/health'
```

如果健康检查返回 `status: ok`，直接复用已有部署服务，进入下一步。如果连接失败，在终端 A 启动：

```powershell
& '.\.venv\Scripts\python.exe' -u src/pipeline/serve.py
```

看到 `listening on http://127.0.0.1:8600/deploy` 后保持终端 A 打开。不要重复启动同一端口的服务。

### 2.3 终端 B：亲自启动真实 Agent 闭环

再打开一个 PowerShell，完整复制下面四行：

```powershell
Set-Location 'D:\Study\SIMENS PLC'
$taskName = 'manual_square_' + (Get-Date -Format 'yyyyMMdd_HHmmss')
& '.\.venv\Scripts\python.exe' -u -m src.agent.closed_loop --task-id $taskName --max-iters 6
$agentExitCode = $LASTEXITCODE
```

这次运行会重新调用模型理解需求、生成完整 XML，最多尝试六轮。每次执行都产生新任务目录。不要并行启动多个闭环任务，它们使用同一 OpenPLC 实例和仿真桥端口。

默认任务为：先抬笔至 Z=10mm，定位到 (20,20)，落笔后依次经过 (80,20)、(80,80)、(20,80)、(20,20)，抬笔后回中心 (50,50)，点亮完成灯。该入口内置这段标准自然语言需求，无需再输入其他内容。

默认 AML：`examples/aml/plotter_square_csp_station.aml`，位置反馈 `%QW0..2`、指令 `%QW3..5`、程序身份 `%QW20=4`。X/Y 量程 0..100 工程百分比，Z 0..10 毫米。运行会替换 OpenPLC 当前程序，控制输入由 LLM 计划交给执行器自动操作，无需手动点击按钮。

新版入口不依赖固定 `trigger` 标记，也不补写画方启动协议。LLM 根据需求与设备规格输出 `test_plan.json`：初始输入、启动仿真、复位/使能/启动动作、等待条件、全部映射信号的采样周期、结束条件、清理输入、实际反馈路点和绘图线段。计划须先通过结构、地址、方向、类型、时长、物理反馈及设备容差校验；不合法时反馈给规划器，最多三次，不进入 PLC 编译。有效计划在 PLC 修复轮次间保持不变，防止通过修改验收目标掩盖代码错误。

确定的物理余量冲突会直接停止，不消耗三次规划修补：例如抬笔与落笔间距1、设备poswin1。应先修正规格或设备条件，再新建任务。新版修复从 `best.json` 指向的最佳版本出发，候选退化则拒绝替换，连续两次无可靠推进停止。每轮 `repair_contract.json`、`progress.json` 及任务 `progress_history.json` 解释修复目标与选择原因；规则和边界见《gc-需求理解与闭环编排详细设计》§4.1。

仿真进程、Modbus 读写和判定由固定 Python 执行器实现。LLM 生成的是受限声明式数据，不是任意 Python 脚本。执行器只能写合法的控制输入，不能伪造轴反馈或输出信号；无论完成或超时，均尝试执行计划中的输入清理。原始回复和计划校验错误保留在 `test_plan_generation/`，每轮动作及清理结果见 `execution.json`。

运行时会依次显示需求理解、完整 XML 生成、校验、编译部署、在线验收。推理生成需要等待；历史 Pro 画方实验约 5 分 17 秒，实际时长随模型响应和修复轮数变化。编译或验收失败后出现“反馈”“定向修复”“下一轮”属于闭环过程。

成功时末尾应出现：

```text
PASS AC1 ...
PASS AC2 ...
PASS AC3 ...
PASS AC4 ...
PASS EXECUTION ...
PASS PATH ... 全部 8 个路点按顺序实际到达 ...
场景验收: 全部通过
RESULT: final -> D:\Study\SIMENS PLC\runs\manual_square_...
```

`RESULT: final` 且 `$agentExitCode` 为 0 表示完成。`best_effort` 表示尚未收敛，不能当作全部通过。

### 2.4 使用原有 CSP Studio 图形 App

原来的 App 位于 `runtime/csp_studio.py`，实际使用 PySide6，界面仍为左侧输入/日志、右侧 MuJoCo。先打开 Docker Desktop，再启动：

```powershell
Set-Location 'D:\Study\SIMENS PLC'
& '.\.venv\Scripts\python.exe' runtime/csp_studio.py
```

本机 PySide6 已安装。如果新环境提示缺包，先执行：

```powershell
& '.\.venv\Scripts\python.exe' -m pip install -r requirements.txt -r runtime/gui-requirements.txt
```

1. 保留默认 AML `examples/aml/plotter_square_csp_station.aml`。
2. 在底部输入“画一个正方形”或“画一个三角形”，点击“运行完整闭环”。自动检查 OpenPLC；未启动时尝试启动已有 `openplc` 容器，部署服务缺失时自动启动 :8600。
3. 状态栏与日志依次显示需求理解、LLM 测试计划、完整 XML、真实编译部署、按计划仿真验收；PLC 失败后在最多六轮内反馈修复。模型沿用 `.env` 配置。
4. 仿真阶段右侧显示**本次验收实例**的实时画面，按住鼠标左键拖动旋转、滚轮缩放、左键双击恢复初始视角。自动运行期间 HMI 禁用，视角操作仍可用，触发与采集由验收器负责。
5. 显示“全部验收通过”后，点击“打开本次结果”。结果位于独立 `runs/studio_<日期时间>_<编号>/`，成功判定核对退出码、manifest、真实部署与验收 gate、全部 spec 判据、计划路径及 EXECUTION，并核对计划哈希与清理成功记录。

模型失败时不会退回种子程序。点击“停止”会终止本次后台进程树，尝试复位本任务 BOOL 输入并保留已有证据；不会将停止标为通过。关闭窗口也会清理自己启动的任务与部署服务，复用的已有部署服务继续保留。共享的 OpenPLC 容器不会随窗口关闭而停止。

完成后仍可旋转查看最后实际显示状态，包括该次仿真的笔迹。运行时会在任务目录保存 `live_frame.model.mjb` 和 `live_frame.state.npz`；仿真进程结束后，App仅从这些文件重新渲染，不步进物理、不发送PLC指令、不重跑验收。视角未变化时不重复渲染；启动新任务、手动加载模型或关闭App时释放查看线程。模型与状态的校验值不匹配时拒绝加载，保留原图片并显示错误。

旧任务只有末帧图片，没有实际三维状态，不能恢复当时的三维视角；更新并重启App后的新任务支持结束后旋转。也可用“加载模型”进入已有工程的手动调试视图，这不会重新调用模型生成程序。“加载模型”“部署运行”是独立手动调试入口，不会重新执行生成闭环。新版图形入口不再追加固定正方形需求；形状、坐标及验收路径来自本任务的规格和 LLM 计划。不要同时从终端或第二个窗口运行另一任务。

2026-10-21相机验证：实时相机、Qt鼠标操作、结果查看、笔迹与原始状态保持等专项28项通过；完整本地回归462项通过，1项原生编译缺工具链未执行，既有异步Modbus退出清理警告仍在。另启动真实MuJoCo运行时加载已生成的场景，在独立15020端口验证实时转动和结束后的Qt拖动；查看前后设备位置、仿真时间和保存状态不变，模型调用0、PLC连接0。证据见 `workspace/camera_validation_20261021/result.json`、`before.png`、`after.png`。

手动复跑已生成的产物时，在两栏分别选择同一任务的 `final/scene.spec.json` 和 `final/plcopen.xml`，然后按以下顺序操作：

1. 点击“加载模型”，加载场景、该任务的规格、IO 映射和 HMI。
2. 点击“部署运行”。部署会重启 OpenPLC，期间 HMI 暂停；仿真桥会关闭失效连接并自动重连。
3. 等状态栏显示“手动运行就绪”，点击 `run 使能`，按钮变绿表示 PLC 中该位已置高。
4. 保持“复位”关闭，点击“启动画方”。短点击会产生至少150ms的启动脉冲，也支持按住保持；日志显示 ON/OFF。

运行过程中仪表显示实际指令/反馈，鼠标悬停指示灯可查看信号。再次部署后同样等待连接就绪再操作，无需重新生成 XML。若手动运行无响应，先看状态栏是否仍在重连、run 是否变绿、复位是否关闭，以及 z_fb 是否由0恢复到9~10。此前 Windows 10053 断连后一直使用失效 socket 的问题已修复；更新后需要重启 App。

### 2.5 在终端 B 查看自己的结果

继续在同一个终端 B 执行（保留上面的 `$taskName` 变量）：

```powershell
$runPath = Join-Path (Get-Location).Path ('runs\' + $taskName)
Get-Content -LiteralPath (Join-Path $runPath 'manifest.json') -Raw -Encoding UTF8
Get-Content -LiteralPath (Join-Path $runPath 'summary.md') -Raw -Encoding UTF8
Get-Content -LiteralPath (Join-Path $runPath 'final\verdict.json') -Raw -Encoding UTF8
```

重点检查 `manifest.json` 中 `generation_mode=full_xml`、`seed_used=false`、`status=final`、`test_plan_source=llm`；`verdict.json` 中 `verdict=PASS`，所有 `details[].pass` 均为 true；`execution.json` 中 `status=passed`、`cleanup_ok=true`。检查数量随本任务判据与路径变化。

打开任务文件夹可以查看 XML、场景和轨迹证据：

```powershell
Invoke-Item -LiteralPath $runPath
```

模型原始输出在 `model_calls/` 和每轮 `generation/`；最终 XML 在 `final/plcopen.xml`；物理采样在 `final/trace.json`。本入口默认无头仿真，终端显示验收结果，运行时不会弹出 3D 窗口。

### 2.6 结束和重新运行

Agent 正常结束后，MuJoCo 子进程自动退出。需要再跑一次时，重新执行 §2.3 的四行命令生成新的任务名即可。部署服务可继续复用。

如果不再使用，先在部署服务所在的终端 A 按 Ctrl+C，然后执行：

```powershell
docker stop openplc
```

OpenPLC 使用 Web :8080 / Modbus :502，部署服务使用 :8600，仿真桥使用 :5020。

## 3. 严格交付与证据

- 部署或在线验收被跳过，严格模式不得输出 `final`。
- 规格、场景、IO 映射和 trace 绑定本轮目录；不读取上一次 `runs/latest`。
- 完整 XML 模式不做程序化地址纠偏，生成地址必须通过 spec 一致性检查。
- 初值必须使用 `initialValue/simpleValue` 子元素；误写成属性会在静态检查拒绝，交由模型修复，不再静默变成0。
- IO 映射的 PLC 地址来自本轮 spec，而非场景条目序号。MuJoCo 路径不再因 eager import 强制依赖 USD/pxr。
- 每次模型调用保存请求体与回复，不保存鉴权头。可核对最终 XML 与原始模型回复一致。
- 每轮保存 `generation/`、`plcopen.xml`、`plc.st`、`scene.spec.json`、`scene.xml`、`io_map.json`、`runtime.log`、`trace.json`、`verdict.json` 和 `gate.json`。失败证据进入归因与定向修复；成功轮复制至 `final/` 供交付。
- spec 判据之外，按 LLM 计划检查实际反馈路点和工艺状态。绘图还检查相邻落笔点之间的实际线段偏差与持续落笔，防止只点亮完成灯、仅到达顶点或抬笔绕行也通过。计划容差不得放宽设备 poswin。
- 空 trace、缺信号、无健康量程或未知判据类型不能判为通过。事件准则区分上升沿与下降沿。
- MuJoCo 开场真实关节位姿与桥的开场目标一致，先发布实际反馈，回环连续在线后才声明启动就绪。绘图拐点按首次连续到位区间内最接近顶点的采样分段，避免把入边采样误归到出边；原始 trace 始终保留。

`region_containment` 当前实现针对轴反馈工程量，本例中心 `[50,50,10]`；场景 IO range 使用 SI 米制，桥负责换算。其他对象的位置区域判定须明确相应信号和量纲，不能把工程量和米混用。

## 4. 本次实证

### 新版 LLM 测试计划：三角形

原始输入“画一个三角形”，生成 A(20,20)、B(80,20)、C(50,80)，落笔闭合后抬笔回A。原GUI实验受验证脚本900秒限时取消，取消证据未改写；继续实验复用原规格和原LLM计划，完成XML模型修复、真实编译部署及全部7项判定：AC1~AC5、EXECUTION、PATH_TRIANGLE。603个全量样本，命令到完成10.67秒，线段最大偏差0.89≤原容差2。

冻结产物不改XML或计划，重新真编译部署并在Qt中显示同次仿真，7项再次全过：598样本，10.56秒，最大偏差1.34≤2，102帧，模型调用0次。原生成5次调用、继续实验3次，最终XML和计划均与原始模型回复相符，原计划在各轮保持不变。

证据目录：`runs/studio_20261019_165328_34f1f0/continued_001/studio_20261019_165328_34f1f0/`。查看 `experiment_result.md`、`verification_record.json`、`final/` 和 `replay_001/gui_verified.png`。实测修复包括非法XML初值静默丢失、仿真开场未抬笔、拐点分段误判；没有放宽原计划容差。322项回归通过。该单任务恢复实验不代表全新任务首轮成功率或跨设备泛化能力。

### 旧版固定画方入口的历史证据

任务目录：`runs/full_xml_square_20261019_102248/`。

- 模型：DeepSeek V4 Pro，high 推理。真实模型调用 3 次：需求理解、首次完整 XML、定向修复。
- 第 1 轮：XML 静态校验通过；真实 matiec 编译报告 END_IF 缺分号，反馈回模型。
- 第 2 轮：模型修复完整 XML，编译、部署、MuJoCo 和四类判据及 PATH 全部通过。采集 368 样本，观测完成时间 7.890 秒。
- 最终产物重新编译部署复跑：`replay_001/`，五项再次全部通过，观测完成时间 7.969 秒。
- 最终 XML 已核对与原始模型回复一致，没有种子替换或运动栈拼装。

这是单一画方任务的端到端实证，不构成模型成功率或跨场景泛化能力的统计结论。自动化回归与复现证据详见该任务的 `experiment_result.md`。

图形入口实证：`runs/studio_20261019_111325_ec961e/`。Qt 按钮触发真实需求理解与完整 XML 生成（Pro high，共 2 次模型调用），首轮编译部署与五项验收均通过；采集 378 个样本，物理验收 8.109 秒，界面收到同一仿真实例的 69 帧画面，整个任务约 6 分 50 秒。原始模型回复与最终 XML 已核对一致。检查记录见 `gui_check.json`，日志见 `gui.log`。`gui_verified.png` 是修正中文字体与显示比例后，重新打开同次结果和实际末帧的截图。

用户复跑暴露的验收配置缺陷已修复：`studio_20261019_112727_aef78c` 的模型规格省略两个启动标记，旧入口未提前拦截，导致六轮编译成功后均在采集初始化失败。修正启动元数据后，原 `iter_001/plcopen.xml` 不作修改重新部署与物理验收，五项全部通过（7.954秒）；没有额外调用模型。原始失败记录保留，补充复验结果在该任务 `recovery_001/`，不把原失败 manifest 改成成功。更新代码后需要关闭并重新启动 App。

## 5. 常见情况

| 现象 | 处理方法 |
|---|---|
| Docker 无法连接引擎 | 打开 Docker Desktop，待引擎启动后再运行 `docker start openplc` |
| :8600 健康检查失败 | 在终端 A 启动 `src/pipeline/serve.py`，并保持终端打开 |
| 启动服务提示 10048／地址已被使用 | 先检查 `/health`；如果为 ok，复用已有服务 |
| 模型接口 HTTP 401 | 核对本地 `.env` 的接口地址与密钥；重新启动 Agent，不要把密钥贴进日志 |
| 模型接口 HTTP 402 | 查看该 API 账户的余额／配额，再运行新任务 |
| `RESULT: api_blocked` | 已停止模型调用并保留最佳版；查看 `api_error.json`。充值或更换配置后重启App，再启动新任务 |
| 候选未替换最佳版／连续候选没有可靠推进 | 查看 `summary.md` 比较表与 `progress_history.json`；`best.json` 指向下一次应使用的修复源码，未全部通过时仍不能当作final |
| `best_effort` 或非 0 退出码 | 查看 `summary.md` 和失败轮 `iter_NNN/gate.json`；保留整个任务目录以便排查 |
| 旧版本报“严格验收缺少触发输入或完成信号” | 重启更新后的 App；新版先生成并校验显式测试计划，不再依赖固定 trigger 标记。该错误来自规格/采集接线，反复重写 XML 无法解决 |
| 加载后部署成功，但 HMI 启动画方没有动作 | 更新后重启 App；等待“手动运行就绪”，run变绿、复位关闭，再点击启动。部署期间连接中断属于正常重启过程，随后应出现“PLC 反馈连接已就绪” |
| 模型生成阶段暂时没有输出 | Pro 可能正在推理；先观察终端进程和任务目录，不要同时再启动一个任务 |
| :5020 被占用或 MuJoCo 启动失败 | 查看该轮 `runtime.log`，确认没有另一个闭环／手动仿真进程正在使用同一桥端口 |

## 6. 修改输入的当前边界

`--request` 可以替换自然语言文本，例如 `--request '画一个三角形'`。GUI 同样原样传递请求，不使用固定 `plan_square()`。未指定尺寸时由需求理解与规划器选择设备工作范围内的一致坐标；需要特定尺寸时请写出坐标、单位、抬落笔要求和完成位置。

当前执行后端与检查仍面向设备规格中已映射的运动轴、BOOL/INT 控制信号及折线路径。换设备需要有效 AML、受支持组件与判据；任意形状的可表达性不等于已完成物理实证，曲线、工艺扩展和跨设备成功率需要另行验证。旧的固定画方协议辅助函数保留兼容用途，新 CLI/GUI 不调用它。

`--spec <file>` 可跳过需求理解以复用固定的规格文件，代码仍由 LLM 重新生成。这适合隔离调试代码生成阶段；如果要亲自验证从自然语言开始的全流程，使用 §2.3 默认命令即可。
