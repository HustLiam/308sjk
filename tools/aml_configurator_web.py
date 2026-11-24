#!/usr/bin/env python
"""标准 AML 配置器 · Web 前端服务（端口 8766，独立于 gc 的 8765 工作台）。

交互流（负责人要求的产品形态）：
  ① 上传任意子集文件（.aml 基座 或 tree/geometry/poses/joints.json + .stl/.dae/.obj
     + io.json + 可选 adapter/motion/requirements.json）
  ② POST /api/analyze  → L1 确定性快查（硬错误红条）+ L2 出题（真实 LLM，降级 Mock）
  ③ 前端问答卡片逐题作答 → POST /api/assemble（answers）
     → supplement 应用 + L3 固定组装器 → verify 摘要 + 产物下载

会话：上传时生成 session id（workspace/aml_configurator/web/<id>/upload/），
三步全进程内调用 tools/aml_agent_configurator / aml_configurator 的函数（无子进程）。
启动：python tools/aml_configurator_web.py [--port 8766] [--mock]
"""

import argparse
import json
import os
import re
import shutil
import sys
import tempfile
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, REPO)

import aml_configurator as cfg            # noqa: E402
import aml_agent_configurator as agent    # noqa: E402
import aml_parts                           # noqa: E402

UI_DIR = os.path.join(HERE, "aml_configurator_ui")
SESSIONS = os.path.join(REPO, "workspace", "aml_configurator", "web")
XSD = os.path.join(REPO, "AML_RobotCell_en_public", "AML_RobotCell_en",
                   "CAEX_ClassModel_V2.15.xsd")
KNOWN_JSON = {"io.json": "io", "tree.json": "tree", "geometry.json": "geometry",
              "poses.json": "poses", "joints.json": "joints",
              "adapter.json": "adapter", "motion.json": "motion",
              "requirements.json": "requirements"}
GEOM_EXT = (".stl", ".dae", ".obj")
FORCE_MOCK = False


class SessionError(ValueError):
    pass


# ---------------------------------------------------------------- 会话与分类

def session_dir(sid):
    d = os.path.join(SESSIONS, sid)
    if not re.fullmatch(r"[0-9a-f]{12}", sid or "") or not os.path.isdir(d):
        raise SessionError("会话不存在或已过期")
    return d


def classify(files):
    """上传文件 → 分类清单。files: {filename: abs_path}。"""
    out = {"base_aml": None, "parts": {}, "io": None, "optional": {}, "geometry": [], "unknown": []}
    for name in sorted(files):
        base, ext = os.path.splitext(os.path.basename(name))
        low = base.lower()
        if ext.lower() == ".aml":
            out["base_aml"] = files[name]
        elif ext.lower() in GEOM_EXT:
            out["geometry"].append(os.path.basename(name))
            out["parts"].setdefault("__geom_files__", []).append(files[name])
        elif ext.lower() == ".json" or low in KNOWN_JSON:
            key = KNOWN_JSON.get(low)
            try:
                data = json.load(open(files[name], encoding="utf-8"))
            except Exception:                              # noqa: BLE001
                out["unknown"].append(name)
                continue
            key = KNOWN_JSON.get(low + ".json", key) if low + ".json" in KNOWN_JSON else key
            if "io_list" in data:
                key, target = "io", files[name]
            elif key in ("tree", "geometry", "poses", "joints", "adapter", "motion", "requirements"):
                target = files[name]
            else:
                out["unknown"].append(name)
                continue
            if key == "io":
                out["io"] = target
            elif key in ("adapter", "motion", "requirements"):
                out["optional"][key] = target
            else:
                out["parts"][key] = target
        else:
            out["unknown"].append(name)
    return out


