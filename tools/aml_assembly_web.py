#!/usr/bin/env python
"""标准 AML 装配与 IO 绑定 Web 服务（:8767）。

消费"含 IO 的标准 AML"（308sjk.standard_aml/1）：上传（或一键示例）→ 解析装配 →
MuJoCo scene.xml + 组件↔仿真节点映射 → IO 绑定（信号自带 binding）→ 固定规则
PLCopen XML/ST + plc_test_contract → 交互闭环（发目标→伺服→读反馈→扫描确认）。

API：
  POST /api/upload   multipart：standard.aml + assets/*.dae（几何/运动学资源）
  POST /api/demo     一键示例（仓库六轴夹具现场组装标准 AML 并装配）
  GET  /api/model    装配模型（组件树/关节/信号/接线/待确认 + 渲染网格）
  POST /api/step     {sid, targets:{信号:raw}, steps} → 适配器步进状态
  POST /api/test     IO 验收（按 SimulationRequirements 容差裁定）
  GET  /api/download scene.xml / mapping.json / io_test.xml / io_test.st /
                     plc_test_contract.json / modbus.json / report.json
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

import aml_assembly as asm                                    # noqa: E402

PORT = 8767
UI_DIR = os.path.join(HERE, "aml_assembly_ui")
WORK_ROOT = os.path.join(REPO, "workspace", "aml_assembly")
SESSIONS = {}


def session_dir(sid):
    d = os.path.join(WORK_ROOT, sid)
    if not os.path.isdir(d):
        raise FileNotFoundError(sid)
    return d


def parse_multipart(body, boundary):
    files = {}
    delim = b"--" + boundary
    for part in body.split(delim):
        part = part.strip(b"\r\n")
        if not part or part == b"--":
            continue
        header, _, payload = part.partition(b"\r\n\r\n")
        m = re.search(rb'filename="([^"]+)"', header)
        if m and m.group(1):
            files[m.group(1).decode("utf-8")] = payload
    return files


def _load_pipeline(aml_path, out_dir):
    """full_run + 建 IOSession，入 SESSIONS。返回前端摘要 dict。"""
    r = asm.full_run(aml_path, out_dir)
    sid = os.path.basename(out_dir)
    ios = None
    if r["scene_stats"].get("compile"):
        ios = asm.IOSession(r["scene_stats"]["scene"], r["model"]["signals"],
                            r["model"]["joints"], r["model"]["requirements"])
    SESSIONS[sid] = {"model": r["model"], "meshes": r["meshes"],
                     "stats": r["scene_stats"], "session": ios,
                     "st_generated": r["st_generated"],
                     "st_problems": r["st_problems"],
                     "acceptance": r["acceptance"], "out_dir": out_dir,
                     "aml_path": aml_path}
    s, m = r["scene_stats"], r["model"]
    return {"ok": bool(s.get("compile")), "sid": sid,
            "summary": {"components": len(m["components"]),
                        "joints": len(m["joints"]), "signals": len(m["signals"]),
                        "links": len(m["links"]), "pending": len(m["pending"]),
                        "compile": s.get("compile"),
                        "nbody": s.get("nbody"), "njnt": s.get("njnt"),
                        "nu": s.get("nu"), "nsensor": s.get("nsensor"),
                        "st_generated": r["st_generated"],
                        "acceptance_ok": (r["acceptance"] or {}).get("ok")},
            "errors": [] if s.get("compile") else [s.get("error", "编译失败")]}


def api_upload(files):
    aml = next((v for k, v in files.items() if k.lower().endswith(".aml")), None)
    if not aml:
        return {"ok": False, "errors": ["缺少标准 AML 文件（.aml）"]}
    sid = uuid.uuid4().hex[:12]
    d = os.path.join(WORK_ROOT, sid)
    os.makedirs(os.path.join(d, "assets"), exist_ok=True)
    open(os.path.join(d, "standard.aml"), "wb").write(aml)
    for k, v in files.items():
        if k.lower().endswith((".dae", ".obj", ".stl")):
            open(os.path.join(d, "assets", os.path.basename(k)), "wb").write(v)
    return _load_pipeline(os.path.join(d, "standard.aml"), d)


def api_demo():
    """一键示例：仓库六轴夹具 → 配置器现场组装标准 AML → 装配。"""
    sid = "demo_" + uuid.uuid4().hex[:8]
    d = os.path.join(WORK_ROOT, sid)
    parts = os.path.join(REPO, "tools", "tests", "fixtures", "parts")
    r = subprocess.run(
        [sys.executable, os.path.join(HERE, "aml_configurator.py"),
         "--parts-dir", parts, "--io", os.path.join(parts, "io.json"),
         "-o", os.path.join(d, "standard.aml"),
         "--xsd", os.path.join(REPO, "AML_RobotCell_en_public", "AML_RobotCell_en",
                               "CAEX_ClassModel_V2.15.xsd")],
        capture_output=True, text=True)
    if r.returncode != 0:
        return {"ok": False, "errors": ["示例组装失败: " + r.stdout[-300:]]}
    return _load_pipeline(os.path.join(d, "standard.aml"), d)


def api_model(sid):
    st = SESSIONS.get(sid) or session_state_reload(sid)
    if not st:
        return {"ok": False, "errors": ["会话不存在"]}
    return {"ok": True, "model": st["model"], "meshes": st["meshes"],
            "stats": st["stats"], "st_generated": st["st_generated"],
            "acceptance": st["acceptance"]}


def session_state_reload(sid):
    """进程重启后按磁盘产物重建（不含交互会话）。"""
    d = session_dir(sid)
    aml = os.path.join(d, "standard.aml")
    if not os.path.isfile(aml):
        return None
    try:
        return _load_pipeline(aml, d)
    except Exception as e:                                      # noqa: BLE001
        return None


def api_step(sid, payload):
    st = SESSIONS.get(sid)
    if not st or not st["session"]:
        return {"ok": False, "errors": ["会话不存在或模型未编译"]}
    s = st["session"]
    for name, raw in (payload.get("targets") or {}).items():
        try:
            s.set_target(name, int(raw))
        except StopIteration:
            return {"ok": False, "errors": [f"未知信号 {name}"]}
    state = s.step(int(payload.get("steps", 1)))
    return {"ok": True, "state": state}


def api_test(sid):
    st = SESSIONS.get(sid)
    if not st or not st["session"]:
        return {"ok": False, "errors": ["会话不存在或模型未编译"]}
    s = asm.IOSession(st["stats"]["scene"], st["model"]["signals"],
                      st["model"]["joints"], st["model"]["requirements"])
    rep = asm.run_acceptance(s, os.path.join(st["out_dir"], "report.json"))
    st["acceptance"] = rep
    return {"ok": True, "acceptance": rep}


DOWNLOADS = ("scene.xml", "mapping.json", "io_test.xml", "io_test.st",
             "plc_test_contract.json", "modbus.json", "report.json", "standard.aml")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def _send(self, code, data, ctype="application/json; charset=utf-8", attachment=None):
        body = data if isinstance(data, bytes) else json.dumps(
            data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        try:
            p = urlsplit(self.path).path
            if p in ("/", "/index.html", "/app.js", "/style.css"):
                name = "index.html" if p in ("/", "/index.html") else p[1:]
                self._send(200, open(os.path.join(UI_DIR, name), "rb").read(),
                           {"index.html": "text/html", "app.js": "text/javascript",
                            "style.css": "text/css"}[name] + "; charset=utf-8")
            elif p == "/api/model":
                q = parse_qs(urlsplit(self.path).query)
                self._send(200, api_model(q.get("sid", [""])[0]))
            elif p == "/api/download":
                q = parse_qs(urlsplit(self.path).query)
                sid, name = q.get("sid", [""])[0], q.get("name", [""])[0]
                if name not in DOWNLOADS:
                    self._send(400, {"error": f"name 须为 {DOWNLOADS} 之一"})
                    return
                path = os.path.join(session_dir(sid), name)
                self._send(200, open(path, "rb").read(),
                           "application/octet-stream", attachment=name)
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:                                  # noqa: BLE001
            self._send(400, {"error": str(e)})

    def do_POST(self):
        try:
            p = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            if p == "/api/upload":
                m = re.search(r'boundary=(\S+)', self.headers.get("Content-Type", ""))
                self._send(200, api_upload(parse_multipart(body, m.group(1).encode())))
            elif p == "/api/demo":
                self._send(200, api_demo())
            elif p == "/api/step":
                q = parse_qs(urlsplit(self.path).query)
                self._send(200, api_step(q.get("sid", [""])[0], json.loads(body)))
            elif p == "/api/test":
                q = parse_qs(urlsplit(self.path).query)
                self._send(200, api_test(q.get("sid", [""])[0]))
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:                                  # noqa: BLE001
            self._send(400, {"error": str(e)})


def main():
    os.makedirs(WORK_ROOT, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"标准 AML 装配与 IO 绑定 → http://127.0.0.1:{PORT}")
    srv.serve_forever()


if __name__ == "__main__":
    main()
