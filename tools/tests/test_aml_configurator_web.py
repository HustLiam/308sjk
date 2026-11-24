"""标准 AML 配置器 Web 服务级测试：起服务 → upload → analyze → assemble → download。

运行：python tools/tests/test_aml_configurator_web.py 或 pytest。
覆盖：文件分类（含 bug 回归：tree.json 不得落入 unknown）、问答出题、组装产物回读、
下载端点、错误路径（缺 io.json 拒绝）。
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
SRV = os.path.join(REPO, "tools", "aml_configurator_web.py")
FIXTURE = os.path.join(REPO, "tools", "tests", "fixtures", "parts")


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    return s.getsockname()[1]


class Server:
    def __init__(self, extra=()):
        self.port = _free_port()
        self.proc = subprocess.Popen(
            [sys.executable, SRV, "--port", str(self.port), *extra],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.base = f"http://127.0.0.1:{self.port}"
        for _ in range(50):
            try:
                requests.get(self.base + "/", timeout=1)
                return
            except Exception:                              # noqa: BLE001
                time.sleep(0.2)
        raise RuntimeError("服务未起")

    def stop(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except Exception:                                  # noqa: BLE001
            self.proc.kill()


def test_web_full_pipeline():
    srv = Server(["--mock"])
    try:
        files = [("files", (f, open(os.path.join(FIXTURE, f), "rb")))
                 for f in os.listdir(FIXTURE)]
        r = requests.post(f"{srv.base}/api/upload", files=files)
        assert r.status_code == 200
        sid = r.json()["sid"]

        an = requests.post(f"{srv.base}/api/analyze", json={"sid": sid}).json()
        assert an["ok"], an
        # 分类回归：四件全被识别（曾因 KNOWN_JSON 键带扩展名全落 unknown）
        assert sorted(an["classification"]["parts_files"]) == \
            ["geometry", "joints", "poses", "tree"]
        assert 0 < len(an["questions"]) <= 6            # tree 齐备：仅缺位姿/可选件类问题

        answers = {q["id"]: {"question": q["ask"], "apply": q.get("apply"),
                             "answer": q.get("default", "")} for q in an["questions"]}
        asm = requests.post(f"{srv.base}/api/assemble",
                            json={"sid": sid, "answers": answers}).json()
        assert asm["ok"], asm
        v = asm["report"]["verify"]
        assert asm["report"]["assembly"]["signals"] == 14
        assert v["xsd"]["ok"] and v["roundtrip"]["io_readback"] == 14

        dl = requests.get(f"{srv.base}/api/download?sid={sid}")
        assert dl.status_code == 200 and b"CAEXFile" in dl.content[:200]
    finally:
        srv.stop()


def test_web_reject_missing_io():
    srv = Server(["--mock"])
    try:
        d = tempfile.mkdtemp(prefix="web_noio_")
        open(os.path.join(d, "bracket.stl"), "wb").write(b"solid x endsolid x")
        r = requests.post(f"{srv.base}/api/upload", files=[
            ("files", ("bracket.stl", open(os.path.join(d, "bracket.stl"), "rb")))])
        sid = r.json()["sid"]
        an = requests.post(f"{srv.base}/api/analyze", json={"sid": sid}).json()
        assert an["ok"] is False
        assert any("io.json" in e for e in an["errors"])
    finally:
        srv.stop()


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS：{t.__name__}")
    print(f"PASS：{len(tests)} 项 Web 服务级断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
