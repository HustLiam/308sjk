# -*- coding: utf-8 -*-
"""CSP Studio v2：左 agent 对话（含 AML 选择）/ 右 MuJoCo 铺满嵌入。

流程：选 AML → 输入需求 → agent（编排器：⓪解析 AML→①需求理解 LLM→②生成
XML+场景 JSON→闸门→③OpenPLC 真编译部署）→ 自动加载 runs/latest 模型开仿真
→ ④ 契约④判据验收 + 固定规则归因回显。LLM 全流程未收敛时自动回退 CSP 种子战役。
"""
import faulthandler
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
faulthandler.enable(file=open(os.path.join(HERE, "studio_crash.log"), "a", encoding="utf-8"),
                    all_threads=True)   # 原生崩溃捕获：闪退时堆栈落 studio_crash.log
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "src", "pipeline"))

import numpy as np
import mujoco
from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import (QApplication, QFileDialog, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QPushButton, QSplitter, QVBoxLayout,
                               QWidget)

from gantry_bridge import GantryBridge, derive_layout
from plc_link import PlcLink
from mujoco_build import build_mjcf
from mujoco_verdict import Collector, evaluate, attribute

SPEC = os.path.join(REPO, "examples", "specs", "plotter_square_csp.spec.json")
SEED = os.path.join(REPO, "src", "plc", "plotter_square_csp.xml")
LATEST_SPEC = os.path.join(REPO, "runs", "latest", "scene.spec.json")
LATEST_IOMAP = os.path.join(REPO, "runs", "latest", "io_map.json")
DEFAULT_AML = os.path.join(REPO, "examples", "aml", "motion3axis_station.aml")
AXES = ("X", "Y", "Z")


