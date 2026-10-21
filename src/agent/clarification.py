"""生成前主动澄清。对话元数据单独保存，不修改 requirement_spec 契约。"""

import json
import math
import re

from .trajectory import DEFAULTS, goal_text, plan_with_confirm

_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)"
_SHAPES = {"square": r"正方形|方形|square", "circle": r"圆|circle"}


class ClarificationRequired(ValueError):
    def __init__(self, questions):
        self.questions = questions
        super().__init__("需求仍需澄清：" + "；".join(q["question"] for q in questions))


def extract_explicit_geometry(text):
    """保守的离线识别；只抽取有明确标签的数字，不从任意数字猜几何。"""
    found = [shape for shape, pattern in _SHAPES.items() if re.search(pattern, text, re.I)]
    shape = found[0] if len(found) == 1 else "unknown"
    params = {}
    for key, label in (("size", r"边长|size"), ("radius", r"半径|radius"),
                       ("segments", r"段数|segments")):
        match = re.search(r"(?:%s)\s*[:：=]?\s*(%s)" % (label, _NUMBER), text, re.I)
        if match:
            value = float(match[1])
            params[key] = int(value) if value.is_integer() else value
    match = re.search(r"(?:中心|圆心|center)\s*[:：=]?\s*[（(\[]?\s*(%s)\s*[,，]\s*(%s)" %
                      (_NUMBER, _NUMBER), text, re.I)
    if match:
        params["center"] = [float(match[1]), float(match[2])]
    match = re.search(r"(?:直径|diameter)\s*[:：=]?\s*(%s)" % _NUMBER, text, re.I)
    if match and "radius" not in params:
        params["radius"] = float(match[1]) / 2
    return {"shape": shape, "params": params, "multiple_shapes": len(found) > 1}


def _coordinate_info(device_model):
    axes = (device_model or {}).get("kinematics", {}).get("axes", [])
    return {a["axis"].removesuffix("_axis").lower(): a for a in axes}


def _question(key, question, default=None, **extra):
    return {"key": key, "question": question, "default": default, **extra}


def analyze_request(text, device_model=None, shape_info=None):
    """返回具体问题、明确参数与来源；几何缺项有可审查的站默认值。"""
    local = extract_explicit_geometry(text)
    info = shape_info or {}
    shape = local["shape"] if local["shape"] != "unknown" else info.get("shape", "unknown")
    if local["multiple_shapes"]:
        shape = "unknown"
    params = {k: v for k, v in (info.get("params") or {}).items() if v is not None}
    params.update(local["params"])
    questions = []
    drawing = bool(re.search(r"画|绘制|绘图(?!仪)|draw|plot", text, re.I) or shape in DEFAULTS)
    if drawing and shape not in DEFAULTS:
        questions.append(_question("shape", "这次要画正方形还是圆？当前一次规划一个图形。", choices=["square", "circle"]))
    elif not drawing and (len(text.strip()) < 8 or re.fullmatch(r"[请帮我\s]*(?:控制|运行|启动)(?:设备|机器|系统)[。！!]?", text)):
        questions.append(_question("process", "设备要按什么顺序完成哪些动作？请补充启动条件和完成状态。"))
    axes = _coordinate_info(device_model)
    units = {a.get("unit") for key, a in axes.items() if key in ("x", "y") and a.get("unit")}
    unit = next(iter(units)) if len(units) == 1 else "设备坐标"
    # 百分比坐标不能把用户的毫米数原样当坐标值。
    physical = bool(re.search(r"毫米|厘米|(?<![a-z])(?:mm|cm)(?![a-z])", text, re.I))
    mismatch = physical and (unit in ("%", "设备坐标") or len(units) != 1)
    if mismatch:
        questions.append(_question("coordinate_values", "需求使用物理长度，设备轴使用 %s。请提供换算后的设备坐标参数 JSON（如 {\"size\": 3, \"center\": [50,50]}），不能直接把毫米当设备坐标。" % unit))
    if shape in DEFAULTS:
        labels = {"center": "绘图中心 [x,y]", "size": "正方形边长", "radius": "圆半径", "segments": "圆折线段数（8..360）"}
        for key, default in DEFAULTS[shape].items():
            if params.get(key) is None:
                questions.append(_question(key, "%s是多少？%s" % (labels[key], "坐标单位：%s。" % unit if key != "segments" else ""), default))
    return {"shape": shape, "params": params, "questions": questions,
            "unit": unit, "drawing": drawing}


