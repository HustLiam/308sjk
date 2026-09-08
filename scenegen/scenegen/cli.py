"""命令行入口。

用法：
  python -m scenegen.cli validate <spec.json>
  python -m scenegen.cli build <spec.json> -o <outdir>
  python -m scenegen.cli all <spec.json> -o <outdir>      # validate + build + 结构冒烟
  python -m scenegen.cli build-mjcf <spec.json> -o <outdir>  # validate + io_map 分配 + MJCF
  python -m scenegen.cli components [--json] [-o FILE]    # 组件契约导出（表格 / JSON）
"""

import argparse
import json
import os
import sys

from . import build_usd, components, iomap, smoke
from .validate import validate


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _mjcf_module():
    """跨包引入 runtime/mujoco_build（MJCF 组装器；无 pxr 依赖）。"""
    here = os.path.dirname(os.path.abspath(__file__))
    runtime = os.path.normpath(os.path.join(here, "..", "..", "runtime"))
    if runtime not in sys.path:
        sys.path.insert(0, runtime)
    import mujoco_build
    return mujoco_build


def cmd_validate(args) -> int:
    spec = _load(args.spec)
    errors = validate(spec)
    if errors:
        print(f"FAIL：{len(errors)} 处错误")
        for e in errors:
            print(f"  - {e}")
        return 1
    print(f"OK：{spec['scene_id']} 通过静态校验（{len(spec['assets'])} 资产 / {len(spec['io_map'])} IO）")
    return 0


def cmd_build(args) -> int:
    spec = _load(args.spec)
    errors = validate(spec)
    if errors:
        print("构建中止：spec 未通过校验")
        for e in errors:
            print(f"  - {e}")
        return 1
    result = build_usd.build(spec, args.outdir)
    io_map_path = f"{args.outdir}/io_map.json"
    with open(io_map_path, "w", encoding="utf-8") as f:
        json.dump(result["io_map"], f, ensure_ascii=False, indent=2)
    with open(f"{args.outdir}/st_io_declaration.st", "w", encoding="utf-8") as f:
        f.write(result["st_declaration"] + "\n")
    with open(f"{args.outdir}/modbus_summary.json", "w", encoding="utf-8") as f:
        json.dump(result["modbus_summary"], f, ensure_ascii=False, indent=2)

    print(f"OK：{result['scene_usd']}")
    print(f"     {io_map_path}")
    print("  Modbus 分配：")
    for e in result["io_map"]:
        m = e["modbus"]
        print(f"    {e['plc_var']:<16} {m['plc_addr']:<8} {m['area']:<18} {e['bind']['asset']}.{e['bind']['quantity']}")
    return 0


def cmd_all(args) -> int:
    rc = cmd_validate(args)
    if rc:
        return rc
    rc = cmd_build(args)
    if rc:
        return rc
    with open(f"{args.outdir}/io_map.json", encoding="utf-8") as f:
        io_map = json.load(f)
    issues = smoke.structural_check(f"{args.outdir}/scene.usda", io_map)
    if issues:
        print(f"FAIL：结构冒烟 {len(issues)} 处问题")
        for i in issues:
            print(f"  - {i}")
        return 1
    print("OK：结构冒烟通过（stage 可打开、prim 齐全、关节完整、无 NaN）")
    return 0


# ---------------- 组件契约导出（【csk 2026-09-08 新增】契约发布机制） ----------------

# USD 构建器为显式占位（raise NotImplementedError）的组件类型——契约中标注 USD 不可用
_USD_STUB_BUILDERS = ("_build_car", "_build_plotter_static")
_CONTRACT_VERSION = "1.1"


def _contract_json() -> dict:
    mjcf_types = set(_mjcf_module().MJCF_TYPES)
    types = {}
    for name in sorted(components.REGISTRY):
        cdef = components.REGISTRY[name]
        types[name] = {
            "backends": {
                "usd": cdef.build.__name__ not in _USD_STUB_BUILDERS,
                "mjcf": name in mjcf_types,
            },
            "quantities": [
                {"name": q.name, "direction": q.direction, "dtype": q.dtype}
                for q in cdef.quantities
            ],
            "params": [
                {"name": p.name, "kind": p.kind, "required": p.required,
                 "default": p.default,
                 **({"minimum": p.minimum} if p.minimum is not None else {}),
                 **({"maximum": p.maximum} if p.maximum is not None else {}),
                 **({"exclusive_min": True} if p.exclusive_min else {}),
                 **({"values": list(p.values)} if p.values else {})}
                for p in cdef.params
            ],
        }
    return {"contract_version": _CONTRACT_VERSION,
            "generated_from": "scenegen.components.REGISTRY",
            "direction_semantics": {"in": "指令进仿真（PLC 输出 → io_map.dir=output）",
                                    "out": "量测出仿真（PLC 输入 → io_map.dir=input）"},
            "types": types}


