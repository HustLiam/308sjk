#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
停机工具：停止 OpenPLC 运行时的逻辑扫描（GET /stop_plc）。

停止的是程序扫描线程：定时器冻结、逻辑停跑；Modbus(:502)/Web(:8080) 服务保持，
%Q 缓冲保持最后写入状态仍可读。重新启动用 run_deploy（或 web 的 start）。

用法:
    python src/pipeline/stop_plc.py [--url http://127.0.0.1:8080]
退出码 0 = 已确认 STOPPED。
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from openplc_client import OpenPLCClient, OpenPLCError  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description="Stop OpenPLC runtime logic scan")
    parser.add_argument("--url", default=os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080"))
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    try:
        client = OpenPLCClient(base_url=args.url)
        client.login()
        status = client.stop()
    except OpenPLCError as e:
        print("[stop] 失败: %s" % e)
        return 1

    print("[stop] 运行时状态: %s（Modbus/Web 服务保持，%%Q 缓冲保持最后状态）" % status)
    return 0 if status == "STOPPED" else 1


if __name__ == "__main__":
    sys.exit(main())
