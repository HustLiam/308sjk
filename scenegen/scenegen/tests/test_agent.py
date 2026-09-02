"""agent 回归测试（离线，不需要 API Key）：python scenegen/tests/test_agent.py"""

import copy
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from scenegen import build_usd  # noqa: E402
from scenegen.agent.core import SceneGenAgent, contract_errors  # noqa: E402
from scenegen.agent.llm import MockLLM  # noqa: E402
from scenegen.validate import validate  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REQUIREMENT = os.path.normpath(os.path.join(
    HERE, "..", "agent", "examples", "requirement_sort.json"))
GANTRY_REQ = os.path.normpath(os.path.join(
    HERE, "..", "agent", "examples", "requirement_gantry_circle.json"))


def load_req() -> dict:
    with open(REQUIREMENT, encoding="utf-8") as f:
        return json.load(f)


class BrokenFirstLLM:
    """首轮返回带错的 spec（气缸行程非法），验证反馈重试闭环。"""

    def __init__(self):
        self.inner = MockLLM()
        self.calls = 0

    def chat(self, messages, temperature=0.2):
        self.calls += 1
        spec = json.loads(self.inner.chat(messages, temperature))
        if self.calls == 1:
            for a in spec["assets"]:
                if a["type"] == "pneumatic_cylinder":
                    a["params"]["stroke"] = 0        # 超出 (0,1]
            spec["io_map"][0]["type"] = "float"       # 类型契约破坏
        return json.dumps(spec, ensure_ascii=False)


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="scenegen_agent_test_")
    n = 0

    # ① Mock 生成：一次通过校验与契约，端到端可构建
    req = load_req()
    spec, report = SceneGenAgent(MockLLM(), max_retries=4).generate(req)
    assert spec is not None, f"Mock 应一次通过: {report}"
    assert report["ok"] and report["attempts"] == 1
    assert validate(spec) == []
    assert contract_errors(spec, req) == []
    result = build_usd.build(spec, tmp)
    by_var = {e["plc_var"]: e for e in result["io_map"]}
    assert by_var["PE1_detected"]["modbus"]["plc_addr"] == "%IW0"
    assert by_var["Cyl1_extend"]["modbus"]["plc_addr"] == "%QX0.0"
    types = {a["type"] for a in spec["assets"]}
    assert "bin_chute" in types, "有气缸时应自动补料槽"
    n += 1

    # ② 契约检查：io_list 出现不可映射设备 → 缺变量错误 → 重试耗尽返回 None
    bad_req = copy.deepcopy(req)
    bad_req["io_list"].append(
        {"name": "Weight1", "dir": "input", "type": "float",
         "device": "load_cell", "semantic": "称重"})
    spec2, report2 = SceneGenAgent(MockLLM(), max_retries=2).generate(bad_req)
    assert spec2 is None
    assert any("缺少 io_list 变量 Weight1" in e for e in report2["best_errors"])
    n += 1

    # ③ 反馈重试闭环：首轮注入两类错误，第二轮修复通过
    spec3, report3 = SceneGenAgent(BrokenFirstLLM(), max_retries=4).generate(req)
    assert spec3 is not None and report3["ok"] and report3["attempts"] == 2
    assert validate(spec3) == []
    n += 1

    # ④ 三轴画圆模组：Mock 生成 → 校验/契约/构建全通过
    with open(GANTRY_REQ, encoding="utf-8") as f:
        req4 = json.load(f)
    spec4, report4 = SceneGenAgent(MockLLM(), max_retries=4).generate(req4)
    assert spec4 is not None, f"gantry Mock 应一次通过: {report4}"
    io4 = {e["plc_var"]: e for e in spec4["io_map"]}
    assert len(io4) == 6
    assert io4["AxisX_cmd"]["bind"]["quantity"] == "x_cmd"
    assert io4["AxisZ_pos"]["bind"]["quantity"] == "z_pos"
    tmp4 = tempfile.mkdtemp(prefix="scenegen_gantry_")
    r4 = build_usd.build(spec4, tmp4)
    from pxr import Usd  # noqa: E402
    stage4 = Usd.Stage.Open(r4["scene_usd"])
    gantry_root = {e["plc_var"]: e for e in r4["io_map"]}["AxisX_cmd"]["usd_prim"].rsplit("/", 1)[0]
    for jn in ("joint_x", "joint_y", "joint_z"):
        assert stage4.GetPrimAtPath(f"{gantry_root}/{jn}").IsValid()
    mb4 = {e["plc_var"]: e["modbus"] for e in r4["io_map"]}
    assert mb4["AxisX_cmd"]["plc_addr"] == "%QW0" and mb4["AxisX_cmd"]["length"] == 2
    assert mb4["AxisZ_cmd"]["plc_addr"] == "%QW4"
    assert mb4["AxisX_pos"]["plc_addr"] == "%IW0" and mb4["AxisY_pos"]["plc_addr"] == "%IW2"
    n += 1

    print(f"PASS：{n} 组断言全部通过（产物目录 {tmp}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
