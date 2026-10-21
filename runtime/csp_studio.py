# -*- coding: utf-8 -*-
"""CSP Studio：Qt 对话界面复用严格完整 XML 闭环，实时显示同一次 MuJoCo 验收画面。"""
import faulthandler
import json
import os
import re
import subprocess
import sys
import time
import threading
from pathlib import Path
from uuid import uuid4

HERE = os.path.dirname(os.path.abspath(__file__))
faulthandler.enable(file=open(os.path.join(HERE, "studio_crash.log"), "a", encoding="utf-8"),
                    all_threads=True)   # 原生崩溃捕获：闪退时堆栈落 studio_crash.log
REPO = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, REPO)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "src", "pipeline"))

import numpy as np
import mujoco
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl
from PySide6.QtGui import QImage, QPainter, QDesktopServices, QFont, QFontDatabase
from PySide6.QtWidgets import (QApplication, QFileDialog, QFrame, QGridLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSplitter, QVBoxLayout, QWidget)

from gantry_bridge import GantryBridge, derive_layout
from plc_link import PlcLink
from mujoco_build import build_mjcf
from studio_backend import (pipeline_command, verified_result, stop_process_tree,
                            reset_cancelled_inputs, reset_plc_inputs, RemoteCamera)
from src.agent.config import MODEL

SPEC = os.path.join(REPO, "examples", "specs", "plotter_square_csp.spec.json")
SEED = os.path.join(REPO, "src", "plc", "plotter_square_csp.xml")
LATEST_SPEC = os.path.join(REPO, "runs", "latest", "scene.spec.json")
LATEST_IOMAP = os.path.join(REPO, "runs", "latest", "io_map.json")
DEFAULT_AML = os.path.join(REPO, "examples", "aml", "plotter_square_csp_station.aml")
AXES = ("X", "Y", "Z")


