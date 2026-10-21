"""Studio 与严格 CLI 的接线及结果核验，不依赖 Qt。"""
import json
import hashlib
import os
import re
from pathlib import Path
import signal
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def pipeline_command(aml, request, task):
    from src.agent.closed_loop import DEFAULT_REQUEST
    text = request.strip() or DEFAULT_REQUEST
    run = ROOT / "runs" / task
    return [sys.executable, "-u", "-m", "src.agent.closed_loop", "--aml", aml,
            "--request", text, "--task-id", task, "--max-iters", "6",
            "--frame-output", str(run / "live_frame.npy")]


def verified_result(run, returncode):
    """进程退出码、manifest、gate 和本轮 verdict 必须同时证明成功。"""
    run = Path(run)
    if returncode != 0:
        return False
    try:
        manifest = json.loads((run / "manifest.json").read_text(encoding="utf-8"))
        gate = json.loads((run / "final/gate.json").read_text(encoding="utf-8"))
        verdict = json.loads((run / "final/verdict.json").read_text(encoding="utf-8"))
        gates = gate["gates"]
        required = {"AC1", "AC2", "AC3", "AC4", "PATH"}
        if manifest.get("test_plan_source") == "llm":
            plan = json.loads((run / "final/test_plan.json").read_text(encoding="utf-8"))
            spec = json.loads((run / "requirements/requirement_spec.json").read_text(encoding="utf-8"))
            execution = json.loads((run / "final/execution.json").read_text(encoding="utf-8"))
            digest = hashlib.sha256((run / "final/test_plan.json").read_bytes()).hexdigest()
            if (digest != manifest.get("test_plan_sha256")
                    or digest != verdict.get("sha256", {}).get("test_plan")
                    or execution.get("status") != "passed" or execution.get("cleanup_ok") is not True):
                return False
            required = {a["id"] for a in spec["acceptance"]} | {c["id"] for c in plan["path_checks"]} | {"EXECUTION"}
        return (manifest.get("status") == "final"
                and manifest.get("generation_mode") == "full_xml"
                and manifest.get("seed_used") is False
                and manifest.get("require_online") is True
                and gate.get("ok") is True
                and gates.get("deploy", {}).get("state") == "ok"
                and gates.get("acceptance", {}).get("state") == "ok"
                and verdict.get("verdict") == "PASS"
                and bool(verdict.get("details"))
                and required.issubset(
                    {r.get("id") for r in verdict["details"]})
                and all(r.get("pass") is True for r in verdict["details"]))
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False


def stop_process_tree(proc):
    if proc is None or proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10)
    else:
        os.killpg(proc.pid, signal.SIGTERM)


def reset_cancelled_inputs(run):
    """强制终止会跳过采集器 finally；仅复位本任务程序的 BOOL 输入。"""
    path = Path(run) / "requirements/requirement_spec.json"
    if not path.is_file():
        return
    reset_plc_inputs(path)


def reset_plc_inputs(path):
    """用独立连接复位规格的输入位，不与反馈线程共享 Modbus 客户端。"""
    from pymodbus.client import ModbusTcpClient
    path = Path(path)
    spec = json.loads(path.read_text(encoding="utf-8"))
    bits, inputs = [], []
    for item in spec["io_list"]:
        match = re.fullmatch(r"%QX(\d+)\.([0-7])", item.get("address", ""))
        if match and item["type"] == "BOOL":
            bit = int(match[1]) * 8 + int(match[2])
            bits.append(bit)
            if item["dir"] == "input":
                inputs.append(bit)
    if not inputs:
        return
    client = ModbusTcpClient(os.environ.get("MODBUS_HOST", "127.0.0.1"),
                             port=int(os.environ.get("MODBUS_PORT", "502")), timeout=1)
    try:
        if not client.connect():
            raise ConnectionError("OpenPLC 不可达")
        identity = client.read_holding_registers(address=20, count=1)
        if identity.isError() or identity.registers[0] != spec["prog_id"]:
            raise RuntimeError("当前 PLC 程序身份不匹配，未写入输入")
        span = ((max(bits) // 16) + 1) * 16
        response = client.read_coils(address=0, count=span)
        if response.isError():
            raise RuntimeError("不能读取 PLC 输入")
        values = [bool(v) for v in response.bits[:span]]
        for bit in inputs:
            values[bit] = False
        if client.write_coils(address=0, values=values).isError():
            raise RuntimeError("输入复位失败")
    finally:
        client.close()


class RemoteCamera:
    """只控制实际运行时的相机参数，不发送 PLC 控制信号。"""
    def __init__(self, frame_path):
        self.path = Path(frame_path).with_suffix(".camera.json")
        self.camera = {"azimuth": 135, "elevation": -40, "distance": 2.5}

    def _save(self):
        if not self.path.parent.is_dir():
            return
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.camera), encoding="utf-8")
        os.replace(tmp, self.path)

    def orbit(self, dx, dy):
        self.camera["azimuth"] -= 0.3 * dx
        self.camera["elevation"] = max(-89, min(89, self.camera["elevation"] + 0.3 * dy))
        self._save()

    def zoom(self, dy):
        self.camera["distance"] = max(0.3, min(8.0, self.camera["distance"] * (1 + 0.1 * dy)))
        self._save()

    def reset_camera(self):
        self.camera = {"azimuth": 135, "elevation": -40, "distance": 2.5}
        self._save()
