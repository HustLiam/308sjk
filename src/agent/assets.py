"""工作台的不可变基础资产：AML、组件库（契约 + 物理参数）、SVG。"""
import copy
import hashlib
import json
import math
import re
import tempfile
from pathlib import Path

from .aml_parser import AMLParseError, parse_aml
from .config import PROJECT_ROOT
from .requirement import build_io_list
from .scene_gen import SceneSpecGenerator
from runtime.mujoco_build import build_mjcf
from runtime.physics_assets import load_physics, validate_physics

LIBRARY_FORMAT = "308sjk.component-library.v1"
BUILTIN_CONTRACT = PROJECT_ROOT / "contract/components.v1.1.json"
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_EXT = {"aml": ".aml", "library": ".json", "svg": ".svg"}


def read_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("JSON 存在重复字段：%s" % key)
            result[key] = value
        return result
    try:
        value = json.loads(data.decode("utf-8-sig"), object_pairs_hook=pairs,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError("JSON 不允许非有限数值")))
    except (UnicodeError, ValueError) as exc:
        raise ValueError("资产 JSON 无法解析：%s" % exc) from exc
    if not isinstance(value, dict):
        raise ValueError("资产 JSON 必须是对象")
    return value


def validate_contract(contract):
    """允许替换已实现接口的参数规则；新增接口需有对应后端实现。"""
    baseline = json.loads(BUILTIN_CONTRACT.read_text(encoding="utf-8"))
    if not isinstance(contract, dict) or contract.get("contract_version") != baseline["contract_version"]:
        raise ValueError("当前构建器支持组件接口契约 v%s" % baseline["contract_version"])
    types = contract.get("types")
    if not isinstance(types, dict) or not types or not set(types) <= set(baseline["types"]):
        raise ValueError("组件库包含尚无后端实现的组件类型，或类型表为空")
    if "gantry_xyz" not in types:
        raise ValueError("当前绘图场景生成器需要 gantry_xyz 组件")
    for name, definition in types.items():
        original = baseline["types"][name]
        if not isinstance(definition, dict):
            raise ValueError("组件 %s 定义必须是对象" % name)
        for key in ("quantities", "backends"):
            if definition.get(key) != original.get(key):
                raise ValueError("组件 %s 的 %s 与现有后端接口不兼容" % (name, key))
        params = definition.get("params")
        if not isinstance(params, list) or not all(isinstance(p, dict) for p in params):
            raise ValueError("组件 %s 参数表必须是对象数组" % name)
        expected = {p["name"]: p for p in original["params"]}
        if (len(params) != len(expected) or {p.get("name") for p in params} != set(expected)):
            raise ValueError("组件 %s 参数名称与现有后端不兼容" % name)
        for parameter in params:
            base = expected[parameter["name"]]
            if set(parameter)-set(base):
                raise ValueError("组件参数包含未支持字段")
            for key in ("kind", "required", "values"):
                if parameter.get(key) != base.get(key):
                    raise ValueError("组件 %s 参数 %s 的 %s 不兼容" % (name, parameter["name"], key))
            for key in ("minimum", "maximum"):
                value = parameter.get(key)
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value)):
                    raise ValueError("组件参数上下界必须是有限数值")
            lo, hi = parameter.get("minimum"), parameter.get("maximum")
            if lo is not None and hi is not None and lo >= hi:
                raise ValueError("组件参数下界必须小于上界")
            if "exclusive_min" in parameter and not isinstance(parameter["exclusive_min"], bool):
                raise ValueError("exclusive_min 必须是布尔值")
    return copy.deepcopy(contract)


def validate_library(data):
    if not isinstance(data, dict) or data.get("format") != LIBRARY_FORMAT:
        raise ValueError("请导入工作台导出的组件库 JSON（format=%s）" % LIBRARY_FORMAT)
    if set(data)-{"format", "name", "version", "contract", "physics"}:
        raise ValueError("组件库包含未支持字段")
    for key in ("name", "version"):
        if not isinstance(data.get(key), str) or not 0 < len(data[key]) <= 180:
            raise ValueError("组件库 %s 应为 1..180 字符" % key)
    data = copy.deepcopy(data)
    data["contract"] = validate_contract(data.get("contract"))
    data["physics"] = validate_physics(data.get("physics"))
    return data


def validate_pair(model, library):
    io_list, pending = build_io_list(model)
    if pending:
        raise ValueError("AML IO 信息不完整："+"；".join(pending))
    scene = SceneSpecGenerator(library["contract"]).generate(
        {"task_id": "asset_check", "io_list": io_list}, model)["scene"]
    return build_mjcf(scene, library["physics"])


