"""组件注册表：每个工业组件的 quantity 清单、参数规则、USD 构建函数与包围盒。

只使用标准 UsdPhysics/UsdGeom 模式，产出的 USD 在 Isaac Sim 中直接可跑，
在纯 usd-core 环境中可构建与检查。组件构建函数在根 Xform 的局部坐标系内
authoring（世界位姿由 build_usd 统一复合后写到根 Xform 上）。
"""

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

from . import geom

AABB = Tuple[geom.Vec3, geom.Vec3]


# ---------------------------------------------------------------- 注册表结构

@dataclass(frozen=True)
class Quantity:
    name: str
    direction: str   # "in"=指令进仿真（PLC 输出）; "out"=量测出仿真（PLC 输入）
    dtype: str       # "bool" | "float"


@dataclass(frozen=True)
class ParamSpec:
    name: str
    kind: str        # "float" | "int" | "str" | "enum" | "vec3"
    required: bool = False
    default: Any = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    exclusive_min: bool = False
    values: Tuple[Any, ...] = ()


@dataclass
class ComponentDef:
    type_name: str
    quantities: Tuple[Quantity, ...]
    params: Tuple[ParamSpec, ...]
    build: Callable[..., Dict[str, str]]      # (stage, root_path, params) -> quantity -> prim path
    footprint: Callable[[Dict[str, Any]], Optional[AABB]]  # 局部 AABB；None = 不参与布局检查


def default_params(cdef: ComponentDef) -> Dict[str, Any]:
    return {p.name: p.default for p in cdef.params if p.default is not None}


# ---------------------------------------------------------------- USD 工具

BELT_MAT_PATH = "/World/Looks/BeltMat"


def _cube(stage: Usd.Stage, path: str, size3, translate, *,
          color=None, collision=True, rigid=False, mass=None,
          material: Optional[UsdShade.Material] = None) -> UsdGeom.Cube:
    cube = UsdGeom.Cube.Define(stage, path)
    cube.CreateSizeAttr(1.0)
    xf = UsdGeom.Xformable(cube)
    xf.AddTranslateOp().Set(Gf.Vec3d(*translate))
    # Cube size=1 的棱长经 scale 放大为全长：scale 必须等于目标尺寸
    xf.AddScaleOp().Set(Gf.Vec3f(*[float(s) for s in size3]))
    if color is not None:
        cube.CreateDisplayColorAttr([Gf.Vec3f(*color)])
    prim = cube.GetPrim()
    if collision:
        UsdPhysics.CollisionAPI.Apply(prim)
    if rigid:
        UsdPhysics.RigidBodyAPI.Apply(prim)
    if mass is not None:
        UsdPhysics.MassAPI.Apply(prim).CreateMassAttr(float(mass))
    if material is not None:
        UsdShade.MaterialBindingAPI.Apply(prim).Bind(material)
    return cube


def _dyn_body(stage: Usd.Stage, path: str, translate: geom.Vec3, mass: float) -> Usd.Prim:
    """动态刚体：Xform 承载位姿与 RigidBodyAPI（自身不带 scale，PhysX 最稳），
    碰撞网格作为子 prim 挂在下面（自动归入该刚体）。"""
    body = stage.DefinePrim(path, "Xform")
    UsdGeom.Xformable(body).AddTranslateOp().Set(Gf.Vec3d(*translate))
    UsdPhysics.RigidBodyAPI.Apply(body)
    UsdPhysics.MassAPI.Apply(body).CreateMassAttr(float(mass))
    return body


def _kinematic_body(stage: Usd.Stage, path: str, translate: geom.Vec3 = (0.0, 0.0, 0.0)) -> Usd.Prim:
    """运动学刚体（无限质量、永不移动）：作关节的固定端锚点。

    PhysX 不接受以纯静态碰撞体（只有 CollisionAPI 的 prim）作为关节 body——
    整条关节会被丢弃，链条在重力下散架（部件"飘移"的根源）。
    关节固定端必须也是刚体，kinematic 是 USD 的固定锚标准做法。"""
    body = stage.DefinePrim(path, "Xform")
    UsdGeom.Xformable(body).AddTranslateOp().Set(Gf.Vec3d(*translate))
    rb = UsdPhysics.RigidBodyAPI.Apply(body)
    rb.CreateKinematicEnabledAttr(True)
    return body


