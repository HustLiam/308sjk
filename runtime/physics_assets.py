"""可替换的物理参数资产；与场景接口契约分开，不接受可执行代码。"""
import copy
import json
import math
from pathlib import Path

DEFAULT_PHYSICS = Path(__file__).with_name("physics_defaults.json")
VECTOR_KEYS = {"mass", "kp", "damping", "saddle_half", "head_half", "slider_half"}
SCALAR_KEYS = {"pen_length", "pen_radius"}


def validate_physics(data):
    if not isinstance(data, dict) or set(data) != {"schema_version", "units", "gantry"}:
        raise ValueError("物理参数须包含 schema_version、units 和 gantry")
    if data["schema_version"] != "1.0" or data["units"] != "SI":
        raise ValueError("物理参数仅支持 schema_version=1.0、units=SI")
    constants = data["gantry"]
    if not isinstance(constants, dict) or set(constants) != VECTOR_KEYS | SCALAR_KEYS:
        raise ValueError("gantry 物理参数字段不完整或含未知字段")
    for key, value in constants.items():
        values = value if key in VECTOR_KEYS else [value]
        if not isinstance(values, list) or (key in VECTOR_KEYS and len(values) != 3):
            raise ValueError("物理参数 %s 须为三个数值（X/Y/Z 或半尺寸）" % key)
        for number in values:
            if (isinstance(number, bool) or not isinstance(number, (int, float)) or
                    not math.isfinite(number) or number > 1e6 or
                    (number < 0 if key == "damping" else number <= 0)):
                raise ValueError("物理参数 %s 须为有限正数（阻尼可为 0），且不超过 1e6" % key)
    if constants["pen_radius"] >= constants["pen_length"]:
        raise ValueError("笔长度须大于笔半径")
    return copy.deepcopy(data)


def load_physics(path=DEFAULT_PHYSICS):
    return validate_physics(json.loads(Path(path).read_text(encoding="utf-8-sig")))
