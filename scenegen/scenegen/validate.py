"""SceneSpec 静态校验：JSON Schema + 组件参数规则 + 引用完整性 + 布局/物理检查。

返回错误字符串列表（空 = 通过）。所有错误信息面向 LLM 反馈：带资产 id 与具体原因。
"""

import json
import os
from typing import Any, Dict, List, Tuple

import jsonschema

from . import components, geom

_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.json")
_OVERLAP_TOL = 0.01  # 相交深度超过该值视为穿模（1cm）
_GROUND_MARGIN = 1.0


def _load_schema() -> Dict[str, Any]:
    with open(_SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def _check_params(asset: Dict[str, Any], errors: List[str]) -> None:
    cdef = components.REGISTRY[asset["type"]]
    params = asset.get("params", {})
    for spec in cdef.params:
        if spec.name not in params:
            if spec.required:
                errors.append(f"{asset['id']}({asset['type']}): 缺少必填参数 {spec.name}")
            continue
        v = params[spec.name]
        if spec.kind == "enum":
            if v not in spec.values:
                errors.append(
                    f"{asset['id']}: 参数 {spec.name}={v!r} 不在枚举 {list(spec.values)} 内")
        elif spec.kind in ("float",):
            if not isinstance(v, (int, float)) or isinstance(v, bool):
                errors.append(f"{asset['id']}: 参数 {spec.name} 须为数字，得到 {v!r}")
                continue
            lo_ok = spec.minimum is None or (v > spec.minimum if spec.exclusive_min else v >= spec.minimum)
            hi_ok = spec.maximum is None or v <= spec.maximum
            if not lo_ok or not hi_ok:
                op = ">" if spec.exclusive_min else ">="
                errors.append(
                    f"{asset['id']}: 参数 {spec.name}={v} 超出区间 ({op}{spec.minimum}, <={spec.maximum})")
        elif spec.kind == "vec3":
            ok = (isinstance(v, (list, tuple)) and len(v) == 3
                  and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v))
            if not ok:
                errors.append(f"{asset['id']}: 参数 {spec.name} 须为 3 元数字数组")
        elif spec.kind == "str":
            if not isinstance(v, str):
                errors.append(f"{asset['id']}: 参数 {spec.name} 须为字符串")
    unknown = set(params) - {p.name for p in cdef.params}
    if unknown:
        errors.append(
            f"{asset['id']}({asset['type']}): 未知参数 {sorted(unknown)}，"
            f"合法参数: {[p.name for p in cdef.params]}")


