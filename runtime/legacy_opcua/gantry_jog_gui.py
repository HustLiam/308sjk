#!/usr/bin/env python
"""龙门 XY 轴鼠标示教器（OPC UA 客户端）【已废弃 → Modbus 版】。

主路线已切换 Modbus TCP（见 ../gantry_jog_gui.py）；本文件是 OPC UA 时期的
客户端实现，仅为将来评估 OpenPLC Runtime v4 + OPC UA 备选链路（csk 文档 §4.5）
时保留参考。配套服务端与本目录 isaac_opcua_server.py、test_opcua_loop.py。
"""

import argparse
import threading
import time
import tkinter as tk

from asyncua import sync

WS_W_M, WS_H_M = 0.6, 0.4          # 与 gantry_xyz 的 travel_x/y 一致
SCALE = 700                         # 像素/米
MARGIN = 56
AXIS_NAMES = ("AxisX_cmd", "AxisY_cmd", "AxisZ_cmd")


class GantryOpcUaClient:
    """OPC UA 客户端：连接 Isaac 服务端，浏览定位 Objects/Gantry 下轴节点并写入。"""

    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        self._cli = None
        self._nodes = {}

    def connect(self) -> None:
        cli = sync.Client(self.endpoint)
        cli.connect()
        ns = cli.get_namespace_index("gantry")
        gantry = cli.nodes.objects.get_child([f"{ns}:Gantry"])
        self._nodes = {n: gantry.get_child([f"{ns}:{n}"]) for n in AXIS_NAMES}
        self._cli = cli

    def disconnect(self) -> None:
        if self._cli is not None:
            try:
                self._cli.disconnect()
            finally:
                self._cli = None

    @property
    def connected(self) -> bool:
        return self._cli is not None

    def write_axis(self, name: str, value_m: float) -> None:
        self._nodes[name].write_value(float(value_m))

    def write_xy(self, x_m: float, y_m: float) -> None:
        self.write_axis("AxisX_cmd", x_m)
        self.write_axis("AxisY_cmd", y_m)


class JogApp:
    def __init__(self, root: tk.Tk, endpoint: str):
        self.root = root
        root.title("龙门 XY 鼠标示教器（拖动画笔 → OPC UA → Isaac Sim）")
        self.client = GantryOpcUaClient(endpoint)
        self._lock = threading.Lock()
        self._target = (0.0, 0.0)          # 米
        self._sent = None
        self._writer_on = False

        top = tk.Frame(root)
        top.pack(fill=tk.X, padx=8, pady=6)
        tk.Label(top, text="端点:").pack(side=tk.LEFT)
        self.ep_var = tk.StringVar(value=endpoint)
        tk.Entry(top, textvariable=self.ep_var, width=42).pack(side=tk.LEFT, padx=4)
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

        self.readout = tk.Label(root, text="X = 0.000 m    Y = 0.000 m    （Z 不参与，保持抬笔）",
                                font=("Consolas", 11))
        self.readout.pack(pady=(0, 8))

        self.cv.bind("<Button-1>", self._on_press)
        self.cv.bind("<B1-Motion>", self._on_drag)

        self._thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._thread.start()

    # ---------- 画布 ----------

    def _draw_workspace(self, w_px: int, h_px: int) -> None:
        x0, y0 = MARGIN, MARGIN
        x1, y1 = MARGIN + w_px, MARGIN + h_px
        self.cv.create_rectangle(x0, y0, x1, y1, fill="#ffffff", outline="#888")
        # 0.1m 网格
        gx = x0
        while gx <= x1 + 0.1:
            self.cv.create_line(gx, y0, gx, y1, fill="#eee")
            gx += SCALE / 10
        gy = y0
        while gy <= y1 + 0.1:
            self.cv.create_line(x0, gy, x1, gy, fill="#eee")
            gy += SCALE / 10
        # 行程中位十字（画圆圆心）
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        self.cv.create_line(cx - 10, cy, cx + 10, cy, fill="#4a7dc9")
        self.cv.create_line(cx, cy - 10, cx, cy + 10, fill="#4a7dc9")
        # 坐标标注
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

    # ---------- 鼠标 ----------

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
            self._target = (round(x_m, 4), round(y_m, 4))
        self.readout.config(text=f"X = {x_m:.3f} m    Y = {y_m:.3f} m    （Z 不参与，保持抬笔）")

    # ---------- OPC UA 写线程 ----------

    def toggle_connect(self):
        if self.client.connected:
            self._writer_on = False
            self.client.disconnect()
            self.btn.config(text="连接")
            self.status.config(text="未连接", fg="#a00")
            return
        try:
            self.client.endpoint = self.ep_var.get().strip()
            self.client.connect()
        except Exception as exc:
            self.status.config(text=f"连接失败: {exc}", fg="#a00")
            return
        self._writer_on = True
        self.btn.config(text="断开")
        self.status.config(text=f"已连接 {self.client.endpoint}", fg="#070")

    def _writer_loop(self):
        """20Hz 写线程：目标变化 >1mm 才写，避免刷爆 OPC UA。"""
        while True:
            if self._writer_on and self.client.connected:
                with self._lock:
                    tgt = self._target
                if tgt != self._sent:
                    try:
                        self.client.write_xy(*tgt)
                        self._sent = tgt
                    except Exception as exc:
                        self.status.config(text=f"写入失败: {exc}", fg="#a00")
                        self._writer_on = False
                        self.root.after(0, lambda: self.btn.config(text="连接"))
            time.sleep(0.05)


def main():
    parser = argparse.ArgumentParser(description="龙门 XY 鼠标示教器")
    parser.add_argument("--endpoint", default="opc.tcp://127.0.0.1:4840",
                        help="Isaac 端 OPC UA 服务端地址")
    args = parser.parse_args()

    root = tk.Tk()
    JogApp(root, args.endpoint)
    root.mainloop()


if __name__ == "__main__":
    main()
