#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
②b 场景描述生成器（gc 文档 §3.3 的实现，主方案 §3.3）。

契约对齐（2026-09-09，csk→gc 契约包 contract/ v1.1 落地）：本模块产出**单一工件**
scene.spec.json，其中内嵌 io_map（ioEntry 数组）——字段语义以 contract/README.md
为唯一权威，与 csk 侧 `python -m scenegen.cli validate` 闸门同源：

  · 资产类型封闭：type 必须取自 contract/components.v*.json 注册表（本模块运行时
    加载该契约文件，不维护平行的类型目录）；需要新组件 → 走 RFC，不发明类型名；
  · 三轴设备走 **gantry_xyz 单资产路线**（csk 组装器原生分支=完整机械结构+逐轴
    位置执行器；travel=stroke×scale、speed=首轴 vmax×scale、pose=笔尖行程原点）；
    io_list 缺位置指令变量时合成 <axis>_cmd 驱动通道（见 is_driver_channel）；

  · io_map 只收录**可绑物理通道**（bind.quantity 必须是该组件注册的 quantity）：
    路由规则 `<axis>_fb`→`<axis>_axis`.pos（direction=out）、`<axis>_cmd`→cmd
    （direction=in）；dir=output 绑 in 量、dir=input 绑 out 量；
  · 按钮/指示灯**不进 io_map**（hmi_panel 无注册 quantity），落在 panel.params
    的 buttons/lamps（csk 运行时按名接线，见 contract/scene.spec.example.json 范本）；
  · range 一律 SI 米制 = stroke × scale_m_per_unit；
  · Modbus 地址**不在本产物**——csk 侧 build-mjcf 按 io_map 声明顺序确定性分配
    （%QX 逐位 / %QW float32 大端 / %IW 传感区块），见 contract/example1_iomap.json；
  · NC 设定值（<axis>_sp）/状态字（<axis>_sw）/速度指令（<axis>_v）为非物理通道
    （v1.1 无对应 quantity），不进 io_map——速度指令轴若需仿真支持须向 csk 发
    契约变更请求（RFC）。

v0 为**确定性生成**（非 LLM）：同一 spec+device_model 必产出逐字节相同产物，
便于评审与 R5 对账回归；LLM 布局创意（后续）只允许改 pose/params，不允许动
io_map 骨架（io_map 是对账契约，不交给概率性组件）。

用法:
    from agent.scene_gen import SceneSpecGenerator
    gen = SceneSpecGenerator()
    out = gen.generate(spec, device_model)   # → {"scene": {...内嵌 io_map...}, "io_map": [...]}
