"""网页 Studio 的 IO 采集和验收展示，复用桌面工具与确定性判定器。"""
import hashlib
import json
import re
import time
from pathlib import Path


def read_signals(client, spec):
    points = spec.get("io_list", [])
    values = {}
    bits, words = {}, {}
    for p in points:
        addr = p.get("address", "")
        bit = re.fullmatch(r"%QX(\d+)\.([0-7])", addr)
        word = re.fullmatch(r"%QW(\d+)", addr)
        if bit and p["type"] == "BOOL":
            bits[p["name"]] = int(bit[1])*8+int(bit[2])
        elif word and p["type"] == "INT":
            words[p["name"]] = (int(word[1]), p.get("range") or [0])
    def checked(response):
        if response is None or response.isError():
            raise ConnectionError("PLC 信号读取失败")
        return response
    if bits:
        response = checked(client.read_coils(address=0, count=(max(bits.values())//16+1)*16))
        values.update({name: bool(response.bits[bit]) for name, bit in bits.items()})
    # 分块读取，遵循 Modbus 单次最多 125 个寄存器。
    for start in sorted({(addr//120)*120 for addr, _ in words.values()}):
        group = {n: (a, r) for n, (a, r) in words.items() if start <= a < start+120}
        regs = checked(client.read_holding_registers(address=start, count=max(a for a, _ in group.values())-start+1)).registers
        for name, (addr, value_range) in group.items():
            v = regs[addr-start]
            values[name] = v-65536 if value_range[0] < 0 and v >= 32768 else v
    return values


def panel(spec):
    from .hmi_gen import build_hmi
    result = build_hmi(spec)
    labels = {"run":"使能", "quickstop":"急停", "cmd_draw":"开始绘制",
              "cmd_reset":"故障复位", "cmd_home":"回原点", "cmd_go":"定位",
              "jog_fwd":"正向点动", "jog_rev":"反向点动", "plot_done":"绘制完成",
              "all_oe":"已使能", "fault_any":"故障", "pen_down":"落笔"}
    for e in result["elements"]:
        e["label"] = labels.get(e["signal"], re.sub(r"^OperatorPanel\s*", "", e["label"]))
    return result


def save_verification(directory, run_id, spec, trace, status, mode, error, physical_trace=None):
    """仅复用实际采样；停止/失败/手动调试均不能报告验收通过。"""
    from src.pipeline.mujoco_verdict import build_channels, evaluate, attribute
    from src.pipeline.scenario_mujoco import verify_trajectory
    directory = Path(directory)
    scene_path = directory/"simulation/scene.spec.json"
    scene = json.loads(scene_path.read_text(encoding="utf-8"))
    channels, _, done = build_channels(spec, scene["io_map"])
    results = evaluate(spec.get("acceptance", []), trace, channels) if trace else []
    trajectory = directory/"trajectory.json"
    if trace and trajectory.is_file() and spec.get("axes"):
        result = verify_trajectory(physical_trace if physical_trace is not None else trace,
            json.loads(trajectory.read_text(encoding="utf-8")), spec)
        result["source"] = "mujoco_joint_feedback" if physical_trace is not None else "plc_register_feedback"
        results.append(result)
        if physical_trace is not None:
            from .svg_verification import verify_svg
            results.extend(verify_svg(physical_trace,json.loads(trajectory.read_text(encoding='utf-8')),spec))
    imported = directory/"import.json"
    configured = not imported.is_file() or json.loads(imported.read_text(encoding="utf-8"))["verification_configured"]
    eligible = configured and mode != "manual" and status == "completed" and not error
    passed = eligible and bool(results) and all(r.get("pass") is True for r in results)
    verdict = {"id":run_id, "task":directory.name, "mode":mode,
        "created_at":time.time(), "verdict":"PASS" if passed else "FAIL" if eligible else "UNVERIFIED",
        "status":status, "error":error, "samples":len(trace), "verification_configured":configured,
        "duration_s":trace[-1]["t"] if trace else 0, "details":results,
        "sha256":{name:hashlib.sha256(path.read_bytes()).hexdigest() for name,path in
            {"xml":directory/"final/plcopen.xml", "scene":scene_path,
             "io_map":directory/"simulation/io_map.json"}.items()},
        "spec_sha256":hashlib.sha256(json.dumps(spec,sort_keys=True,ensure_ascii=False).encode()).hexdigest()}
    report = attribute(results, trace, channels, done) if trace and results and not passed else ""
    target = directory/"simulation/verifications"/run_id
    target.mkdir(parents=True, exist_ok=True)
    for name, value in (("trace.json",trace),("verdict.json",verdict)):
        (target/name).write_text(json.dumps(value,ensure_ascii=False),encoding="utf-8")
    if physical_trace is not None:
        (target/"physical_trace.json").write_text(json.dumps(physical_trace,ensure_ascii=False),encoding="utf-8")
        verdict["physical_samples"] = len(physical_trace)
        verdict['physical_sha256']=hashlib.sha256(json.dumps(physical_trace,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        if trajectory.is_file():
            verdict['trajectory_sha256']=hashlib.sha256(trajectory.read_bytes()).hexdigest()
        (target/"verdict.json").write_text(json.dumps(verdict,ensure_ascii=False),encoding="utf-8")
    (target/"report.md").write_text(report,encoding="utf-8")
    return verdict


def verification_records(directory):
    records = []
    for path in sorted((Path(directory)/"simulation/verifications").glob("*/verdict.json"), reverse=True):
        records.append(json.loads(path.read_text(encoding="utf-8")))
        if len(records) == 30:
            break
    return records
