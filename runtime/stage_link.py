"""stage ⇄ GantryBridge 接线（编辑器脚本与独立运行时共用，不含 omni 依赖）。

从 gantry 根 prim 的 simio 标记（posBody/posRest/travel*/axisSpeed）与 io_map.json 推导：
- 指令寄存器 → 关节驱动属性 drive:trans*:physics:targetPosition（**按 axisSpeed 速率限制**，
  任何寄存器阶跃都不会变成驱动力饱和弹射——实机教训：joint_z 开场 0.2m 误差直接把
  滑块弹穿纸面，见 components._build_gantry 注释）
- 刚体 translate 分量 − 关节零位坐标 → 位置反馈寄存器
不依赖 PhysX 专有 state API，在编辑器 Play 与独立 World 主循环下行为一致。
"""

try:
    from .gantry_bridge import AXES          # 作为包内模块使用时
except ImportError:                          # runtime 目录平铺导入（编辑器粘贴/独立运行时/测试）
    from gantry_bridge import AXES

DEFAULT_AXIS_SPEED = 0.5                     # m/s，与 gantry_xyz 的 speed 参数默认一致


def find_gantry_root(stage):
    """按 simio:assetType == gantry_xyz 定位组件根 prim。"""
    for prim in stage.Traverse():
        if prim.GetAttribute("simio:assetType").Get() == "gantry_xyz":
            return prim
    return None


class StageLink:
    """把一个已就绪的 stage 绑到 bridge 上；apply_once(dt) 每物理帧调用一次。"""

    def __init__(self, stage, bridge, io_map=None):
        gantry = find_gantry_root(stage)
        assert gantry is not None, "场景中找不到 simio:assetType == gantry_xyz 的组件"
        self.stage = stage
        self.bridge = bridge
        self.travel = {a: float(gantry.GetAttribute(f"simio:travel{a}").Get())
                       for a in AXES}
        speed = gantry.GetAttribute("simio:axisSpeed").Get()
        self.axis_speed = float(speed) if speed else DEFAULT_AXIS_SPEED
        pos_body = list(gantry.GetAttribute("simio:posBody").Get())
        pos_rest = list(gantry.GetAttribute("simio:posRest").Get())
        self._body_prim = {a: stage.GetPrimAtPath(p) for a, p in zip(AXES, pos_body)}
        self._pos_rest = [float(v) for v in pos_rest]

        # 指令 → 关节驱动属性：优先 io_map 的 usd_prim（契约），缺省退回命名约定
        root_path = str(gantry.GetPath())
        self._cmd_attr = {}
        if io_map:
            for e in io_map:
                q = e["bind"]["quantity"]
                if q.endswith("_cmd"):
                    a = q[0].upper()
                    self._cmd_attr[a] = (e["usd_prim"],
                                         f"drive:trans{a}:physics:targetPosition")
        for a in AXES:                                   # 兜底： joint_<x> 命名约定
            self._cmd_attr.setdefault(
                a, (f"{root_path}/joint_{a.lower()}",
                    f"drive:trans{a}:physics:targetPosition"))
        # 场景开场 q=0（所有关节驱动目标为 0、零初始误差）——跟踪起点即作者位姿
        self._last_cmd = {a: 0.0 for a in AXES}

    def read_axis(self, axis: str) -> float:
        """关节坐标 q（米）= 刚体 translate 分量 − 零位坐标，钳位到行程。"""
        t = self._body_prim[axis].GetAttribute("xformOp:translate").Get()
        q = float(t[AXES.index(axis)]) - self._pos_rest[AXES.index(axis)]
        return max(0.0, min(q, self.travel[axis]))

    def apply_once(self, dt: float = 1.0 / 60, verbose: bool = False) -> None:
        """单帧：指令寄存器 →（axisSpeed 速率限制）→ 关节驱动目标；轴位置 → 反馈寄存器。

        dt 为本次调用间隔（物理帧 1/physics_hz；编辑器轮询为真实间隔）。
        首次跟踪起点 = 0（作者位姿），寄存器里的抬笔指令会在 ~travel/speed 秒内平滑抬起。"""
        step = self.axis_speed * dt
        for a, target in self.bridge.read_commands().items():
            cur = self._last_cmd[a]
            delta = target - cur
            new = target if abs(delta) <= step else cur + step * (1.0 if delta > 0 else -1.0)
            if abs(new - cur) > 1e-9:
                path, attr = self._cmd_attr[a]
                self.stage.GetPrimAtPath(path).GetAttribute(attr).Set(new)
                if verbose:
                    print(f"[bridge] Axis{a} drive -> {new:.3f} m")
            self._last_cmd[a] = new
        self.bridge.write_positions({a: self.read_axis(a) for a in AXES})