def _world_poses(spec: Dict[str, Any]) -> Dict[str, Tuple[geom.Vec3, geom.Mat3]]:
    """解析父子链得到每个资产的世界位姿；同时做存在性/环检测。"""
    by_id = {a["id"]: a for a in spec["assets"]}
    poses: Dict[str, Tuple[geom.Vec3, geom.Mat3]] = {}

    def resolve(aid: str, seen: Tuple[str, ...]) -> Tuple[geom.Vec3, geom.Mat3]:
        if aid in poses:
            return poses[aid]
        if aid in seen:
            raise ValueError(f"parent 链成环: {' -> '.join(seen + (aid,))}")
        a = by_id[aid]
        parent = a.get("parent")
        if parent is None or parent not in by_id:
            # parent 缺失的错误由引用完整性检查负责；此处按世界原点继续，保证布局检查可运行
            base = ((0.0, 0.0, 0.0), ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
        else:
            base = resolve(parent, seen + (aid,))
        pose = a["pose"]
        rpy = pose.get("rpy_deg", (0, 0, 0))
        wp = geom.compose_pose(base[0], base[1], tuple(pose["position"]), geom.rpy_deg_matrix(rpy))
        poses[aid] = wp
        return wp

    for aid in by_id:
        resolve(aid, ())
    return poses


def validate(spec: Dict[str, Any]) -> List[str]:
    errors: List[str] = []

    # ① JSON Schema
    try:
        jsonschema.validate(spec, _load_schema())
    except jsonschema.ValidationError as e:
        path = "/".join(str(p) for p in e.absolute_path)
        errors.append(f"Schema 校验失败 @ {path or '<root>'}: {e.message}")
        return errors  # 结构不完整时后续检查无意义

    # ② 组件类型 + 参数规则
    ids = set()
    for asset in spec["assets"]:
        if asset["id"] in ids:
            errors.append(f"资产 id 重复: {asset['id']}")
        ids.add(asset["id"])
        if asset["type"] not in components.REGISTRY:
            errors.append(
                f"{asset['id']}: 未知组件类型 {asset['type']!r}，"
                f"封闭枚举: {sorted(components.REGISTRY)}")
        else:
            _check_params(asset, errors)

    valid_assets = {a["id"]: a for a in spec["assets"] if a["type"] in components.REGISTRY}

    # ③ 引用完整性（parent / io_map 绑定 / spawn 模板）
    for asset in spec["assets"]:
        parent = asset.get("parent")
        if parent is not None and parent not in ids:
            errors.append(f"{asset['id']}: parent {parent!r} 未声明")
    for e in spec["io_map"]:
        bind = e["bind"]
        target = valid_assets.get(bind["asset"])
        if target is None:
            errors.append(f"io_map[{e['plc_var']}]: 绑定资产 {bind['asset']!r} 不存在或类型非法")
            continue
        q = components.quantity_of(target["type"], bind["quantity"])
        if q is None:
            errors.append(
                f"io_map[{e['plc_var']}]: {target['type']} 无 quantity {bind['quantity']!r}，"
                f"可用: {[x.name for x in components.REGISTRY[target['type']].quantities]}")
            continue
        want_dir = "out" if e["dir"] == "input" else "in"
        if q.direction != want_dir:
            errors.append(
                f"io_map[{e['plc_var']}]: dir={e['dir']} 应绑定 direction={want_dir} 的 quantity，"
                f"{bind['quantity']} 是 {q.direction}")
        if q.dtype != e["type"]:
            errors.append(
                f"io_map[{e['plc_var']}]: type={e['type']} 与 quantity {bind['quantity']}({q.dtype}) 不匹配")
        rng = bind.get("range")
        if rng and rng[0] >= rng[1]:
            errors.append(f"io_map[{e['plc_var']}]: range 下界须小于上界")
    plcs = [e["plc_var"] for e in spec["io_map"]]
    if len(plcs) != len(set(plcs)):
        errors.append("io_map 存在重复 plc_var")
    for spawn in spec["script"].get("spawn_schedule", []):
        if spawn["asset_template"] not in ids:
            errors.append(f"spawn_schedule 模板 {spawn['asset_template']!r} 未声明")

    # ④ 布局：父子位姿复合 + 包围盒两两穿模 + 地面范围
    try:
        world = _world_poses(spec)
    except ValueError as e:
        errors.append(str(e))
        return errors

    boxes = {}
    for aid, asset in valid_assets.items():
        cdef = components.REGISTRY[asset["type"]]
        local = cdef.footprint(components.apply_params(cdef, asset.get("params", {})))
        if local is None:
            continue
        pos, rot = world[aid]
        boxes[aid] = geom.aabb_transform(local[0], local[1], pos, rot)

    parent_pairs = {(a.get("parent"), a["id"]) for a in spec["assets"] if a.get("parent")}
    ids_list = sorted(boxes)
    for i, a1 in enumerate(ids_list):
        for a2 in ids_list[i + 1:]:
            if (a1, a2) in parent_pairs or (a2, a1) in parent_pairs:
                continue
            depth = geom.aabb_overlap_depth(boxes[a1], boxes[a2])
            if all(d > _OVERLAP_TOL for d in depth):
                errors.append(
                    f"布局穿模: {a1} 与 {a2} 包围盒三轴交叠 {tuple(round(d, 3) for d in depth)} m"
                    f"（容差 {_OVERLAP_TOL} m）")

    ground = spec.get("ground", {})
    if ground.get("size"):
        half = [s / 2 + _GROUND_MARGIN for s in ground["size"]]
        for aid, (mins, maxs) in boxes.items():
            for k, lim in enumerate(half):
                if mins[k] < -lim or maxs[k] > lim:
                    errors.append(f"{aid}: 超出地面范围（轴 {['x','y','z'][k]}）")

    # ⑤ 物理量纲
    g = spec.get("physics", {}).get("gravity", (0, 0, -9.81))
    if sum(v * v for v in g) == 0.0:
        errors.append("physics.gravity 不能为零向量")
    for spawn in spec["script"].get("spawn_schedule", []):
        if spawn["at_time"][0] > spec["script"]["termination"]["max_sim_time"]:
            errors.append("spawn_schedule 事件晚于 termination.max_sim_time")

    return errors
