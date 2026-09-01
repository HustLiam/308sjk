# PLC 代码生成与执行引擎详细设计（lx 负责部分）

> 本文档是《总体实施方案》中 **② PLC 控制代码生成模块**、**④a PLC 执行引擎（验收链路 B）**与 **⑤ 验证模块的 PLC 侧行为验收**的详细设计与实施记录，负责人 **lx**。
>
> 与仿真验证侧（csk，《csk-仿真环境与IO闭环详细设计》，负责 ①③④b⑤ 及链路 A）的衔接方式：**同一份 PLCopen XML + io_map.json**。链路 A（matiec 编译为 C 库、Isaac Sim 进程内 lockstep）与链路 B（OpenPLC 软 PLC + Modbus TCP）跑同一份代码，本文档负责其中的代码契约与链路 B 的全部实现。

---

## 0. 职责范围（TL;DR）

| 总体方案模块 | 本侧职责 | 仓库实现 | 状态 |
|---|---|---|---|
| ② PLC 代码生成契约 | PLCopen XML（IEC 61131-10）结构契约、静态校验、XML→ST 机械转换 | `src/pipeline/xml2st.py` + `tests/test_xml2st.py` | ✅ 完成 |
| ④a 执行引擎（链路 B） | 校验→转换→上传→编译→启动的全脚本化部署编排 + HTTP 服务化 | `run_deploy.py` / `openplc_client.py` / `serve.py` | ✅ 已打通 |
| ⑤ PLC 侧行为验收 | Modbus 安全 IO 层、冒烟验证、场景验收脚本 | `modbus_io.py` / `verify_modbus.py` / `scenario_*.py` | ✅ 5 场景全过 |
| 运行时工程资产 | 已验收的 PLCopen XML 场景库 | `src/plc/*.xml` | ✅ |

## 1. 在总体架构中的位置

```
需求规格 requirement_spec.json
        │  io_list（IO 清单：变量名/方向/类型/量程 —— 三方一致性源头）
        ▼
② 代码生成（agent 产出 plc_project.xml —— 唯一源码，强制 61131-10）
        │
        ├──► 【链路 A · 仿真侧负责】matiec iec2c → plc_logic.dll → Isaac 进程内 lockstep
        │
        ▼    【链路 B · 本侧负责】
xml2st 静态校验 + XML→ST ──► OpenPLC v3（Docker）：上传 / matiec 编译 / 启动
        │                         │ 编译失败 → 错误日志回喂 agent 重生成 ↩
        ▼                         ▼
workspace/program.st       Modbus TCP :502
                                 │
                    SafeCoilIO / verify_modbus / scenario_*（行为验收）
```

两链路分工（总体方案 §3.4）：**A 做开发/CI 闭环**（lockstep、无通信抖动、归因可排除通信因素），**B 做工业代表性验收**（真实软 PLC 运行时）。本侧的 xml2st 契约即"matiec 可编译子集"，保证同一份 XML 两条链路都能编译。

## 2. 设计原则（agent 生成契约的四条红线）

1. **XML 是唯一源码**：agent 只产出 PLCopen XML；可执行 `.st` 由 xml2st 机械推导，人不手改、不直接编辑；
2. **ST 本体子集**：POU 一律 ST 语言本体，类型限 PROGRAM / FUNCTION_BLOCK / FUNCTION；LD/FBD/SFC 图形本体不支持；
3. **防信息丢失**：未支持的构造（自定义 DUT、action/method、persistentVars、configuration 内容等）一律**显式拒绝并逐条报错**，绝不静默丢弃——错误信息直接回喂 agent；
4. **全链路纯 API**：校验、上传、编译、启动、验证全部脚本化 / HTTP 化，零 GUI、零许可证限制（CODESYS 版因 Script Engine + 演示版时长限制废弃，保留于 git 历史作标准符合性参考）。

