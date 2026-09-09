"""SceneSpec → USD 确定性构建器。产出 scene.usda 与富化 io_map（含 Modbus 地址）。"""

import json
import os
from typing import Any, Dict, Tuple

from pxr import Gf, Sdf, Usd, UsdGeom, UsdPhysics, UsdShade

from . import components, geom, iomap
from .validate import _world_poses


def _define_physics_scene(stage: Usd.Stage, spec: Dict[str, Any]) -> None:
    scene = UsdPhysics.Scene.Define(stage, "/World/PhysicsScene")
    g = spec.get("physics", {}).get("gravity", (0.0, 0.0, -9.81))
    scene.CreateGravityDirectionAttr(Gf.Vec3f(*geom.normalize(tuple(g))))
    scene.CreateGravityMagnitudeAttr(float(sum(v * v for v in g) ** 0.5))


def _define_materials(stage: Usd.Stage, spec: Dict[str, Any]) -> Dict[str, UsdShade.Material]:
    ground_friction = spec.get("ground", {}).get("friction", 0.8)

    def make_mat(path: str, static: float, dynamic: float) -> UsdShade.Material:
        mat = UsdShade.Material.Define(stage, path)
        pm = UsdPhysics.MaterialAPI.Apply(mat.GetPrim())
        pm.CreateStaticFrictionAttr(static)
        pm.CreateDynamicFrictionAttr(dynamic)
        pm.CreateRestitutionAttr(0.1)
        return mat

    mats = {
        "ground": make_mat("/World/Looks/GroundMat", ground_friction, ground_friction * 0.9),
        "belt": make_mat("/World/Looks/BeltMat", 1.2, 1.0),
    }
    return mats


def _define_ground(stage: Usd.Stage, spec: Dict[str, Any], mats: Dict[str, UsdShade.Material]) -> None:
    size = spec.get("ground", {}).get("size", (20.0, 20.0))
    root = stage.DefinePrim("/World/Ground", "Xform")
    UsdGeom.Xformable(root).AddTranslateOp().Set(Gf.Vec3d(0.0, 0.0, -0.05))
    plate = UsdGeom.Cube.Define(stage, "/World/Ground/Plate")
    plate.CreateSizeAttr(1.0)
    xf = UsdGeom.Xformable(plate)
    xf.AddScaleOp().Set(Gf.Vec3f(size[0], size[1], 0.1))
    plate.CreateDisplayColorAttr([Gf.Vec3f(0.32, 0.33, 0.35)])
    UsdPhysics.CollisionAPI.Apply(plate.GetPrim())
    UsdShade.MaterialBindingAPI.Apply(plate.GetPrim()).Bind(mats["ground"])


def build(spec: Dict[str, Any], out_dir: str) -> Dict[str, Any]:
    """构建场景。返回 {scene_usd, io_map, modbus_summary, st_declaration, assets}。"""
    world = _world_poses(spec)
    os.makedirs(out_dir, exist_ok=True)
    usd_path = os.path.join(out_dir, "scene.usda")
    if os.path.exists(usd_path):
        os.remove(usd_path)

    stage = Usd.Stage.CreateNew(usd_path)
    root = UsdGeom.Xform.Define(stage, "/World")
    stage.SetDefaultPrim(root.GetPrim())
    UsdGeom.SetStageUpAxis(stage, UsdGeom.Tokens.z)
    UsdGeom.SetStageMetersPerUnit(stage, 1.0)
    root.GetPrim().CreateAttribute("simio:sceneId", Sdf.ValueTypeNames.String).Set(spec["scene_id"])

    _define_physics_scene(stage, spec)
    mats = _define_materials(stage, spec)
    _define_ground(stage, spec, mats)

    quantity_prims: Dict[str, Dict[str, str]] = {}
    by_id = {a["id"]: a for a in spec["assets"]}
    for asset in spec["assets"]:
        cdef = components.REGISTRY[asset["type"]]
        prim_path = f"/World/{asset['id']}"
        xform = UsdGeom.Xform.Define(stage, prim_path)
        prim = xform.GetPrim()
        pos, rot = world[asset["id"]]
        w, x, y, z = geom.quat_from_matrix(rot)
        xform.AddTranslateOp().Set(Gf.Vec3d(*pos))
        xform.AddOrientOp().Set(Gf.Quatf(w, Gf.Vec3f(x, y, z)))
        params = components.apply_params(cdef, asset.get("params", {}))
        ctx = {"root_prim": prim, "materials": mats, "spec": spec, "asset": asset}
        quantity_prims[asset["id"]] = cdef.build(stage, prim_path, params, ctx)

    enriched = iomap.enrich(spec["io_map"], quantity_prims)
    enriched = iomap.assign_modbus(enriched)
    for e in enriched:
        e["_asset_type"] = by_id[e["bind"]["asset"]]["type"]

    stage.GetRootLayer().Save()
    return {
        "scene_usd": usd_path,
        "io_map": enriched,
        "modbus_summary": iomap.modbus_summary(enriched),
        "st_declaration": iomap.st_io_declaration(enriched),
        "assets": {aid: {"usd_prim": f"/World/{aid}"} for aid in by_id},
    }