def _parse_answer(key, value):
    if not isinstance(value, str):
        return value
    text = value.strip()
    if key == "shape":
        aliases = {"正方形": "square", "方形": "square", "圆": "circle", "圆形": "circle"}
        return aliases.get(text, text.lower())
    if key == "center":
        return json.loads(text) if text.startswith("[") else [float(v) for v in re.split(r"[,，]", text)]
    if key == "coordinate_values":
        return json.loads(text)
    if key == "process":
        return text
    return json.loads(text)


def prepare_request(text, device_model=None, shape_info=None, answers=None, ask=None, accept_defaults=False):
    """ask(question) 返回答案；无输入来源的必需问题抛 ClarificationRequired。"""
    info = analyze_request(text, device_model, shape_info)
    answers = dict(answers or {})
    resolved, sources = [], {}

    def resolve(q):
        key = q["key"]
        for attempt in range(3):
            raw = answers.get(key) if attempt == 0 else None
            source = "answer" if raw is not None else "interactive"
            if raw is None and ask is not None:
                raw = ask(q)
            if raw is None or raw == "":
                if q["default"] is not None and (accept_defaults or ask is not None):
                    raw, source = q["default"], "default"
                else:
                    raise ClarificationRequired([q])
            try:
                value = _parse_answer(key, raw)
                if key == "shape" and value not in DEFAULTS:
                    raise ValueError("请选择 square 或 circle")
                if key == "process" and (not isinstance(value, str) or len(value.strip()) < 8):
                    raise ValueError("请说明具体动作、启动条件与完成状态")
                if key == "coordinate_values" and (not isinstance(value, dict) or not value):
                    raise ValueError("请输入非空参数 JSON 对象")
                if key == "center":
                    if not isinstance(value, (list, tuple)) or len(value) != 2:
                        raise ValueError("中心需要两个坐标")
                    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value):
                        raise ValueError("中心需要两个有限数值")
                if key in ("size", "radius", "segments"):
                    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                        raise ValueError("请输入有限正数")
                    if key == "segments" and (not isinstance(value, int) or not 8 <= value <= 360):
                        raise ValueError("段数必须是 8..360 的整数")
                resolved.append({"key": key, "question": q["question"], "answer": value, "source": source})
                sources[key] = source
                return value
            except (ValueError, TypeError) as exc:
                if ask is None or attempt == 2:
                    raise ClarificationRequired([{**q, "question": "%s（%s）" % (q["question"], exc)}]) from exc
                q = {**q, "question": "上次输入无效：%s。%s" % (exc, q["question"])}
        raise AssertionError("unreachable")

    for q in info["questions"]:
        if q["key"] == "shape":
            info["shape"] = resolve(q)
        elif q["key"] == "process":
            text += "\n补充工艺：" + resolve(q)
        elif q["key"] == "coordinate_values":
            info["params"].update(resolve(q))
    shape = info["shape"]
    trajectory = None
    if shape in DEFAULTS:
        params = {k: v for k, v in info["params"].items() if k in DEFAULTS[shape]}
        params.update({k: _parse_answer(k, v) for k, v in answers.items() if k in DEFAULTS[shape]})
        for key, default in DEFAULTS[shape].items():
            if key not in params:
                q = next((q for q in info["questions"] if q["key"] == key),
                         _question(key, "请确认 %s（设备坐标单位 %s）" % (key, info["unit"]), default))
                params[key] = resolve(q)
            else:
                sources.setdefault(key, "answer" if key in answers else "explicit")
        for attempt in range(3):
            try:
                trajectory = plan_with_confirm(shape, params)
                for name, axis in _coordinate_info(device_model).items():
                    if name not in ("x", "y", "z") or not axis.get("stroke"):
                        continue
                    idx, (low, high) = "xyz".index(name), axis["stroke"]
                    points = [s["to"] for s in trajectory["steps"] if s.get("to")]
                    points += [p for s in trajectory["steps"] for p in s.get("waypoints", [])]
                    if any(not low <= p[idx] <= high for p in points):
                        raise ValueError("%s 轴目标超出 AML 行程 %s..%s" % (name, low, high))
                break
            except (ValueError, TypeError) as exc:
                if ask is None or attempt == 2:
                    raise ClarificationRequired([_question("geometry", "轨迹参数无效：%s；请修正中心或尺寸。" % exc)]) from exc
                for key in ("center", "size" if shape == "square" else "radius"):
                    params[key] = resolve(_question(key, "轨迹无法执行：%s。请重新给出 %s" % (exc, key), params[key]))
        text += "\n已确认几何（设备坐标 %s，以此为准）：%s" % (info["unit"], goal_text(trajectory))
    return {"request_text": text, "trajectory": trajectory,
            "clarifications": resolved, "sources": sources, "unit": info["unit"]}
