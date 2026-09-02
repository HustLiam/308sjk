"""OPC UA 回环无头测试：模拟 Isaac 服务端（与 isaac_opcua_server.py 相同的
命名空间/节点结构），用 gantry_jog_gui.GantryOpcUaClient 连接/浏览/写入，
断言服务端收到目标值。不打开 GUI 窗口、不需要 Isaac Sim。

运行：python runtime/tests/test_opcua_loop.py
"""

import asyncio
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # legacy 自包含

from asyncua import Server  # noqa: E402

from gantry_jog_gui import GantryOpcUaClient  # noqa: E402

ENDPOINT = "opc.tcp://127.0.0.1:48411/test"


class EmuServer:
    """后台线程里的 asyncua Server，复刻 Isaac 侧节点结构。"""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.ready = threading.Event()
        self.thread.start()
        self.ready.wait(timeout=10)

    def _run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_until_complete(self._main())

    async def _main(self):
        srv = Server()
        await srv.init()
        srv.set_endpoint(ENDPOINT)
        idx = await srv.register_namespace("gantry")
        obj = await srv.nodes.objects.add_object(idx, "Gantry")
        self.nodes = {}
        for name in ("AxisX_cmd", "AxisY_cmd", "AxisZ_cmd"):
            n = await obj.add_variable(idx, name, 0.0)
            await n.set_writable()
            self.nodes[name] = n
        await srv.start()
        self._srv = srv
        self.ready.set()
        await asyncio.Event().wait()          # 挂起直到 stop

    def value(self, name: str) -> float:
        async def _read():
            return await self.nodes[name].read_value()
        return asyncio.run_coroutine_threadsafe(_read(), self.loop).result(timeout=5)

    def stop(self):
        async def _halt():
            await self._srv.stop()
        asyncio.run_coroutine_threadsafe(_halt(), self.loop).result(timeout=5)
        self.loop.call_soon_threadsafe(self.loop.stop)


def wait_value(getter, expect, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if abs(getter() - expect) < 1e-6:
                return True
        except Exception:
            pass
        time.sleep(0.05)
    return False


def main() -> int:
    srv = EmuServer()
    n = 0
    try:
        cli = GantryOpcUaClient(ENDPOINT)
        cli.connect()
        assert cli.connected
        n += 1

        cli.write_xy(0.3, 0.2)                # 与 GUI 拖拽等效的写入入口
        assert wait_value(lambda: srv.value("AxisX_cmd"), 0.3), "AxisX_cmd 未到服务端"
        assert wait_value(lambda: srv.value("AxisY_cmd"), 0.2), "AxisY_cmd 未到服务端"
        n += 1

        cli.write_axis("AxisZ_cmd", 0.1)      # Z 节点存在且可写（GUI 不用它）
        assert wait_value(lambda: srv.value("AxisZ_cmd"), 0.1), "AxisZ_cmd 未到服务端"
        n += 1

        cli.disconnect()
        cli2 = GantryOpcUaClient(ENDPOINT)    # 断开后可重连（GUI 反复连接场景）
        cli2.connect()
        cli2.write_xy(0.0, 0.4)
        assert wait_value(lambda: srv.value("AxisY_cmd"), 0.4), "重连后写入失败"
        cli2.disconnect()
        n += 1

        print(f"PASS：{n} 组断言全部通过（服务端 {ENDPOINT}）")
        return 0
    finally:
        srv.stop()


if __name__ == "__main__":
    sys.exit(main())