class SimThread(QThread):
    frame = Signal(object)

    def __init__(self, spec_path=LATEST_SPEC, io_map_path=LATEST_IOMAP):
        super().__init__()
        self.spec_path, self.io_map_path = spec_path, io_map_path
        self._run = True
        self.renderer = None   # GL 上下文须在使用线程内创建

    def run(self):
        spec = json.load(open(self.spec_path, encoding="utf-8"))
        io_map = json.load(open(self.io_map_path, encoding="utf-8")) \
            if os.path.isfile(self.io_map_path) else []
        self.model = mujoco.MjModel.from_xml_string(build_mjcf(spec))
        self.data = mujoco.MjData(self.model)
        travels = {"X": 1.0, "Y": 1.0, "Z": 1.0}
        for a in spec.get("assets", []):
            if a.get("type") == "gantry_xyz":
                travels = {k: float(a["params"].get("travel_%s" % k.lower(), 1))
                           for k in AXES}
        for attempt in range(6):                    # 端口释放重试
            try:
                layout, n = derive_layout(io_map, travels)
                self.bridge = GantryBridge(layout, n, host="127.0.0.1", port=5020)
                self.bridge.set_commands({a: travels[a] if a == "Z" else 0.0 for a in AXES})
                self.bridge.serve_forever()
                break
            except Exception:
                if attempt == 5:
                    raise
                time.sleep(1)
        self.link = PlcLink(self.bridge, io_map, spec=SPEC)
        self.link.start()
        self.speed = {a: 0.5 for a in AXES}
        self.aid = {a: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR,
                                         "drive_%s" % a.lower()) for a in AXES}
        pen = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "pen")
        paper = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "paper")
        self.model.vis.global_.offwidth = 1280   # 离屏帧缓冲扩大，Renderer 尺寸不再受默认 640 限制
        self.model.vis.global_.offheight = 960
        self.renderer = mujoco.Renderer(self.model, 720, 960)
        self.cam = mujoco.MjvCamera()
        self.cam.azimuth, self.cam.elevation, self.cam.distance = 135, -35, 1.6
        self.cam.lookat[:] = np.array([0.5, 0.5, 0.1])
        self.ink, self.ink_last = [], None
        frame_s, last, last_cmd = 1 / 120, 1 / 30, {a: 0.0 for a in AXES}
        self.model.opt.timestep = frame_s
        next_render = 0.0
        while self._run:
            t0 = time.perf_counter()
            for a, tgt in self.bridge.read_commands().items():
                step = self.speed[a] * frame_s
                d = tgt - last_cmd[a]
                last_cmd[a] = tgt if abs(d) <= step else last_cmd[a] + step * (1 if d > 0 else -1)
                self.data.ctrl[self.aid[a]] = np.clip(last_cmd[a], 0.0, 1.0)
            mujoco.mj_step(self.model, self.data)
            tip = self.data.geom_xpos[pen].copy()
            tip[2] -= self.model.geom_size[pen][1]
            top = self.data.geom_xpos[paper][2] + self.model.geom_size[paper][2]
            if tip[2] <= top + 0.012 and (
                    self.ink_last is None or
                    (tip[0] - self.ink_last[0]) ** 2 + (tip[1] - self.ink_last[1]) ** 2 >= 0.004 ** 2):
                self.ink.append((tip[0], tip[1], top + 0.0015))
                self.ink_last = tip.copy()
            if t0 >= next_render and self.renderer is not None:
                scn = self.renderer.scene
                self.renderer.update_scene(self.data, self.cam)
                for i, (x, y, z) in enumerate(self.ink[-2000:]):
                    if i >= scn.maxgeom - scn.ngeom:
                        break
                    mujoco.mjv_initGeom(scn.geoms[scn.ngeom], mujoco.mjtGeom.mjGEOM_SPHERE,
                                        np.array([0.004] * 3), np.array([x, y, z]),
                                        np.eye(3).flatten(),
                                        np.array([0.1, 0.1, 0.55, 1.0], dtype=np.float32))
                    scn.ngeom += 1
                self.frame.emit(self.renderer.render().copy())
                next_render = t0 + last
            rest = frame_s - (time.perf_counter() - t0)
            if rest > 0:
                time.sleep(rest)
        # 渲染器必须在本线程内显式关闭——若留给 GC 在主线程 __del__ 里 close()
        # 会跨线程销毁 GL 上下文（access violation，实测闪退根因）
        try:
            self.renderer.close()
        except Exception:
            pass
        self.renderer = None
        self.link.stop()
        self.bridge.stop()

    def orbit(self, dx, dy):
        if hasattr(self, "cam"):
            self.cam.azimuth -= 0.3 * dx
            self.cam.elevation = max(-89, min(89, self.cam.elevation + 0.3 * dy))

    def zoom(self, dy):
        if hasattr(self, "cam"):
            self.cam.distance = max(0.3, min(5.0, self.cam.distance * (1 + 0.1 * dy)))


class SimView(QWidget):
    def __init__(self):
        super().__init__()
        self.thread, self.img = None, None
        self.setMinimumSize(520, 520)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self.img is not None:
            p.drawImage(self.rect(), self.img)
        else:
            p.setPen(Qt.white)
            p.drawText(self.rect(), Qt.AlignCenter, "发送需求后：agent 生成 → 部署 → 自动加载模型仿真")
        p.end()

    def mousePressEvent(self, e):
        self._drag = e.position()

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None and self.thread:
            d = e.position() - self._drag
            self._drag = e.position()
            self.thread.orbit(d.x(), d.y())

    def wheelEvent(self, e):
        if self.thread:
            self.thread.zoom(1 if e.angleDelta().y() < 0 else -1)


