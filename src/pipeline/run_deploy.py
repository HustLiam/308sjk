#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
部署编排器（OpenPLC 版）：XML → .st → 上传编译 → 启动。

链路：
    src/plc/counter.xml (61131-10)
        │ xml2st 校验+转换
        ▼
    workspace/program.st
        │ openplc_client (HTTP)
        ▼
    OpenPLC v3 运行时（Docker/WSL2/远程 Linux）
        │ 编译失败 → 错误日志回喂 agent ↩
        ▼
    start_plc → Modbus TCP :502 → Isaac Sim 桥接 / verify_modbus.py

用法:
    python src/pipeline/run_deploy.py [--xml path.xml] [--url http://127.0.0.1:8080]
结果写入 workspace/deploy_result.json（status/steps/errors，供 agent 回喂）。
"""

import argparse
import json
import os
import sys
from pathlib import Path

import requests

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from xml2st import convert                      # noqa: E402
from openplc_client import OpenPLCClient, OpenPLCError  # noqa: E402

WORKSPACE = REPO_ROOT / "workspace"
RESULT_JSON = WORKSPACE / "deploy_result.json"


def main():
    parser = argparse.ArgumentParser(description="Deploy PLCopen XML to OpenPLC runtime")
    parser.add_argument("--xml", default=str(REPO_ROOT / "src" / "plc" / "motion3axis.xml"))
    parser.add_argument("--url", default=os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080"))
    parser.add_argument("--name", default="agent_program")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    steps, errors = [], []

    def step(name, ok, detail=""):
        steps.append({"step": name, "ok": bool(ok), "detail": str(detail)})
        print("  [%s] %s%s" % ("PASS" if ok else "FAIL", name,
                               ("  -- " + str(detail)) if detail else ""))

    def finish(status, code):
        WORKSPACE.mkdir(exist_ok=True)
        RESULT_JSON.write_text(
            json.dumps({"status": status, "steps": steps, "errors": errors},
                       ensure_ascii=False, indent=2), encoding="utf-8")
        print("\n[deploy] RESULT: %s" % status)
        return code

    # ① 校验 + 转换
    xml_path = Path(args.xml)
    if not xml_path.exists():
        errors.append("XML 不存在: %s" % xml_path)
        return finish("FAILED", 1)
    ok, st_text, problems = convert(xml_path)
    if not ok:
        errors.extend(problems)
        step("validate & convert XML", False, "%d 个问题" % len(problems))
        return finish("FAILED", 1)
    step("validate & convert XML", True, "%s -> %d 行 ST" % (xml_path.name, st_text.count("\n")))

    st_path = WORKSPACE / "program.st"
    WORKSPACE.mkdir(exist_ok=True)
    st_path.write_text(st_text, encoding="utf-8")
    step("write program.st", True, str(st_path))

    # ② 上传 + 编译 + 启动
    try:
        client = OpenPLCClient(base_url=args.url)
        client.login()
        step("login runtime", True, args.url)

        st_file = client.upload_and_compile(st_text, args.name)
        step("upload & trigger compile", True, st_file)

        comp_ok, comp_log = client.wait_compilation()
        if not comp_ok:
            # 编译日志直接作为 agent 的纠错输入
            tail = "\n".join(comp_log.splitlines()[-40:])
            errors.append("matiec 编译失败:\n%s" % tail)
            step("compile (matiec)", False, "见 errors 中的编译日志")
            return finish("FAILED", 1)
        step("compile (matiec)", True)

        status = client.start()
        if status != "RUNNING":
            errors.append("start 后状态异常: %s" % status)
            step("start PLC", False, status)
            return finish("FAILED", 1)
        step("start PLC", True, "Modbus TCP :502 已随运行时开启")
    except OpenPLCError as e:
        errors.append(str(e))
        step("runtime interaction", False, str(e))
        return finish("FAILED", 1)
    except requests.exceptions.ConnectionError as e:
        msg = ("无法连接 OpenPLC 运行时 %s —— 是否已启动？"
               "（Docker: docker run -d -p 8080:8080 -p 502:502 openplc/openplc-v3，"
               "见 docs/部署手册-OpenPLC.md §1）" % args.url)
        errors.append(msg + " / " + str(e.__cause__ or e))
        step("connect runtime", False, msg)
        return finish("FAILED", 1)

    return finish("OK", 0)


if __name__ == "__main__":
    sys.exit(main())