def _child_cube(stage: Usd.Stage, path: str, size3, *,
                translate=(0.0, 0.0, 0.0), color=None, collision=True) -> UsdGeom.Cube:
    return _cube(stage, path, size3, translate, color=color, collision=collision)


def _simio_attr(prim: Usd.Prim, name: str, value, vtype=None) -> None:
    """组件标记属性：运行时 IOBridge 冒烟自检用（参数已烘焙进 schema，此处冗余记录）。"""
    type_map = {
        float: Sdf.ValueTypeNames.Float, int: Sdf.ValueTypeNames.Int,
        str: Sdf.ValueTypeNames.String, bool: Sdf.ValueTypeNames.Bool,
    }
    if vtype is not None:
        t = vtype
    elif isinstance(value, (tuple, list)):
        t = Sdf.ValueTypeNames.Float3 if len(value) == 3 else Sdf.ValueTypeNames.FloatArray
    else:
        t = type_map[type(value)]
    prim.CreateAttribute(f"simio:{name}", t).Set(value)


def _unit_axis(axis: str) -> geom.Vec3:
    return {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}[axis]


def _prismatic_joint(stage: Usd.Stage, path: str, parent_path: str, child_path: str,
                     axis: str, local_pos0, local_pos1, low: float, high: float,
                     max_force: float = 500.0,
                     stiffness: float = 6000.0, damping: float = 250.0,
                     target: float = 0.0) -> UsdPhysics.PrismaticJoint:
    """平移关节。两刚体姿态与根坐标系对齐（无 localRot），axis 即运动轴；
    关节零位 = authoring 位姿，local_pos* 为同一原点 O 在两刚体系下的坐标。

    刚度/阻尼按 GUI 默认步长 1/60 数值稳定整定：ω=√(k/m_eff) 需满足 dt·ω < 2，
    并保持足够刚度使重力下坠 < 1mm。target 为开场驱动目标（关节坐标）。"""
    joint = UsdPhysics.PrismaticJoint.Define(stage, path)
    joint.CreateBody0Rel().SetTargets([stage.GetPrimAtPath(parent_path).GetPath()])
    joint.CreateBody1Rel().SetTargets([stage.GetPrimAtPath(child_path).GetPath()])
    joint.CreateCollisionEnabledAttr(False)      # 关节成对刚体不互撞
    axis_token = {"x": "X", "y": "Y", "z": "Z"}[axis]
    joint.CreateAxisAttr(axis_token)
    joint.CreateLocalPos0Attr(Gf.Vec3f(*local_pos0))
    joint.CreateLocalPos1Attr(Gf.Vec3f(*local_pos1))
    limit = UsdPhysics.LimitAPI.Apply(joint.GetPrim(), f"trans{axis_token}")
    limit.CreateLowAttr(float(low))
    limit.CreateHighAttr(float(high))
    drive = UsdPhysics.DriveAPI.Apply(joint.GetPrim(), f"trans{axis_token}")
    drive.CreateTypeAttr("position")
    drive.CreateTargetPositionAttr(float(target))   # IOBridge/桥按指令改写
    drive.CreateMaxForceAttr(float(max_force))
    drive.CreateStiffnessAttr(float(stiffness))
    drive.CreateDampingAttr(float(damping))
    return joint


# ---------------------------------------------------------------- 各组件构建

