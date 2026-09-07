#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
②b 场景描述生成器（gc 文档 §3.3 的实现，主方案 §3.3）。

职责边界（csk 分支 f39debd 已收窄：SceneSpec JSON 生成归 gc，csk 自 scene.spec.json
起负责仿真全链路）：本模块产出两个工件——
  · scene.spec.json：场景中间表示（设备清单/位姿/参数/终止条件；LLM 不写 USD，
    USD 确定性构建属 ③b）；
  · io_map.json：三方映射契约（plc_var ↔ io_channel ↔ bind{prim, quantity}，
    含方向/类型/量程换算——字段结构权威见主方案 §3.3）。

v0 为**确定性生成**（非 LLM）：同一 spec+device_model 必产出逐字节相同产物，
便于评审与 R5 对账回归；LLM 布局创意（后续）只允许改 pose/params，不允许动
io_map 骨架（io_map 是对账契约，不交给概率性组件）。

组件类型封闭枚举（本表为 gc 维护的生成侧目录；csk 组件库/SceneSpec Schema
冻结后以其为准对齐——见看板共同议题）：
  work_table / linear_axis / tool_head / pen / hmi_panel / ground

quantity 词汇表（bind.quantity，按 prim 类型分组）：
  linear_axis: joint_pos / vel_cmd / status_word
  hmi_panel : <signal>_btn（输入按钮）/ <signal>_lamp（输出指示灯）/ <signal>_target（数值端子）

用法:
    from agent.scene_gen import SceneSpecGenerator
    gen = SceneSpecGenerator()
    out = gen.generate(spec, device_model)     # → {"scene": {...}, "io_map": {...}}
