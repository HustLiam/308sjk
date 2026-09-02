"""冒烟检查。

- structural_check：纯 pxr（无需 Isaac Sim），验证 stage 可打开、io_map 的 prim 存在、
  关节连接完整、无 NaN——CI 每轮必跑。
- isaac_headless：加载 Isaac Sim headless 跑空 PLC 短仿真（可选，需安装 isaacsim）。
"""

import json
import math
import os
from typing import Any, Dict, List

from pxr import Usd


def structural_check(usd_path: str, io_map: List[Dict[str, Any]]) -> List[str]:
    issues: List[str] = []
    stage = Usd.Stage.Open(usd_path)
    if not stage:
        return [f"无法打开 stage: {usd_path}"]

    default = stage.GetDefaultPrim()
    if not default or not default.IsValid():
        issues.append("缺少 defaultPrim（/World）")

    for e in io_map:
        prim = stage.GetPrimAtPath(e["usd_prim"])
        if not prim or not prim.IsValid():
            issues.append(f"io_map[{e['plc_var']}]: prim 不存在 {e['usd_prim']}")

    # 关节连接与驱动（属性名兼容新旧 USD：applied-schema 前缀式 / physics: 前缀式）
    for prim in stage.Traverse():
        if prim.GetTypeName() == "PhysicsPrismaticJoint":
            tag = str(prim.GetPath())
            rel0 = prim.GetRelationship("physics:body0")
            rel1 = prim.GetRelationship("physics:body1")
            if not rel0 or not rel1 or not rel0.GetTargets() or not rel1.GetTargets():
                issues.append(f"关节 {tag} 缺少 body0/body1 连接")
                continue
            # 关节两端必须是刚体：纯静态碰撞体会被 PhysX 拒用（整链散架、部件飘移）
            for end, rel in (("body0", rel0), ("body1", rel1)):
                target = stage.GetPrimAtPath(rel.GetTargets()[0])
                if not target or not target.IsValid():
                    issues.append(f"关节 {tag} {end} 指向不存在的 prim")
                elif not target.HasAPI("PhysicsRigidBodyAPI"):
                    issues.append(
                        f"关节 {tag} {end} 指向非刚体 {rel.GetTargets()[0]}"
                        "（缺 RigidBodyAPI，PhysX 将拒用该关节）")
            has_drive = any(
                p.GetName().endswith("physics:targetPosition") or p.GetName().endswith("physics:target")
                for p in prim.GetProperties())
            if not has_drive:
                issues.append(f"关节 {tag} 缺少驱动 targetPosition 属性")

    # 数值健全性：资产的平移属性非 NaN
    for prim in stage.Traverse():
        attr = prim.GetAttribute("xformOp:translate")
        if attr and attr.HasAuthoredValue():
            try:
                v = attr.Get()
            except Exception:
                continue
            if v is not None and any(math.isnan(float(x)) for x in v):
                issues.append(f"prim {prim.GetPath()} 平移含 NaN")

    return issues


def isaac_headless(usd_path: str, seconds: float = 2.0) -> Dict[str, Any]:
    """需安装 isaacsim；在闭环环境中由编排器调用。未安装时返回 skipped。"""
    try:
        os.environ.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
        from isaacsim.simulation_app import SimulationApp  # noqa: WPS433
    except ImportError:
        return {"status": "skipped", "reason": "isaacsim 未安装，仅做结构检查"}

    app = SimulationApp({"headless": True})
    try:
        import omni.usd  # noqa: WPS433
        from isaacsim.core.api import World  # noqa: WPS433

        ctx = omni.usd.get_context()
        ctx.open_stage(usd_path)
        world = World(physics_dt=1 / 120, rendering_dt=1 / 30)
        world.reset()
        steps = int(seconds * 120)
        for _ in range(steps):
            world.step(render=False)
        return {"status": "ok", "steps": steps}
    finally:
        app.close()