对外接口约定：**带 `AT` 地址的定位变量即对外接口**——`%QX`/`%QW` 映射 Modbus 线圈/保持寄存器；不带 AT 的变量是 POU 内部状态，不对外发布。

## 3. PLCopen XML 校验与转换（xml2st.py）

纯标准库实现（`re` + `xml.etree`），无需运行时即可跑，是部署流水线的第一道闸门。

### 3.1 生成契约（agent 必须遵守，超出即拒）

**工程骨架**：PLCopen 命名空间下的 `<project>` 根元素，必含 `fileHeader / contentHeader / types / instances`；至少一个 `pouType="program"` 的 POU。

**POU 与变量块**：

| 项目 | 允许 | 拒绝（显式报错） |
|---|---|---|
| POU 类型 | program / functionBlock / function | 其他 |
| 本体语言 | ST（`body/ST`），且非空 | LD / FBD / SFC、空本体 |
| 变量块 | inputVars / inOutVars / outputVars / localVars，支持 CONSTANT / RETAIN 限定 | externalVars / temporaryVars / tempVars、PERSISTENT 限定 |
| 变量类型 | 全部基本类型（BOOL/INT/REAL/TIME/STRING…）、数组 `ARRAY[1..5] OF T`、derived（FB 实例）、FUNCTION 的 returnType | `<dataTypes>` 中的自定义类型 |
| POU 内构造 | — | action / method / property / transition / step |
| 工程级构造 | — | persistentVars；带内容的 configuration（任务/资源由流水线模板统一装配） |

**定位变量与位宽契约**（地址格式 `%[IQ][WDX]?数字[.位]`，名称须为合法标识符）：

| 地址宽度 | 允许类型 | 链路 B 的 Modbus 映射 |
|---|---|---|
| `%QX0.3`（位） | 仅 BOOL | 线圈（fc01 读 / fc15 写） |
| `%QW0`（字） | INT / UINT / WORD | 保持寄存器（fc03 读 / fc06、fc16 写） |
| `%QD`（双字） | **禁用** | 不映射 Modbus 缓冲区——编译能过但外部读不到（静默故障），校验器直接拒；32 位值用两个连续 `%QW` 由客户端拼接 |

说明：校验器对 `%I` 区同样按上表校验位宽（matiec 可编译）；但**对外 IO 统一约定只用 `%Q` 区**（双链路一致，传感器注入也走 `%QX/%QW`，见 §5.1），`%I` 区变量不进 io_map。

### 3.2 matiec 语法适配（转换器自动处理）

- **定位变量独立分块**：同一个 VAR 块内，FB 实例等普通声明与带 AT 的定位声明混放，matiec 报 `invalid variable(s) declaration`——转换器自动拆为两个 VAR 块；
- **结束符统一 `END_VAR`**：matiec 无 `END_VAR_INPUT` 等变体；
- 声明渲染为 `名称 AT 地址 : 类型 := 初值;`，初值取自 `<initialValue><simpleValue value="...">`。

### 3.3 ST 装配（to_st）

各 POU（声明 + ST 本体）之后，流水线统一追加任务配置模板：

```iecst
CONFIGURATION Config0
  RESOURCE Res0 ON PLC
    TASK task0(INTERVAL := T#20ms, PRIORITY := 0);
    PROGRAM instance0 WITH task0 : PLC_PRG;
  END_RESOURCE
END_CONFIGURATION
```

产物 `workspace/program.st`，OpenPLC v3 直接可编译。用法：

```bash
python src/pipeline/xml2st.py <file.xml> --check    # 只校验，打印各 POU 的 ST 本体
python src/pipeline/xml2st.py <file.xml> --out workspace/program.st
```

## 4. 部署编排（run_deploy.py + openplc_client.py + serve.py）

### 4.1 部署流水线（5 步，结果 JSON 化供 agent 回喂）

