"""验收证据附加文件；仅记录，不参与判定或修改验收契约。"""

import json
import os
from pathlib import Path


def save_acceptance_evidence(trace, results=None, directory=None):
    directory = directory or os.environ.get("AGENT_EVIDENCE_DIR")
    if not directory:
        return None
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    if results is not None:
        (directory / "verdict.json").write_text(
            json.dumps(results, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    return directory