class AssetStore:
    def __init__(self, root, aml):
        self.root = Path(root)/"assets"
        self.root.mkdir(parents=True, exist_ok=True)
        aml_data = Path(aml).read_bytes()
        self.default_aml, self.default_model = self.import_aml(Path(aml).name, aml_data)
        library = {"format": LIBRARY_FORMAT, "name": "内置运动组件库", "version": "1.0",
                   "contract": json.loads(BUILTIN_CONTRACT.read_text(encoding="utf-8")),
                   "physics": load_physics()}
        validate_pair(self.default_model, library)
        self.default_library = self.put("library", "component-library.json", self.encode(library))
        self.default_library["parts"] = {"contract":"components.v1.1.json", "physics":"physics_defaults.json"}

    @staticmethod
    def encode(data):
        return json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")

    def put(self, kind, name, data):
        if kind not in _EXT or not isinstance(data, bytes) or not 0 < len(data) <= 2*1024*1024:
            raise ValueError("资产须非空且不超过 2 MiB")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("资产文件名不能为空")
        digest = hashlib.sha256(data).hexdigest()
        path = self.path(kind, digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        # 内容寻址，历史资产永不覆盖；同进程写入由工作台锁串行化。
        if not path.exists():
            with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
                handle.write(data)
                temporary = Path(handle.name)
            temporary.replace(path)
        return {"kind":kind, "id":digest, "sha256":digest, "bytes":len(data),
                "name":name.replace("\\", "/").split("/")[-1][:180]}

    def path(self, kind, digest):
        if kind not in _EXT or not isinstance(digest, str) or not _HASH.fullmatch(digest):
            raise ValueError("资产标识无效")
        return self.root/kind/digest/("source"+_EXT[kind])

    def bytes(self, record):
        data = self.path(record["kind"], record["id"]).read_bytes()
        if hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError("保存的资产校验值不一致，请重新导入")
        return data

    def import_aml(self, name, data):
        if not isinstance(name, str) or not name.lower().endswith(".aml"):
            raise ValueError("请选择 .aml 设备描述文件")
        if not isinstance(data, bytes) or not 0 < len(data) <= 2*1024*1024:
            raise ValueError("AML 文件须非空且不超过 2 MiB")
        upper = data.replace(b"\x00", b"").upper()
        if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
            raise ValueError("AML 不支持 DTD 或实体定义")
        # 解析候选文件后才进入资产库，失败不改变当前选择。
        with tempfile.NamedTemporaryFile(suffix=".aml", dir=self.root, delete=False) as handle:
            handle.write(data)
            temporary = Path(handle.name)
        try:
            try:
                model, problems = parse_aml(temporary)
            except AMLParseError as exc:
                raise ValueError("AML 解析失败：%s" % exc) from exc
        finally:
            temporary.unlink(missing_ok=True)
        if problems:
            raise ValueError("AML 校验失败："+"；".join(problems))
        model["source"]["file"] = name.replace("\\", "/").split("/")[-1][:180]
        axes = {a["axis"].removesuffix("_axis").lower():a for a in model.get("kinematics", {}).get("axes", [])}
        if not {"x", "y", "z"} <= set(axes):
            raise ValueError("SVG 绘图工作台需要含 X/Y/Z 三轴的 AML")
        for axis in axes.values():
            if axis.get("unit") not in ("%", "mm"):
                raise ValueError("当前场景构建器支持轴单位 % 或 mm")
            for value in axis.get("stroke", []) + list((axis.get("limits") or {}).values()):
                if not isinstance(value, (int,float)) or isinstance(value, bool) or not math.isfinite(value):
                    raise ValueError("AML 轴参数必须为有限数值")
        return self.put("aml", name, data), model

    def library(self, record):
        return validate_library(read_json(self.bytes(record)))

    def summary(self, assets, model):
        library = self.library(assets["library"])
        result = copy.deepcopy(assets)
        for record in result.values():
            if record:
                record["url"] = "/asset/"+record["kind"]+"/"+record["id"]+"/source"
        result["aml"].update(station=model["station"], devices=len(model.get("devices", [])),
                             axes=model.get("kinematics", {}).get("axes", []), io_points=model.get("io_points", []))
        result["library"].update(title=library["name"], version=library["version"], contract=library["contract"], physics=library["physics"],
            contract_url="/asset/library/"+assets["library"]["id"]+"/contract",
            physics_url="/asset/library/"+assets["library"]["id"]+"/physics")
        return result
