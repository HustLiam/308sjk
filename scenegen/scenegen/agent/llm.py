"""LLM 客户端抽象。

- OpenAICompatLLM：任意 OpenAI 兼容 /chat/completions 端点（GLM/OpenAI/DeepSeek/vLLM/
  Ollama 兼容层），纯标准库实现，无额外依赖。配置来自环境变量：
    SCENEGEN_LLM_BASE_URL  如 https://open.bigmodel.cn/api/paas/v4
    SCENEGEN_LLM_API_KEY
    SCENEGEN_LLM_MODEL     如 glm-4.7
- MockLLM：离线确定性生成器（按 io_list 的 device 字段套用标准布局模板），
  用于无 API Key 时验证全流程与回归测试。
"""

import json
import os
import re
import urllib.error
import urllib.request
from typing import Dict, List, Optional, Protocol


class LLM(Protocol):
    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        ...


class OpenAICompatLLM:
    def __init__(self, base_url: str, api_key: str, model: str, timeout: int = 600,
                 json_mode: bool = True, stream: bool = True, thinking: Optional[str] = None):
        self.endpoint = base_url.rstrip("/") + "/chat/completions"
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.json_mode = json_mode
        # 流式读取：推理型模型（GLM-5.3 等）整次生成可能很久，逐块接收避免单次长读超时
        self.stream = stream
        # 可选："enabled"/"disabled"（GLM 系 thinking 参数），None 表示不发送
        self.thinking = thinking

    @classmethod
    def from_env(cls) -> "OpenAICompatLLM":
        base = os.environ.get("SCENEGEN_LLM_BASE_URL")
        key = os.environ.get("SCENEGEN_LLM_API_KEY")
        model = os.environ.get("SCENEGEN_LLM_MODEL")
        missing = [n for n, v in (
            ("SCENEGEN_LLM_BASE_URL", base),
            ("SCENEGEN_LLM_API_KEY", key),
            ("SCENEGEN_LLM_MODEL", model)) if not v]
        if missing:
            raise RuntimeError(
                f"缺少环境变量 {missing}；或使用 --mock 走离线生成器")
        timeout = int(os.environ.get("SCENEGEN_LLM_TIMEOUT", "600"))
        thinking = os.environ.get("SCENEGEN_LLM_THINKING") or None
        return cls(base, key, model, timeout=timeout, thinking=thinking)

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        body = {"model": self.model, "messages": messages, "temperature": temperature}
        if self.json_mode:
            body["response_format"] = {"type": "json_object"}
        if self.thinking:
            body["thinking"] = {"type": self.thinking}
        if self.stream:
            body["stream"] = True
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                if self.stream:
                    return self._read_stream(resp)
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            raise RuntimeError(f"LLM 请求失败 HTTP {e.code}: {detail}") from e
        return data["choices"][0]["message"]["content"]

    @staticmethod
    def _read_stream(resp) -> str:
        parts = []
        for raw in resp:
            line = raw.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if payload == "[DONE]":
                break
            chunk = json.loads(payload)
            delta = chunk.get("choices", [{}])[0].get("delta", {})
            if delta.get("content"):
                parts.append(delta["content"])
        return "".join(parts)


# ---------------------------------------------------------------- 离线 Mock

_BELT_ID = "belt_1"
_TEMPLATE = {
    "spec_version": "1.1",
    "units": "m",
    "physics": {"gravity": [0, 0, -9.81], "physics_dt": 0.00833, "solver": "tgs"},
    "ground": {"size": [20, 20], "friction": 0.8},
    "lighting": "warehouse_preset",
}


