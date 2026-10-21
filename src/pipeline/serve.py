#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署服务：把「XML → OpenPLC 运行」暴露成 HTTP API（agent 自动循环——生成→部署→验收→失败回喂——的部署端点）。

用法:
    python src/pipeline/serve.py [--port 8600]

接口:
    POST /deploy        body 为 PLCopen XML 内容
    GET  /health        存活检查
    GET  /status        运行时状态 + 当前程序身份（gc 编排半环一站式确认）
    POST /runtime/start 启动当前 Runtime 程序
    POST /runtime/stop  停止当前 Runtime 程序
    GET  /runtime/logs  读取 Runtime 日志

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
import threading
import hashlib
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO_ROOT = Path(__file__).resolve().parents[2]
RUN_DEPLOY = Path(__file__).resolve().parent / "run_deploy.py"
sys.path.insert(0, str(Path(__file__).resolve().parent))

from modbus_io import PROG_ID_REG, connect, read_reg          # noqa: E402
from openplc_client import OpenPLCClient                       # noqa: E402

# prog_id → 场景名（与 lx 文档 §5.3 prog_id 分配一致；新场景在此顺延登记）
PROG_NAMES = {1: "motion3axis", 2: "plotter3axis", 3: "plotter_circle",
              4: "plotter_square_csp"}
OPERATION_LOCK = threading.Lock()
RUNTIME_ACCESS_LOCK = threading.Lock()
LAST_DEPLOYMENT = None


def runtime_client():
    return OpenPLCClient(base_url=os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080"),
                         username=os.environ.get("OPENPLC_USERNAME", "openplc"),
                         password=os.environ.get("OPENPLC_PASSWORD", "openplc"), timeout=10)


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
        # OpenPLC 旧版内部 RPC 不适合并发访问；部署期间不穿插状态查询。
        if OPERATION_LOCK.locked() or not RUNTIME_ACCESS_LOCK.acquire(blocking=False):
            return {"serve":"ok", "runtime":{"url":os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080"),
                    "status":"BUSY"}, "prog_id":None, "program":None,
                    "capabilities":["deploy","start","stop","logs"], "deployment":LAST_DEPLOYMENT}
        try:
            return DeployHandler._query_status()
        finally:
            RUNTIME_ACCESS_LOCK.release()

    @staticmethod
    def _query_status():
        """汇总运行时状态与当前程序身份；任何一项查询失败都不 500，如实报字段。"""
        info = {"serve": "ok",
                "runtime": {"url": os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080"),
                            "status": "UNREACHABLE"},
                "prog_id": None, "program": None}
        info["capabilities"] = ["deploy", "start", "stop", "logs"]
        info["deployment"] = LAST_DEPLOYMENT
        info["modbus"] = {"host":os.environ.get("MODBUS_HOST","127.0.0.1"),
                          "port":int(os.environ.get("MODBUS_PORT","502"))}
        try:
            client = runtime_client()
            try:
                info["runtime"]["status"] = client.status()
            finally:
                client.session.close()
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
        elif self.path == "/runtime/logs":
            if OPERATION_LOCK.locked() or not RUNTIME_ACCESS_LOCK.acquire(blocking=False):
                self._json(409, {"status":"BUSY", "errors":["Runtime 正在执行其他操作，请稍后读取日志"]})
                return
            try:
                client = runtime_client()
                try:
                    self._json(200, {"status": "OK", "logs": client.runtime_logs()[-16000:]})
                finally:
                    client.session.close()
            except Exception as exc:
                self._json(502, {"status": "FAILED", "errors": [str(exc)]})
            finally:
                RUNTIME_ACCESS_LOCK.release()
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        # 启停请求也可能带 JSON 请求体。未读取就关闭连接会让 Windows 发送 RST，
        # 出现“服务已返回200，但调用方收到10053”的间歇性错误。
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._json(400, {"errors": ["无效的 Content-Length"]})
            return
        if not 0 <= length <= 8 * 1024 * 1024:
            self._json(413, {"errors": ["请求超过 8 MiB"]})
            return
        self.request_body = self.rfile.read(length)
        # 浏览器通过工作台带 token 的接口访问；本地 CLI 仍可直接使用部署服务。
        if self.headers.get("Origin") or self.headers.get("Sec-Fetch-Site") == "cross-site":
            self._json(403, {"errors": ["请通过本地工作台操作"]})
            return
        if self.path not in ("/deploy", "/runtime/start", "/runtime/stop"):
            self._json(404, {"error": "not found"})
            return
        if not OPERATION_LOCK.acquire(blocking=False):
            self._json(409, {"status": "BUSY", "errors": ["已有部署或启停操作正在执行"]})
            return
        try:
            # 状态查询先结束即可，不能把正常的读请求误报为另一次部署。
            with RUNTIME_ACCESS_LOCK:
                if self.path == "/deploy":
                    self._deploy()
                else:
                    client = runtime_client()
                    try:
                        action = self.path.rsplit("/", 1)[-1]
                        status = getattr(client, action)()
                        expected = "RUNNING" if action == "start" else "STOPPED"
                        self._json(200 if status == expected else 502,
                                   {"status": "OK" if status == expected else "FAILED",
                                    "runtime": status,
                                    "errors": [] if status == expected else ["Runtime 状态未达到 " + expected]})
                    finally:
                        client.session.close()
        except Exception as exc:
            self._json(502, {"status": "FAILED", "errors": [str(exc)]})
        finally:
            OPERATION_LOCK.release()

    def _deploy(self):
        global LAST_DEPLOYMENT
        xml_bytes = self.request_body
        if not xml_bytes:
            self._json(413, {"status": "REJECTED", "errors": ["XML 为空或超过 8 MiB"]})
            return
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
            # 同一工作区结果文件仅由锁内的本次子进程产生，不能回传上轮成功结果。
            result_path = REPO_ROOT / "workspace" / "deploy_result.json"
            result_path.unlink(missing_ok=True)
            LAST_DEPLOYMENT = None
            proc = subprocess.run(
                [sys.executable, str(RUN_DEPLOY), "--xml", str(tmp)],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=900)
            # 响应到达时本次上传临时件已经回收，避免调用方看见半完成的清理状态。
            tmp.unlink(missing_ok=True)
            if result_path.exists():
                result = json.loads(result_path.read_text(encoding="utf-8"))
                if proc.returncode == 0 and result.get("status") == "OK":
                    LAST_DEPLOYMENT = {"sha256": hashlib.sha256(xml_bytes).hexdigest()}
                result["deployment"] = LAST_DEPLOYMENT
                self._json(200 if proc.returncode == 0 else 500,
                           result)
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
    print("[deploy-server] http://127.0.0.1:%d: /deploy | /health | /status | /runtime/start | /runtime/stop | /runtime/logs" % args.port)
    server.serve_forever()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    sys.exit(main())
