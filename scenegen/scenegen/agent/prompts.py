"""Prompt 组装：组件目录自动从 REGISTRY 导出（与代码永远同步），含契约规则与示例。"""

import json
import os
from typing import Dict

from .. import components

_HERE = os.path.dirname(os.path.abspath(__file__))
_EXAMPLE_PATH = os.path.normpath(os.path.join(_HERE, "..", "examples", "conveyor_sort.json"))


def _fmt_range(p: components.ParamSpec) -> str:
    if p.kind == "enum":
        return f"枚举 {list(p.values)}"
    if p.kind == "vec3":
        return "3 元数字数组"
    if p.kind == "str":
        return "字符串"
    if p.minimum is not None:
        op = ">" if p.exclusive_min else ">="
        return f"{op}{p.minimum}" + (f" 且 <={p.maximum}" if p.maximum is not None else "")
    return "数值"


def component_catalog() -> str:
    """把 REGISTRY 导出为 Prompt 用的组件目录（type 封闭枚举的唯一权威来源）。"""
    lines = []
    for cdef in components.REGISTRY.values():
        qtext = ", ".join(f"{q.name}({q.direction},{q.dtype})" for q in cdef.quantities)
        lines.append(f"- {cdef.type_name}  quantity: {qtext}")
        if cdef.params:
            ptext = "; ".join(
                f"{p.name}={_fmt_range(p)}{'必填' if p.required else f'默认{p.default}'}"
                for p in cdef.params)
            lines.append(f"    参数: {ptext}")
    return "\n".join(lines)


def example_spec() -> Dict:
    with open(_EXAMPLE_PATH, encoding="utf-8") as f:
        return json.load(f)


SYSTEM_PROMPT = f"""你是工业仿真场景生成 Agent。输入是需求理解模块的输出（requirement_spec.json），\
你的任务是产出**一个 JSON**（SceneSpec），它将被确定性代码转换为 Isaac Sim 的 USD 仿真场景。

## 输出 JSON 的顶层结构（缺一不可，不得增删改名）
scene_id, spec_version, units, physics, ground, lighting, assets, io_map, script
其中 script 内必须含 termination；assets 元素含 id/type/pose(+/parent/params)；\
io_map 元素含 plc_var/dir/type/bind。

## 可用组件（type 封闭枚举，只能用下列类型，禁止发明新类型）
{component_catalog()}

quantity 方向约定：in=指令进仿真（对应 PLC 输出）；out=量测出仿真（对应 PLC 输入）。

## io_map 契约（硬性）
- io_map 的 plc_var 必须与需求规格 io_list **逐一对应**：名字相同、数量相同、dir 相同、type 相同，不多不少；
- dir=input 的条目只能绑定 direction=out 的 quantity，dir=output 只能绑定 direction=in 的 quantity；
- type 必须与 quantity 的数据类型一致（bool↔bool，float↔float）。

## 坐标与布局约定（Z 轴向上，单位米，地面 z=0）
- 传送带中心为位姿原点，带面顶 = height/2；物料箱投放位置略高于带面（+0.02~0.05）；
- 气缸装在带侧面（parent=传送带），axis 指向推送方向，stroke 到带中心线附近，料槽在推送方向带外落地；
- 光电传感器横跨带面（beam_direction 指向另一侧，beam_length ≥ 带宽）；
- gantry_xyz 为固定基座三轴模组：travel_x/y/z 为各轴行程，轴指令/位置量程均为 0..travel（米）；\
z=0 为抬笔、z=travel_z 为笔尖触台面，行程中央即画圆圆心；io_list 的 range 应与行程一致；
- parent 存在时 pose 是相对父级的局部位姿；资产包围盒不得互相穿模（校验器会检查）；
- script.spawn_schedule 描述物料投放，termination.max_sim_time 按任务时长设置。

## 完整示例（结构照此，内容按需求调整）
{json.dumps(example_spec(), ensure_ascii=False, indent=1)}

## 输出
只输出一个 JSON 对象（SceneSpec），不要 markdown 代码块、不要解释文字。
"""

_DEVICE_HINT = """
## 需求 device 语义 → 组件类型参考
光电/检测→photoelectric_sensor；气缸/推出→pneumatic_cylinder；传送带/输送→conveyor_belt；
料槽/料箱→bin_chute；夹爪/吸盘→vacuum_gripper；机械臂→articular_arm；箱体/物料→rigid_box（作 spawn 模板）。
"""


def build_user_prompt(requirement: Dict) -> str:
    return (
        "请根据以下需求规格生成 SceneSpec JSON。\n"
        + _DEVICE_HINT
        + "需求规格：\n" + json.dumps(requirement, ensure_ascii=False, indent=2)
    )


def build_feedback_prompt(errors: list, previous_spec: Dict | None) -> str:
    prev = json.dumps(previous_spec, ensure_ascii=False) if previous_spec else "（无）"
    errs = "\n".join(f"- {e}" for e in errors)
    return (
        f"你上一轮生成的 SceneSpec 未通过校验，错误如下（已带资产 id 与原因，请定向修改，"
        f"不要改动无关部分）：\n{errs}\n\n"
        f"上一轮输出：\n{prev}\n\n"
        "请输出修正后的完整 SceneSpec JSON（仍然只输出 JSON）。"
    )


def extract_json(text: str) -> Dict | None:
    """从 LLM 回复中提取 JSON：优先整体解析，再剥 markdown 围栏，再取首尾大括号。"""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    if "```" in text:
        inner = text.split("```")[1]
        inner = inner[inner.find("\n") + 1:] if inner[:4].lower().startswith("json") else inner
        try:
            return json.loads(inner.strip())
        except json.JSONDecodeError:
            pass
    lo, hi = text.find("{"), text.rfind("}")
    if lo >= 0 and hi > lo:
        try:
            return json.loads(text[lo:hi + 1])
        except json.JSONDecodeError:
            return None
    return None