class PipelineWorker(QThread):
    line = Signal(str)
    scene_ready = Signal()

    def __init__(self, aml, request):
        super().__init__()
        self.aml, self.request = aml, request

    def _orch(self, say, *extra):
        cmd = [sys.executable, "-u", "-m", "src.agent.orchestrator", *extra]
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        final = False
        for ln in proc.stdout:
            ln = ln.rstrip()
            say("  " + ln)
            final = final or "RESULT: final" in ln
        proc.wait()
        return final

    def run(self):
        say = self.line.emit
        say("▸ ⓪ 解析 AML：%s" % os.path.basename(self.aml))
        say("▸ ① 需求理解（LLM）→ ② 生成 XML + 场景 JSON → 闸门 → ③ OpenPLC 部署…")
        try:
            final = self._orch(say, "--aml", self.aml, "--request", self.request,
                               "--deploy", "--task-id", "studio_llm")
        except Exception as exc:
            say("  LLM 流程异常：%s" % exc)
            final = False
        if not final:
            say("▸ LLM 流程未收敛——自动回退 CSP 种子战役（已验证路径）…")
            final = self._orch(say, SPEC, "--seed", SEED, "--deploy",
                               "--task-id", "studio")
        if not final:
            say("✗ 部署未成功，见上方闸门输出（不加载模型）")
            return
        say("▸ 模型与程序已就绪（runs/latest 已刷新）——加载 MuJoCo 仿真…")
        self.scene_ready.emit()


class DeployWorker(QThread):
    line = Signal(str)
    done_ok = Signal()

    def __init__(self, xml):
        super().__init__()
        self.xml = xml

    def run(self):
        cmd = [sys.executable, "-u", os.path.join(REPO, "src", "pipeline", "run_deploy.py"),
               "--xml", self.xml]
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        for ln in proc.stdout:
            self.line.emit("  " + ln.rstrip())
        proc.wait()
        if proc.returncode == 0:
            self.done_ok.emit()
        else:
            self.line.emit("✗ 部署失败（退出码 %d）" % proc.returncode)