def _contract_md() -> str:
    c = _contract_json()
    lines = [f"# SceneSpec 组件契约表 v{_CONTRACT_VERSION}",
             "",
             f"> 由 `scenegen.components.REGISTRY` 生成（`python -m scenegen.cli components`）；"
             "参数/quantity 规则与 `cli validate` 闸门同源——**gc 生成 spec 前请对照本表**，"
             "未注册类型会被闸门拒绝。契约变更走 RFC（主方案 §8.3）。",
             "",
             "| 类型 | USD | MJCF | quantities | 参数 |",
             "|---|---|---|---|---|"]
    for name, t in c["types"].items():
        qs = "、".join(f"{q['name']}({q['direction']},{q['dtype']})" for q in t["quantities"]) or "—"
        lines.append(f"| `{name}` | {'✓' if t['backends']['usd'] else '✗'} | "
                     f"{'✓' if t['backends']['mjcf'] else '✗'} | {qs} | {len(t['params'])} |")
    lines.append("")
    for name, t in c["types"].items():
        lines += [f"## {name}", ""]
        if t["quantities"]:
            lines += ["| quantity | direction | dtype |", "|---|---|---|"]
            lines += [f"| `{q['name']}` | {q['direction']} | {q['dtype']} |" for q in t["quantities"]]
            lines.append("")
        if t["params"]:
            lines += ["| 参数 | 型 | 必填 | 默认 | 约束 |", "|---|---|---|---|---|"]
            for p in t["params"]:
                cons = []
                if "values" in p:
                    cons.append(f"枚举 {p['values']}")
                if "minimum" in p:
                    cons.append((">" if p.get("exclusive_min") else "≥") + str(p["minimum"]))
                if "maximum" in p:
                    cons.append("≤" + str(p["maximum"]))
                lines.append(f"| `{p['name']}` | {p['kind']} | {'是' if p['required'] else '否'} | "
                             f"{p['default'] if p['default'] is not None else '—'} | {'，'.join(cons) or '—'} |")
        lines.append("")
    return "\n".join(lines)


def cmd_components(args) -> int:
    content = json.dumps(_contract_json(), ensure_ascii=False, indent=2) if args.fmt == "json" \
        else _contract_md()
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(content + "\n")
        print(f"OK：组件契约（{args.fmt}）已写出 {args.out}"
              f"（{len(components.REGISTRY)} 类型，v{_CONTRACT_VERSION}）")
    else:
        print(content)
    return 0


# ---------------- MuJoCo 链一键组装（【csk 2026-09-08 新增】纯命令工作流） ----------------

def cmd_build_mjcf(args) -> int:
    """validate → io_map 地址分配 → MJCF 落盘。io_map 缺失/非法直接退出（不代拟）。"""
    spec = _load(args.spec)
    errors = validate(spec)
    if errors:
        print("构建中止：spec 未通过校验（io_map 等缺口请回 gc 侧补全，本命令不代拟）")
        for e in errors:
            print(f"  - {e}")
        return 1
    mb = _mjcf_module()
    xml = mb.build_mjcf(spec)
    os.makedirs(args.outdir, exist_ok=True)
    io = iomap.assign_modbus(spec["io_map"])
    with open(f"{args.outdir}/io_map.json", "w", encoding="utf-8") as f:
        json.dump(io, f, ensure_ascii=False, indent=2)
    with open(f"{args.outdir}/st_io_declaration.st", "w", encoding="utf-8") as f:
        f.write(iomap.st_io_declaration(io) + "\n")
    with open(f"{args.outdir}/modbus_summary.json", "w", encoding="utf-8") as f:
        json.dump(iomap.modbus_summary(io), f, ensure_ascii=False, indent=2)
    with open(f"{args.outdir}/scene.xml", "w", encoding="utf-8") as f:
        f.write(xml + "\n")
    with open(f"{args.outdir}/scene.spec.json", "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print(f"OK：{args.outdir}/scene.xml（MJCF）")
    print(f"     {args.outdir}/io_map.json / modbus_summary.json / st_io_declaration.st / scene.spec.json")
    print("  Modbus 分配：")
    for e in io:
        m = e["modbus"]
        print(f"    {e['plc_var']:<16} {m['plc_addr']:<8} {m['area']:<18} {e['bind']['asset']}.{e['bind']['quantity']}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scenegen", description="SceneSpec → 仿真环境生成（USD / MuJoCo）")
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate", help="静态校验 SceneSpec")
    p_validate.add_argument("spec")
    p_validate.set_defaults(func=cmd_validate)

    p_build = sub.add_parser("build", help="构建 scene.usda + io_map.json")
    p_build.add_argument("spec")
    p_build.add_argument("-o", "--outdir", default="out")
    p_build.set_defaults(func=cmd_build)

    p_all = sub.add_parser("all", help="validate + build + 结构冒烟")
    p_all.add_argument("spec")
    p_all.add_argument("-o", "--outdir", default="out")
    p_all.set_defaults(func=cmd_all)

    p_mjcf = sub.add_parser("build-mjcf", help="validate + io_map 分配 + MJCF（MuJoCo 链一键组装）")
    p_mjcf.add_argument("spec")
    p_mjcf.add_argument("-o", "--outdir", default="out")
    p_mjcf.set_defaults(func=cmd_build_mjcf)

    p_comp = sub.add_parser("components", help="导出组件契约（表格 / JSON，gc 生成 spec 的依据）")
    p_comp.add_argument("--json", dest="fmt", action="store_const", const="json", default="md")
    p_comp.add_argument("-o", "--out", default=None)
    p_comp.set_defaults(func=cmd_components)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
