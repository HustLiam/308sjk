"""命令行入口。

用法：
  python -m scenegen.cli validate <spec.json>
  python -m scenegen.cli build <spec.json> -o <outdir>
  python -m scenegen.cli all <spec.json> -o <outdir>      # validate + build + 结构冒烟
"""

import argparse
import json
import sys

from . import build_usd, smoke
from .validate import validate


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


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


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scenegen", description="SceneSpec → USD 仿真环境生成")
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

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
