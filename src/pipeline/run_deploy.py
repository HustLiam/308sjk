#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
XML → CODESYS 部署编排器（闭环的第①段：把 PLCopen XML 部署到软 PLC 并运行）。

职责：
  1. 定位 CODESYS.exe（环境变量 CODESYS_EXE 优先，否则扫描 Program Files）
  2. 无头拉起 CODESYS 执行 src/codesys/deploy_project.py（--noUI --runscript）
  3. 透传子进程日志，解析 deploy_result.json 汇总各步骤成败
  4. 以 0/1 退出码把结果交给上层（未来 agent 的迭代编排循环）

用法:
    python src/pipeline/run_deploy.py [--xml path/to/plcopen.xml] [--keep-result]
"""

import argparse
import glob
import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_XML = REPO_ROOT / "src" / "plc" / "counter.xml"
CODESYS_SCRIPT = REPO_ROOT / "src" / "codesys" / "deploy_project.py"
WORKSPACE = REPO_ROOT / "workspace"
DEFAULT_PROJECT = WORKSPACE / "counter.project"
RESULT_JSON = WORKSPACE / "deploy_result.json"

CODESYS_GLOBS = [
    # 旧版布局（SP17 及以前常见）
    r"C:\Program Files\CODESYS*\CODESYS\CODESYS.exe",
    r"C:\Program Files (x86)\CODESYS*\CODESYS\CODESYS.exe",
    # 新版布局（SP22 起 exe 位于 CODESYS\Common\）
    r"C:\Program Files\CODESYS*\CODESYS\Common\CODESYS.exe",
    r"D:\CODESYS*\CODESYS\CODESYS.exe",
    r"D:\CODESYS*\CODESYS\Common\CODESYS.exe",
]
TIMEOUT_SECONDS = 600  # 无头导入+编译+下载的正常耗时在 1~2 分钟，留足余量


def find_codesys_exe(cli_override=None):
    """返回 CODESYS.exe 路径；优先级: --codesys 参数 > CODESYS_EXE 环境变量 > 常见位置扫描。"""
    for candidate in (cli_override, os.environ.get("CODESYS_EXE")):
        if candidate:
            if Path(candidate).exists():
                return candidate
            return None  # 显式指定但不存在，直接失败避免误扫到别的版本
    found = []
    for pattern in CODESYS_GLOBS:
        found.extend(glob.glob(pattern))
    if not found:
        return None
    found.sort()  # 目录名含版本号，字典序最新者最后
    return found[-1]


def detect_profile(codesys_exe):
    """--noUI 模式必须带 --profile。从 <安装目录>\\CODESYS\\Profiles\\*.profile.xml 探测名称。"""
    override = os.environ.get("CODESYS_PROFILE")
    if override:
        return override
    # exe 位于 ...\CODESYS\Common\CODESYS.exe（新）或 ...\CODESYS\CODESYS.exe（旧）
    codesys_dir = Path(codesys_exe).resolve().parent
    if codesys_dir.name.lower() == "common":
        codesys_dir = codesys_dir.parent
    profiles_dir = codesys_dir / "Profiles"
    if profiles_dir.is_dir():
        for p in sorted(profiles_dir.glob("*.profile.xml")):
            return p.name[: -len(".profile.xml")]
    return None


def main():
    parser = argparse.ArgumentParser(description="Deploy PLCopen XML to CODESYS Control Win V3")
    parser.add_argument("--xml", default=str(DEFAULT_XML), help="待部署的 PLCopen XML 路径")
    parser.add_argument("--codesys", default=None, help="CODESYS.exe 完整路径（默认自动扫描）")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    xml_path = Path(args.xml).resolve()
    if not xml_path.exists():
        print("[orchestrator] 找不到 PLCopen XML: %s" % xml_path)
        return 1

    codesys_exe = find_codesys_exe(args.codesys)
    if not codesys_exe:
        print("[orchestrator] 未找到 CODESYS.exe。")
        print("  1) 若尚未安装：见 docs/部署手册.md 第 1 节（CODESYS Development System + Control Win V3）")
        print("  2) 若已安装在非默认位置：python src\\pipeline\\run_deploy.py --codesys D:\\安装目录\\CODESYS.exe")
        return 1

    WORKSPACE.mkdir(exist_ok=True)
    if RESULT_JSON.exists():
        RESULT_JSON.unlink()

    env = dict(os.environ)
    env["PLCOPEN_XML"] = str(xml_path)
    env["CODESYS_PROJECT"] = str(DEFAULT_PROJECT)
    env["DEPLOY_RESULT"] = str(RESULT_JSON)

    profile = detect_profile(codesys_exe)
    # 注意：CODESYS 解析原始命令行，要求引号紧贴等号（--profile="name"）。
    # Python 列表传参会对含空格的参数整体加引号（"--profile=name"），CODESYS 不识别，
    # 因此这里手工构造原始命令行字符串（Windows 下字符串直接作为 lpCommandLine）。
    cmd = '"%s"' % codesys_exe
    if profile:
        cmd += ' --profile="%s"' % profile
    cmd += ' --noUI --runscript="%s"' % CODESYS_SCRIPT
    print("[orchestrator] 启动无头 CODESYS 部署")
    print("[orchestrator]   CODESYS : %s" % codesys_exe)
    print("[orchestrator]   PROFILE: %s" % (profile or "(未探测到——若失败请设 CODESYS_PROFILE)"))
    print("[orchestrator]   XML    : %s" % xml_path)
    print("[orchestrator]   PROJECT: %s" % DEFAULT_PROJECT)
    print("-" * 60)

    try:
        proc = subprocess.run(cmd, env=env, timeout=TIMEOUT_SECONDS,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            output = proc.stdout.decode("utf-8")
        except UnicodeDecodeError:
            output = proc.stdout.decode("gbk", errors="replace")
        print(output or "(CODESYS 无标准输出)")
    except subprocess.TimeoutExpired:
        print("[orchestrator] 部署超时（>%d 秒），详见 CODESYS 日志" % TIMEOUT_SECONDS)
        return 1
    except OSError as e:
        print("[orchestrator] 无法启动 CODESYS: %s" % e)
        return 1

    print("-" * 60)
    if not RESULT_JSON.exists():
        print("[orchestrator] 未生成 deploy_result.json——脚本可能在早期环境检查处失败，")
        print("               请检查上方日志与《部署手册》故障排查一节。")
        return 1

    result = json.loads(RESULT_JSON.read_text(encoding="utf-8"))
    for s in result.get("steps", []):
        mark = "PASS" if s.get("ok") else "FAIL"
        detail = ("  -- " + s["detail"]) if s.get("detail") else ""
        print("  [%s] %s%s" % (mark, s.get("step"), detail))
    for err in result.get("errors", []):
        print("  [ERROR] %s" % err.splitlines()[0])

    status = result.get("status")
    if status == "OK":
        print("\n[orchestrator] 部署成功，PLC 已在运行。")
        print("               用 UaExpert 连接 opc.tcp://localhost:4840 观察 PLC_PRG.cnt 每秒 +1。")
        return 0
    print("\n[orchestrator] 部署失败（status=%s），按上面 FAIL/ERROR 行排查，手册含对应处理办法。" % status)
    return 1


if __name__ == "__main__":
    sys.exit(main())
