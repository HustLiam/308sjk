#!/usr/bin/env python
"""龙门三轴鼠标示教器（Modbus TCP 客户端）。

同一台电脑或局域网内任意机器运行：连接 Isaac Sim 内的 Modbus 服务端
（runtime/isaac_modbus_server.py，:5020），在 2D 画布上拖动"画笔"，鼠标位置
实时换算为 AxisX/Y_cmd（米，float32 大端）写入指令寄存器；Z 轴由"抬笔/落笔"
按钮控制；位置反馈寄存器回读实际坐标驱动画笔显示（真实闭环位置，非指令值）。

用法：
    python gantry_jog_gui.py                                   # 默认连本机 :5020
    python gantry_jog_gui.py --host 192.168.x.x
    python gantry_jog_gui.py --io-map ../scenegen/out/gantry/io_map.json
依赖：pymodbus（任意 3.x；服务端须 <3.9，见 runtime/requirements.txt）。tkinter 自带。
"""

import argparse
import os
import struct
import sys
import threading
import time
import tkinter as tk

from pymodbus.client import ModbusTcpClient

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from gantry_bridge import AXES, derive_layout, load_io_map  # noqa: E402

WS_W_M, WS_H_M = 0.6, 0.4          # 与 gantry_xyz 的 travel_x/y 一致（布局推导会按 io_map 修正画布）
SCALE = 700                         # 像素/米
MARGIN = 56
DEFAULT_IO_MAP = os.path.normpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "scenegen", "out", "gantry", "io_map.json"))


class GantryModbusClient:
    """Modbus TCP 客户端：写轴指令寄存器（FC16），读轴位置反馈寄存器（FC03）。"""

    def __init__(self, host: str, port: int, layout: dict):
        self.endpoint = f"{host}:{port}"
        self.layout = layout
        self._cli = None

    def connect(self) -> None:
        cli = ModbusTcpClient(self.endpoint.split(":")[0],
                              port=int(self.endpoint.split(":")[1]), timeout=2.0)
        if not cli.connect():
            raise ConnectionError(f"无法连接 {self.endpoint}")
        self._cli = cli

    def disconnect(self) -> None:
        if self._cli is not None:
            try:
                self._cli.close()
            finally:
                self._cli = None

    @property
    def connected(self) -> bool:
        return self._cli is not None

    def write_cmd(self, axis: str, value_m: float) -> None:
        cfg = self.layout[axis]
        regs = list(struct.unpack(">2H", struct.pack(">f", float(value_m))))
        rr = self._cli.write_registers(address=cfg["cmd_reg"], values=regs, slave=1)
        if rr.isError():
            raise IOError(f"写 Axis{axis}_cmd 失败: {rr}")

    def write_xy(self, x_m: float, y_m: float) -> None:
        self.write_cmd("X", x_m)
        self.write_cmd("Y", y_m)

    def read_positions(self) -> dict:
        out = {}
        for axis in AXES:
            cfg = self.layout[axis]
            rr = self._cli.read_holding_registers(address=cfg["pos_reg"], count=2, slave=1)
            if rr.isError():
                raise IOError(f"读 Axis{axis}_pos 失败: {rr}")
            out[axis] = struct.unpack(">f", struct.pack(">2H", *rr.registers))[0]
        return out


