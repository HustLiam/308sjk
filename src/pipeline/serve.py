#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署服务：把「XML → OpenPLC 运行」暴露成 HTTP API（agent 闭环的部署端点）。

用法:
    python src/pipeline/serve.py [--port 8600]

接口:
    POST /deploy        body 为 PLCopen XML 内容
    GET  /health        存活检查
    GET  /status        运行时状态 + 当前程序身份（gc 编排半环一站式确认）

    curl -X POST http://127.0.0.1:8600/deploy --data-binary @src/plc/motion3axis.xml
    curl http://127.0.0.1:8600/status
返回 deploy_result.json 同构数据（status/steps/errors，errors 可直接回喂 agent）；
/status 返回 {runtime:{url,status}, prog_id, program}——prog_id 经 Modbus 读
%QW20（契约② v1.1 程序身份约定；STOPPED 下 %Q 缓冲保持仍可读）。
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_DEPLOY = Path(__file__).resolve().parent / "run_deploy.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modbus_io import PROG_ID_REG, connect, read_reg          # noqa: E402
from openplc_client import OpenPLCClient                       # noqa: E402

# prog_id → 场景名（与 lx 文档 §5.3 prog_id 分配一致；新场景在此顺延登记）
PROG_NAMES = {1: "motion3axis"}


class DeployHandler(BaseHTTPRequestHandler):
    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _collect_status():
        """汇总运行时状态与当前程序身份；任何一腿失败都不 500，如实报字段。"""
        info = {"serve": "ok",
                "runtime": {"url": os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080"),
                            "status": "UNREACHABLE"},
                "prog_id": None, "program": None}
        try:
            client = OpenPLCClient(base_url=info["runtime"]["url"], timeout=5)
            info["runtime"]["status"] = client.status()
        except Exception as e:
            info["runtime"]["error"] = str(e)
        try:
            m = connect(host=os.environ.get("MODBUS_HOST", "127.0.0.1"),
                        port=int(os.environ.get("MODBUS_PORT", "502")))
            try:
                pid = read_reg(m, PROG_ID_REG)
                info["prog_id"] = pid
                info["program"] = PROG_NAMES.get(pid, "unknown(prog_id=%d)" % pid)
            finally:
                m.close()
        except Exception as e:
            info["modbus_error"] = str(e)
        return info

    def do_GET(self):
        if self.path == "/health":
            self._json(200, {"status": "ok"})
        elif self.path == "/status":
            self._json(200, self._collect_status())
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/deploy":
            self._json(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", 0))
        xml_bytes = self.rfile.read(length)
        if not xml_bytes.strip().startswith(b"<?xml"):
            self._json(400, {"status": "REJECTED",
                             "errors": ["body 不是 XML（应以 <?xml 开头）"]})
            return

        name = self.headers.get("X-Deploy-Xml-Name", "upload.xml")
        with tempfile.NamedTemporaryFile(
                prefix="deploy_", suffix=Path(name).suffix or ".xml",
                dir=str(REPO_ROOT / "workspace"), delete=False) as tf:
            tf.write(xml_bytes)
            tmp = Path(tf.name)
        try:
            proc = subprocess.run(
                [sys.executable, str(RUN_DEPLOY), "--xml", str(tmp)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=900)
            result_path = REPO_ROOT / "workspace" / "deploy_result.json"
            if result_path.exists():
                self._json(200 if proc.returncode == 0 else 500,
                           json.loads(result_path.read_text(encoding="utf-8")))
            else:
                self._json(500, {
                    "status": "FAILED",
                    "errors": ["run_deploy 未产出结果文件",
                               proc.stdout.decode("utf-8", errors="replace")[-2000:]],
                })
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass

    def log_message(self, fmt, *args):
        print("[deploy-server] " + fmt % args)


def main():
    parser = argparse.ArgumentParser(description="PLCopen XML deploy HTTP service (OpenPLC)")
    parser.add_argument("--port", type=int, default=8600)
    args = parser.parse_args()
    (REPO_ROOT / "workspace").mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), DeployHandler)
    print("[deploy-server] listening on http://127.0.0.1:%d/deploy | /health | /status" % args.port)
    server.serve_forever()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