class MockLLM:
    """按 io_list.device 关键词套用"传送带分拣"标准布局，离线产出合法 SceneSpec。"""

    DEVICE_MAP = {
        "photoelectric_sensor": ("光电", "photoelectric", "pe_", "检测"),
        "pneumatic_cylinder": ("气缸", "cylinder", "cyl_", "推出"),
        "conveyor_belt": ("传送带", "belt", "belt_", "输送"),
        "bin_chute": ("料槽", "料箱", "chute", "承接"),
        "vacuum_gripper": ("夹爪", "吸盘", "gripper", "抓取"),
        "articular_arm": ("机械臂", "机器人", "arm", "搬运"),
        "gantry_xyz": ("gantry", "龙门", "三轴", "画圆"),
    }

    def chat(self, messages: List[Dict[str, str]], temperature: float = 0.2) -> str:
        req = json.loads(messages[1]["content"].split("需求规格：", 1)[1].strip())
        return json.dumps(self.generate(req), ensure_ascii=False)

    def _device_kind(self, entry: Dict) -> Optional[str]:
        text = " ".join(str(entry.get(k, "")) for k in ("device", "semantic", "name"))
        for kind, keys in self.DEVICE_MAP.items():
            if any(k in text.lower() or k in text for k in keys):
                return kind
        return None

    def generate(self, requirement: Dict) -> Dict:
        io_list = requirement.get("io_list", [])
        kinds = []
        for e in io_list:
            kind = self._device_kind(e)
            if kind and kind not in kinds:
                kinds.append(kind)

        has_belt = "conveyor_belt" in kinds
        assets, io_map = [], []
        pos = 0  # 命名序号，同型多设备递增

        def next_id(prefix: str) -> str:
            nonlocal pos
            pos += 1
            return f"{prefix}{pos}"

        belt_id = None
        if has_belt or "pneumatic_cylinder" in kinds or "photoelectric_sensor" in kinds:
            belt_id = _BELT_ID
            assets.append({
                "id": belt_id, "type": "conveyor_belt",
                "pose": {"position": [0, 0, 0.5], "rpy_deg": [0, 0, 0]},
                "params": {"length": 3.0, "width": 0.6, "height": 0.1, "max_speed": 0.8},
            })

        # 推料气缸隐含承接料槽（无 IO 绑定，仅供验收准则的区域判定使用）
        if "pneumatic_cylinder" in kinds and "bin_chute" not in kinds:
            kinds.append("bin_chute")

        asset_of_kind = {"conveyor_belt": belt_id}
        cyl_id = pe_id = None
        for kind in kinds:
            if kind == "conveyor_belt":
                continue
            if kind == "pneumatic_cylinder":
                cyl_id = next_id("cyl_")
                assets.append({
                    "id": cyl_id, "type": "pneumatic_cylinder",
                    "parent": belt_id,
                    "pose": {"position": [2.0, -0.35, 0.1]},
                    "params": {"axis": "y", "stroke": 0.4, "rod_diameter": 0.02,
                               "extend_speed": 1.0, "retract_speed": 1.0},
                })
                asset_of_kind[kind] = cyl_id
            elif kind == "photoelectric_sensor":
                pe_id = next_id("pe_")
                assets.append({
                    "id": pe_id, "type": "photoelectric_sensor",
                    "parent": belt_id,
                    "pose": {"position": [1.8, -0.25, 0.1]},
                    "params": {"beam_direction": [0, 1, 0], "beam_length": 0.5},
                })
                asset_of_kind[kind] = pe_id
            elif kind == "bin_chute":
                chute_id = next_id("chute_")
                assets.append({
                    "id": chute_id, "type": "bin_chute",
                    "pose": {"position": [2.0, 0.75, 0.0]},
                    "params": {"size": [0.4, 0.4, 0.5]},
                })
                asset_of_kind[kind] = chute_id
            elif kind == "gantry_xyz":
                gid = next_id("gantry_")
                assets.append({
                    "id": gid, "type": "gantry_xyz",
                    "pose": {"position": [0, 0, 0], "rpy_deg": [0, 0, 0]},
                    "params": {"travel_x": 0.6, "travel_y": 0.4, "travel_z": 0.2, "speed": 0.5},
                })
                asset_of_kind[kind] = gid

        qty_of = {
            "photoelectric_sensor": "beam_broken",
            "pneumatic_cylinder": None,   # 按 dir 选择 extend_cmd / position
            "conveyor_belt": None,        # bool 输出 → run_cmd；float 输入 → measured_speed
            "bin_chute": "object_inside",
            "vacuum_gripper": None,
            "articular_arm": None,
        }
        for e in io_list:
            kind = self._device_kind(e)
            bind_asset = asset_of_kind.get(kind) if kind else None
            bind = None
            if kind == "pneumatic_cylinder" and bind_asset:
                bind = {"asset": bind_asset,
                        "quantity": "extend_cmd" if e["dir"] == "output" else "position"}
            elif kind == "conveyor_belt" and bind_asset:
                bind = {"asset": bind_asset,
                        "quantity": "run_cmd" if e["dir"] == "output" else "measured_speed"}
            elif kind == "gantry_xyz" and bind_asset:
                q = _gantry_quantity(e)
                bind = {"asset": bind_asset, "quantity": q} if q else None
            elif bind_asset:
                bind = {"asset": bind_asset, "quantity": qty_of[kind]}
            if bind:
                entry = {"plc_var": e["name"], "dir": e["dir"], "type": e["type"], "bind": bind}
                if e.get("range"):
                    entry["bind"]["range"] = e["range"]
                io_map.append(entry)

        if not io_map:  # 保底：至少一条合法 IO，避免 Schema 最小项失败
            io_map.append({
                "plc_var": "Box1_pos", "dir": "input", "type": "float",
                "bind": {"asset": "box_a", "quantity": "position"}})

        # 物料箱仅用于带输送/抓取类场景；龙门等加工类场景无物料，避免与模组穿模
        if belt_id is not None or not io_map:
            assets.append({
                "id": "box_a", "type": "rigid_box",
                "pose": {"position": [0, 0, 0.62 if belt_id else 0.12]},
                "params": {"size": 0.1, "mass": 0.5, "color": "#d9534f"},
            })

        spec = dict(_TEMPLATE)
        spec["scene_id"] = requirement.get("task_id", "mock_scene").lower()
        spec["assets"] = assets
        spec["io_map"] = io_map
        spec["script"] = {
            "spawn_schedule": ([{"asset_template": "box_a", "at_time": [0.0],
                                 "position": [0, 0, 0.62]}]
                               if belt_id is not None else []),
            "perturbations": [],
            "termination": {"max_sim_time": 30.0, "early_stop": "all_boxes_settled"},
        }
        return spec


def _gantry_quantity(entry: Dict) -> Optional[str]:
    """从 io_list 条目名推断 gantry quantity：AxisX_cmd → x_cmd、AxisY_pos → y_pos。"""
    name = entry.get("name", "")
    m = re.search(r"axis[_\s-]*([xyz])", name, re.IGNORECASE)
    if not m:
        return None
    axis = m.group(1).lower()
    n = name.lower()
    if re.search(r"cmd|target|set|ref", n):
        return f"{axis}_cmd"
    if re.search(r"pos|fb|actual|feedback", n):
        return f"{axis}_pos"
    return f"{axis}_cmd" if entry.get("dir") == "output" else f"{axis}_pos"


def from_env() -> LLM:
    """优先真实 LLM；未配置时退回 MockLLM（离线可用）。"""
    if os.environ.get("SCENEGEN_LLM_BASE_URL"):
        return OpenAICompatLLM.from_env()
    return MockLLM()