class JogApp:
    def __init__(self, root: tk.Tk, client: GantryModbusClient, travel: dict):
        self.root = root
        self.client = client
        self.travel = travel
        root.title("龙门三轴鼠标示教器（拖动画笔 → Modbus → Isaac Sim）")
        self._lock = threading.Lock()
        self._target = {"X": 0.0, "Y": 0.0, "Z": travel["Z"]}   # 开场与场景一致：抬笔
        self._sent = None
        self._actual = {"X": 0.0, "Y": 0.0, "Z": travel["Z"]}
        self._writer_on = False
        self._pen_down = False

        top = tk.Frame(root)
        top.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(top, text="服务端:").pack(side=tk.LEFT)
        self.ep_var = tk.StringVar(value=client.endpoint)
        tk.Entry(top, textvariable=self.ep_var, width=28).pack(side=tk.LEFT, padx=4)
        self.btn = tk.Button(top, text="连接", width=10, command=self.toggle_connect)
        self.btn.pack(side=tk.LEFT, padx=4)
        self.status = tk.Label(top, text="未连接", fg="#a00")
        self.status.pack(side=tk.LEFT, padx=8)

        w_px, h_px = int(WS_W_M * SCALE), int(WS_H_M * SCALE)
        self.cv = tk.Canvas(root, width=w_px + 2 * MARGIN, height=h_px + 2 * MARGIN,
                            bg="#f4f4ef", highlightthickness=0)
        self.cv.pack(padx=8, pady=4)
        self._draw_workspace(w_px, h_px)
        self.pen = self.cv.create_oval(0, 0, 0, 0, fill="#d93025", outline="#7a1010", width=2)
        self._place_pen(0.0, 0.0)

        ctrl = tk.Frame(root)
        ctrl.pack(pady=(0, 4))
        self.z_btn = tk.Button(ctrl, text="落笔", width=10, command=self.toggle_z,
                               state=tk.DISABLED)
        self.z_btn.pack()
        self.readout = tk.Label(root, text="X = --    Y = --    Z = --（反馈寄存器实际位置）",
                                font=("Consolas", 11))
        self.readout.pack(pady=(0, 4))
        self.reginfo = tk.Label(root, text="", font=("Consolas", 9), fg="#666")
        self.reginfo.pack(pady=(0, 8))

        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)

        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._thread.start()
        self._reader.start()

    # ---------- 画布 ----------

    def _draw_workspace(self, w_px: int, h_px: int) -> None:
        x0, y0 = MARGIN, MARGIN
        x1, y1 = MARGIN + w_px, MARGIN + h_px
        self.cv.create_rectangle(x0, y0, x1, y1, fill="#ffffff", outline="#888")
        gx = x0
        while gx <= x1 + 0.1:
            self.cv.create_line(gx, y0, gx, y1, fill="#eee")
            gx += SCALE / 10
        gy = y0
        while gy <= y1 + 0.1:
            self.cv.create_line(x0, gy, x1, gy, fill="#eee")
            gy += SCALE / 10
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.cv.create_line(cx - 10, cy, cx + 10, cy, fill="#4a7dc9")
        self.cv.create_line(cx, cy - 10, cx, cy + 10, fill="#4a7dc9")
        self.cv.create_text(x0, y1 + 14, text="(0, 0) 行程原点", anchor="nw", fill="#666")
        self.cv.create_text(x1, y0 - 8, text=f"({WS_W_M} m, {WS_H_M} m)", anchor="ne", fill="#666")

    def _m_to_canvas(self, x_m: float, y_m: float):
        return (MARGIN + x_m * SCALE,
                MARGIN + (WS_H_M - y_m) * SCALE)          # 屏幕 y 向下 = 画布向上为 +Y

    def _canvas_to_m(self, px: float, py: float):
        x = (px - MARGIN) / SCALE
        y = (WS_H_M - (py - MARGIN) / SCALE)
        return (max(0.0, min(x, WS_W_M)), max(0.0, min(y, WS_H_M)))

    def _place_pen(self, x_m: float, y_m: float) -> None:
        px, py = self._m_to_canvas(x_m, y_m)
        r = 9
        self.cv.coords(self.pen, px - r, py - r, px + r, py + r)
        self.cv.tag_raise(self.pen)

    # ---------- 鼠标 / Z 轴 ----------

    def _on_press(self, e):
        self._apply_cursor(e.x, e.y)

    def _on_drag(self, e):
        self._apply_cursor(e.x, e.y)

    def _apply_cursor(self, px, py):
        if not (MARGIN - 4 <= px <= MARGIN + WS_W_M * SCALE + 4
                and MARGIN - 4 <= py <= MARGIN + WS_H_M * SCALE + 4):
            return
        x_m, y_m = self._canvas_to_m(px, py)
        self._place_pen(x_m, y_m)
        with self._lock:
            self._target["X"], self._target["Y"] = round(x_m, 4), round(y_m, 4)

    def toggle_z(self):
        with self._lock:
            self._pen_down = not self._pen_down
            self._target["Z"] = 0.0 if self._pen_down else self.travel["Z"]
        self.z_btn.config(text="抬笔" if self._pen_down else "落笔")

    # ---------- 连接与读写线程 ----------

    def toggle_connect(self):
        if self.client.connected:
            self._writer_on = False
            self.client.disconnect()
            self.z_btn.config(state=tk.DISABLED)
            self.btn.config(text="连接")
            self.status.config(text="未连接", fg="#a00")
            return
        host, _, port = self.ep_var.get().strip().partition(":")
        self.client.endpoint = f"{host}:{port or 5020}"
        self.client.__init__(host or "127.0.0.1", int(port or 5020), self.client.layout)
        try:
            self.client.connect()
        except Exception as exc:
            self.status.config(text=f"连接失败: {exc}", fg="#a00")
            return
        self._writer_on = True
        self.z_btn.config(state=tk.NORMAL)
        self.btn.config(text="断开")
        self.status.config(text=f"已连接 {self.client.endpoint}", fg="#070")

    def _writer_loop(self):
        """20Hz 写线程：目标变化 >1mm 才写。断线不再反复重试（GUI 点"连接"恢复）。"""
        while True:
            if self._writer_on and self.client.connected:
                with self._lock:
                    tgt = dict(self._target)
                if tgt != self._sent:
                    try:
                        self.client.write_xy(tgt["X"], tgt["Y"])
                        self.client.write_cmd("Z", tgt["Z"])
                        self._sent = tgt
                    except Exception as exc:
                        self._writer_on = False
                        self._sent = None
                        self.root.after(0, lambda: (
                            self.z_btn.config(state=tk.DISABLED),
                            self.btn.config(text="连接"),
                            self.status.config(text=f"写入失败: {exc}", fg="#a00")))
            time.sleep(0.05)

    def _reader_loop(self):
        """5Hz 读反馈寄存器：画笔跟随实际位置（闭环验证直观可见跟随误差）。"""
        while True:
            if self._writer_on and self.client.connected:
                try:
                    act = self.client.read_positions()
                    with self._lock:
                        self._actual = act
                    self._place_pen(act["X"], act["Y"])
                    self.readout.config(text="X = {:.3f} m    Y = {:.3f} m    Z = {:.3f} m"
                                             "    （反馈实际位置）".format(
                                                 act["X"], act["Y"], act["Z"]))
                except Exception:
                    pass                      # 单次读失败不打断，下一轮重试
            time.sleep(0.2)


def main():
    parser = argparse.ArgumentParser(description="龙门三轴鼠标示教器（Modbus）")
    parser.add_argument("--host", default="127.0.0.1", help="Isaac 侧 Modbus 服务端地址")
    parser.add_argument("--port", type=int, default=5020)
    parser.add_argument("--io-map", default=DEFAULT_IO_MAP,
                        help="io_map.json 路径（推导寄存器布局与行程；缺省用仓库 gantry 场景）")
    args = parser.parse_args()

    io_map = load_io_map(args.io_map if os.path.isfile(args.io_map) else None)
    layout, _ = derive_layout(io_map)
    travel = {a: layout[a]["travel"] for a in AXES}

    root = tk.Tk()
    client = GantryModbusClient(args.host, args.port, layout)
    app = JogApp(root, client, travel)
    app.reginfo.config(text="  ".join(
        f"Axis{a}: cmd@{layout[a]['cmd_reg']} pos@{layout[a]['pos_reg']}" for a in AXES))
    root.mainloop()


if __name__ == "__main__":
    main()
