#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键回归（主方案 §8.4 CI 门禁的落地实体）。

三层按序执行，便宜层先拦截（协作指南 §3.2 分层测试策略——能在便宜层
拦截的问题绝不到贵层，前置层失败则不再跑后续层）：

    L1 静态校验   src/plc/*.xml 逐个过 xml2st 契约校验（无需运行时，<1s）
    L2 单元测试   python -m pytest tests/（无需运行时，<3s）
    L3 在线验收   逐场景 run_deploy 部署 + scenario_<场景>.py 验收（需运行时，分钟级）
                  运行时不可达时整层 SKIP；--require-online 把 SKIP 视为失败

场景发现：src/plc/<name>.xml × src/pipeline/scenario_<name>.py 配对；
缺验收脚本的 XML 在 L3 记 SKIP 并提示补脚本。

用法：
    python src/pipeline/run_regression.py [--skip-online] [--require-online] [--url ...]

结果写 workspace/regression_result.json（供 CI 消费）；退出码 0=全绿 / 1=有失败。
"""

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from xml2st import convert  # noqa: E402

WORKSPACE = REPO_ROOT / "workspace"
RESULT_JSON = WORKSPACE / "regression_result.json"
PIPELINE = Path(__file__).resolve().parent


def run_static(xml_files):
    """L1：全部场景 XML 过生成契约校验（位宽/结构/prog_id），直接复用 xml2st。"""
    items, ok = [], True
    for xml in xml_files:
        passed, _st, problems = convert(xml)
        items.append({"xml": xml.name, "ok": passed, "problems": problems})
        ok &= passed
        print("  [%s] %s%s" % ("PASS" if passed else "FAIL", xml.name,
                               ("  -- %d 个契约问题" % len(problems)) if problems else ""))
    return ok, items


def run_pytest():
    """L2：转换器/一致性/编排器单测。"""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q"],
        cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, timeout=300)
    out = proc.stdout.decode("utf-8", errors="replace")
    tail = out.strip().splitlines()[-1] if out.strip() else "(无输出)"
    print("  [%s] pytest  -- %s" % ("PASS" if proc.returncode == 0 else "FAIL", tail))
    return proc.returncode == 0, {"summary": tail}


def probe_runtime(url):
    """探测 OpenPLC Web 可达性（不需要登录，能连上即视为运行时在）。"""
    import requests
    try:
        requests.get(url, timeout=3, allow_redirects=True)
        return True
    except requests.exceptions.RequestException:
        return False


def run_online(scenes, url):
    """L3：逐场景「部署 → 验收」。scenes 为 (xml_path, scenario_py) 配对。"""
    items, ok = [], True
    for xml, scenario in scenes:
        item = {"scene": xml.stem, "deploy": None, "acceptance": None}
        print("  ── 场景 %s ──" % xml.stem)
        proc = subprocess.run(
            [sys.executable, str(PIPELINE / "run_deploy.py"), "--xml", str(xml), "--url", url],
            cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT, timeout=300)
        dep_ok = proc.returncode == 0
        item["deploy"] = "OK" if dep_ok else "FAIL"
        out = proc.stdout.decode("utf-8", errors="replace")
        if not dep_ok:
            item["deploy_detail"] = out.strip().splitlines()[-3:]
        print("    [%s] deploy" % ("PASS" if dep_ok else "FAIL"))

        if dep_ok:
            proc = subprocess.run(
                [sys.executable, str(scenario)],
                cwd=str(REPO_ROOT), stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT, timeout=600)
            acc_ok = proc.returncode == 0
            item["acceptance"] = "PASS" if acc_ok else "FAIL"
            out = proc.stdout.decode("utf-8", errors="replace")
            if not acc_ok:
                item["acceptance_detail"] = out.strip().splitlines()[-10:]
            print("    [%s] acceptance" % ("PASS" if acc_ok else "FAIL"))
            ok &= acc_ok
        else:
            item["acceptance"] = "SKIPPED"
            ok = False
        items.append(item)
    return ok, items


def main():
    parser = argparse.ArgumentParser(description="lx 一键回归：静态校验 + pytest + 逐场景在线验收")
    parser.add_argument("--xml-dir", default=str(REPO_ROOT / "src" / "plc"))
    parser.add_argument("--skip-online", action="store_true",
                        help="只跑本地两层（静态+单测），不触碰运行时")
    parser.add_argument("--require-online", action="store_true",
                        help="在线层不可达（SKIP）也视为失败——完整合入门禁用")
    parser.add_argument("--url", default=os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080"))
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    t0 = time.time()
    result = {"layers": {}, "errors": []}
    exit_code = 0

    # 场景发现：XML × 验收脚本配对
    xml_files = sorted(Path(args.xml_dir).glob("*.xml"))
    if not xml_files:
        print("[regression] src/plc 下没有场景 XML")
        return 1
    scenes = []
    for xml in xml_files:
        scenario = PIPELINE / ("scenario_%s.py" % xml.stem)
        scenes.append((xml, scenario))

    # L1 静态校验
    print("[L1] 静态校验（xml2st 契约）")
    ok, items = run_static(xml_files)
    result["layers"]["static"] = {"status": "PASS" if ok else "FAIL", "items": items}
    if not ok:
        result["errors"].append("L1 静态校验失败")
        return finish(result, exit_code=1, t0=t0)

    # L2 单元测试
    print("[L2] 单元测试（pytest）")
    ok, detail = run_pytest()
    result["layers"]["pytest"] = dict({"status": "PASS" if ok else "FAIL"}, **detail)
    if not ok:
        result["errors"].append("L2 单元测试失败")
        return finish(result, exit_code=1, t0=t0)

    # L3 在线验收
    if args.skip_online:
        print("[L3] 在线验收：SKIP（--skip-online）")
        result["layers"]["online"] = {"status": "SKIP", "reason": "--skip-online"}
        return finish(result, exit_code=0, t0=t0)

    if not probe_runtime(args.url):
        print("[L3] 在线验收：SKIP（运行时不可达 %s）" % args.url)
        result["layers"]["online"] = {"status": "SKIP",
                                      "reason": "runtime unreachable: %s" % args.url}
        return finish(result, exit_code=1 if args.require_online else 0, t0=t0)

    missing = [(xml, sc) for xml, sc in scenes if not sc.exists()]
    for xml, _sc in missing:
        print("  [SKIP] %s 缺验收脚本 scenario_%s.py" % (xml.name, xml.stem))
    runnable = [(xml, sc) for xml, sc in scenes if sc.exists()]

    print("[L3] 在线验收（部署+验收，%d 个场景）" % len(runnable))
    ok, items = run_online(runnable, args.url)
    for xml, _sc in missing:
        items.append({"scene": xml.stem, "deploy": "SKIPPED",
                      "acceptance": "SKIPPED", "note": "缺验收脚本"})
    result["layers"]["online"] = {"status": "PASS" if ok else "FAIL", "items": items}
    if not ok:
        result["errors"].append("L3 在线验收失败")
        exit_code = 1

    return finish(result, exit_code=exit_code, t0=t0)


def finish(result, exit_code, t0):
    result["status"] = "FAIL" if exit_code else "PASS"
    result["duration_sec"] = round(time.time() - t0, 1)
    WORKSPACE.mkdir(exist_ok=True)
    RESULT_JSON.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print("\n[regression] RESULT: %s（%.1fs，明细 %s）"
          % (result["status"], result["duration_sec"], RESULT_JSON))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