"""

import json
import re
from pathlib import Path

SCENE_SPEC_VERSION = None  # 由契约版本决定（见 _init_contract）

_CONTRACT_DIR = Path(__file__).resolve().parents[2] / "contract"

# io_list 名称 → 物理通道路由（fb=位置反馈；cmd=位置指令）
_AXIS_RE = re.compile(r"^([xyz])_(fb|cmd)$")
_SYNTH_CMD_RE = re.compile(r"^([xyz])_cmd$")
_SCALE_BY_UNIT = {"%": 0.01, "mm": 0.001}  # unit → scale_m_per_unit（降级默认规则）
# io_list 类型 → ioEntry 类型（契约 enum bool|float；模拟量 INT 工程值走 float 通道）
_ENTRY_TYPE = {"BOOL": "bool", "INT": "float"}

# gantry 路线（2026-09-09 裁决：取 csk 组装器原生单资产分支的完整机械结构——
# 底板/立柱/导轨/三级滑座/带球头补偿的笔/逐轴位置执行器）。代价：work_table
# （paper_area 百分比）与 hmi_panel（buttons/lamps）不能与 gantry 共存（组装器
# 检测到 plotter 族资产即转多资产简化路径），语义由验收脚本侧承载。
_WORKSPACE_CENTER = (0.5, 0.5)                 # 笔尖扫掠区中心（继承 v0 工位布局）
_GANTRY_FALLBACK_TRAVEL = {"x": 1.0, "y": 1.0, "z": 0.01}  # 缺轴信息时的行程缺省（米）
_GANTRY_SPEED_DEFAULT = 0.5                    # 无 vmax 时的轴速缺省（米/秒，csk 库值）


def is_driver_channel(entry):
    """判定 io_map 条目是否为「仿真侧驱动通道」（gantry 位置指令合成通道）。

    io_list 现为速度指令型（x_v 驱动伺服），无位置指令对外变量；为使模型可被
    驱动，②b 按 gantry quantity 合成 <axis>_cmd（dir=output）三条通道。它们不
    是 PLC 对外变量（不在 io_list），R5/V3 对该形态定向豁免；**待 RFC 把位置
    指令通道并入契约②（io_list/XML/AML 同步）后撤销豁免、改为真实对账**。
    """
    var = str(entry.get("plc_var", ""))
    return bool(_SYNTH_CMD_RE.match(var)
                and entry.get("dir") == "output"
                and entry.get("type") == "float"
                and (entry.get("bind") or {}).get("quantity") == var)


def _version_key(path):
    m = re.search(r"v(\d+)\.(\d+)\.json$", path.name)
    return (int(m.group(1)), int(m.group(2))) if m else (0, 0)


def load_contract(contract_dir=None):
    """加载 contract/ 下最新版机器可读契约。

    返回 (contract_version, {type: tdef})，tdef 含 quantities（name→(direction,
    dtype)）与 params 原始列表。契约换版（v1.2…）只换文件，本代码不随之修改。
    """
    directory = Path(contract_dir) if contract_dir else _CONTRACT_DIR
    files = [p for p in directory.glob("components.v*.json") if _version_key(p) > (0, 0)]
    if not files:
        raise FileNotFoundError("契约包缺失：contract/components.v*.json 不可达（%s）" % directory)
    data = json.loads(max(files, key=_version_key).read_text(encoding="utf-8"))
    registry = {
        name: {"quantities": {q["name"]: (q["direction"], q["dtype"])
                              for q in tdef.get("quantities", [])},
               "params": tdef.get("params", [])}
        for name, tdef in data["types"].items()
    }
    return data["contract_version"], registry


def _init_contract():
    version, registry = load_contract()
    global SCENE_SPEC_VERSION, CONTRACT_TYPES
    SCENE_SPEC_VERSION = version
    CONTRACT_TYPES = registry
    return version, registry


CONTRACT_VERSION, CONTRACT_TYPES = _init_contract()


def _clean_params(raw):
    """剔除值为 None 的参数（契约参数校验拒绝 null；缺省即取注册表默认）。"""
    return {k: v for k, v in raw.items() if v is not None}


def _axes_info(device_model, io_list=()):
    """归一化三轴信息 {axis: {stroke, scale, vmax}}（优先 device_model 运动学
    v1.1：vmax 读 limits 分层；无模型时从 io_list 的 <axis>_fb 量程/单位推断；
    再缺省的键不在此补——是否可走 gantry 由调用方按可解析轴集判定，缺口由
    _gantry_asset 用库缺省填）。"""
    axes = {}
    for a in (device_model or {}).get("kinematics", {}).get("axes", []):
        name = a.get("axis") or a.get("device", "").rsplit("/", 1)[-1]
        short = name.replace("_axis", "")
        unit = a.get("unit") or "%"
        axes[short] = {"stroke": a.get("stroke") or [0, 100],
                       "scale": _SCALE_BY_UNIT.get(unit, 0.01),
                       "vmax": (a.get("limits") or {}).get("vmax")}
    if not axes:
        for item in io_list:
            m = _AXIS_RE.match(item.get("name", ""))
            if m and m.group(2) == "fb":
                unit = item.get("unit") or "%"
                axes[m.group(1)] = {"stroke": item.get("range") or [0, 100],
                                    "scale": _SCALE_BY_UNIT.get(unit, 0.01),
                                    "vmax": None}
    return axes


def _travel_of(info, fallback):
    if info is None or info.get("stroke") is None:
        return fallback
    lo, hi = info["stroke"]
    return round((float(hi) - float(lo)) * info["scale"], 9)


def _clamp_to_contract(param_name, value):
    """按契约参数界钳位。当前唯一触发点：travel_z 开区间下界 >0.01 恰好排除
    绘图笔 10mm 行程（10×0.001=0.01）——钳到下界+10% 余量（笔行程量级保留，
    0=落笔/travel=抬笔语义不变）；**待与 csk 对齐 z 下界后移除**（RFC 议题）。"""
    for p in CONTRACT_TYPES.get("gantry_xyz", {}).get("params", []):
        if p.get("name") != param_name:
            continue
        lo = p.get("minimum")
        if lo is None or value > lo:
            return value
        return round(lo * 1.1, 9) if p.get("exclusive_min") else round(lo, 9)
    return value


def _gantry_asset(device_model, io_list=()):
    """gantry_xyz 单资产（csk 组装器原生分支：完整机械结构+位置执行器）。

    travel_* = stroke×scale（米，按契约界钳位）；speed 取首轴（x）vmax×scale
    （单速参数约束下以主轴为准）；pose = 笔尖行程原点（组装器按 travel 铺底板/
    纸面——扫掠区以 _WORKSPACE_CENTER 为中心，与 v0 工位布局对齐）。
    """
    info = _axes_info(device_model, io_list)
    travel = {ax: _clamp_to_contract("travel_%s" % ax,
                                     _travel_of(info.get(ax), fb))
              for ax, fb in _GANTRY_FALLBACK_TRAVEL.items()}
    vmax_x, scale_x = info.get("x", {}).get("vmax"), info.get("x", {}).get("scale")
    speed = round(vmax_x * scale_x, 9) if vmax_x and scale_x else _GANTRY_SPEED_DEFAULT
    cx, cy = _WORKSPACE_CENTER
    return {
        "id": "gantry", "type": "gantry_xyz",
        "pose": {"position": [round(cx - travel["x"] / 2, 9),
                              round(cy - travel["y"] / 2, 9), 0.0],
                 "rpy_deg": [0, 0, 0]},
        "params": _clean_params({
            "travel_x": travel["x"], "travel_y": travel["y"],
            "travel_z": travel["z"], "speed": speed,
        }),
    }


def _axis_io_bindings(device_model):
    """axis_objects.io → {short: {role: 通道名}}（device_model v1.1，生成方案 §6.2
    ②b 行：通道名从 io 绑定取）。io 缺失/None 的轴不在此列（⓪ 已记 problems）。"""
    out = {}
    for a in (device_model or {}).get("kinematics", {}).get("axes", []):
        name = a.get("axis") or a.get("device", "").rsplit("/", 1)[-1]
        short = name[:-len("_axis")] if name.endswith("_axis") else name
        if a.get("io"):
            out[short] = a["io"]
    return out


def route_physical_channels(io_list, scene, device_model=None):
    """io_list → 契约 io_map 条目（确定性；同一函数供生成与自检复用）。

    <axis>_fb → gantry.<axis>_pos、<axis>_cmd → gantry.<axis>_cmd（range=travel，
    SI 米）；io_list 缺位置指令变量时**合成** <axis>_cmd 三条驱动通道（见
    is_driver_channel——待 RFC 并入契约②后撤销）。提供 device_model 时通道名
    优先取 axis_objects.io 绑定（无绑定/无模型的轴回退 <axis>_fb 正则推断）。
    其余通道（按钮/灯/NC 设定值/状态字/速度指令）无契约 quantity 不路由。
    """
    gantry = next((a for a in scene.get("assets", [])
                   if a.get("type") == "gantry_xyz"), None)
    if gantry is None:
        return []          # 非 gantry 资产无可绑通道（plotter 多资产模块已移除）
    bindings = _axis_io_bindings(device_model)
    if bindings:
        return _route_gantry_bound(io_list, gantry, bindings)
    return _route_gantry(io_list, gantry)


def _travel_params(gantry):
    p = gantry.get("params", {})
    return {"x": p.get("travel_x", 1.0), "y": p.get("travel_y", 1.0),
            "z": p.get("travel_z", 0.01)}


def _entry(gantry, axis, plc_var, kind, travel):
    return {
        "plc_var": plc_var,
        "dir": "input" if kind == "fb" else "output",
        "type": "float",
        "bind": {"asset": gantry["id"],
                 "quantity": "%s_%s" % (axis, "pos" if kind == "fb" else "cmd"),
                 "range": [0.0, round(float(travel[axis]), 9)]},
    }


def _route_gantry_bound(io_list, gantry, bindings):
    """按 axis_objects.io 绑定路由（生成方案 §6.2）。绑定名即 io_points 实名
    （role 推断按命名规则匹配，io_list 由 ⓪ 预填保证同源），故与正则路由
    同产物；差异仅在命名偏离范本时以绑定点为准。"""
    travel = _travel_params(gantry)
    io_by_name = {item.get("name"): item for item in io_list}
    entries = []
    for axis in ("x", "y", "z"):
        fb_var = bindings.get(axis, {}).get("fb", "%s_fb" % axis)
        item = io_by_name.get(fb_var)
        if item is None or item.get("dir") != "input" \
                or _ENTRY_TYPE.get(item.get("type")) != "float":
            continue
        entries.append(_entry(gantry, axis, fb_var, "fb", travel))
    have = {e["plc_var"] for e in entries}
    for axis in ("x", "y", "z"):
        var = "%s_cmd" % axis
        if var not in have:
            entries.append({
                "plc_var": var, "dir": "output", "type": "float",
                "bind": {"asset": gantry["id"], "quantity": var,
                         "range": [0.0, round(float(travel[axis]), 9)]},
            })
    return entries


def _route_gantry(io_list, gantry):
    travel = _travel_params(gantry)
    entries = []
    for item in io_list:
        m = _AXIS_RE.match(item.get("name", ""))
        if not m:
            continue
        axis, kind = m.groups()
        expect_dir = "input" if kind == "fb" else "output"
        if item.get("dir") != expect_dir or _ENTRY_TYPE.get(item.get("type")) != "float":
            continue
        entries.append({
            "plc_var": item["name"],
            "dir": expect_dir,
            "type": "float",
            "bind": {"asset": gantry["id"],
                     "quantity": "%s_%s" % (axis, "pos" if kind == "fb" else "cmd"),
                     "range": [0.0, round(float(travel[axis]), 9)]},
        })
    # 驱动通道合成：io_list 无真实 <axis>_cmd 输出变量时补齐（模型可驱动的最低形态）
    have = {e["plc_var"] for e in entries}
    for axis in ("x", "y", "z"):
        var = "%s_cmd" % axis
        if var not in have:
            entries.append({
                "plc_var": var, "dir": "output", "type": "float",
                "bind": {"asset": gantry["id"], "quantity": var,
                         "range": [0.0, round(float(travel[axis]), 9)]},
            })
    return entries


class SceneSpecGenerator:
    """spec (+device_model) → scene.spec.json（内嵌 io_map），确定性。"""

    def generate(self, spec, device_model=None):
        io_list = spec.get("io_list", [])
        # gantry 唯一路线（plotter 多资产遗留模块已移除，2026-09-09）：
        # 三轴信息缺项用库缺省行程补齐（_GANTRY_FALLBACK_TRAVEL）
        assets = [_gantry_asset(device_model, io_list)]

        io_map = route_physical_channels(io_list, {"assets": assets}, device_model)
        scene = {
            "scene_id": spec.get("task_id", "task"),
            "spec_version": SCENE_SPEC_VERSION,
            "units": "m",
            "physics": {"gravity": [0, 0, -9.81], "physics_dt": 0.008333, "solver": "tgs"},
            "ground": {"size": [1.5, 1.5], "friction": 0.8},
            "lighting": "dome",
            "assets": assets,
            "io_map": io_map,
            "script": {
                "termination": {"max_sim_time": 90.0,
                                "early_stop": "plot_done settled"},
            },
        }
        problems = validate_scene_outputs(scene, io_list)
        if problems:
            raise ValueError("scene.spec 生成产物自检失败: %s" % problems)
        return {"scene": scene, "io_map": io_map}


def _check_asset_params(asset, tdef, problems):
    """契约参数规则（与 scenegen.validate._check_params 同源）：必填/类型/枚举/
    数值界/未知参数。契约 JSON 携带全部约束，规则不硬编码。"""
    aid, params = asset.get("id"), asset.get("params", {})
    for pdef in tdef["params"]:
        name = pdef["name"]
        if name not in params:
            if pdef.get("required"):
                problems.append("V1: 资产 %r(%s) 缺少必填参数 %r" % (aid, asset.get("type"), name))
            continue
        v = params[name]
        kind = pdef.get("kind")
        if kind == "enum":
            if v not in pdef.get("values", []):
                problems.append("V1: 资产 %r 参数 %s=%r 不在枚举 %s 内"
                                % (aid, name, v, pdef.get("values")))
        elif kind == "float":
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                problems.append("V1: 资产 %r 参数 %s=%r 须为数字" % (aid, name, v))
                continue
            lo, hi = pdef.get("minimum"), pdef.get("maximum")
            lo_ok = lo is None or (v > lo if pdef.get("exclusive_min") else v >= lo)
            if not lo_ok or (hi is not None and v > hi):
                problems.append("V1: 资产 %r 参数 %s=%s 超出契约区间 (%s%s, %s]"
                                % (aid, name, v, ">" if pdef.get("exclusive_min") else ">=", lo, hi))
        elif kind in ("vec2", "vec3"):
            n = 2 if kind == "vec2" else 3
            ok = (isinstance(v, (list, tuple)) and len(v) == n
                  and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v))
            if not ok:
                problems.append("V1: 资产 %r 参数 %s 须为 %d 元数字数组" % (aid, name, n))
        elif kind == "str":
            if not isinstance(v, str):
                problems.append("V1: 资产 %r 参数 %s=%r 须为字符串" % (aid, name, v))
        elif kind == "str_list":
            if not (isinstance(v, list) and all(isinstance(x, str) for x in v)):
                problems.append("V1: 资产 %r 参数 %s 须为字符串数组" % (aid, name))
    unknown = set(params) - {p["name"] for p in tdef["params"]}
    if unknown:
        problems.append("V1: 资产 %r(%s) 未知参数 %s（合法: %s）"
                        % (aid, asset.get("type"), sorted(unknown),
                           [p["name"] for p in tdef["params"]]))


def validate_scene_outputs(scene, io_list):
    """生成产物自检（编排闸门消费；问题清单为空=通过）。

    V0 场景骨架（scene_id/spec_version 模式、units/physics/script 结构）；
    V1 资产（id 唯一、type 在契约注册表、pose.position 三元、parent 已声明、
       参数符合契约规则）；
    V2 io_map 结构（ioEntry：plc_var/dir/type{bool,float}/bind{asset,quantity}，
       plc_var 唯一）；
    V3 对账（每条 ioEntry ⊆ io_list 且 dir/类型兼容——**合成的仿真侧驱动通道
       （is_driver_channel）豁免**，待 RFC 并入契约②；路由覆盖——route_physical_
       channels 判定可绑的物理通道必须全部在 io_map，杜绝静默丢通道）；
    V4 绑定合法（bind.asset 是场景资产、quantity 已注册于该类型、dir↔quantity
       方向匹配、type↔dtype 匹配、range 若有则下界<上界）。
    """
    problems = []
    if not re.match(r"^[a-z0-9_]{1,64}$", scene.get("scene_id") or ""):
        problems.append("V0: scene_id %r 须匹配 ^[a-z0-9_]{1,64}$" % scene.get("scene_id"))
    if not re.match(r"^[0-9]+\.[0-9]+$", scene.get("spec_version") or ""):
        problems.append("V0: spec_version %r 须为 X.Y" % scene.get("spec_version"))
    if scene.get("units") != "m":
        problems.append("V0: units 须为 'm'（SI 米制，契约 §3）")

    seen = set()
    for a in scene.get("assets", []):
        if a.get("id") in seen:
            problems.append("V1: 资产 id %r 重复" % a.get("id"))
        seen.add(a.get("id"))
        tdef = CONTRACT_TYPES.get(a.get("type"))
        if tdef is None:
            problems.append("V1: 资产 %r 类型 %r 不在契约注册表 %s"
                            % (a.get("id"), a.get("type"), sorted(CONTRACT_TYPES)))
        else:
            _check_asset_params(a, tdef, problems)
        pos = (a.get("pose") or {}).get("position")
        if not (isinstance(pos, (list, tuple)) and len(pos) == 3):
            problems.append("V1: 资产 %r 缺 pose.position[x,y,z]" % a.get("id"))
    for a in scene.get("assets", []):
        if a.get("parent") is not None and a["parent"] not in seen:
            problems.append("V1: 资产 %r 的 parent %r 未声明" % (a.get("id"), a.get("parent")))

    io_by_name = {p.get("name"): p for p in io_list}
    entries = scene.get("io_map")
    if not isinstance(entries, list):
        return problems + ["V2: scene.io_map 应为 ioEntry 数组（契约 v1.1）"]
    asset_by_id = {a.get("id"): a for a in scene.get("assets", [])}
    seen_vars = set()
    for idx, e in enumerate(entries):
        path = "io_map[%d]" % idx
        if not isinstance(e, dict):
            problems.append("V2: %s 须为对象" % path)
            continue
        var = e.get("plc_var")
        if var in seen_vars:
            problems.append("V2: %s plc_var %r 重复" % (path, var))
        seen_vars.add(var)
        if not var or e.get("dir") not in ("input", "output") or e.get("type") not in ("bool", "float"):
            problems.append("V2: %s 缺 plc_var 或 dir/type 不在契约枚举" % path)
            continue
        point = io_by_name.get(var)
        if point is None:
            if is_driver_channel(e):
                pass  # 仿真侧驱动通道（合成 <axis>_cmd）豁免 ⊆ 检查——待 RFC 并入契约②后撤销
            else:
                problems.append("V3: %s 的 plc_var %r 不在 io_list" % (path, var))
            continue
        if e["dir"] != point.get("dir"):
            problems.append("V3: %s dir=%r 与 io_list %r 不一致" % (path, e["dir"], point.get("dir")))
        if _ENTRY_TYPE.get(point.get("type")) != e["type"]:
            problems.append("V3: %s type=%r 与 io_list %r 不兼容（BOOL↔bool、INT↔float）"
                            % (path, e["type"], point.get("type")))
        bind = e.get("bind") or {}
        if not (isinstance(bind, dict) and bind.get("asset") and bind.get("quantity")):
            problems.append("V2: %s 的 bind 必须是 {asset, quantity}" % path)
            continue
        target = asset_by_id.get(bind["asset"])
        if target is None:
            problems.append("V4: %s bind.asset %r 不在场景资产内" % (path, bind["asset"]))
            continue
        quantity = CONTRACT_TYPES.get(target.get("type"), {}).get("quantities", {}).get(bind["quantity"])
        if quantity is None:
            problems.append("V4: %s %s 无注册 quantity %r"
                            % (path, target.get("type"), bind["quantity"]))
            continue
        want_dir = "out" if e["dir"] == "input" else "in"
        if quantity[0] != want_dir:
            problems.append("V4: %s dir=%s 须绑 direction=%s 的 quantity，%s 是 %s"
                            % (path, e["dir"], want_dir, bind["quantity"], quantity[0]))
        if quantity[1] != e["type"]:
            problems.append("V4: %s type=%s 与 quantity %s(%s) 不匹配"
                            % (path, e["type"], bind["quantity"], quantity[1]))
        rng = bind.get("range")
        if rng is not None and not (isinstance(rng, (list, tuple)) and len(rng) == 2 and rng[0] < rng[1]):
            problems.append("V4: %s bind.range 须为 [min,max] 且 min<max" % path)

    # 路由覆盖按绑定（asset+quantity）判存在——plc_var 命名归 io_list/范本自由
    # （csk example1 用 AxisX_cmd，与合成 x_cmd 同绑 gantry.x_cmd，覆盖即满足）
    routed = route_physical_channels(io_list, scene)
    for expect in routed:
        key = (expect["bind"]["asset"], expect["bind"]["quantity"])
        got = next((e for e in entries if isinstance(e, dict)
                    and (e.get("bind", {}).get("asset"), e.get("bind", {}).get("quantity")) == key),
                   None)
        if got is None:
            problems.append("V3: 可绑物理通道 %s.%s 未进 io_map（路由覆盖，杜绝静默丢通道）" % key)
    return problems
