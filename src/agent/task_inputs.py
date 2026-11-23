"""读取任务冻结输入，兼容更新前的工作台任务。"""
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def load_task_spec(directory):
    directory = Path(directory)
    path = directory / "requirements/requirement_spec.json"
    if not path.is_file():
        path = directory / "request.json"
    spec = json.loads(path.read_text(encoding="utf-8"))
    xml = directory / "final/plcopen.xml"
    if xml.is_file():
        addresses = {v.get("name"): v.get("address") for v in ET.parse(xml).iter()
                     if v.tag.rsplit("}", 1)[-1] == "variable" and v.get("address")}
        for point in spec.get("io_list", []):
            if point["name"] in addresses:
                point.setdefault("address", addresses[point["name"]])
    if not spec.get("axes") and (directory / "assets/device.aml").is_file():
        from .aml_parser import parse_aml, build_axes
        model, errors = parse_aml(directory / "assets/device.aml")
        if errors:
            raise ValueError("任务 AML 无效：" + "；".join(errors))
        spec["axes"] = build_axes(model)
    return spec
