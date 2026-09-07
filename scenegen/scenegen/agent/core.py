"""SceneGenAgent：requirement_spec → SceneSpec 的生成-校验-反馈闭环。"""

from typing import Dict, List, Optional, Tuple

from ..validate import validate
from . import prompts


def contract_errors(spec: Dict, requirement: Dict) -> List[str]:
    """SceneSpec 的 io_map 与需求规格 io_list 的三方一致性检查（validator 看不到需求，故由 agent 层做）。"""
    want = {e["name"]: e for e in requirement.get("io_list", [])}
    got = {e["plc_var"]: e for e in spec.get("io_map", [])} if isinstance(spec, dict) else {}
    errors: List[str] = []
    for name in want:
        if name not in got:
            errors.append(f"io_map 缺少 io_list 变量 {name}")
    for name in got:
        if name not in want:
            errors.append(f"io_map 含 io_list 之外的变量 {name}")
    for name, w in want.items():
        g = got.get(name)
        if g is None:
            continue
        if g.get("dir") != w.get("dir"):
            errors.append(f"io_map[{name}]: dir={g.get('dir')} 与 io_list 的 {w.get('dir')} 不一致")
        if g.get("type") != w.get("type"):
            errors.append(f"io_map[{name}]: type={g.get('type')} 与 io_list 的 {w.get('type')} 不一致")
    return errors


class SceneGenAgent:
    def __init__(self, llm, max_retries: int = 4, temperature: float = 0.2):
        self.llm = llm
        self.max_retries = max_retries
        self.temperature = temperature

    def generate(self, requirement: Dict) -> Tuple[Optional[Dict], Dict]:
        """返回 (spec | None, report)。spec 非 None 即已通过全部静态校验与契约检查。"""
        messages: List[Dict[str, str]] = [
            {"role": "system", "content": prompts.SYSTEM_PROMPT},
            {"role": "user", "content": prompts.build_user_prompt(requirement)},
        ]
        history = []
        best: Optional[Dict] = None
        best_errors: Optional[List[str]] = None

        for attempt in range(1, self.max_retries + 1):
            reply = self.llm.chat(messages, temperature=self.temperature)
            spec = prompts.extract_json(reply)
            if spec is None:
                errors = ["LLM 输出无法解析为 JSON"]
            else:
                errors = validate(spec) + contract_errors(spec, requirement)
            history.append({"attempt": attempt, "errors": errors})
            if not errors:
                return spec, {"ok": True, "attempts": attempt, "history": history}
            if best_errors is None or len(errors) < len(best_errors):
                best, best_errors = spec, errors
            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user",
                             "content": prompts.build_feedback_prompt(errors, spec)})

        return None, {"ok": False, "attempts": self.max_retries,
                      "history": history, "best_errors": best_errors or []}
