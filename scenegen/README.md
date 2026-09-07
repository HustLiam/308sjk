# scenegen —— 仿真环境生成模块

闭环系统③号模块：**SceneSpec（JSON 中间表示）→ USD 仿真场景 + io_map 契约**。
LLM 只生成 SceneSpec（语法面小、可校验），确定性代码转换为 USD——场景合法性由
组件库与校验器保证，与代码生成、运行时模块的接口详见
`../docs/仿真环境与IO闭环详细设计.md` 第 2 章。

## 安装

```bash
pip install -r requirements.txt        # usd-core（pxr）+ jsonschema，Python 3.10+（3.12 与 Isaac Sim 6.0 对齐）
```

运行时（Isaac Sim headless 冒烟）另需 `isaacsim`，见文档 3.2 节；未安装时冒烟退化为纯 pxr 结构检查。

## 用法

```bash
# 静态校验（Schema + 参数规则 + 引用完整性 + 布局穿模 + 物理量纲）
python -m scenegen.cli validate scenegen/examples/conveyor_sort.json

# 构建：scene.usda + io_map.json + st_io_declaration.st + modbus_summary.json
python -m scenegen.cli build scenegen/examples/conveyor_sort.json -o out/example

# 全流程（校验 → 构建 → 结构冒烟）
python -m scenegen.cli all scenegen/examples/conveyor_sort.json -o out/example

# 回归测试（无 pytest 依赖）
python scenegen/tests/test_scenegen.py
python scenegen/tests/test_agent.py     # agent 离线测试
```

## SceneSpec 生成 Agent（scenegen/agent/）

从需求理解模块的 `requirement_spec.json` 生成 SceneSpec，内置"生成 → 校验 → 定向反馈 → 重试"闭环：

```bash
# 离线 Mock 生成器（无 API Key 时验证全流程）
python -m scenegen.agent.cli gen scenegen/agent/examples/requirement_sort.json -o out/agent --mock

# 接真实 LLM（任意 OpenAI 兼容端点：GLM/OpenAI/DeepSeek/vLLM/Ollama）
export SCENEGEN_LLM_BASE_URL=https://open.bigmodel.cn/api/paas/v4
export SCENEGEN_LLM_API_KEY=你的key
export SCENEGEN_LLM_MODEL=glm-4.7
python -m scenegen.agent.cli gen 你的requirement.json -o out/agent
```

要点：

- **Prompt 里的组件目录自动从 `components.REGISTRY` 导出**——新增组件后提示词自动同步，不会漂移；
- **io_map 契约由 agent 层强制**：`io_map` 必须与 `io_list` 名字/数量/方向/类型完全一致
  （validator 看不到需求规格，这层检查在 `agent/core.py:contract_errors`）；
- 校验失败的错误列表（带资产 id 与原因）直接拼进反馈 Prompt 定向修复，默认重试 4 次；
- 产物：`scene.spec.json`（通过全部校验）、`scene.usda` + `io_map.json` + `st_io_declaration.st`
  （`--no-build` 可跳过构建）、`gen_report.json`（每轮尝试与错误历史）。


## 模块结构

| 文件 | 职责 |
|---|---|
| `schema.json` | SceneSpec 的 JSON Schema（draft-07），`type`/参数枚举封闭 |
| `geom.py` | 位姿复合（父子链）、欧拉角/四元数、包围盒变换 |
| `components.py` | 组件注册表：quantity 清单、参数规则、USD 构建函数、包围盒 |
| `validate.py` | 五层静态校验，错误信息面向 LLM 反馈（带资产 id 与具体原因） |
| `iomap.py` | io_map 富化：usd_prim 绑定 + OpenPLC Modbus 地址确定性分配 |
| `build_usd.py` | 确定性构建器（只使用标准 UsdPhysics 模式，Isaac 直接可跑） |
| `smoke.py` | 结构冒烟（纯 pxr）+ Isaac headless 冒烟（可选） |
| `cli.py` | 命令行入口 |

## 产物说明（outdir）

- **`scene.usda`**：USD 场景（文本格式，可 diff、可入 git）。含物理场景/重力、地面、
  物理材质、各组件实例（刚体/碰撞/关节驱动/限位已按参数烘焙）；组件根 prim 带
  `simio:*` 标记属性供运行时 IOBridge 自检。
- **`io_map.json`**：三方契约（`plc_var` ↔ `usd_prim` ↔ `modbus`）。
  Modbus 地址按 io_map 声明顺序确定性分配：
  输出 bool → `%QX` 线圈；输出 float → `%QW`（2 寄存器 float32 大端）；
  输入（bool/float）→ Isaac 传感区块 / OpenPLC `%IW`（bool 占 1 寄存器 0/1）。
- **`st_io_declaration.st`**：与地址分配一致的 ST 全局定位变量声明，
  供代码生成模块（②）生成 `plc_project.xml` 时对齐，保证
  `io_list ≡ ST 定位变量 ≡ io_map` 三方一致。
- **`modbus_summary.json`**：运行时/桥端通道概览（线圈数、%QW 寄存器数、
  传感区块长度、OpenPLC 轮询表配置项）。

## 首批组件（封闭枚举）

`conveyor_belt` / `pneumatic_cylinder` / `photoelectric_sensor` / `bin_chute` /
`rigid_box` / `contact_pad` / `vacuum_gripper` / `gantry_xyz`（三轴龙门，X/Y/Z
直线轴 + 笔针，q=0..travel，Z 行程末端笔尖触台面）/ `articular_arm`（引用外部 USD）

每个组件在 `components.py` 的 `REGISTRY` 注册四件事：quantity 清单（名称/方向/类型）、
参数规则（区间与枚举）、USD 构建函数、局部包围盒。新增组件 = 新增一个注册项，
Schema 的封闭枚举随即生效，校验器与构建器无需改动。

## 与闭环其他模块的边界

- 输入：`requirement_spec.json` 的 io_list（IO 单一源头）→ LLM 生成 SceneSpec；
- 输出：`scene.usda` + `io_map.json` 交执行与仿真引擎（④）；
  `st_io_declaration.st` 交代码生成模块（②）；
- 校验失败错误列表直接拼进反馈 Prompt，LLM 定向修改后重生成。
