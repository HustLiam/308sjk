"""工作台到 lx 部署服务的薄适配；PLC 转换、编译、启停仍由 pipeline 负责。"""
import json
from .task_inputs import load_task_spec
import os
from pathlib import Path

import requests


class RuntimeControlError(RuntimeError):
    def __init__(self, message, result=None):
        super().__init__(message)
        self.result = result or {}


class LXRuntime:
    def __init__(self):
        self.deploy_url = os.environ.get("DEPLOY_URL", "http://127.0.0.1:8600/deploy").rstrip("/")
        self.base = self.deploy_url.rsplit("/", 1)[0]

    def request(self, method, path, **kwargs):
        url = self.deploy_url if path == "/deploy" else self.base + path
        try:
            with requests.Session() as client:
                client.trust_env = False
                response = client.request(method, url, **kwargs)
                result = response.json()
                if not isinstance(result, dict):
                    raise ValueError("部署服务响应不是对象")
                if not response.ok:
                    errors = result.get("errors") or [result.get("error", "部署服务请求失败")]
                    raise RuntimeControlError("；".join(map(str, errors)), result)
                return result
        except (requests.RequestException, ValueError) as exc:
            raise RuntimeControlError("lx 部署服务不可用：" + str(exc)) from exc

    def status(self):
        return self.request("GET", "/status", timeout=(3, 15))

    def logs(self):
        return self.request("GET", "/runtime/logs", timeout=(3, 15))

    def action(self, action, xml=None):
        if action == "deploy":
            return self.request("POST", "/deploy", data=xml,
                                headers={"Content-Type": "application/xml", "X-Deploy-Xml-Name": "plcopen.xml"},
                                timeout=(3, 920))
        if action not in ("start", "stop"):
            raise ValueError("未知 Runtime 操作")
        return self.request("POST", "/runtime/" + action, json={}, timeout=(3, 30))


def deployment_preflight(directory):
    """再核对冻结输入和便宜闸门，之后才允许 lx 上传、真编译与启动。"""
    from .aml_parser import parse_aml
    # 沿用一致性闸门加载的 lx 转换器，兼容 agent.* 和 src.agent.* 两种入口。
    from .consistency_check import consistency_check, xml2st
    from .scene_gen import SceneSpecGenerator, validate_scene_outputs

    directory = Path(directory)
    xml = directory / "final/plcopen.xml"
    spec = load_task_spec(directory)
    scene = json.loads((directory / "final/scene.spec.json").read_text(encoding="utf-8"))
    library = json.loads((directory / "assets/components.json").read_text(encoding="utf-8"))
    model, problems = parse_aml(directory / "assets/device.aml")
    if problems:
        raise RuntimeControlError("AML 校验失败：" + "；".join(problems))
    ok, _, problems = xml2st.convert(xml)
    if not ok:
        raise RuntimeControlError("XML 校验失败：" + "；".join(problems))
    ok, problems = consistency_check(xml, spec["io_list"], scene.get("io_map"), model, scene)
    if not ok:
        raise RuntimeControlError("一致性检查失败：" + "；".join(problems))
    problems = validate_scene_outputs(scene, spec["io_list"], SceneSpecGenerator(library).registry)
    if problems:
        raise RuntimeControlError("场景检查失败：" + "；".join(problems))
    from .position_control import position_io,position_program_problems
    if position_io(model):
        table={p['name']:p['address'] for p in model['io_points'] if p.get('address')}
        problems=position_program_problems(xml.read_text(encoding='utf-8'),table,spec.get('prog_id',2),spec['io_list'])
        if problems:raise RuntimeControlError('位置输出检查失败：'+'；'.join(problems))
    return xml.read_bytes()
