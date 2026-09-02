"""agent 命令行：需求规格 → SceneSpec →（可选）USD 构建。

用法：
  python -m scenegen.agent.cli gen requirement.json -o outdir [--mock] [--no-build]
环境变量（真实 LLM）：SCENEGEN_LLM_BASE_URL / SCENEGEN_LLM_API_KEY / SCENEGEN_LLM_MODEL
"""

import argparse
import json
import os
import sys

from .. import build_usd, smoke
from .core import SceneGenAgent
from .llm import MockLLM, OpenAICompatLLM, from_env


def cmd_gen(args) -> int:
    with open(args.requirement, encoding="utf-8") as f:
        requirement = json.load(f)

    llm = MockLLM() if args.mock else from_env()
    if not args.mock and isinstance(llm, MockLLM):
        print("（未配置 SCENEGEN_LLM_* 环境变量，退回离线 Mock 生成器）")

    agent = SceneGenAgent(llm, max_retries=args.max_retries)
    spec, report = agent.generate(requirement)

    os.makedirs(args.outdir, exist_ok=True)
    with open(os.path.join(args.outdir, "gen_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    if spec is None:
        print(f"FAIL：{report['attempts']} 次尝试均未通过（报告 {args.outdir}/gen_report.json）")
        for e in report.get("best_errors", [])[:10]:
            print(f"  - {e}")
        return 1

    spec_path = os.path.join(args.outdir, "scene.spec.json")
    with open(spec_path, "w", encoding="utf-8") as f:
        json.dump(spec, f, ensure_ascii=False, indent=2)
    print(f"OK：第 {report['attempts']} 次尝试通过校验 → {spec_path}")

    if args.no_build:
        return 0
    result = build_usd.build(spec, args.outdir)
    with open(os.path.join(args.outdir, "io_map.json"), "w", encoding="utf-8") as f:
        json.dump(result["io_map"], f, ensure_ascii=False, indent=2)
    issues = smoke.structural_check(result["scene_usd"], result["io_map"])
    if issues:
        print(f"WARN：结构冒烟 {len(issues)} 处问题")
        for i in issues:
            print(f"  - {i}")
        return 1
    print(f"OK：{result['scene_usd']} 构建并通过结构冒烟（io_map.json / st_io_declaration.st 已同步生成）")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="scenegen.agent", description="需求规格 → SceneSpec → USD")
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("gen", help="由 requirement_spec 生成场景")
    p.add_argument("requirement")
    p.add_argument("-o", "--outdir", default="out/agent")
    p.add_argument("--mock", action="store_true", help="强制使用离线 Mock 生成器")
    p.add_argument("--no-build", action="store_true", help="只生成 SceneSpec，不构建 USD")
    p.add_argument("--max-retries", type=int, default=4)
    p.set_defaults(func=cmd_gen)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