class SimThread(QThread):
    frame = Signal(object)
    error = Signal(str)
    connection = Signal(bool, str)

    def __init__(self, spec_path=LATEST_SPEC, io_map_path=LATEST_IOMAP, requirement_spec=SPEC):
        super().__init__()
        self.spec_path, self.io_map_path = spec_path, io_map_path
        self.requirement_spec = requirement_spec
        self._run = True
        self.renderer = None   # GL 上下文须在使用线程内创建

    def run(self):
        try:
            self._run_scene()
        except Exception as exc:
            self.error.emit("手动仿真失败：%s" % exc)
        finally:
            if self.renderer is not None:
                self.renderer.close()
                self.renderer = None
            if getattr(self, "link", None) is not None:
                self.link.stop()
            if getattr(self, "bridge", None) is not None:
                self.bridge.stop()

    def _run_scene(self):
        spec = json.load(open(self.spec_path, encoding="utf-8"))
        io_map = json.load(open(self.io_map_path, encoding="utf-8")) \
            if os.path.isfile(self.io_map_path) else []
        self.model = mujoco.MjModel.from_xml_string(build_mjcf(spec))
        self.data = mujoco.MjData(self.model)
        travels = {"X": 1.0, "Y": 1.0, "Z": 1.0}
        axis_speed = {a: 0.5 for a in AXES}
        for a in spec.get("assets", []):
            p = a.get("params", {})
            if a.get("type") == "gantry_xyz":
                travels = {k: float(p.get("travel_%s" % k.lower(), 1))
                           for k in AXES}
                axis_speed = {a: float(p.get("speed", 0.5)) for a in AXES}
            elif a.get("type") == "linear_axis":
                lo, hi = p["stroke"]
                travels[p["axis"].upper()] = (float(hi) - float(lo)) * float(p["scale_m_per_unit"])
                axis_speed[p["axis"].upper()] = float(p.get("vmax", 40)) * float(p["scale_m_per_unit"])
        for attempt in range(12):                   # 端口释放重试（切换场景时旧桥释放慢）
            try:
                layout, n = derive_layout(io_map, travels)
                self.bridge = GantryBridge(layout, n, host="127.0.0.1", port=5020)
                self.bridge.set_commands({a: travels.get(a, 1.0) if a == "Z" else 0.0
                                          for a in layout})
                self.bridge.serve_forever()
                break
            except Exception:
                if attempt == 11:
                    raise
                time.sleep(1)
        # 加载仅就位；复位使用独立连接，避免与后台轮询交叉读写同一 socket。
        try:
            reset_plc_inputs(self.requirement_spec)
        except Exception as exc:
            self.error.emit("加载时未复位 PLC 输入：%s" % exc)
        self.link = PlcLink(self.bridge, io_map, spec=self.requirement_spec,
                            on_status=self.connection.emit)
        self.link.start()
        # 参与轴 = 桥布局轴（io_map 推导）；场景缺该轴关节/执行器时显式失败
        # （mj_name2id 的 -1 负索引会静默写错轴，2026-09-22 轴集通用化）
        self.axes = list(self.bridge.layout)
        self.speed = {a: axis_speed[a] for a in self.axes}
        self.aid = {a: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR,
                                         "drive_%s" % a.lower()) for a in self.axes}
        self.jid = {a: mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_JOINT,
                                         "joint_%s" % a.lower()) for a in self.axes}
        missing = ["%s(joint/actuator)" % a for a in self.axes
                   if self.aid[a] < 0 or self.jid[a] < 0]
        if missing:
            raise RuntimeError("场景缺少 io_map 绑定轴的关节/执行器：%s——核对 io_map 与场景资产"
                               % ", ".join(missing))
        pen = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "pen")
        paper = mujoco.mj_name2id(self.model, mujoco.mjtObj.mjOBJ_GEOM, "paper")
        inkable = pen >= 0 and paper >= 0     # 绘图场景才有笔/纸；通用站台跳过墨迹
        self.model.vis.global_.offwidth = 1280   # 离屏帧缓冲扩大，Renderer 尺寸不再受默认 640 限制
        self.model.vis.global_.offheight = 960
        self.model.vis.headlight.ambient[:] = 0.5
        self.model.vis.headlight.diffuse[:] = 0.7
        self.model.vis.headlight.specular[:] = 0.1
        self.renderer = mujoco.Renderer(self.model, 720, 960)
        self.cam = mujoco.MjvCamera()
        self.cam.azimuth, self.cam.elevation, self.cam.distance = 135, -40, 2.5
        self.cam.lookat[:] = np.array([0.5, 0.5, 0.1])
        self.ink, self.ink_last = [], None
        frame_s, last, last_cmd = 1 / 120, 1 / 30, {a: 0.0 for a in self.axes}
        self.model.opt.timestep = frame_s
        next_render = 0.0
        while self._run:
            t0 = time.perf_counter()
            for a, tgt in self.bridge.read_commands().items():
                step = self.speed[a] * frame_s
                d = tgt - last_cmd[a]
                last_cmd[a] = tgt if abs(d) <= step else last_cmd[a] + step * (1 if d > 0 else -1)
                self.data.ctrl[self.aid[a]] = np.clip(last_cmd[a], 0.0,
                                                      self.bridge.layout[a]["travel"])
            mujoco.mj_step(self.model, self.data)
            # qpos 回写桥位置区——PlcLink 靠它把反馈写回 %QW；缺这行 PLC 永远等
            # 不到反馈（2026-09-21 实测教训，对齐 mujoco_jog_runtime 主循环）
            self.bridge.write_positions({a: float(self.data.qpos[self.jid[a]]) for a in self.axes})
            if inkable:
                tip = self.data.geom_xpos[pen].copy()
                tip[2] -= self.model.geom_size[pen][1] + self.model.geom_size[pen][0]
                top = self.data.geom_xpos[paper][2] + self.model.geom_size[paper][2]
                if tip[2] <= top + 0.0003 and (
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

    def orbit(self, dx, dy):
        if hasattr(self, "cam"):
            self.cam.azimuth -= 0.3 * dx
            self.cam.elevation = max(-89, min(89, self.cam.elevation + 0.3 * dy))

    def zoom(self, dy):
        if hasattr(self, "cam"):
            self.cam.distance = max(0.3, min(8.0, self.cam.distance * (1 + 0.1 * dy)))

    def reset_camera(self):
        if hasattr(self, 'cam'):
            self.cam.azimuth, self.cam.elevation, self.cam.distance = 135, -40, 2.5


class SnapshotThread(QThread):
    frame = Signal(object)
    error = Signal(str)

    def __init__(self, frame_path):
        super().__init__()
        self.frame_path, self._run = frame_path, True

    def run(self):
        renderer = None
        try:
            from frame_stream import SnapshotRenderer
            renderer = SnapshotRenderer(self.frame_path)
            camera_path, stamp = Path(self.frame_path).with_suffix('.camera.json'), object()
            while self._run:
                try:
                    current = camera_path.stat().st_mtime_ns
                except OSError:
                    current = None
                if current != stamp:
                    self.frame.emit(renderer.render().copy())
                    stamp = current
                self.msleep(50)
        except Exception as exc:
            self.error.emit('结果视角查看失败：%s' % exc)
        finally:
            if renderer is not None:
                renderer.close()


class SimView(QWidget):
    def __init__(self):
        super().__init__()
        self.thread, self.img = None, None
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip('左键拖动旋转 · 滚轮缩放 · 双击恢复视角')
        self.setMinimumSize(520, 520)

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self.img is not None:
            scaled = self.img.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
            target = scaled.rect()
            target.moveCenter(self.rect().center())
            p.drawImage(target, scaled)
        else:
            p.setPen(Qt.white)
            p.drawText(self.rect(), Qt.AlignCenter, "发送需求后：agent 生成 → 部署 → 自动加载模型仿真")
        p.end()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._drag = None
        self.setCursor(Qt.OpenHandCursor)

    def mouseMoveEvent(self, e):
        if getattr(self, "_drag", None) is not None and self.thread:
            d = e.position() - self._drag
            self._drag = e.position()
            self.thread.orbit(d.x(), d.y())

    def wheelEvent(self, e):
        if self.thread and e.angleDelta().y():
            self.thread.zoom(1 if e.angleDelta().y() < 0 else -1)
            e.accept()

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.LeftButton and self.thread:
            self.thread.reset_camera()
            self._drag = None
            self.setCursor(Qt.OpenHandCursor)
            e.accept()


class PipelineWorker(QThread):
    line = Signal(str)
    stage = Signal(str)
    task_done = Signal(str, str)

    def __init__(self, aml, request):
        super().__init__()
        self.aml, self.request = aml, request
        self.task = time.strftime("studio_%Y%m%d_%H%M%S_") + uuid4().hex[:6]
        self.run_dir = Path(REPO) / "runs" / self.task
        self.proc = None
        self.service = None
        self.cancelled = threading.Event()

    def cancel(self):
        self.cancelled.set()
        if self.proc is not None:
            stop_process_tree(self.proc)

    def _ensure_services(self):
        import requests
        session = requests.Session()
        session.trust_env = False
        def available(url):
            try:
                return session.get(url, timeout=2).status_code < 500
            except requests.RequestException:
                return False
        self.stage.emit("检查运行环境")
        url = os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080")
        if not available(url):
            self.line.emit("▸ OpenPLC 未就绪，尝试启动已有 openplc 容器…")
            started = subprocess.run(["docker", "start", "openplc"], capture_output=True,
                                     text=True, timeout=30,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            if started.returncode:
                raise RuntimeError("OpenPLC 启动失败，请检查 Docker Desktop：" + started.stderr[-500:])
            deadline = time.monotonic() + 20
            while not available(url):
                if self.cancelled.is_set():
                    return
                if time.monotonic() > deadline:
                    raise RuntimeError("OpenPLC Web 服务未就绪：" + url)
                time.sleep(0.3)
        if not available("http://127.0.0.1:8600/health"):
            self.line.emit("▸ 启动本地部署服务 :8600…")
            work = Path(REPO) / "workspace"
            work.mkdir(exist_ok=True)
            with (work / "studio_deploy.log").open("a", encoding="utf-8") as log:
                self.service = subprocess.Popen(
                    [sys.executable, "-u", str(Path(REPO) / "src/pipeline/serve.py")],
                    cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    start_new_session=os.name != "nt")
            deadline = time.monotonic() + 10
            while not available("http://127.0.0.1:8600/health"):
                if self.cancelled.is_set():
                    return
                if self.service.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError("部署服务启动失败，见 workspace/studio_deploy.log")
                time.sleep(0.2)
        session.close()

    def run(self):
        status = "failed"
        try:
            cmd = pipeline_command(self.aml, self.request, self.task)
            self._ensure_services()
            if self.cancelled.is_set():
                status = "cancelled"
                return
            self.line.emit("▸ 任务：%s\n▸ 模型 %s，完整 XML 生成；严格部署与本轮验收。" % (self.task, MODEL))
            self.proc = subprocess.Popen(
                cmd, cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace",
                env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"},
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                start_new_session=os.name != "nt")
            if self.cancelled.is_set():
                stop_process_tree(self.proc)
            for line in self.proc.stdout:
                line = line.rstrip()
                self.line.emit(line)
                if "[requirement]" in line:
                    self.stage.emit("需求理解")
                elif "[test-plan]" in line:
                    self.stage.emit("LLM 规划仿真启动与测试动作")
                elif "[preflight]" in line:
                    self.stage.emit("LLM 测试计划校验通过")
                elif "定向修复" in line:
                    self.stage.emit("反馈修复")
                elif "联系模型" in line or "模型正在输出" in line:
                    self.stage.emit("完整 XML 生成")
                elif "正在部署" in line:
                    self.stage.emit("真实编译部署")
                elif "[runtime]" in line:
                    self.stage.emit("MuJoCo 仿真与验收")
                elif "[execution]" in line:
                    self.stage.emit("按 LLM 计划执行激励、采样与验收")
            self.proc.wait()
            if self.cancelled.is_set():
                status = "cancelled"
            elif verified_result(self.run_dir, self.proc.returncode):
                status = "final"
            elif (self.run_dir / "api_error.json").is_file():
                status = "api_blocked"
            else:
                status = "failed"
        except Exception as exc:
            self.line.emit("✗ 严格闭环失败：%s" % exc)
        finally:
            if self.cancelled.is_set():
                status = "cancelled"
                try:
                    reset_cancelled_inputs(self.run_dir)
                except Exception as exc:
                    self.line.emit("停止后输入复位未完成：%s" % exc)
            self.task_done.emit(status, str(self.run_dir))


class DeployWorker(QThread):
    line = Signal(str)
    done_ok = Signal()

    def __init__(self, xml):
        super().__init__()
        self.xml = xml
        self.proc = None
        self.ok = False

    def run(self):
        cmd = [sys.executable, "-u", os.path.join(REPO, "src", "pipeline", "run_deploy.py"),
               "--xml", self.xml]
        proc = subprocess.Popen(cmd, cwd=REPO, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace",
                                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.proc = proc
        for ln in proc.stdout:
            self.line.emit("  " + ln.rstrip())
        proc.wait()
        if proc.returncode == 0:
            self.ok = True
            self.done_ok.emit()
        else:
            self.line.emit("✗ 部署失败（退出码 %d）" % proc.returncode)




class HmiPanel(QWidget):
    """解析 hmi.json 构建分组操作面板（2026-09-23 负责人设计：HMI 为独立
    生成产物，XML 保持纯 61131-10 不变）。

    布局：操作按钮（4 列网格）/ 指示灯 / 过程量按轴分组（X/Y/Z/其他 各一盒，
    值大字、名小字），整体入滚动区——元素多时不挤压主界面。
    BUTTON → 写线圈开关（SafeCoilIO 读-改-写）；LAMP → 轮询线圈指示；
    GAUGE → 轮询 %QW 数值显示。
    """

    def __init__(self, hmi_path, log):
        super().__init__()
        self.log = log
        self.hmi_path = hmi_path
        self.cli = None
        self.io = None
        self.buttons, self.lamps, self.gauges = {}, [], []
        self.button_widgets = {}
        self._press_times, self._pulse_timers = {}, {}
        self.coil_span = 16
        self._root = QVBoxLayout(self)
        self._root.setContentsMargins(0, 2, 0, 2)
        self._body = None
        self.reload()

    @staticmethod
    def _axis_of(name):
        m = re.match(r"^([xyz])_", name) or re.match(r"^rel_([xyz])_d$", name)
        return m.group(1) if m else "其"

    @staticmethod
    def _button_label(element):
        label = element.get("label") or element["signal"]
        return re.sub(r"^OperatorPanel\s*", "", label).split("（")[0][:10]

    def disconnect(self):
        for timer in self._pulse_timers.values():
            timer.stop()
        self._press_times.clear()
        if self.cli is not None:
            self.cli.close()
        self.cli = self.io = None

    def _press(self, name, bit):
        timer = self._pulse_timers.get(name)
        if timer is not None:
            timer.stop()
        if self._write(name, bit, True):
            self._press_times[name] = time.monotonic()
            self.log.appendPlainText("HMI：%s → ON（启动脉冲）" % name)

    def _release(self, name, bit):
        started = self._press_times.get(name)
        if started is None:
            return
        remaining = 0.15 - (time.monotonic() - started)
        if remaining <= 0:
            self._finish_pulse(name, bit)
            return
        if name not in self._pulse_timers:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(lambda: self._finish_pulse(name, bit))
            self._pulse_timers[name] = timer
        self._pulse_timers[name].start(int(remaining * 1000) + 1)

    def _finish_pulse(self, name, bit):
        self._press_times.pop(name, None)
        if self._write(name, bit, False):
            self.log.appendPlainText("HMI：%s → OFF（脉冲结束）" % name)

    def _bind_button(self, widget, element, bit):
        name = element["signal"]
        if element.get("type") == "trigger":
            widget.setStyleSheet("font-weight: 700;")
            widget.setToolTip("%s（%s）点击有效，最短脉冲150ms；按住保持" % (name, element.get("address")))
            widget.pressed.connect(lambda: self._press(name, bit))
            widget.released.connect(lambda: self._release(name, bit))
        else:
            widget.setCheckable(True)
            widget.setStyleSheet("QPushButton:checked {background: #c7efce; color: #134b20;}")
            widget.setToolTip("%s（%s）点击切换，绿色为已使能" % (name, element.get("address")))
            widget.clicked.connect(lambda: self._toggle(name, bit))
        self.buttons[name] = bit
        self.button_widgets[name] = widget

    def reload(self):
        self.disconnect()
        for timer in self._pulse_timers.values():
            timer.deleteLater()
        self._pulse_timers.clear()
        self.buttons, self.lamps, self.gauges = {}, [], []
        self.button_widgets = {}
        if self._body is not None:
            self._root.removeWidget(self._body)
            self._body.deleteLater()
            self._body = None
        try:
            elements = json.load(open(self.hmi_path, encoding="utf-8"))["elements"]
        except Exception as exc:
            self._root.addWidget(QLabel("HMI：%s 不可读（%s）"
                                        % (os.path.basename(self.hmi_path),
                                           exc.__class__.__name__)))
            return
        bits = [int(m[1]) * 8 + int(m[2]) for e in elements
                if (m := re.fullmatch(r"%QX(\d+)\.([0-7])", e.get("address", "")))]
        self.coil_span = max(16, ((max(bits, default=0) // 16) + 1) * 16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        col = QVBoxLayout(body)
        col.setSpacing(4)

        has_pos = any(e.get("pos") for e in elements)
        if has_pos:
            # v2：agent 设计的统一网格——全部控件按 pos 摆放，分区语义由
            # 设计者用行表达（命令行/状态行/每轴过程量行）
            grid = QGridLayout()
            grid.setSpacing(3)
            for e in elements:
                pos = e.get("pos")
                kind = e.get("kind")
                if not pos or kind not in ("BUTTON", "LAMP", "GAUGE"):
                    continue
                if kind == "BUTTON":
                    b = re.match(r"^%QX(\d+)\.([0-7])$", e.get("address") or "")
                    if not b:
                        continue
                    w = QPushButton(self._button_label(e))
                    w.setFixedHeight(30)
                    bit = int(b.group(1)) * 8 + int(b.group(2))
                    self._bind_button(w, e, bit)
                    grid.addWidget(w, pos[0], pos[1])
                elif kind == "LAMP":
                    b = re.match(r"^%QX(\d+)\.([0-7])$", e.get("address") or "")
                    if not b:
                        continue
                    w = QPushButton("◌")
                    w.setFixedSize(34, 34)
                    w.setEnabled(False)
                    w.setToolTip("%s（%s）" % (e["signal"], e.get("address")))
                    w._bit = int(b.group(1)) * 8 + int(b.group(2))
                    w._label = (e.get("label") or e["signal"]).split("（")[0][:5]
                    self.lamps.append(w)
                    grid.addWidget(w, pos[0], pos[1])
                else:
                    m = re.match(r"^%QW(\d+)$", e.get("address") or "")
                    if not m:
                        continue
                    val = QLabel("--")
                    val.setAlignment(Qt.AlignCenter)
                    val.setMinimumSize(44, 26)
                    val.setStyleSheet(
                        "font: 700 10pt 'Consolas'; border: 1px solid #ccd;"
                        " border-radius: 3px; padding: 1px 4px; background: #fff;")
                    val._addr = int(m.group(1))
                    nm = QLabel(e["signal"])
                    nm.setAlignment(Qt.AlignCenter)
                    nm.setStyleSheet("font: 7pt; color: #666;")
                    cell = QWidget()
                    v = QVBoxLayout(cell)
                    v.setContentsMargins(0, 0, 0, 0)
                    v.setSpacing(0)
                    v.addWidget(val)
                    v.addWidget(nm)
                    self.gauges.append(val)
                    grid.addWidget(cell, pos[0], pos[1])
            col.addLayout(grid)
        else:
            gbox = QGroupBox("操作")
            gl = QGridLayout(gbox)
            gl.setSpacing(3)
            idx = 0
            for e in elements:
                if e.get("kind") != "BUTTON":
                    continue
                b = re.match(r"^%QX(\d+)\.([0-7])$", e.get("address") or "")
                if not b:
                    continue
                label = self._button_label(e)
                w = QPushButton(label)
                w.setFixedHeight(30)
                bit = int(b.group(1)) * 8 + int(b.group(2))
                self._bind_button(w, e, bit)
                gl.addWidget(w, idx // 4, idx % 4)
                idx += 1
            if idx:
                col.addWidget(gbox)

            lbox = QGroupBox("指示")
            hl = QHBoxLayout(lbox)
            hl.setSpacing(3)
            for e in elements:
                if e.get("kind") != "LAMP":
                    continue
                b = re.match(r"^%QX(\d+)\.([0-7])$", e.get("address") or "")
                if not b:
                    continue
                label = (e.get("label") or e["signal"]).split("（")[0][:5]
                w = QPushButton("◌")
                w.setFixedSize(34, 34)
                w.setEnabled(False)
                w.setToolTip("%s（%s）" % (e["signal"], e.get("address")))
                w._bit = int(b.group(1)) * 8 + int(b.group(2))
                w._label = label
                self.lamps.append(w)
                hl.addWidget(w)
            if self.lamps:
                col.addWidget(lbox)

            groups = {}
            for e in elements:
                if e.get("kind") != "GAUGE":
                    continue
                m = re.match(r"^%QW(\d+)$", e.get("address") or "")
                if not m:
                    continue
                groups.setdefault(self._axis_of(e["signal"]), []).append(
                    (e, int(m.group(1))))
            for axis in sorted(groups):
                g = QGroupBox("%s 轴" % axis.upper() if axis != "其" else "其他")
                h = QHBoxLayout(g)
                h.setSpacing(3)
                for e, addr in groups[axis]:
                    val = QLabel("--")
                    val.setAlignment(Qt.AlignCenter)
                    val.setMinimumSize(44, 26)
                    val.setStyleSheet(
                        "font: 700 10pt 'Consolas'; border: 1px solid #ccd;"
                        " border-radius: 3px; padding: 1px 4px; background: #fff;")
                    val._addr = addr
                    nm = QLabel(e["signal"])
                    nm.setAlignment(Qt.AlignCenter)
                    nm.setStyleSheet("font: 7pt; color: #666;")
                    cell = QWidget()
                    v = QVBoxLayout(cell)
                    v.setContentsMargins(0, 0, 0, 0)
                    v.setSpacing(0)
                    v.addWidget(val)
                    v.addWidget(nm)
                    self.gauges.append(val)
                    h.addWidget(cell)
                col.addWidget(g)

        scroll.setWidget(body)
        self._body = scroll
        self._root.addWidget(scroll)
        self.log.appendPlainText("HMI：%d 按钮 / %d 灯 / %d 表（分组布局）\n"
                                 % (len(self.buttons), len(self.lamps),
                                    len(self.gauges)))

    def _write(self, name, bit, value):
        if not self._connect():
            self.log.appendPlainText("HMI：OpenPLC 不可达，无法写 %s\n" % name)
            return False
        try:
            self.io.write(bit, value)
            return True
        except Exception as exc:
            self.log.appendPlainText("HMI：写 %s 失败（%s）\n" % (name, exc))
            self.disconnect()
            return False

    def _toggle(self, name, bit):
        if not self._connect():
            self.log.appendPlainText("HMI：OpenPLC 不可达，无法写 %s\n" % name)
            self.button_widgets[name].setChecked(False)
            return
        try:
            cur = self.io.read(bit)
            self.io.write(bit, not cur)
            self.button_widgets[name].setChecked(not cur)
            self.log.appendPlainText("HMI：%s → %s\n"
                                     % (name, "ON" if not cur else "OFF"))
        except Exception as exc:
            self.log.appendPlainText("HMI：写 %s 失败（%s）\n" % (name, exc))
            self.disconnect()
            self.button_widgets[name].setChecked(False)

    def _connect(self):
        if self.cli is None:
            from pymodbus.client import ModbusTcpClient
            from modbus_io import SafeCoilIO
            cli = ModbusTcpClient(os.environ.get("MODBUS_HOST", "127.0.0.1"),
                                  port=int(os.environ.get("MODBUS_PORT", "502")),
                                  timeout=1.5)
            if not cli.connect():
                return False
            self.cli, self.io = cli, SafeCoilIO(cli, span=self.coil_span)
        return True

    def poll(self):
        """外部定时调用（主窗口 QTimer 统一驱动）：刷灯与表。"""
        if not self.buttons and not self.lamps and not self.gauges:
            return
        if not self._connect():
            return
        try:
            for name, widget in self.button_widgets.items():
                if widget.isCheckable():
                    widget.setChecked(self.io.read(self.buttons[name]))
            for w in self.lamps:
                on = self.io.read(w._bit)
                w.setText("●" if on else "◌")
                w.setToolTip("%s = %s" % (w._label, "ON" if on else "OFF"))
                w.setStyleSheet("color: #0a0; font-size: 13pt;"
                                if on else "color: #aaa; font-size: 10pt;")
            if self.gauges:
                addrs = [w._addr for w in self.gauges]
                rr = self.cli.read_holding_registers(address=0,
                                                     count=max(addrs) + 1, slave=1)
                for w in self.gauges:
                    w.setText("%d" % rr.registers[w._addr])
        except Exception as exc:
            self.log.appendPlainText("HMI：连接中断，将重新连接（%s）" % type(exc).__name__)
            self.disconnect()


class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        if os.name == "nt":
            for family, filename in (("Microsoft YaHei", "msyh.ttc"),
                                     ("Segoe UI Symbol", "seguisym.ttf")):
                if family not in QFontDatabase.families():
                    font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
                    if font.is_file():
                        QFontDatabase.addApplicationFont(str(font))
            ui_font = QFont()
            ui_font.setFamilies(["Microsoft YaHei", "Segoe UI Symbol"])
            ui_font.setPointSize(9)
            self.setFont(ui_font)
        self.setWindowTitle("CSP Studio — 对话生成 × MuJoCo")
        self.resize(1360, 800)
        self.sim = None
        self.inspector = None
        self.worker = None
        self.active_run = None
        self.frame_path = None
        self.frame_stamp = None
        self.received_frames = 0
        self.job_started = None
        self.owned_services = []
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
        self.input.setPlaceholderText('输入工艺需求，例如画正方形或三角形')
        self.input.returnPressed.connect(self.send)
        self.go_button = QPushButton("运行完整闭环")
        self.go_button.clicked.connect(self.send)
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self.cancel_pipeline)
        self.results_button = QPushButton("打开本次结果")
        self.results_button.setEnabled(False)
        self.results_button.clicked.connect(self.open_results)
        row = QHBoxLayout()
        row.addWidget(self.input, 1)
        row.addWidget(self.go_button)
        row.addWidget(self.stop_button)
        left = QVBoxLayout()
        self.status = QLabel("就绪 · %s · 完整 XML" % MODEL)
        self.status.setWordWrap(True)
        left.addWidget(self.status)
        self.elapsed = QLabel("运行时间：—")
        left.addWidget(self.elapsed)
        left.addWidget(self.results_button)
        left.addLayout(aml_row)
        self.scene_edit = QLineEdit(LATEST_SPEC)
        self.scene_edit.setPlaceholderText("场景 scene.spec.json（手动加载模型）")
        b1 = QPushButton("浏览")
        b1.clicked.connect(lambda: self._pick(self.scene_edit, "场景 JSON (*.json)"))
        b2 = QPushButton("加载模型")
        self.load_button = b2
        b2.clicked.connect(self._load_scene_manual)
        r1 = QHBoxLayout(); r1.addWidget(self.scene_edit, 1); r1.addWidget(b1); r1.addWidget(b2)
        left.addLayout(r1)
        self.xml_edit = QLineEdit(SEED)
        self.xml_edit.setPlaceholderText("PLCopen XML（手动部署运行）")
        b3 = QPushButton("浏览")
        b3.clicked.connect(lambda: self._pick(self.xml_edit, "PLCopen XML (*.xml)"))
        b4 = QPushButton("部署运行")
        self.deploy_button = b4
        b4.clicked.connect(self._deploy_xml)
        r2 = QHBoxLayout(); r2.addWidget(self.xml_edit, 1); r2.addWidget(b3); r2.addWidget(b4)
        left.addLayout(r2)
        self.hmi = HmiPanel(os.path.join(REPO, "runs", "latest", "hmi.json"), self.log)
        self.hmi.setMaximumHeight(240)
        left.addWidget(self.hmi)
        self._hmi_timer = QTimer(self)
        self._hmi_timer.timeout.connect(self.hmi.poll)
        self.hmi.setEnabled(False)
        self._frame_timer = QTimer(self)
        self._frame_timer.timeout.connect(self._read_live_frame)
        self._frame_timer.start(80)
        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.timeout.connect(self._update_elapsed)
        self._elapsed_timer.start(1000)
        left.addWidget(self.log, 1)
        left.addLayout(row)
        lw = QWidget()
        lw.setLayout(left)
        sp = QSplitter(Qt.Horizontal)
        sp.addWidget(lw)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self.view, 1)
        right_layout.addWidget(QLabel('左键拖动旋转 · 滚轮缩放 · 双击恢复视角'))
        sp.addWidget(right)
        sp.setStretchFactor(0, 0)
        sp.setStretchFactor(1, 1)
        sp.setSizes([420, 940])
        lay = QVBoxLayout(self)
        lay.addWidget(sp)
        self.log.appendPlainText("就绪。默认 AML 为三轴 CSP 绘图站。输入需求后运行完整闭环："
                                 "需求理解 → LLM 测试计划 → 完整 XML → 编译部署 → 按计划仿真与验收 → 反馈修复。\n"
                                 "右侧画面来自本次验收的实际仿真；自动运行期间 HMI 保持只读。\n")

    def _set_busy(self, busy):
        self.go_button.setEnabled(not busy)
        self.stop_button.setEnabled(busy)
        self.input.setEnabled(not busy)
        self.aml.setEnabled(not busy)
        self.load_button.setEnabled(not busy)
        self.deploy_button.setEnabled(not busy)
        self.hmi.setEnabled(False if busy else self.sim is not None)

    def _update_elapsed(self):
        if self.job_started is not None:
            seconds = int(time.monotonic() - self.job_started)
            self.elapsed.setText("运行时间：%02d:%02d · 已收到 %d 帧" %
                                 (seconds // 60, seconds % 60, self.received_frames))

    def _stop_manual_sim(self):
        if self.inspector is not None:
            self.inspector._run = False
            if not self.inspector.wait(5000):
                self.log.appendPlainText('结果视角仍在停止，请稍后重试。')
                return False
            self.inspector = None
        self._hmi_timer.stop()
        self.hmi.setEnabled(False)
        self.hmi.disconnect()
        if self.sim is not None:
            try:
                reset_plc_inputs(self.sim.requirement_spec)
            except Exception as exc:
                self.log.appendPlainText("停止手动仿真时输入复位未完成：%s" % exc)
            self.sim._run = False
            if not self.sim.wait(5000):
                self.log.appendPlainText("手动仿真仍在停止，请稍后重试。")
                return False
            self.sim = None
        self.view.thread = None
        return True

    def _manual_connection(self, sim, online, message):
        if sim is not self.sim:
            return
        deploying = getattr(self, "deployer", None) is not None and self.deployer.isRunning()
        deploy_failed = getattr(self, "deployer", None) is not None and not deploying and not self.deployer.ok
        self.log.appendPlainText("手动仿真：%s" % message)
        self.hmi.setEnabled(online and not deploying and not deploy_failed)
        if not online:
            self._hmi_timer.stop()
            self.hmi.disconnect()
        elif not deploying and not deploy_failed:
            self._hmi_timer.start(400)
        self.status.setText("手动部署中 · 等待 PLC 重启" if deploying else
                            ("部署失败 · HMI 已暂停" if deploy_failed else
                             ("手动运行就绪 · 按本任务 HMI 使能并启动" if online else "PLC 连接中断 · 自动重连中")))

    def _manual_deploy_finished(self):
        self.go_button.setEnabled(True)
        self.load_button.setEnabled(True)
        self.deploy_button.setEnabled(True)
        self.hmi.disconnect()
        if not self.deployer.ok:
            self.hmi.setEnabled(False)
            self.status.setText("部署失败 · HMI 已暂停，请检查部署日志")
            return
        online = (self.sim is not None and self.sim.isRunning()
                  and getattr(self.sim, "link", None) is not None and self.sim.link.online)
        self.hmi.setEnabled(online)
        if online:
            self.status.setText("手动运行就绪 · 按本任务 HMI 使能并启动")
            self._hmi_timer.start(400)
        elif self.sim is not None:
            self.status.setText("等待 PLC 反馈连接 · 自动重连中")

    def _read_live_frame(self):
        if not self.frame_path or not self.frame_path.is_file():
            return
        try:
            stamp = self.frame_path.stat().st_mtime_ns
            if stamp == self.frame_stamp:
                return
            with self.frame_path.open("rb") as f:
                arr = np.load(f, allow_pickle=False)
            if arr.dtype != np.uint8 or arr.ndim != 3 or arr.shape[2] != 3:
                return
            self.frame_stamp = stamp
            self.received_frames += 1
            self._frame(arr)
        except (OSError, ValueError, EOFError):
            pass

    def cancel_pipeline(self):
        if self.worker is not None and self.worker.isRunning():
            self.status.setText("正在停止本次任务…")
            self.worker.cancel()

    def open_results(self):
        if self.active_run and self.active_run.is_dir():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.active_run)))

    def _pipeline_done(self, status, run_dir):
        self.active_run = Path(run_dir)
        if self.worker.service is not None:
            self.owned_services.append(self.worker.service)
        self.results_button.setEnabled(self.active_run.is_dir())
        self._set_busy(False)
        self._read_live_frame()
        self._start_inspection()
        self._update_elapsed()
        self.job_started = None
        if status == "final":
            self.status.setText("全部验收通过 · %s" % self.active_run.name)
            self.log.appendPlainText("✓ 本次完整 XML、真实编译及物理验收全部通过。结果已保存。")
            final = self.active_run / "final"
            self.scene_edit.setText(str(final / "scene.spec.json"))
            self.xml_edit.setText(str(final / "plcopen.xml"))
            self.hmi.hmi_path = str(final / "hmi.json")
            self.hmi.reload()
        elif status == "cancelled":
            self.status.setText("任务已停止 · 过程证据已保留")
            self.log.appendPlainText("本次任务已停止，未作为验收通过交付。")
        elif status == "api_blocked":
            self.status.setText("模型接口余额或鉴权阻断 · 结果与最佳版已保存")
            self.log.appendPlainText("本次模型调用已停止；充值或更新配置后需启动新进程。best.json指向已保留的最佳版本，该版本尚未全部通过。")
        else:
            self.status.setText("闭环未通过 · 查看本次日志与 gate.json")
            self.log.appendPlainText("✗ 本次闭环未全部通过，失败证据已保留；未使用种子替代。")

    def _start_inspection(self):
        if not self.frame_path or not self.frame_path.with_suffix('.state.npz').is_file():
            return
        path = self.frame_path
        self.frame_path = None
        self.inspector = SnapshotThread(path)
        current = self.inspector
        self.inspector.frame.connect(lambda arr: self._frame(arr) if self.inspector is current else None)
        self.inspector.error.connect(self.log.appendPlainText)
        self.view.thread = RemoteCamera(path)
        # 沿用本轮相机，避免任务结束时跳回默认视角。
        camera_path = path.with_suffix('.camera.json')
        if camera_path.is_file():
            try:
                self.view.thread.camera.update(json.loads(camera_path.read_text(encoding='utf-8')))
            except (OSError, ValueError, TypeError):
                pass
        self.inspector.start()
        self.log.appendPlainText('画面为本次实际状态，可拖动查看；只改变视角，不推进物理或操作PLC，HMI保持只读。')

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
        if self.worker is not None and self.worker.isRunning():
            return
        if getattr(self, "deployer", None) is not None and self.deployer.isRunning():
            return
        xml = self.xml_edit.text().strip()
        if not os.path.isfile(xml):
            self.log.appendPlainText("XML 不存在。\n")
            return
        self.log.appendPlainText("▸ 手动部署运行：%s" % xml)
        self._hmi_timer.stop()
        self.hmi.setEnabled(False)
        self.hmi.disconnect()
        for button in (self.go_button, self.load_button, self.deploy_button):
            button.setEnabled(False)
        self.status.setText("手动部署中 · HMI 暂停，等待 PLC 重启")
        self.deployer = DeployWorker(xml)
        self.deployer.line.connect(self.log.appendPlainText)
        self.deployer.done_ok.connect(lambda: self.log.appendPlainText(
            "▸ 部署成功。已加载模型时将自动恢复反馈连接；连接就绪后可用 HMI 操作。"))
        self.deployer.finished.connect(self._manual_deploy_finished)
        self.deployer.start()

    def _frame(self, arr):
        self.view.img = QImage(arr.data, arr.shape[1], arr.shape[0],
                               3 * arr.shape[1], QImage.Format_RGB888).copy()
        self.view.update()

    def _load_scene(self, spec_path=LATEST_SPEC, io_map_path=LATEST_IOMAP, acc=False):
        if not self._stop_manual_sim():
            return
        self.frame_path = None
        hmi_path = Path(spec_path).parent / "hmi.json"
        if hmi_path.is_file():
            self.hmi.hmi_path = str(hmi_path)
            self.hmi.reload()
        requirement = Path(spec_path).parent.parent / "requirements/requirement_spec.json"
        self.sim = SimThread(spec_path, io_map_path, str(requirement) if requirement.is_file() else SPEC)
        self.sim.frame.connect(self._frame)
        self.sim.error.connect(self.log.appendPlainText)
        current = self.sim
        self.sim.connection.connect(lambda online, message: self._manual_connection(current, online, message))
        self.sim.finished.connect(lambda: self.hmi.setEnabled(False) if self.sim is current else None)
        self.view.thread = self.sim
        self.sim.start()
        self.hmi.setEnabled(False)
        self.status.setText("正在加载手动仿真 · 等待反馈连接")

    def send(self):
        if self.worker is not None and self.worker.isRunning():
            return
        if getattr(self, "deployer", None) is not None and self.deployer.isRunning():
            self.log.appendPlainText("请等待手动部署结束后再运行闭环。")
            return
        text = self.input.text().strip() or "画一个正方形"
        aml = self.aml.text().strip()
        self.input.clear()
        self.log.appendPlainText("你：%s" % text)
        if not os.path.isfile(aml):
            self.log.appendPlainText("Agent：AML 文件不存在，请先选择。\n")
            return
        if not self._stop_manual_sim():
            return
        self._hmi_timer.stop()
        self._set_busy(True)
        self.status.setText("启动严格完整 XML 闭环…")
        self.job_started = time.monotonic()
        self.elapsed.setText("运行时间：00:00 · 等待模型与运行环境")
        self.worker = PipelineWorker(aml, text)
        self.active_run = self.worker.run_dir
        self.frame_path = self.active_run / "live_frame.npy"
        self.frame_stamp, self.received_frames = None, 0
        self.view.img = None
        self.view.thread = RemoteCamera(self.frame_path)
        self.worker.line.connect(self.log.appendPlainText)
        self.worker.stage.connect(self.status.setText)
        self.worker.task_done.connect(self._pipeline_done)
        self.worker.start()

    def closeEvent(self, event):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            if not self.worker.wait(10000):
                event.ignore()
                return
        deployer = getattr(self, "deployer", None)
        if deployer is not None and deployer.isRunning():
            stop_process_tree(deployer.proc)
            if not deployer.wait(10000):
                event.ignore()
                return
        if not self._stop_manual_sim():
            event.ignore()
            return
        for proc in self.owned_services:
            stop_process_tree(proc)
        if self.worker is not None and self.worker.service is not None:
            stop_process_tree(self.worker.service)
        self.hmi.disconnect()
        event.accept()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