```
① validate & convert XML     xml2st：结构契约 + 位宽校验 + 转 .st
② write program.st           workspace/program.st
③ login runtime              POST /login（会话约 5 分钟，过期自动重登录一次）
④ upload & trigger compile   两段式上传表单 → 触发 matiec → 轮询编译日志
⑤ start PLC                  GET /start_plc → dashboard 确认 RUNNING
```

任一步失败立即终止，结果写 `workspace/deploy_result.json`（`{status, steps[], errors[]}`）；**errors 原样回喂 agent**（编译失败时附 matiec 日志尾部 40 行）。

### 4.2 openplc_client 封装的运行时路由（字段名以容器内 webserver.py 源码实测为准）

| 操作 | HTTP | 说明 |
|---|---|---|
| 登录 | POST /login | 302 → dashboard 即成功；默认账号 openplc/openplc |
| 上传 | POST /upload-program → /upload-program-action | 字段 `prog_name / prog_descr / prog_file / epoch_time`；随机 .st 文件名从响应隐藏域动态解析 |
| 编译 | GET /compile-program?file=\<st\> | 触发 matiec |
| 编译状态 | GET /compilation-logs | `Compilation finished successfully!` / `... with errors!` / 500 = 编译从未启动 |
| 启停 | GET /start_plc、/stop_plc | **换程序必须 stop→start**，仅 start 不会加载新二进制 |
| 状态 | GET /dashboard | RUNNING / STOPPED / COMPILING |
| 日志 | GET /runtime-logs | 归因素材 |

### 4.3 serve.py：部署服务化（agent 闭环对接点）

```
POST /deploy    body 为 PLCopen XML 内容（应以 <?xml 开头，否则 400 REJECTED）
GET  /health    存活检查
```

```bash
python src/pipeline/serve.py --port 8600
curl -X POST http://127.0.0.1:8600/deploy --data-binary @src/plc/counter.xml
```

成功返回 200 + deploy_result 同构 JSON；校验/编译失败返回 500 + errors——上层编排器据此走"回喂重生成"分支，不进仿真。

### 4.4 运行时形态

```bash
docker run -d --name openplc -p 8080:8080 -p 502:502 fdamador/openplc
```

Web API :8080、Modbus TCP :502；URL 与账号可经环境变量 `OPENPLC_URL / OPENPLC_USER / OPENPLC_PASS` 覆盖。

## 5. Modbus IO 与行为验收

### 5.1 地址映射与传感器注入

| ST 侧 | Modbus 通道 | 用途 |
|---|---|---|
| `%QX0.3` | 线圈 3 | PLC 输出读 / 按钮类输入注入（脉冲） |
| `%QW0` | 保持寄存器 0 | PLC 输出读 / 模拟量注入（如液位 0~100 定点值） |

链路 B 的传感器注入不走 `%I` 区，而是**外部直接写 `%QX/%QW`、PLC 程序读回**（pump 场景的液位 set_level 即写保持寄存器 0；sorting 场景的光电即 SafeCoilIO 脉冲打线圈位）。这是 OpenPLC Modbus 映射下的实测可行通道，场景均按此约定。

### 5.2 SafeCoilIO：线圈安全访问层（modbus_io.py）

实测怪癖：OpenPLC 的 Modbus 服务端在**窄范围线圈写入**（fc05 单线圈 / 少量 fc15）时会破坏同缓冲区的相邻位——例如写 %QX0.3 会把 %QX1.0 的电机自锁打掉。读-改-写整组线圈（fc15 覆盖完整 16 位跨度）则完全正常。

封装约定：所有线圈写入一律"读整组 → 改一位 → 整组写回"；提供 `pulse()` 模拟按钮/光电信号。**未来 Isaac Sim 桥接若走链路 B，线圈访问必须经由此层，禁止裸 write_coil / 窄范围 write_coils。**

### 5.3 冒烟验证与场景验收

