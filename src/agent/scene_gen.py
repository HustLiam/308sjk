#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
②b 场景描述生成器（gc 文档 §3.3 的实现，主方案 §3.3）。

契约对齐（2026-09-09，csk→gc 契约包 contract/ v1.1 落地）：本模块产出**单一工件**
scene.spec.json，其中内嵌 io_map（ioEntry 数组）——字段语义以 contract/README.md
为唯一权威，与 csk 侧 `python -m scenegen.cli validate` 闸门同源：

  · 资产类型封闭：type 必须取自 contract/components.v*.json 注册表（本模块运行时
    加载该契约文件，不维护平行的类型目录）；需要新组件 → 走 RFC，不发明类型名；
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

# io_list 名称 → 物理通道路由（fb=位置反馈→pos；cmd=位置指令→cmd）
_AXIS_RE = re.compile(r"^([xyz])_(fb|cmd)$")
_SCALE_BY_UNIT = {"%": 0.01, "mm": 0.001}  # unit → scale_m_per_unit（降级默认规则）
# io_list 类型 → ioEntry 类型（契约 enum bool|float；模拟量 INT 工程值走 float 通道）
_ENTRY_TYPE = {"BOOL": "bool", "INT": "float"}


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


def _axis_assets(device_model, io_list=()):
    """轴资产确定性生成：优先 device_model 运动学参数；无模型时从 io_list 的
    <axis>_fb 量程/单位推断（降级路径）。scale_m_per_unit 按单位缺省规则导出。"""
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
        unit = a.get("unit") or "%"
        out.append({
            "id": "%s_axis" % short,
            "type": "linear_axis",
            "pose": {"position": [0.5, 0.5, 0.78], "rpy_deg": [0, 0, 0]},
            "params": _clean_params({
                "axis": short,
                "axis_type": a.get("type", "linear"),
                "stroke": a.get("stroke") or [0, 100],
                "unit": unit,
                "vmax": a.get("vmax"), "accel": a.get("accel"), "poswin": a.get("poswin"),
                "scale_m_per_unit": _SCALE_BY_UNIT.get(unit, 0.01),
            }),
        })
    return out


def route_physical_channels(io_list, scene):
    """io_list → 契约 io_map 条目（确定性；同一函数供生成与自检复用）。

    只路由可绑物理通道：<axis>_fb（dir=input，INT）→ <axis>_axis.pos；
    <axis>_cmd（dir=output，INT）→ <axis>_axis.cmd。range=stroke×scale（SI 米）。
    其余通道（按钮/灯/NC 设定值/状态字/速度指令）无契约 quantity，不路由。
    """
    by_id = {a["id"]: a for a in scene.get("assets", [])}
    entries = []
    for item in io_list:
        m = _AXIS_RE.match(item.get("name", ""))
        if not m:
            continue
        axis, kind = m.groups()
        asset = by_id.get("%s_axis" % axis)
        if asset is None or asset.get("type") != "linear_axis":
            continue
        expect_dir = "input" if kind == "fb" else "output"
        if item.get("dir") != expect_dir:
            continue
        etype = _ENTRY_TYPE.get(item.get("type"))
        if etype != "float":
            continue
        p = asset.get("params", {})
        stroke, scale = p.get("stroke") or [0, 100], p.get("scale_m_per_unit") or 0.01
        entries.append({
            "plc_var": item["name"],
            "dir": expect_dir,
            "type": "float",
            "bind": {"asset": asset["id"],
                     "quantity": "pos" if kind == "fb" else "cmd",
                     "range": [round(stroke[0] * scale, 9), round(stroke[1] * scale, 9)]},
        })
    return entries


class SceneSpecGenerator:
    """spec (+device_model) → scene.spec.json（内嵌 io_map），确定性。"""

    def generate(self, spec, device_model=None):
        io_list = spec.get("io_list", [])
        assets = [{
            "id": "ground", "type": "ground",
            "pose": {"position": [0, 0, 0]},
            "params": {"size": [1.5, 1.5], "friction": 0.8},
        }, {
            "id": "table", "type": "work_table",
            "pose": {"position": [0.5, 0.5, 0.0]},
            "params": {"size": [1.2, 1.2, 0.75], "paper_area": "20..80 x 20..80"},
        }]
        assets.extend(axis_assets := _axis_assets(device_model, io_list))
        if axis_assets:  # 轴链按声明顺序，tool/pen 挂链尾（契约 README 字段语义 §5）
            tail = axis_assets[-1]["id"]
            assets.append({
                "id": "plot_head", "type": "tool_head", "parent": tail,
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

        io_map = route_physical_channels(io_list, {"assets": assets})
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
    V3 对账（每条 ioEntry ⊆ io_list 且 dir/类型兼容；路由覆盖——route_physical_
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

    routed = {e["plc_var"]: e for e in route_physical_channels(io_list, scene)}
    for var, expect in routed.items():
        got = next((e for e in entries if isinstance(e, dict) and e.get("plc_var") == var), None)
        if got is None:
            problems.append("V3: 可绑物理通道 %r 未进 io_map（路由覆盖，杜绝静默丢通道）" % var)
        elif (got.get("bind", {}).get("asset") != expect["bind"]["asset"]
              or got.get("bind", {}).get("quantity") != expect["bind"]["quantity"]):
            problems.append("V3: %r 绑定与确定性路由不一致（期望 %s.%s）"
                            % (var, expect["bind"]["asset"], expect["bind"]["quantity"]))
    return problems
