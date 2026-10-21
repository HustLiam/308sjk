"""发布实际MuJoCo画面和状态，供Qt实时显示或停止后查看。"""
import json
import hashlib
import os
from pathlib import Path
import time

import mujoco
import numpy as np


class FramePublisher:
    def __init__(self, model, path, lookat, fps=8, save_snapshot=True):
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
        self.save_snapshot = save_snapshot
        if save_snapshot:
            model_path = self.path.with_suffix('.model.mjb')
            tmp = model_path.with_suffix('.tmp')
            model_buffer = np.empty(mujoco.mj_sizeModel(model), dtype=np.uint8)
            mujoco.mj_saveModel(model, buffer=model_buffer)
            model_bytes = model_buffer.tobytes()
            tmp.write_bytes(model_bytes)
            self.model_digest = hashlib.sha256(model_bytes).hexdigest()
            os.replace(tmp, model_path)

    def read_camera(self):
        try:
            camera = json.loads(self.path.with_suffix('.camera.json').read_text(encoding='utf-8'))
            values = [float(camera[k]) for k in ('azimuth', 'elevation', 'distance')]
            if not np.isfinite(values).all():
                return
            self.cam.azimuth = values[0]
            self.cam.elevation = max(-89, min(89, values[1]))
            self.cam.distance = max(0.3, min(8.0, values[2]))
        except (OSError, ValueError, KeyError, TypeError):
            pass

    def render(self, data):
        self.read_camera()
        self.renderer.update_scene(data, self.cam)
        scene = self.renderer.scene
        for pos in self.ink:
            if scene.ngeom >= scene.maxgeom:
                break
            mujoco.mjv_initGeom(scene.geoms[scene.ngeom], mujoco.mjtGeom.mjGEOM_SPHERE,
                              np.array([0.003] * 3), np.array(pos), np.eye(3).flatten(),
                              np.array([0.1, 0.1, 0.55, 1.0], dtype=np.float32))
            scene.ngeom += 1
        return self.renderer.render()

    def snapshot(self, data):
        signature = mujoco.mjtState.mjSTATE_INTEGRATION
        state = np.empty(mujoco.mj_stateSize(self.model, signature))
        mujoco.mj_getState(self.model, data, state, signature)
        tmp = self.path.with_suffix('.state.tmp')
        with tmp.open('wb') as stream:
            np.savez(stream, version=1, model_sha256=self.model_digest,
                     signature=int(signature), state=state,
                     ink=np.asarray(self.ink).reshape(-1, 3), lookat=self.cam.lookat.copy(),
                     camera=[self.cam.azimuth, self.cam.elevation, self.cam.distance])
        os.replace(tmp, self.path.with_suffix('.state.npz'))

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
        pixels = self.render(data)
        tmp = self.path.with_suffix(".tmp")
        try:
            if self.save_snapshot:
                self.snapshot(data)
            with tmp.open("wb") as f:
                np.save(f, pixels, allow_pickle=False)
            os.replace(tmp, self.path)
        except PermissionError:
            tmp.unlink(missing_ok=True)

    def close(self):
        self.renderer.close()


class SnapshotRenderer:
    """查看实际采样状态，只重新计算显示数据，不步进物理或连接PLC。"""
    def __init__(self, frame_path):
        path = Path(frame_path)
        model_path = path.with_suffix('.model.mjb')
        with np.load(path.with_suffix('.state.npz'), allow_pickle=False) as saved:
            if saved['version'].item() != 1:
                raise ValueError('不支持的仿真显示状态版本')
            model_bytes = model_path.read_bytes()
            if hashlib.sha256(model_bytes).hexdigest() != saved['model_sha256'].item():
                raise ValueError('仿真显示状态与模型不匹配')
            self.model = mujoco.MjModel.from_binary_path('captured.mjb', assets={'captured.mjb': model_bytes})
            self.data = mujoco.MjData(self.model)
            signature = int(saved['signature'].item())
            mujoco.mj_setState(self.model, self.data, saved['state'], signature)
            mujoco.mj_forward(self.model, self.data)
            self.publisher = FramePublisher(self.model, path, saved['lookat'], save_snapshot=False)
            self.publisher.ink = saved['ink'].tolist()
            (self.publisher.cam.azimuth, self.publisher.cam.elevation,
             self.publisher.cam.distance) = saved['camera']

    def render(self):
        return self.publisher.render(self.data)

    def close(self):
        self.publisher.close()