"""

import re

SCENE_SPEC_VERSION = "1.0"
IO_MAP_VERSION = "1.0.0-draft.1"

# 生成侧组件类型封闭集（csk 组件库冻结后对齐）
ASSET_TYPES = {"work_table", "linear_axis", "tool_head", "pen", "hmi_panel", "ground"}

# io_list 名称 → 场景资产的确定性路由规则（axis 后缀 → 资产 id/quantity）
_AXIS_RE = re.compile(r"^([xyz])_(fb|sp|sw|v)$")
_QUANTITY_BY_SUFFIX = {"fb": "joint_pos", "v": "vel_cmd", "sw": "status_word"}

# 按钮类输入（hmi_panel）与指示灯类输出的信号语义关键词
_BUTTON_HINTS = ("btn", "cmd", "run", "jog", "quickstop", "home", "go", "draw", "reset")
_LAMP_HINTS = ("oe", "done", "moving", "fault", "pen", "plot")


def _modbus_channel(addr):
    """%QX0.3 → {area: coils, address: 3}；%QW6 → {area: holding_registers, address: 6}。"""
    m = re.match(r"^%QX(\d+)\.(\d+)$", addr)
    if m:
        return {"area": "coils", "address": int(m.group(1)) * 8 + int(m.group(2))}
    m = re.match(r"^%QW(\d+)$", addr)
    if m:
        return {"area": "holding_registers", "address": int(m.group(1))}
    raise ValueError("非法 PLC 地址 %r（统一 %%Q 区：%%QXn.n / %%QWn）" % addr)


def _device_io_points(device_model):
    """device_model.io_points 的 {name: point}（io_map 的 ⓪ 侧地址来源）；无模型时返回空。"""
    if not device_model:
        return {}
    return {p["name"]: p for p in device_model.get("io_points", [])}


def _bind_for(name, point, asset_ids):
    """信号 → {prim, quantity} 的确定性路由。"""
    m = _AXIS_RE.match(name)
    if m:
        axis, suffix = m.groups()
        prim = "/World/%s_axis" % axis
        if suffix == "sp":  # 定位设定值来自操作台 NC 数值端子
            return {"prim": "/World/panel", "quantity": "%s_target" % axis}
        return {"prim": prim, "quantity": _QUANTITY_BY_SUFFIX[suffix]}
    if point.get("dir") == "input":  # 指令类输入 → 操作台按钮
        return {"prim": "/World/panel", "quantity": "%s_btn" % name}
    return {"prim": "/World/panel", "quantity": "%s_lamp" % name}


def _axis_assets(device_model, io_list=()):
    """轴资产确定性生成：优先 device_model 运动学参数；无模型时从 io_list 的
    <axis>_fb 量程/单位推断（降级路径，地址腿同样降级——见 generate）。"""
    axes = (device_model or {}).get("kinematics", {}).get("axes", [])
    if not axes:
        fb_by_axis = {}
        for item in io_list:
            m = _AXIS_RE.match(item.get("name", ""))
            if m and m.group(2) == "fb":
                fb_by_axis[m.group(1)] = item
        axes = [{"axis": "%s_axis" % a, "type": "linear",
                 "stroke": fb.get("range") or [0, 100],
                 "unit": fb.get("unit") or "%"}
                for a, fb in sorted(fb_by_axis.items())]
    out = []
    for a in axes:
        name = a.get("axis") or a.get("device", "").rsplit("/", 1)[-1]
        short = name.replace("_axis", "")
        out.append({
            "id": "%s_axis" % short,
            "type": "linear_axis",
            "pose": {"position": [0.5 if short == "x" else 0.5, 0.5, 0.78], "rpy_deg": [0, 0, 0]},
            "params": {
                "axis": short,
                "axis_type": a.get("type", "linear"),
                "stroke": a.get("stroke") or [0, 100],
                "unit": a.get("unit") or "%",
                "vmax": a.get("vmax"), "accel": a.get("accel"), "poswin": a.get("poswin"),
                "scale_m_per_unit": 0.01 if (a.get("unit") or "%") == "%" else 0.001,
            },
        })
    return out


class SceneSpecGenerator:
    """spec (+device_model) → {scene: scene.spec, io_map: io_map}，确定性。"""

    def generate(self, spec, device_model=None):
        io_list = spec.get("io_list", [])
        points = _device_io_points(device_model)
        addr_counter = {"coils": 0, "holding_registers": 0}

        def next_addr(vtype):
            """⓪ 模型缺位时的降级：按 io_list 顺序确定性分配（线圈位空间独立进位）。"""
            if vtype == "BOOL":
                n = addr_counter["coils"]
                addr_counter["coils"] += 1
                return "%%QX%d.%d" % (n // 8, n % 8)
            n = addr_counter["holding_registers"]
            addr_counter["holding_registers"] += 1
            return "%%QW%d" % n

        asset_ids = set()
        assets = [{
            "id": "ground", "type": "ground",
            "pose": {"position": [0, 0, 0]},
            "params": {"size": [1.5, 1.5], "friction": 0.8},
        }, {
            "id": "table", "type": "work_table",
            "pose": {"position": [0.5, 0.5, 0.0]},
            "params": {"size": [1.2, 1.2, 0.75], "paper_area": "20..80 x 20..80"},
        }]
        axis_assets = _axis_assets(device_model, io_list)
        assets.extend(axis_assets)
        asset_ids.update(a["id"] for a in assets)
        assets.append({
            "id": "plot_head", "type": "tool_head", "parent": "y_axis",
            "pose": {"position": [0.5, 0.5, 0.79]},
            "params": {"carries": "pen", "acceptance_asset": True},
        })
        assets.append({
            "id": "pen", "type": "pen", "parent": "plot_head",
            "pose": {"position": [0.5, 0.5, 0.785]},
            "params": {"tip_diameter_mm": 0.5, "stroke_mm": 10},
        })
        assets.append({
            "id": "panel", "type": "hmi_panel",
            "pose": {"position": [-0.35, 0.5, 0.0]},
            "params": {"buttons": sorted(p["name"] for p in io_list if p["dir"] == "input"
                                          and p["type"] == "BOOL"),
                       "lamps": sorted(p["name"] for p in io_list if p["dir"] == "output"
                                       and p["type"] == "BOOL")},
        })
        asset_ids.update({"plot_head", "pen", "panel"})

        mappings = []
        for item in io_list:
            name = item["name"]
            point = points.get(name) or {}
            addr = point.get("address")
            if not addr:
                addr = next_addr(item["type"])
            entry = {
                "plc_var": name,
                "io_channel": {"plc_addr": addr, "modbus": _modbus_channel(addr)},
                "bind": _bind_for(name, {"dir": item["dir"]}, asset_ids),
                "dir": item["dir"],
                "type": item["type"],
            }
            if item.get("range") is not None:
                entry["range"] = item["range"]
            if item.get("unit") is not None:
                entry["unit"] = item["unit"]
            entry["scale"] = {"offset": 0, "factor": 1.0}  # 工程量=INT 原值（定点换算系数）
            mappings.append(entry)

        scene = {
            "scene_id": spec.get("task_id", "task"),
            "spec_version": SCENE_SPEC_VERSION,
            "units": "m",
            "physics": {"gravity": [0, 0, -9.81], "physics_dt": 0.008333, "solver": "tgs"},
            "ground": {"size": [1.5, 1.5], "friction": 0.8},
            "lighting": "dome",
            "assets": assets,
            "script": {
                "termination": {"max_sim_time": 90.0,
                                "early_stop": "plot_done settled"},
            },
        }
        io_map = {
            "schema_version": IO_MAP_VERSION,
            "scene_id": scene["scene_id"],
            "mappings": mappings,
        }
        problems = validate_scene_outputs(scene, io_map, io_list)
        if problems:
            raise ValueError("scene/io_map 生成产物自检失败: %s" % problems)
        return {"scene": scene, "io_map": io_map}


def _prims_of(scene):
    """场景内全部 prim 路径（/World/<asset_id>；parent 挂接不影响顶层路径）。"""
    return {"/World/%s" % a["id"] for a in scene.get("assets", [])}


def validate_scene_outputs(scene, io_map, io_list):
    """生成产物自检（编排闸门消费；问题清单为空=通过）。

    V1 资产清单完整（id 唯一、type 在封闭集、pose.position 三元）；
    V2 io_map 结构（plc_var/io_channel/bind/dir/type；主方案 §3.3）；
    V3 覆盖（io_list ↔ mappings 双向、dir/type 一致）；
    V4 bind.prim 必须落在场景资产内（杜绝悬空绑定）。
    """
    problems = []
    seen = set()
    for a in scene.get("assets", []):
        if a.get("id") in seen:
            problems.append("V1: 资产 id %r 重复" % a.get("id"))
        seen.add(a.get("id"))
        if a.get("type") not in ASSET_TYPES:
            problems.append("V1: 资产 %r 类型 %r 不在封闭集 %s"
                            % (a.get("id"), a.get("type"), sorted(ASSET_TYPES)))
        pos = (a.get("pose") or {}).get("position")
        if not (isinstance(pos, (list, tuple)) and len(pos) == 3):
            problems.append("V1: 资产 %r 缺 pose.position[x,y,z]" % a.get("id"))

    prims = _prims_of(scene)
    io_by_name = {p["name"]: p for p in io_list}
    mapped = set()
    entries = io_map.get("mappings") if isinstance(io_map, dict) else None
    if not isinstance(entries, list):
        return problems + ["V2: io_map 应为 {mappings: [...]}（主方案 §3.3）"]
    for idx, e in enumerate(entries):
        path = "mappings[%d]" % idx
        for key in ("plc_var", "io_channel", "bind", "dir", "type"):
            if key not in e:
                problems.append("V2: %s 缺字段 %r" % (path, key))
        var = e.get("plc_var")
        base = io_by_name.get(var)
        if base is None:
            problems.append("V3: %s 的 plc_var %r 不在 io_list" % (path, var))
            continue
        if e.get("dir") != base["dir"] or e.get("type") != base["type"]:
            problems.append("V3: %s dir/type 与 io_list 不一致" % path)
        bind = e.get("bind") or {}
        if not (isinstance(bind, dict) and bind.get("prim") and bind.get("quantity")):
            problems.append("V2: %s 的 bind 必须是 {prim, quantity}" % path)
        elif bind["prim"] not in prims:
            problems.append("V4: %s bind.prim %r 不在场景资产内" % (path, bind["prim"]))
        mapped.add(var)
    for name in io_by_name:
        if name not in mapped:
            problems.append("V3: io_list 变量 %r 未生成 io_map 映射" % name)
    return problems