def _build_conveyor(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    l, w, h = p["length"], p["width"], p["height"]
    belt_mat = ctx["materials"]["belt"]
    _cube(stage, f"{root}/belt", (l, w, h), (0, 0, 0),
          color=(0.18, 0.18, 0.20), material=belt_mat)
    # 侧挡板，防止物料滑出带面
    for i, sgn in enumerate((-1, 1)):
        _cube(stage, f"{root}/rail_{i}", (l, 0.02, 0.04), (0, sgn * (w / 2 - 0.01), h / 2 + 0.02),
              color=(0.55, 0.55, 0.58))
    _simio_attr(ctx["root_prim"], "assetType", "conveyor_belt")
    _simio_attr(ctx["root_prim"], "maxSpeed", float(p["max_speed"]))
    return {q: root for q in ("run_cmd", "speed_setpoint", "measured_speed")}


def _footprint_conveyor(p: Dict[str, Any]) -> AABB:
    # 仅计带体；两侧挡板是边缘薄构件，不参与布局检查（避免对带面物料误报穿模）
    return ([-p["length"] / 2, -p["width"] / 2, -p["height"] / 2],
            [p["length"] / 2, p["width"] / 2, p["height"] / 2])


def _build_cylinder(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    axis = p["axis"]
    a = _unit_axis(axis)
    housing = p["housing"]
    stroke = p["stroke"]
    rod_len = stroke + housing * 0.5
    # 收缩态杆末端与基座前端面齐平；关节零位 = 收缩
    c0 = housing / 2.0 - rod_len / 2.0

    base_size = [housing if k != axis else housing for k in ("x", "y", "z")]
    # 基座为 kinematic 锚刚体 + 子网格：关节 body 必须是刚体（纯静态碰撞体会被 PhysX 拒用）
    base = _kinematic_body(stage, f"{root}/base")
    _cube(stage, f"{root}/base/mesh", base_size, (0, 0, 0), color=(0.30, 0.30, 0.34))

    rod_size = [p["rod_diameter"] if k != axis else rod_len for k in ("x", "y", "z")]
    rod_center = tuple(a[i] * c0 for i in range(3))
    rod = _dyn_body(stage, f"{root}/rod", rod_center, mass=1.0)
    _child_cube(stage, f"{root}/rod/mesh", rod_size, color=(0.75, 0.75, 0.78))

    _prismatic_joint(stage, f"{root}/joint", f"{root}/base", f"{root}/rod",
                     axis, (0.0, 0.0, 0.0), tuple(-a[i] * c0 for i in range(3)),
                     0.0, stroke, max_force=500.0, stiffness=5000.0, damping=150.0)

    _simio_attr(ctx["root_prim"], "assetType", "pneumatic_cylinder")
    _simio_attr(ctx["root_prim"], "stroke", float(stroke))
    _simio_attr(ctx["root_prim"], "extendSpeed", float(p["extend_speed"]))
    _simio_attr(ctx["root_prim"], "retractSpeed", float(p["retract_speed"]))
    return {"extend_cmd": f"{root}/joint", "position": f"{root}/joint", "at_end": f"{root}/joint"}


def _footprint_cylinder(p: Dict[str, Any]) -> AABB:
    axis = p["axis"]
    housing = p["housing"]
    stroke = p["stroke"]
    reach = housing + stroke          # 行程 + 杆超出量，保守取整段
    half = {"x": housing / 2, "y": housing / 2, "z": housing / 2}
    mins = dict(half)
    maxs = dict(half)
    if axis == "x":
        maxs["x"] = reach
    elif axis == "y":
        maxs["y"] = reach
    else:
        maxs["z"] = reach
    return ([-mins["x"], -mins["y"], -mins["z"]], [maxs["x"], maxs["y"], maxs["z"]])


def _build_photoelectric(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    _cube(stage, f"{root}/housing", (0.04, 0.04, 0.04), (0, 0, 0),
          color=(0.9, 0.75, 0.1), collision=False)
    _simio_attr(ctx["root_prim"], "assetType", "photoelectric_sensor")
    _simio_attr(ctx["root_prim"], "beamDirection", tuple(float(v) for v in p["beam_direction"]))
    _simio_attr(ctx["root_prim"], "beamLength", float(p["beam_length"]))
    return {"beam_broken": root}


def _footprint_photoelectric(p: Dict[str, Any]) -> AABB:
    return ([-0.02, -0.02, -0.02], [0.02, 0.02, 0.02])   # 光束非实体，不计入


def _build_chute(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    w, d, h = p["size"]
    t = 0.02
    _cube(stage, f"{root}/floor", (w, d, t), (0, 0, t / 2), color=(0.45, 0.45, 0.48))
    walls = [
        ((w, t, h), (0, d / 2 - t / 2, h / 2)),
        ((w, t, h), (0, -(d / 2 - t / 2), h / 2)),
        ((t, d, h), (w / 2 - t / 2, 0, h / 2)),
        ((t, d, h), (-(w / 2 - t / 2), 0, h / 2)),
    ]
    for i, (size, pos) in enumerate(walls):
        _cube(stage, f"{root}/wall_{i}", size, pos, color=(0.45, 0.45, 0.48))
    _simio_attr(ctx["root_prim"], "assetType", "bin_chute")
    _simio_attr(ctx["root_prim"], "regionCenter", (0.0, 0.0, h / 2))
    _simio_attr(ctx["root_prim"], "regionSize", (w - 2 * t, d - 2 * t, h))
    return {"object_inside": root}


def _footprint_chute(p: Dict[str, Any]) -> AABB:
    w, d, h = p["size"]
    return ([-w / 2, -d / 2, 0.0], [w / 2, d / 2, h])


def _build_rigid_box(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    size = p["size"]
    size3 = (size, size, size) if isinstance(size, (int, float)) else tuple(size)
    _dyn_body(stage, f"{root}/body", (0.0, 0.0, 0.0), mass=float(p["mass"]))
    _child_cube(stage, f"{root}/body/mesh", size3,
                color=_hex_color(p.get("color", "#c0504d")))
    _simio_attr(ctx["root_prim"], "assetType", "rigid_box")
    return {"position": root}


def _footprint_rigid_box(p: Dict[str, Any]) -> AABB:
    size = p["size"]
    s = float(size) if isinstance(size, (int, float)) else max(size)
    h = s / 2
    return ([-h, -h, -h], [h, h, h])


def _build_contact_pad(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    _cube(stage, f"{root}/pad", (0.06, 0.06, 0.01), (0, 0, 0), color=(0.1, 0.6, 0.3))
    _simio_attr(ctx["root_prim"], "assetType", "contact_pad")
    return {"in_contact": root}


def _footprint_contact_pad(p: Dict[str, Any]) -> AABB:
    return ([-0.03, -0.03, -0.005], [0.03, 0.03, 0.005])


def _build_gripper(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    _cube(stage, f"{root}/pad", (0.08, 0.08, 0.02), (0, 0, 0), color=(0.35, 0.35, 0.6))
    _simio_attr(ctx["root_prim"], "assetType", "vacuum_gripper")
    return {"suck_cmd": root, "holding": root}


def _footprint_gripper(p: Dict[str, Any]) -> AABB:
    return ([-0.04, -0.04, -0.01], [0.04, 0.04, 0.01])


def _build_gantry(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    tx, ty, tz = p["travel_x"], p["travel_y"], p["travel_z"]
    plate_top = 0.04
    H = tz + 0.35                        # 导轨高度 = Z 行程 + 顶隙
    carriage_z = H - 0.06                # Y 滑座（桥上滑动体）中心
    pen_len = 0.27                       # 笔杆长度
    pen_tip_clear = 0.002                # 落笔位：笔尖距台面 2mm（视觉贴纸，物理不接触）
    pen_cz = plate_top + pen_tip_clear + pen_len / 2.0   # Z 轴 q=0（落笔）时笔体中心

    # ---- 固定端：kinematic 锚刚体 + 静态几何（关节 body 必须是刚体，见 _kinematic_body） ----
    base = _kinematic_body(stage, f"{root}/base")
    _cube(stage, f"{root}/base/base_plate", (tx + 0.3, ty + 0.3, 0.04), (0, 0, 0.02),
          color=(0.75, 0.76, 0.78))
    _cube(stage, f"{root}/base/paper", (tx * 0.8, ty * 0.8, 0.002), (0, 0, plate_top + 0.001),
          color=(0.97, 0.97, 0.94), collision=False)     # 视觉件：绘图纸面
    for i, sgn in enumerate((-1, 1)):
        _cube(stage, f"{root}/base/column_{i}", (0.06, 0.06, H), (sgn * (tx / 2 + 0.08), 0, H / 2),
              color=(0.35, 0.36, 0.40))
    _cube(stage, f"{root}/base/x_rail", (tx + 0.22, 0.07, 0.07), (0, 0, H), color=(0.35, 0.36, 0.40))

    # ---- 运动链（显式三轴：每根轴一个刚体，全为 Xform 刚体 + 子网格）：
    #   base(锚) --joint_x(X)--> x_carriage --joint_y(Y)--> y_carriage --joint_z(Z)--> z_carriage(笔)
    x_car = _dyn_body(stage, f"{root}/x_carriage", (-tx / 2, 0.0, H - 0.05), mass=4.0)
    _child_cube(stage, f"{root}/x_carriage/saddle", (0.09, ty + 0.02, 0.06),
                color=(0.20, 0.55, 0.85))                 # 跨桥滑座，挂在导轨下方
    _child_cube(stage, f"{root}/x_carriage/clamp", (0.10, 0.075, 0.03),
                translate=(0.0, 0.0, 0.05), color=(0.16, 0.42, 0.66), collision=False)

    y_car = _dyn_body(stage, f"{root}/y_carriage", (-tx / 2, -ty / 2, carriage_z), mass=3.0)
    _child_cube(stage, f"{root}/y_carriage/head", (0.10, 0.10, 0.08),
                color=(0.20, 0.55, 0.85))                 # 桥前端主轴头
    _child_cube(stage, f"{root}/y_carriage/z_guide", (0.035, 0.035, tz + 0.12),
                translate=(0.0, 0.0, -(0.04 + (tz + 0.12) / 2)),
                color=(0.45, 0.46, 0.50))                 # 垂向导轨：上端接主轴头下沿

    z_car = _dyn_body(stage, f"{root}/z_carriage", (-tx / 2, -ty / 2, pen_cz), mass=0.4)
    _child_cube(stage, f"{root}/z_carriage/slider", (0.06, 0.06, 0.09),
                translate=(0.0, 0.0, 0.155), color=(0.16, 0.42, 0.66))   # 骑在垂向导轨上的滑块
    _child_cube(stage, f"{root}/z_carriage/pen", (0.015, 0.015, pen_len),
                color=(0.85, 0.20, 0.20), collision=False)               # 笔：无碰撞，避免与台面接触抖动

    # 关节零位 = authoring 位姿；+q 沿轴正向。
    # Z 轴语义：q=0 落笔（笔尖贴纸面）、q=tz 抬笔（抬起 tz）→ 开场 target=tz 保持抬笔。
    _prismatic_joint(stage, f"{root}/joint_x", f"{root}/base", f"{root}/x_carriage", "x",
                     (-tx / 2, 0.0, H - 0.05), (0.0, 0.0, 0.0),
                     0.0, tx, max_force=1000.0)
    _prismatic_joint(stage, f"{root}/joint_y", f"{root}/x_carriage", f"{root}/y_carriage", "y",
                     (0.0, -ty / 2, -0.01), (0.0, 0.0, 0.0),
                     0.0, ty, max_force=1000.0)
    _prismatic_joint(stage, f"{root}/joint_z", f"{root}/y_carriage", f"{root}/z_carriage", "z",
                     (0.0, 0.0, pen_cz - carriage_z), (0.0, 0.0, 0.0),
                     0.0, tz, max_force=300.0,
                     stiffness=4000.0, damping=80.0, target=tz)
    # joint_z 整定（m=0.4, 60Hz）：ω=100 → dt·ω=1.67<2；ζ=1 临界阻尼；重力下坠 ≈0.98mm<1mm

    _simio_attr(ctx["root_prim"], "assetType", "gantry_xyz")
    _simio_attr(ctx["root_prim"], "travelX", float(tx))
    _simio_attr(ctx["root_prim"], "travelY", float(ty))
    _simio_attr(ctx["root_prim"], "travelZ", float(tz))
    _simio_attr(ctx["root_prim"], "axisSpeed", float(p["speed"]))
    # 位置回读来源（运行时桥用）：按 x/y/z 顺序，读刚体 translate 分量减去关节零位坐标 = 关节坐标 q
    _simio_attr(ctx["root_prim"], "posBody",
                [f"{root}/x_carriage", f"{root}/y_carriage", f"{root}/z_carriage"],
                vtype=Sdf.ValueTypeNames.StringArray)
    _simio_attr(ctx["root_prim"], "posRest", [-tx / 2, -ty / 2, pen_cz],
                vtype=Sdf.ValueTypeNames.FloatArray)
    return {q: f"{root}/joint_{a}"
            for q, a in (("x_cmd", "x"), ("x_pos", "x"), ("y_cmd", "y"),
                         ("y_pos", "y"), ("z_cmd", "z"), ("z_pos", "z"))}


def _footprint_gantry(p: Dict[str, Any]) -> AABB:
    tx, ty = p["travel_x"], p["travel_y"]
    H = p["travel_z"] + 0.35
    return ([-(tx + 0.3) / 2, -(ty + 0.3) / 2, 0.0], [(tx + 0.3) / 2, (ty + 0.3) / 2, H + 0.06])


def _build_arm(stage, root: str, p: Dict[str, Any], ctx) -> Dict[str, str]:
    ref = p["asset_path"]
    if not os.path.isabs(ref):
        raise ValueError(f"articular_arm asset_path 须为绝对路径: {ref}")
    ctx["root_prim"].GetReferences().AddReference(ref)
    _simio_attr(ctx["root_prim"], "assetType", "articular_arm")
    return {"joint_cmd": root, "joint_pos": root}


def _footprint_arm(p: Dict[str, Any]) -> Optional[AABB]:
    return None  # 外部引用资产，包围盒未知，不参与布局检查


def _hex_color(hexstr: str):
    hexstr = hexstr.lstrip("#")
    return tuple(int(hexstr[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


# ---------------------------------------------------------------- 注册表

REGISTRY: Dict[str, ComponentDef] = {
    "conveyor_belt": ComponentDef(
        type_name="conveyor_belt",
        quantities=(
            Quantity("run_cmd", "in", "bool"),
            Quantity("speed_setpoint", "in", "float"),
            Quantity("measured_speed", "out", "float"),
        ),
        params=(
            ParamSpec("length", "float", required=True, minimum=0.1, maximum=20),
            ParamSpec("width", "float", required=True, minimum=0.05, maximum=5),
            ParamSpec("height", "float", required=True, minimum=0.01, maximum=2),
            ParamSpec("max_speed", "float", required=True, exclusive_min=True, minimum=0.0, maximum=10),
        ),
        build=_build_conveyor,
        footprint=_footprint_conveyor,
    ),
    "pneumatic_cylinder": ComponentDef(
        type_name="pneumatic_cylinder",
        quantities=(
            Quantity("extend_cmd", "in", "bool"),
            Quantity("position", "out", "float"),
            Quantity("at_end", "out", "bool"),
        ),
        params=(
            ParamSpec("axis", "enum", required=True, values=("x", "y", "z")),
            ParamSpec("stroke", "float", required=True, exclusive_min=True, minimum=0.0, maximum=1.0),
            ParamSpec("rod_diameter", "float", default=0.02, exclusive_min=True, minimum=0.0, maximum=0.2),
            ParamSpec("extend_speed", "float", default=1.0, exclusive_min=True, minimum=0.0, maximum=5.0),
            ParamSpec("retract_speed", "float", default=1.0, exclusive_min=True, minimum=0.0, maximum=5.0),
            ParamSpec("housing", "float", default=0.08, exclusive_min=True, minimum=0.0, maximum=0.5),
        ),
        build=_build_cylinder,
        footprint=_footprint_cylinder,
    ),
    "photoelectric_sensor": ComponentDef(
        type_name="photoelectric_sensor",
        quantities=(Quantity("beam_broken", "out", "bool"),),
        params=(
            ParamSpec("beam_direction", "vec3", required=True),
            ParamSpec("beam_length", "float", required=True, exclusive_min=True, minimum=0.0, maximum=5.0),
        ),
        build=_build_photoelectric,
        footprint=_footprint_photoelectric,
    ),
    "bin_chute": ComponentDef(
        type_name="bin_chute",
        quantities=(Quantity("object_inside", "out", "bool"),),
        params=(
            ParamSpec("size", "vec3", required=True),
        ),
        build=_build_chute,
        footprint=_footprint_chute,
    ),
    "rigid_box": ComponentDef(
        type_name="rigid_box",
        quantities=(Quantity("position", "out", "float"),),
        params=(
            ParamSpec("size", "float", required=True, exclusive_min=True, minimum=0.0, maximum=2.0),
            ParamSpec("mass", "float", required=True, exclusive_min=True, minimum=0.0, maximum=100.0),
            ParamSpec("color", "str", default="#c0504d"),
        ),
        build=_build_rigid_box,
        footprint=_footprint_rigid_box,
    ),
    "contact_pad": ComponentDef(
        type_name="contact_pad",
        quantities=(Quantity("in_contact", "out", "bool"),),
        params=(),
        build=_build_contact_pad,
        footprint=_footprint_contact_pad,
    ),
    "vacuum_gripper": ComponentDef(
        type_name="vacuum_gripper",
        quantities=(
            Quantity("suck_cmd", "in", "bool"),
            Quantity("holding", "out", "bool"),
        ),
        params=(),
        build=_build_gripper,
        footprint=_footprint_gripper,
    ),
    "gantry_xyz": ComponentDef(
        type_name="gantry_xyz",
        quantities=(
            Quantity("x_cmd", "in", "float"), Quantity("y_cmd", "in", "float"),
            Quantity("z_cmd", "in", "float"),
            Quantity("x_pos", "out", "float"), Quantity("y_pos", "out", "float"),
            Quantity("z_pos", "out", "float"),
        ),
        params=(
            ParamSpec("travel_x", "float", required=True, exclusive_min=True, minimum=0.05, maximum=3.0),
            ParamSpec("travel_y", "float", required=True, exclusive_min=True, minimum=0.05, maximum=3.0),
            ParamSpec("travel_z", "float", required=True, exclusive_min=True, minimum=0.01, maximum=1.0),
            ParamSpec("speed", "float", default=0.5, exclusive_min=True, minimum=0.0, maximum=5.0),
        ),
        build=_build_gantry,
        footprint=_footprint_gantry,
    ),
    "articular_arm": ComponentDef(
        type_name="articular_arm",
        quantities=(
            Quantity("joint_cmd", "in", "float"),
            Quantity("joint_pos", "out", "float"),
        ),
        params=(ParamSpec("asset_path", "str", required=True),),
        build=_build_arm,
        footprint=_footprint_arm,
    ),
}


def quantity_of(type_name: str, quantity: str) -> Optional[Quantity]:
    cdef = REGISTRY.get(type_name)
    if cdef is None:
        return None
    for q in cdef.quantities:
        if q.name == quantity:
            return q
    return None


def apply_params(cdef: ComponentDef, raw: Dict[str, Any]) -> Dict[str, Any]:
    """合并默认值 + 类型规整（vec3 归一为 3 元 float 元组）。缺失/非法由 validate 报错。"""
    merged = default_params(cdef)
    for p in cdef.params:
        if p.name in raw:
            v = raw[p.name]
            if p.kind == "vec3":
                merged[p.name] = tuple(float(x) for x in v)
            elif p.kind == "float":
                merged[p.name] = float(v)
            elif p.kind == "str":
                merged[p.name] = str(v)
            else:
                merged[p.name] = v
    return merged