- `verify_modbus.py`：读 `%QW0`（counter 的 cnt），采样 5 次确认每秒 +1，作为链路冒烟；
- 场景脚本模式：`run_deploy --xml 场景.xml` 部署 → 脚本按地址表注入激励（设液位 / 打脉冲）→ `check()` 逐条断言，PASS/FAIL 打印、退出码汇总。

**已验收场景清单**（全部经链路 B 实测通过）：

| 场景 | XML | 考察点 | 结果 |
|---|---|---|---|
| 计数器 | counter.xml | 链路冒烟：%QW 每秒 +1 | ✅ |
| 分拣线 | sorting.xml | 自定义 EdgeFB + TON 时序；SafeCoilIO 首个实战 | 13/13 |
| 双泵交替液位控制 | pump_alternation.xml | 滞后带（<30 启 / >80 停）+ 双泵轮换 + 最小运行 3s + 低液位报警 + 退出自动状态清理 | 12/12 |
| 交通灯 | traffic_light.xml | 定时器链状态机；对向双绿禁止（安全不变量） | 10/10 |
| 双缸顺序控制 | cylinder_seq.xml | CASE 步进链状态机；原位启动联锁；双电磁阀防冲突不变量（逐步校验） | 29/29 |

## 6. 与仿真侧的接口契约

1. **同一份 PLCopen XML 喂两条链路**：链路 A 直接 `iec2c`；链路 B 过 xml2st → OpenPLC。xml2st 的"matiec 可编译子集"契约是两链路的共同底座；
2. **io_map.json**：三方映射契约，字段结构的权威定义见主方案 §3.3（`plc_var` ≡ 定位变量 ↔ `io_channel` ↔ `bind`，含方向、类型、量程换算）；
3. **模拟量统一约定**：双链路统一用 **INT @ %QW + 定点换算**（不用 `REAL` 与 `%I` 区——仅链路 A 技术上可行，为保证两链路跑同一份代码而统一弃用），换算系数在 io_map 中声明；
4. **线圈访问红线**：仿真侧桥接若走链路 B，线圈写必须经 `modbus_io.SafeCoilIO`（§5.2）。

## 7. 待办（按优先级）

1. **双链路联调**：同一场景 A/B 双跑、trace 比对行为一致性（总体方案风险表"双链路行为不一致"的应对；**依赖 csk 链路 A 就绪**，已在协作看板提请求）；
2. **场景库扩充**：气缸顺序控制已完成（cylinder_seq.xml，29/29，2026-09-01）；剩余：机械臂搬运（总体方案 §5 阶段五典型场景）；
3. **配合事项**（非本侧实现）：三方一致性检查器归 gc（复用本侧 `xml2st.parse()`，见其文档 §5）；模拟量 INT 定点换算的量程字段随 io_map 契约定稿（主方案 §3.3，csk 落地），本侧参与评审。

## 8. 仓库实现索引与快速开始

```
src/pipeline/xml2st.py          ② 校验+转换（纯标准库）
src/pipeline/openplc_client.py  ④a OpenPLC v3 HTTP 客户端
src/pipeline/run_deploy.py      ④a 部署编排器（结果 JSON 供回喂）
src/pipeline/serve.py           ④a POST /deploy HTTP 服务（agent 端点，:8600）
src/pipeline/modbus_io.py       ⑤ SafeCoilIO + INT16 寄存器读
src/pipeline/verify_modbus.py   ⑤ Modbus 冒烟验证
src/pipeline/scenario_*.py      ⑤ 场景验收（sorting / pump / traffic）
src/plc/*.xml                   交付物：4 个 61131-10 场景
tests/test_xml2st.py            转换器单测（pytest，无需运行时）
workspace/                      本地生成物（不入库）
```

```bash
pip install -r requirements.txt
python -m pytest tests/ -v                                            # ① 转换器单测
python src/pipeline/run_deploy.py --xml src/plc/pump_alternation.xml  # ② 部署（需运行时）
python src/pipeline/scenario_pump.py                                  # ③ 场景验收
```
