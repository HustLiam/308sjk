# SceneSpec 对接契约包（csk → gc）

> v1.1（2026-09-08）。本目录是 gc 侧生成 scene.spec.json 的**唯一权威依据**：组件类型、
> 参数规则、IO quantity、字段语义、单位制全部以此为准。与 `cli validate` 闸门同源——
> 违反本包的 spec 会被闸门拒绝并返回结构化错误列表（可直接拼进重生成 Prompt）。

## 本包内容

| 文件 | 用途 |
|---|---|
| `components.v1.1.json` | 机器可读契约（类型/参数/quantity/后端支持），生成器输入可直接消费 |
| `组件契约表.md` | 人类可读契约表（同源生成，供 Prompt / 评审引用） |
| `example1.json` | **反向导出实例**：从龙门模型（MJCF）逆向提取的完整 spec——展示「模型→spec」的逆映射形态与信息边界（模型不携带的字段取缺省并标注） |
| `example1_iomap.json` | 上例的 io_map 地址分配产物（%QW0–5 / %IW0–5），与 `iomap.assign_modbus` 同源 |
| `scene.spec.example.json` | **参考 spec**：绘图工位完整示例。其中 `io_map` 为 csk 代拟范本（gc 尚未交付 io 表，正式版由 gc 出） |

## 工作流（全程序调用，无人工补写环节）

```bash
# csk 侧收到 spec 后的三步（任一步失败即打回，不代拟、不跳过）：
python -m scenegen.cli validate    <spec.json>                 # ① 检查（闸门）
python -m scenegen.cli build-mjcf  <spec.json> -o <outdir>     # ② 创建：io_map 分配 + MJCF
python runtime/mujoco_jog_runtime.py --scene <outdir>/scene.spec.json \
                                      --io-map <outdir>/io_map.json   # ③ 仿真 + Modbus(:5020)
```

## 字段语义（决议，生成 spec 时请遵守）

1. **组件类型封闭**：`assets[].type` 必须取自 `components.v1.1.json` 的 `types`；
   需要新组件 → 先向 csk 发契约变更请求（走 RFC，主方案 §8.3），**不要发明类型名**；
2. **io_map 必填**：每个 IO 通道一条，`bind.quantity` 必须是该组件注册的 quantity；
   `dir=output`（PLC 输出/指令进仿真）绑 `direction=in` 的 quantity，`dir=input` 绑 `out`；
3. **单位制**：寄存器与 io_map 的 `range` 一律 **SI 米制**（`linear_axis` 的 %/mm 通过
   `scale_m_per_unit` 换算，PLC 侧工程单位换算归主站/桥——float32/INT16 共同议题）；
4. **pose 语义**：静态件（work_table/hmi_panel/ground）pose = **落位面**（底面高度）；
   运动轴 pose = **行程中心**（水平）+ 安装高度（z），q=0 = 行程起点；
5. **轴链**：多根 linear_axis 按**声明顺序**成链（x→y→z）；tool/pen 挂链尾；
   `parent` 字段仅用于静态挂接，勿与轴链冲突；
6. **paper_area**：`"a..b x c..d"` 百分比字符串，基准 = 所属 work_table 的 size，居中；
7. **动力学参数**：当前组件契约不含质量/伺服（组装器按同级别库值默认）；需要精确
   动力学的场景请在 spec 外提出，随契约 v1.2 评估。

## 地址分配规则（iomap，确定性）

按 io_map **声明顺序**：输出 bool → 线圈 %QX 逐位；输出 float → 保持寄存器 %QW
（float32 大端，2 寄存器/值）；输入 → 传感区块 %IW（bool 占 1 寄存器 0/1，float 占 2）。
产物 `io_map.json / modbus_summary.json / st_io_declaration.st` 随 build-mjcf 落盘。

## 版本与变更

契约版本随注册表演进（当前 v1.1）；任何字段语义变更走 RFC 并在本目录换版，
旧版保留。生成命令：`python -m scenegen.cli components [--json]`（与闸门同源，永不过期）。
