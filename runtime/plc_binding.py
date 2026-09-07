"""链路 A 的 Python 侧绑定：IOLayout（纯逻辑：镜像索引/定点换算/打包）+ SoftPLC（ctypes 薄封装）。

镜像索引规则与 toolchain/shim_gen.py 的 build_layout **必须一致**（shim 的 di/ai/dq/aq
排列 = 本类 pack/unpack 的排列）——两侧由 toolchain/tests/test_link_a.py 的 golden
测试共同锁定，改任一侧必须同步另一侧。

定点换算（契约② §5.1 / 契约③）：analog 一律 INT16 定点，`scale` = 每 LSB 工程量
（unit/LSB，默认 1.0）；raw = round(eng / scale)，钳位 [-32768, 32767]；word 为原始
16 位（无符号语义，如 CiA402 状态字），不做换算；bool 为 0/1。
"""

import ctypes
import struct

INT16_MIN, INT16_MAX = -32768, 32767


def _mirror_index(io_map):
    """与 shim_gen.build_layout 相同的紧凑索引规则（见文件头说明）。"""
    index = {}
    counters = {"di": 0, "ai": 0, "dq": 0, "aq": 0}
    for e in io_map:
        image = ("di", "ai") if e["dir"] == "input" else ("dq", "aq")
        image = image[0] if e["type"] == "bool" else image[1]
        index[e["plc_var"]] = (image, counters[image], e["type"])
        counters[image] += 1
    return index, counters


class IOLayout:
    """io_map → 镜像尺寸 + 定点换算 + 打包/解包（纯 Python，无需 DLL 即可测试）。"""

    def __init__(self, io_map):
        self.io_map = io_map
        self.index, self.counters = _mirror_index(io_map)
        self.by_var = {e["plc_var"]: e for e in io_map}
        self.sizes = {"di": self.counters["di"], "ai": self.counters["ai"],
                      "dq": self.counters["dq"], "aq": self.counters["aq"]}

    # ---------- 定点换算 ----------

    def to_raw(self, plc_var: str, eng) -> int:
        e = self.by_var[plc_var]
        if e["type"] == "bool":
            return 1 if eng else 0
        if e["type"] == "word":
            v = int(eng) & 0xFFFF            # 原始 16 位，无符号语义
            return v
        scale = float(e.get("scale", 1.0))
        raw = int(round(float(eng) / scale))
        return max(INT16_MIN, min(INT16_MAX, raw))

    def to_eng(self, plc_var: str, raw: int):
        e = self.by_var[plc_var]
        if e["type"] == "bool":
            return bool(raw)
        if e["type"] == "word":
            return int(raw) & 0xFFFF          # 无符号语义
        scale = float(e.get("scale", 1.0))
        return int(raw) * scale

    # ---------- 打包 / 解包（与 shim 的 di/ai/dq/aq 排列一致） ----------

    def pack_inputs(self, values: dict):
        """{plc_var: 工程值} → (di: bytes, ai: bytes)——供 plc_write_image。未指定的变量置 0。"""
        di = [0] * self.sizes["di"]
        ai = [0] * self.sizes["ai"]
        for e in self.io_map:
            if e["dir"] != "input":
                continue
            raw = self.to_raw(e["plc_var"], values.get(e["plc_var"], 0 if e["type"] != "bool" else False))
            image, idx, _ = self.index[e["plc_var"]]
            if image == "di":
                di[idx] = raw
            else:
                ai[idx] = raw
        return bytes(di), struct.pack(f"<{len(ai)}h", *ai)

    def unpack_outputs(self, dq_bytes: bytes, aq_bytes: bytes) -> dict:
        """(dq: bytes, aq: bytes) → {plc_var: 工程值}——来自 plc_read_image。"""
        dq = list(dq_bytes)
        aq = list(struct.unpack(f"<{len(aq_bytes)//2}h", aq_bytes)) if aq_bytes else []
        out = {}
        for e in self.io_map:
            if e["dir"] != "output":
                continue
            image, idx, _ = self.index[e["plc_var"]]
            raw = dq[idx] if image == "dq" else aq[idx]
            out[e["plc_var"]] = self.to_eng(e["plc_var"], raw)
        return out


class SoftPLC:
    """加载 shim 编译出的共享库，提供逐扫描周期调用（进程内 lockstep，链路 A 主链路）。

    用法（见 csk 文档 §6.2.3 / §6.3）：
        plc = SoftPLC("plc_logic.dll", io_map)
        plc.init()
        for tick in range(n):
            plc.write_inputs({"PE1_detected": True, ...})   # ① 传感注入
            plc.run(tick)                                    # ② 一个 PLC 扫描
            out = plc.read_outputs()                         # ③ PLC 输出（工程量）
    """

    def __init__(self, lib_path: str, io_map):
        self.layout = io_map if isinstance(io_map, IOLayout) else IOLayout(io_map)
        self.lib = ctypes.CDLL(lib_path)
        self.lib.plc_init.argtypes = []
        self.lib.plc_init.restype = None
        self.lib.plc_run.argtypes = [ctypes.c_ulong]
        self.lib.plc_run.restype = None
        self.lib.plc_write_image.argtypes = [ctypes.POINTER(ctypes.c_ubyte),
                                             ctypes.POINTER(ctypes.c_int16)]
        self.lib.plc_write_image.restype = None
        self.lib.plc_read_image.argtypes = [ctypes.POINTER(ctypes.c_ubyte),
                                            ctypes.POINTER(ctypes.c_int16)]
        self.lib.plc_read_image.restype = None
        self._di = (ctypes.c_ubyte * self.layout.sizes["di"])()
        self._ai = (ctypes.c_int16 * self.layout.sizes["ai"])()
        self._dq = (ctypes.c_ubyte * self.layout.sizes["dq"])()
        self._aq = (ctypes.c_int16 * self.layout.sizes["aq"])()

    def init(self):
        self.lib.plc_init()

    def write_inputs(self, values: dict):
        di, ai = self.layout.pack_inputs(values)
        ctypes.memmove(self._di, di, len(di))
        ctypes.memmove(self._ai, ai, len(ai))
        self.lib.plc_write_image(self._di, self._ai)

    def run(self, tick: int):
        self.lib.plc_run(ctypes.c_ulong(tick))

    def read_outputs(self) -> dict:
        self.lib.plc_read_image(self._dq, self._aq)
        return self.layout.unpack_outputs(bytes(self._dq),
                                          bytes(memoryview(self._aq).cast("B")))