class AcceptanceWorker(QThread):
    line = Signal(str)

    def run(self):
        say = self.line.emit
        time.sleep(3)                     # 等仿真回环稳定（开场抬笔斜坡）
        say("▸ ④ 在线验收（契约④ 判据 + 固定规则归因）…")
        io = json.load(open(LATEST_IOMAP, encoding="utf-8"))
        chs = [{"name": e["plc_var"], "addr": int(e["modbus"]["plc_addr"][3:]),
                "type": "analog"} for e in io if e["type"] == "float"]
        chs += [{"name": n, "type": "coil"} for n in
                ("run", "cmd_draw", "pen_down", "plot_done", "any_moving")]
        try:
            col = Collector()
            trace = col.collect(chs, timeout_s=120.0)
        except Exception as exc:
            say("✗ 采集失败：%s" % exc)
            return
        say("  采集 %d 样本，%.1fs，plot_done=%s"
            % (len(trace), trace[-1]["t"], trace[-1].get("plot_done")))
        results = evaluate(json.load(open(SPEC, encoding="utf-8"))["acceptance"], trace)
        ok = all(r["pass"] for r in results)
        for r in results:
            say("  %s %s — %s" % ("PASS" if r["pass"] else "FAIL", r["id"], r["evidence"]))
        say("✅ 全部通过——画方完成。" if ok else attribute(results, trace, chs))


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("CSP Studio — 对话生成 × MuJoCo")
        self.resize(1360, 800)
        self.sim = None
        self.view = SimView()
        self.aml = QLineEdit(DEFAULT_AML)
        browse = QPushButton("浏览…")
        browse.clicked.connect(lambda: self._pick(self.aml, "AML (*.aml)"))
        aml_row = QHBoxLayout()
        self.aml.setPlaceholderText("AML 设备描述文件路径")
        aml_row.addWidget(self.aml, 1)
        aml_row.addWidget(browse)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.input = QLineEdit()
        self.input.setPlaceholderText('输入需求，回车发送（如"画一个正方形"）')
        self.input.returnPressed.connect(self.send)
        go = QPushButton("发送")
        go.clicked.connect(self.send)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(go)
        left = QVBoxLayout()
        left.addLayout(aml_row)
        self.scene_edit = QLineEdit(LATEST_SPEC)
        self.scene_edit.setPlaceholderText("场景 scene.spec.json（手动加载模型）")
        b1 = QPushButton("浏览")
        b1.clicked.connect(lambda: self._pick(self.scene_edit, "场景 JSON (*.json)"))
        b2 = QPushButton("加载模型")
        b2.clicked.connect(self._load_scene_manual)
        r1 = QHBoxLayout(); r1.addWidget(self.scene_edit, 1); r1.addWidget(b1); r1.addWidget(b2)
        left.addLayout(r1)
        self.xml_edit = QLineEdit(SEED)
        self.xml_edit.setPlaceholderText("PLCopen XML（手动部署运行）")
        b3 = QPushButton("浏览")
        b3.clicked.connect(lambda: self._pick(self.xml_edit, "PLCopen XML (*.xml)"))
        b4 = QPushButton("部署运行")
        b4.clicked.connect(self._deploy_xml)
        r2 = QHBoxLayout(); r2.addWidget(self.xml_edit, 1); r2.addWidget(b3); r2.addWidget(b4)
        left.addLayout(r2)
        left.addWidget(self.log, 1)
        left.addLayout(row)
        lw = QWidget()
        lw.setLayout(left)
        sp = QSplitter(Qt.Horizontal)
        sp.addWidget(lw)
        sp.addWidget(self.view)
        sp.setStretchFactor(0, 0)
        sp.setStretchFactor(1, 1)
        sp.setSizes([420, 940])
        lay = QVBoxLayout(self)
        lay.addWidget(sp)
        self.log.appendPlainText("就绪。选择 AML → 输入需求回车：agent 将解析需求、"
                                 "生成 XML 与场景 JSON、部署 OpenPLC，并自动加载模型开始仿真验收。\n")

    def _pick(self, edit, flt):
        p, _ = QFileDialog.getOpenFileName(self, "选择文件", REPO, flt + ";;All (*)")
        if p:
            edit.setText(p)

    def _load_scene_manual(self):
        spec = self.scene_edit.text().strip()
        if not os.path.isfile(spec):
            self.log.appendPlainText("场景 JSON 不存在。\n")
            return
        io = os.path.join(os.path.dirname(spec), "io_map.json")
        self.log.appendPlainText("▸ 手动加载模型：%s\n" % spec)
        self._load_scene(spec, io if os.path.isfile(io) else LATEST_IOMAP, acc=False)

    def _deploy_xml(self):
        xml = self.xml_edit.text().strip()
        if not os.path.isfile(xml):
            self.log.appendPlainText("XML 不存在。\n")
            return
        self.log.appendPlainText("▸ 手动部署运行：%s" % xml)
        self.deployer = DeployWorker(xml)
        self.deployer.line.connect(self.log.appendPlainText)
        self.deployer.done_ok.connect(lambda: (
            self.log.appendPlainText("▸ 部署成功——加载模型并验收…"),
            self._load_scene(LATEST_SPEC, LATEST_IOMAP, acc=True)))
        self.deployer.start()

    def _frame(self, arr):
        self.view.img = QImage(arr.data, arr.shape[1], arr.shape[0],
                               3 * arr.shape[1], QImage.Format_RGB888).copy()
        self.view.update()

    def _load_scene(self, spec_path=LATEST_SPEC, io_map_path=LATEST_IOMAP, acc=True):
        if self.sim is not None:
            self.sim._run = False
            self.sim.wait(2500)
            try:
                self.sim.stop()
            except Exception:
                pass
            self.view.thread = None
        self.sim = SimThread(spec_path, io_map_path)
        self.sim.frame.connect(self._frame)
        self.view.thread = self.sim
        self.sim.start()
        if acc:
            self.acc = AcceptanceWorker()
            self.acc.line.connect(self.log.appendPlainText)
            self.acc.start()

    def send(self):
        text = self.input.text().strip() or "画一个正方形"
        aml = self.aml.text().strip()
        self.input.clear()
        self.log.appendPlainText("你：%s" % text)
        if not os.path.isfile(aml):
            self.log.appendPlainText("Agent：AML 文件不存在，请先选择。\n")
            return
        self.worker = PipelineWorker(aml, text)
        self.worker.line.connect(self.log.appendPlainText)
        self.worker.scene_ready.connect(self._load_scene)
        self.worker.start()

    def closeEvent(self, _):
        if self.sim is not None:
            self.sim._run = False
            self.sim.wait(2000)


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