def prepare_parts(cls, updir):
    """把上传内容整理成 parts 目录形态（tree/geometry/poses/joints 齐或不齐均可）。"""
    pd = os.path.join(updir, "_parts")
    os.makedirs(pd, exist_ok=True)
    for key in ("tree", "geometry", "poses", "joints"):
        if cls["parts"].get(key):
            shutil.copy(cls["parts"][key], os.path.join(pd, key + ".json"))
    # 未给 geometry.json 但有散几何文件 → 生成登记（unit 未知，由问答补）
    if "geometry" not in cls["parts"] and cls["parts"].get("__geom_files__"):
        geo = {os.path.splitext(os.path.basename(p))[0]: {"file": os.path.basename(p), "unit": "m"}
               for p in cls["parts"]["__geom_files__"]}
        for p in cls["parts"]["__geom_files__"]:
            shutil.copy(p, os.path.join(pd, os.path.basename(p)))
        json.dump(geo, open(os.path.join(pd, "geometry.json"), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    elif cls["parts"].get("__geom_files__"):
        for p in cls["parts"]["__geom_files__"]:
            dst = os.path.join(pd, os.path.basename(p))
            if not os.path.isfile(dst):
                shutil.copy(p, dst)
    return pd


# ---------------------------------------------------------------- analyze / assemble

def api_analyze(sid):
    d = session_dir(sid)
    updir = os.path.join(d, "upload")
    files = {f: os.path.join(updir, f) for f in os.listdir(updir)
             if os.path.isfile(os.path.join(updir, f))}
    cls = classify(files)
    errors = []
    if not cls["io"]:
        errors.append("缺 io.json（IO 是设备事实，无法从几何推断——必须上传）")
    if not cls["base_aml"] and not (cls["parts"].get("tree") or cls["geometry"] or cls["parts"].get("__geom_files__")):
        errors.append("缺设备来源：需上传基座 AML，或散装文件（tree.json / STL 等几何 / io.json）")
    if errors:
        return {"ok": False, "errors": errors, "classification": _cls_view(cls)}

    inputs = {"io": cls["io"], "aml": cls["base_aml"],
              "parts_dir": None if cls["base_aml"] else prepare_parts(cls, updir),
              "adapter": cls["optional"].get("adapter"),
              "motion": cls["optional"].get("motion"),
              "requirements": cls["optional"].get("requirements")}
    json.dump(inputs, open(os.path.join(d, "inputs.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    parts = aml_parts.load_parts(inputs["parts_dir"]) if inputs["parts_dir"] else \
        {"tree": [], "geometry": {}, "poses": {}, "joints": []}
    l1_errors, findings = agent._quick_check(inputs, parts)
    if l1_errors:
        return {"ok": False, "errors": l1_errors, "classification": _cls_view(cls)}

    summary = {"aml": inputs["aml"], "parts_dir": bool(inputs["parts_dir"]),
               "tree_nodes": len(parts["tree"]), "joints": len(parts["joints"]),
               "io_count": len(json.load(open(inputs["io"], encoding="utf-8"))["io_list"]),
               "geometry_files": cls["geometry"],
               "optional_given": {k: bool(inputs[k]) for k in ("adapter", "motion", "requirements")}}
    questions, used_llm = [], False
    if not FORCE_MOCK:
        try:
            questions = agent._llm_questions(summary, findings)
            used_llm = True
        except Exception as e:                              # noqa: BLE001
            summary["_llm_error"] = f"{type(e).__name__}: {str(e)[:120]}"
    if not used_llm:
        questions = agent._mock_questions(findings)
    return {"ok": True, "classification": _cls_view(cls), "summary": summary,
            "findings": findings, "questions": questions, "llm_used": used_llm}


def api_assemble(sid, answers):
    d = session_dir(sid)
    inputs = json.load(open(os.path.join(d, "inputs.json"), encoding="utf-8"))
    work = os.path.join(d, "work")
    os.makedirs(work, exist_ok=True)
    if inputs["parts_dir"]:                     # 工作副本（整目录，含几何）
        src = inputs["parts_dir"]
        for f in os.listdir(src):
            if os.path.isfile(os.path.join(src, f)):
                shutil.copy(os.path.join(src, f), os.path.join(work, f))
    io_work, applied = agent._apply_supplement(work, answers, inputs["io"])
    json.dump({"answers": answers, "applied": applied},
              open(os.path.join(d, "supplement.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    # L3：进程内组装（audit→normalize→assemble→verify）
    io_items = json.load(open(io_work, encoding="utf-8"))["io_list"]
    load = lambda p: json.load(open(p, encoding="utf-8")) if p else None    # noqa: E731
    adapter, motion, requirements = (load(inputs["adapter"]), load(inputs["motion"]),
                                     load(inputs["requirements"]))
    if inputs["parts_dir"]:
        parts = aml_parts.load_parts(work)
        assets = os.path.join(d, "assets")
        dae_refs = aml_parts.convert_geometries(parts, work, assets)
        base = aml_parts.build_base(parts, dae_refs)
        aml_for_audit = work
    else:
        base = cfg.ET.parse(inputs["aml"])
        root_el = base.getroot()
        if root_el.get("SchemaVersion") != "2.15":
            return {"ok": False, "errors": ["基座 SchemaVersion 非 2.15"]}
        aml_for_audit = inputs["aml"]

    root = base.getroot() if hasattr(base, "getroot") else base
    errors, warnings = cfg.audit(root, io_items, adapter, motion, requirements, aml_for_audit)
    if errors:
        return {"ok": False, "stage": "audit", "errors": errors, "warnings": warnings}
    io2, adapter2, motion2, req2, notes = cfg.normalize(io_items, adapter, motion, requirements)
    stats = cfg.assemble(base, io2, adapter2, motion2, req2,
                         {"aml": inputs["parts_dir"] or inputs["aml"], "io": io_work,
                          "adapter": inputs["adapter"], "motion": inputs["motion"],
                          "requirements": inputs["requirements"]})
    out = os.path.join(d, "standard.aml")
    tree_out = base if hasattr(base, "write") else cfg.ET.ElementTree(base)
    cfg.ET.indent(tree_out, space="\t")
    tree_out.write(out, encoding="utf-8", xml_declaration=True)
    v = cfg.verify(out, io2, xsd_path=XSD)
    report = {"assembly": stats, "normalized": notes, "audit_warnings": warnings, "verify": v}
    ok = bool(v.get("well_formed") and v.get("roundtrip", {}).get("ok")
              and (v.get("xsd") is None or v["xsd"]["ok"]))
    return {"ok": ok, "report": report, "download": f"/api/download?sid={sid}",
            "output_file": "standard.aml"}


def _cls_view(cls):
    return {"base_aml": bool(cls["base_aml"]),
            "parts_files": [k for k in cls["parts"] if not k.startswith("__")],
            "geometry": cls["geometry"], "io": bool(cls["io"]),
            "optional": sorted(cls["optional"]), "unknown": cls["unknown"]}


# ---------------------------------------------------------------- multipart 解析

def parse_multipart(body, boundary):
    """无依赖 multipart/form-data 解析 → {filename: bytes}。"""
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


# ---------------------------------------------------------------- HTTP

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
            elif p == "/api/download":
                from urllib.parse import parse_qs
                q = parse_qs(urlsplit(self.path).query)
                d = session_dir(q.get("sid", [""])[0])
                self._send(200, open(os.path.join(d, "standard.aml"), "rb").read(),
                           "application/xml; charset=utf-8", attachment="standard.aml")
            else:
                self._send(404, {"error": "not found"})
        except Exception as e:                              # noqa: BLE001
            self._send(400, {"error": str(e)})

    def do_POST(self):
        try:
            p = urlsplit(self.path).path
            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length)
            if p == "/api/upload":
                m = re.search(r'boundary=(\S+)', self.headers.get("Content-Type", ""))
                if not m:
                    raise SessionError("需 multipart/form-data")
                files = parse_multipart(body, m.group(1).encode())
                if not files:
                    raise SessionError("未收到文件")
                sid = uuid.uuid4().hex[:12]
                updir = os.path.join(SESSIONS, sid, "upload")
                os.makedirs(updir, exist_ok=True)
                for name, data in files.items():
                    safe = os.path.basename(name)
                    open(os.path.join(updir, safe), "wb").write(data)
                self._send(200, {"sid": sid, "files": sorted(files)})
            elif p == "/api/analyze":
                data = json.loads(body or b"{}")
                self._send(200, api_analyze(data.get("sid", "")))
            elif p == "/api/assemble":
                data = json.loads(body or b"{}")
                self._send(200, api_assemble(data.get("sid", ""), data.get("answers", {})))
            else:
                self._send(404, {"error": "not found"})
        except SessionError as e:
            self._send(400, {"error": str(e)})
        except Exception as e:                              # noqa: BLE001
            self._send(500, {"error": f"{type(e).__name__}: {e}"})


def main(argv=None):
    global FORCE_MOCK
    p = argparse.ArgumentParser(description="标准 AML 配置器 Web 前端")
    p.add_argument("--port", type=int, default=8766)
    p.add_argument("--mock", action="store_true", help="强制 Mock 出题（不走 LLM）")
    args = p.parse_args(argv)
    FORCE_MOCK = args.mock
    os.makedirs(SESSIONS, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    srv.daemon_threads = True
    print(f"标准 AML 配置器: http://127.0.0.1:{args.port}  "
          f"(LLM={'Mock' if args.mock else 'deepseek 实时'})")
    srv.serve_forever()


if __name__ == "__main__":
    sys.exit(main())
