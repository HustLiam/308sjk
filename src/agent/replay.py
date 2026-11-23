"""已有 runs 证据的离线故障回放；不连接运行时、不调用模型、不重新判卷。"""

import argparse
import csv
import difflib
import json
import math
import re
from pathlib import Path

from .visualization import trajectory_segments, write_view


def _json(path, fallback, notes):
    if not path.is_file():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=lambda s: None)
    except (ValueError, OSError) as exc:
        notes.append("%s 无法读取：%s" % (path.name, exc))
        return fallback


def _read(path):
    return path.read_text(encoding="utf-8-sig", errors="replace") if path.is_file() else ""


def _samples(directory, errors, notes):
    source = None
    rows = []
    for filename in ("trace.json", "trace.jsonl", "trace.csv"):
        path = directory / filename
        if not path.is_file():
            continue
        source = filename
        try:
            if filename.endswith(".csv"):
                with path.open(encoding="utf-8-sig", newline="") as stream:
                    for row in csv.DictReader(stream):
                        item = {}
                        for key, value in row.items():
                            try:
                                item[key] = json.loads(value)
                            except (ValueError, TypeError):
                                item[key] = value
                        rows.append(item)
            elif filename.endswith(".jsonl"):
                rows = [json.loads(line) for line in _read(path).splitlines() if line.strip()]
            else:
                rows = _json(path, [], notes)
                if isinstance(rows, dict):
                    rows = rows.get("samples", rows.get("trace", []))
        except (ValueError, OSError) as exc:
            notes.append("%s 采样无法解析：%s" % (filename, exc))
            rows = []
        break
    if not isinstance(rows, list):
        notes.append("trace 必须是样本列表")
        rows = []
    if not rows:
        for line in errors:
            match = re.search(r"\[diag t=([\d.]+)s\]\s*(.*)", str(line))
            if match:
                item = {"t": float(match[1])}
                for key, value in re.findall(r"([\w.]+)=([-+]?\d+(?:\.\d+)?)", match[2]):
                    item[key] = float(value)
                rows.append(item)
        if rows:
            source = "diagnostic_log"
    clean = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = {str(k): v for k, v in row.items()
                if isinstance(v, (str, int, float, bool)) and not (isinstance(v, float) and not math.isfinite(v))}
        clean.append(item)
    note = ("没有时序采样：仅回放日志和判定证据，不补造轨迹。" if not clean else
            "来自自动诊断探针的相对时间线，未包含 XY 时不绘制实际轨迹。" if source == "diagnostic_log" else
            "采样来源：%s；缺失通道保留为空，不插值补造证据。" % source)
    return clean, note


def _result_rows(value):
    if isinstance(value, list):
        return [r for r in value if isinstance(r, dict)]
    if isinstance(value, dict):
        for key in ("results", "criteria", "checks"):
            if isinstance(value.get(key), list):
                return _result_rows(value[key])
    return []


def collect_replay(run_dir):
    run_dir = Path(run_dir)
    selected = run_dir.name if re.fullmatch(r"iter_\d+", run_dir.name) else None
    if selected:
        run_dir = run_dir.parent
    if not run_dir.is_dir():
        raise ValueError("运行目录不存在：%s" % run_dir)
    directories = sorted((p for p in run_dir.iterdir() if p.is_dir() and re.fullmatch(r"iter_\d+", p.name)),
                         key=lambda p: int(p.name[5:]))
    if not directories:
        raise ValueError("目录中没有 iter_NNN 运行证据：%s" % run_dir)
    notes = ["判定来自保存的 gate/verdict；回放不会重新计算或改变 PASS/FAIL。"]
    trajectory = _json(run_dir / "trajectory.json", None, notes)
    spec = _json(run_dir / "request.json", {}, notes)
    if not isinstance(spec, dict):
        notes.append("request.json 不是规格对象，保留其他证据。")
        spec = {}
    segments = []
    if trajectory:
        try:
            segments = trajectory_segments(trajectory)
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            notes.append("保存的规划无法预览：%s" % exc)
            trajectory = None
    iterations, previous_st = [], ""
    for directory in directories:
        local_notes = []
        gate = _json(directory / "gate.json", {}, local_notes)
        gate = gate if isinstance(gate, dict) else {}
        errors = gate.get("errors") or []
        if not isinstance(errors, list):
            errors = [str(errors)]
        samples, sample_note = _samples(directory, errors, local_notes)
        verdict = _result_rows(_json(directory / "verdict.json", [], local_notes))
        logs = _read(directory / "acceptance.log")
        stages = gate.get("gates") or {}
        stages = stages if isinstance(stages, dict) else {}
        acc = stages.get("acceptance") or {}
        if not logs and isinstance(acc, dict):
            detail = acc.get("detail", "")
            logs = "\n".join(map(str, detail)) if isinstance(detail, list) else str(detail)
        attribution = gate.get("attribution") or {}
        diagnosis = json.dumps(attribution, ensure_ascii=False, indent=2) if attribution else ""
        if gate.get("lessons"):
            diagnosis += "\n相关历史经验：\n" + json.dumps(gate["lessons"], ensure_ascii=False, indent=2)
        st = _read(directory / "plc.st")
        diff = "\n".join(difflib.unified_diff(previous_st.splitlines(), st.splitlines(),
                          fromfile="上一轮 plc.st", tofile=directory.name + "/plc.st", lineterm="")) if previous_st and st else ""
        if st:
            previous_st = st
        skipped = [name for name in ("deploy", "acceptance") if
                   isinstance(stages.get(name), dict) and stages[name].get("state") == "skipped"]
        status = "失败 · %s" % gate.get("gate", "未知阶段") if gate.get("ok") is False else "结果未记录"
        if gate.get("ok") is True:
            status = "本轮闸门通过"
            online = all(isinstance(stages.get(k), dict) and stages[k].get("state") == "ok" for k in ("deploy", "acceptance"))
            if skipped:
                status += "（跳过：%s）" % "/".join(skipped)
            elif not online:
                status += "（未完成全量在线验证）"
        iterations.append({"number": int(directory.name[5:]), "gate": gate.get("gate"),
                           "ok": gate.get("ok"), "status": status,
                           "errors": list(map(str, errors)), "diagnosis": diagnosis,
                           "samples": samples, "sample_note": sample_note,
                           "verdict": verdict, "logs": logs, "diff": diff,
                           "notes": local_notes})
        notes.extend("%s：%s" % (directory.name, note) for note in local_notes)
    return {"kind": "replay", "title": "故障回放 · %s" % spec.get("task_id", run_dir.name),
            "trajectory": trajectory, "segments": segments, "iterations": iterations,
            "notes": notes, "selected_iteration": int(selected[5:]) if selected else None}


def write_replay(run_dir, output=None):
    data = collect_replay(run_dir)
    root = Path(run_dir)
    if re.fullmatch(r"iter_\d+", root.name):
        root = root.parent
    return write_view(output or root / "replay.html", data)


def main(argv=None):
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except AttributeError:
        pass
    parser = argparse.ArgumentParser(description="离线回放 PLC 任务的故障、变量曲线与修复差异")
    parser.add_argument("run_dir", help="runs/<task> 或其中一个 iter_NNN")
    parser.add_argument("--output", help="HTML 保存路径（默认任务目录 replay.html）")
    parser.add_argument("--open", action="store_true", help="用浏览器打开生成的本地页面")
    args = parser.parse_args(argv)
    try:
        path = write_replay(args.run_dir, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, "%s\n" % exc)
    print("故障回放：%s" % path.resolve())
    if args.open:
        import webbrowser
        webbrowser.open(path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
