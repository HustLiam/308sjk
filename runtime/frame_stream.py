"""发布实际 MuJoCo 实例的离屏画面；Qt 只读帧，不创建第二个仿真实例。"""
import json
import os
from pathlib import Path
import time

import mujoco
import numpy as np


class FramePublisher:
    def __init__(self, model, path, lookat, fps=8):
        self.model, self.path = model, Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        model.vis.global_.offwidth = max(model.vis.global_.offwidth, 640)
        model.vis.global_.offheight = max(model.vis.global_.offheight, 480)
        model.vis.headlight.ambient[:] = 0.5
        model.vis.headlight.diffuse[:] = 0.7
        model.vis.headlight.specular[:] = 0.1
        self.renderer = mujoco.Renderer(model, height=480, width=640)
        self.cam = mujoco.MjvCamera()
        self.cam.lookat[:] = lookat
        self.cam.azimuth, self.cam.elevation, self.cam.distance = 135, -40, 2.5
        self.period, self.next_frame = 1.0 / fps, 0
        self.pen = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "pen")
        self.paper = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_GEOM, "paper")
        self.ink = []

    def publish(self, data):
        if self.pen >= 0 and self.paper >= 0:
            tip = data.geom_xpos[self.pen].copy()
            tip[2] -= self.model.geom_size[self.pen][1] + self.model.geom_size[self.pen][0]
            top = data.geom_xpos[self.paper][2] + self.model.geom_size[self.paper][2]
            if tip[2] <= top + 0.0003 and (not self.ink or
                    sum((tip[i] - self.ink[-1][i]) ** 2 for i in (0, 1)) >= 0.004 ** 2):
                self.ink.append((tip[0], tip[1], top + 0.001))
                self.ink = self.ink[-2000:]
        now = time.monotonic()
        if now < self.next_frame:
            return
        self.next_frame = now + self.period
        try:
            camera = json.loads(self.path.with_suffix(".camera.json").read_text(encoding="utf-8"))
            self.cam.azimuth = float(camera["azimuth"])
            self.cam.elevation = max(-89, min(-5, float(camera["elevation"])))
            self.cam.distance = max(0.3, min(8.0, float(camera["distance"])))
        except (OSError, ValueError, KeyError):
            pass
        self.renderer.update_scene(data, self.cam)
        scene = self.renderer.scene
        for pos in self.ink:
            if scene.ngeom >= scene.maxgeom:
                break
            mujoco.mjv_initGeom(scene.geoms[scene.ngeom], mujoco.mjtGeom.mjGEOM_SPHERE,
                              np.array([0.003] * 3), np.array(pos), np.eye(3).flatten(),
                              np.array([0.1, 0.1, 0.55, 1.0], dtype=np.float32))
            scene.ngeom += 1
        tmp = self.path.with_suffix(".tmp")
        try:
            with tmp.open("wb") as f:
                np.save(f, self.renderer.render(), allow_pickle=False)
            os.replace(tmp, self.path)
        except PermissionError:
            tmp.unlink(missing_ok=True)

    def close(self):
        self.renderer.close()
