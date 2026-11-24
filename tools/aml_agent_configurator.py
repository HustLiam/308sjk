#!/usr/bin/env python
"""标准 AML 配置器 · Agent 前置层（L2）：输入任意子集 → 审查问答 → 补充 → 调固定组装器。

三层架构（负责人确认的流程）：
  L1 确定性快查（本文件 _quick_check，毫秒级）：文件存在/清单完备/树与 io 交叉引用/
     单位未知/位姿缺失——硬错误直接拒，不进 LLM；
  L2 Agent 审查+交互（本文件）：把 L1 findings + 文件摘要交 LLM（.env 的 deepseek 通道，
     无 key/失败自动降级 Mock 规则引擎），产出**结构化问题清单** questions.json；
     CLI 逐问交互（--batch 全取默认），用户回答落 supplement.json；
  L3 固定组装器（tools/aml_configurator.py）：吃全部结构化文件 → 标准 AML + XSD + ⓪回读自检。

设计约束（与项目"LLM 不写产物文本"同构）：LLM 只产出问题与建议选项，
一切结论经用户确认后落 json，组装仍是确定性代码——同输入同输出。

用法（交互）：
  python tools/aml_agent_configurator.py --parts-dir my_parts --io my_io.json -o std.aml
用法（批处理，全取默认，供测试/CI）：
  python tools/aml_agent_configurator.py ... --batch
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

QUESTIONS_SCHEMA_HINT = (
    "你是工业 AML 数据审查员。根据输入清单与发现，产出缺失/歧义问题列表（JSON 数组），"
    "每条：{id, ask(中文问题), options(可选答案数组，可空), default(默认值，尽量给), "
    "apply(应用目标: geometry_unit:{组件id} | default_pose:{组件id} | io_component:{信号名} | "
    "tree_infer:按几何文件名生成平铺关系树 | param:{adapter|motion|requirements}:{字段路径})}。"
    "只问真正缺失或歧义的；能从上下文推断的给 default 不问。"
    "答案/default 必须是机器可直接使用的原子值：io_component 答=关系树中的组件 id 原文"
    "（如 column）；geometry_unit 答 mm 或 m；是非题答 是 或 否；param:default 答 是。"
    "禁止把解释性文字放进答案或 default。输出纯 JSON。"
)


# ================================================================ L1 快查

def _quick_check(inputs, parts):
    """确定性检查：返回 (errors, findings)。errors 阻断；findings 供 LLM 出题。"""
    errors, findings = [], []

    if not inputs.get("io"):
        errors.append("缺 --io（io.json 为必需输入）")
        return errors, findings
    io_items = json.load(open(inputs["io"], encoding="utf-8"))["io_list"]

    if inputs.get("parts_dir"):
        if not parts["tree"]:
            # 任意子集容忍：无关系树 → 交 L2 出题（可按几何文件名推断平铺结构）
            findings.append({"kind": "tree_missing",
                             "detail": "可按几何文件名推断平铺组件结构"})
        ids = {n["id"] for n in parts["tree"]}
        for j in parts["joints"]:
            if j.get("component") not in ids:
                errors.append(f"joints[{j.get('name')}]: component {j.get('component')} 不在 tree")
        for it in io_items:
            cid = it.get("component_id")
            if cid and cid not in ids:
                findings.append({"kind": "io_component_mismatch", "signal": it["name"],
                                 "detail": f"component_id {cid} 不在关系树（需映射或补组件）"})
        # 位姿缺失 / 几何缺失 / 单位未知 → 交给 LLM 出题（非硬错）
        have_pose = set(parts["poses"])
        have_geo = set(parts["geometry"])
        for n in parts["tree"]:
            if n["id"] not in have_pose:
                findings.append({"kind": "pose_missing", "component": n["id"]})
            if n["id"] not in have_geo and not n.get("parent"):
                findings.append({"kind": "geometry_missing", "component": n["id"],
                                 "detail": "根组件通常需要几何（可选）"})
        for cid, g in parts["geometry"].items():
            if g.get("unit") not in ("mm", "m"):
                findings.append({"kind": "unit_unknown", "component": cid,
                                 "detail": f"unit={g.get('unit')!r}"})
    elif inputs.get("aml"):
        if not os.path.isfile(inputs["aml"]):
            errors.append(f"基座 AML 不存在: {inputs['aml']}")

    for k, f in (("adapter", "adapter.json"), ("motion", "motion.json"),
                 ("requirements", "requirements.json")):
        if not inputs.get(k):
            findings.append({"kind": "optional_missing", "file": f})
    return errors, findings


# ================================================================ L2 LLM / Mock

def _llm_questions(summary, findings, timeout=60):
    """真实 LLM（.env deepseek 通道）。失败/无 key 抛异常由调用方降级 Mock。"""
    import requests
    url = os.environ.get("LLM_API_URL", "https://api.deepseek.com").rstrip("/")
    key = os.environ.get("DEEPSEEK_API_KEY", "")
    envf = os.path.join(REPO, ".env")
    if not key and os.path.isfile(envf):
        for ln in open(envf, encoding="utf-8"):
            if ln.startswith("DEEPSEEK_API_KEY="):
                key = ln.split("=", 1)[1].strip()
    if not key:
        raise RuntimeError("无 DEEPSEEK_API_KEY")
    model = os.environ.get("LLM_MODEL", "deepseek-flash")
    payload = {"model": model, "temperature": 0.1,
               "messages": [{"role": "system", "content": QUESTIONS_SCHEMA_HINT},
                            {"role": "user", "content": json.dumps(
                                {"inputs": summary, "findings": findings},
                                ensure_ascii=False)}]}
    r = requests.post(f"{url}/chat/completions",
                      headers={"Authorization": f"Bearer {key}"}, json=payload, timeout=timeout)
    r.raise_for_status()
    text = r.json()["choices"][0]["message"]["content"].strip()
    if text.startswith("```"):
        text = text.split("```")[1].lstrip("json")
    return json.loads(text)


def _mock_questions(findings):
    """Mock 规则引擎：findings → 同结构问题（离线可全链）。"""
    qs = []
    for i, f in enumerate(findings):
        if f["kind"] == "tree_missing":
            qs.append({"id": f"q{i}",
                       "ask": "未提供关系树 tree.json：按几何文件名自动生成平铺结构"
                              "（每个 STL/DAE 一个组件，组件 id=文件名主干）？",
                       "options": ["是", "否"], "default": "是", "apply": "tree_infer"})
        elif f["kind"] == "unit_unknown":
            qs.append({"id": f"q{i}", "ask": f"组件 {f['component']} 的几何单位是？",
                       "options": ["mm", "m"], "default": "mm",
                       "apply": f"geometry_unit:{f['component']}"})
        elif f["kind"] == "pose_missing":
            qs.append({"id": f"q{i}", "ask": f"组件 {f['component']} 缺位姿，用默认原点(0,0,0)？",
                       "options": ["是", "否"], "default": "是",
                       "apply": f"default_pose:{f['component']}"})
        elif f["kind"] == "io_component_mismatch":
            qs.append({"id": f"q{i}",
                       "ask": f"信号 {f['signal']} 的 component_id 需映射到关系树哪个组件？",
                       "options": [], "default": "", "apply": f"io_component:{f['signal']}"})
        elif f["kind"] == "optional_missing":
            qs.append({"id": f"q{i}", "ask": f"缺 {f['file']}，用内置默认值？",
                       "options": ["是", "否"], "default": "是", "apply": f"param:{f['file']}:default"})
        elif f["kind"] == "geometry_missing":
            pass      # 根组件几何可选，不出题
    return qs


# ================================================================ 问答与应用

def _ask(questions, batch=False):
    print("\n—— 审查问答（%d 个问题）——" % len(questions))
    answers = {}
    for q in questions:
        opt = f"  选项: {q.get('options')}" if q.get("options") else ""
        print(f"\n[{q['id']}] {q['ask']}{opt}\n  默认: {q.get('default')!r}  (apply: {q.get('apply')})")
        if batch:
            ans = q.get("default")
            print(f"  → (batch) {ans!r}")
        else:
            try:
                ans = input("  回答（回车=默认）: ").strip() or q.get("default")
            except EOFError:
                ans = q.get("default")
        answers[q["id"]] = {"question": q["ask"], "apply": q.get("apply"), "answer": ans}
    return answers


def _apply_supplement(work_dir, answers, io_path):
    """把回答应用到工作副本（parts 四件 + io.json），组装器吃工作副本。"""
    import aml_parts  # noqa: F401
    applied = []

    def load(p, default):
        return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else default

    def save(p, obj, name):
        json.dump(obj, open(os.path.join(work_dir, name), "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)

    geo = load(os.path.join(work_dir, "geometry.json"), {})
    poses = load(os.path.join(work_dir, "poses.json"), {})
    tree = load(os.path.join(work_dir, "tree.json"), [])
    if not tree:                          # tree_infer 兜底：目录内几何文件 → 平铺树
        ids = []
        for f in sorted(os.listdir(work_dir)):
            if f.lower().endswith((".stl", ".dae", ".obj")):
                ids.append(os.path.splitext(f)[0])
        if not geo:
            geo = {i: {"file": f"{i}.stl", "unit": "m"} for i in ids}
        tree = [{"id": i, "name": i, "parent": None} for i in ids]
        save(work_dir, tree, "tree.json")
        save(work_dir, geo, "geometry.json")
        applied.append(f"tree_infer: {len(tree)} 组件（平铺）")
    io = json.load(open(io_path, encoding="utf-8"))
    for a in answers.values():
        ans, apply = a["answer"], a.get("apply") or ""
        if apply.startswith("geometry_unit:") and ans in ("mm", "m"):
            geo.setdefault(apply.split(":", 1)[1], {})["unit"] = ans
            applied.append(f"geometry[{apply.split(':',1)[1]}].unit={ans}")
        elif apply.startswith("default_pose:") and ans in ("是", "yes", "true", "True"):
            poses.setdefault(apply.split(":", 1)[1], {"pos": [0, 0, 0], "rpy_deg": [0, 0, 0]})
            applied.append(f"poses[{apply.split(':',1)[1]}]=origin")
        elif apply.startswith("io_component:") and ans:
            name = apply.split(":", 1)[1]
            # 防御：LLM/用户答案若含解释文字，从中提取已知组件 id（树 + 几何文件名主干）
            known = {n["id"] for n in tree} if tree else set()
            if not tree:
                known |= {os.path.splitext(f)[0] for f in os.listdir(work_dir)
                          if f.lower().endswith((".stl", ".dae", ".obj"))}
            cand = next((k for k in sorted(known, key=len, reverse=True)
                         if k and k in str(ans)), None)
            val = cand if cand else (ans if ans in known else None)
            if val:
                for it in io["io_list"]:
                    if it["name"] == name:
                        it["component_id"] = val
                        applied.append(f"io[{name}].component_id={val}"
                                       + ("（从答案提取）" if cand and cand != ans else ""))
            else:
                applied.append(f"io[{name}]:component_id 答案 {ans!r} 无法解析为已知组件，未应用")
    save(work_dir, geo, "geometry.json")
    save(work_dir, poses, "poses.json")
    io_out = os.path.join(work_dir, "io.json")
    json.dump(io, open(io_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return io_out, applied


# ================================================================ main

def main(argv=None):
    p = argparse.ArgumentParser(description="Agent 前置层：审查问答 → 补充 → 调组装器")
    p.add_argument("--aml", help="打包基座 AML")
    p.add_argument("--parts-dir", dest="parts_dir", help="散装四件目录")
    p.add_argument("--io", required=True)
    p.add_argument("--adapter"); p.add_argument("--motion"); p.add_argument("--requirements")
    p.add_argument("-o", "--out", required=True)
    p.add_argument("--batch", action="store_true", help="非交互：全部取默认（测试/CI）")
    p.add_argument("--mock", action="store_true", help="强制 Mock（不用 LLM）")
    p.add_argument("--keep-work", help="保留工作目录（调试）")
    args = p.parse_args(argv)
    if not (args.aml or args.parts_dir):
        p.error("--aml 与 --parts-dir 二选一")

    sys.path.insert(0, HERE)
    parts = {"tree": [], "geometry": {}, "poses": {}, "joints": []}
    if args.parts_dir:
        from aml_parts import load_parts
        parts = load_parts(args.parts_dir)

    # L1
    errors, findings = _quick_check(vars(args), parts)
    if errors:
        print("FAIL：L1 确定性审查未通过：")
        for e in errors:
            print("  -", e)
        return 1
    print(f"L1 快查通过（findings {len(findings)} 项）")

    # L2：LLM 出题（降级 Mock）
    summary = {"aml": args.aml, "parts_dir": args.parts_dir,
               "tree_nodes": len(parts["tree"]), "joints": len(parts["joints"]),
               "io_count": len(json.load(open(args.io, encoding="utf-8"))["io_list"]),
               "optional_given": {k: bool(getattr(args, k))
                                  for k in ("adapter", "motion", "requirements")}}
    questions, used_llm = [], False
    if not args.mock:
        try:
            questions = _llm_questions(summary, findings)
            used_llm = True
            print("L2 审查：真实 LLM 通道")
        except Exception as e:                       # noqa: BLE001
            print(f"L2 审查：LLM 不可用（{type(e).__name__}: {str(e)[:80]}）→ 降级 Mock")
    if not used_llm:
        questions = _mock_questions(findings)
    print(f"    问题 {len(questions)} 个")

    # 问答 → supplement
    answers = _ask(questions, batch=args.batch)
    work = args.keep_work or tempfile.mkdtemp(prefix="aml_agent_")
    os.makedirs(work, exist_ok=True)
    if args.parts_dir:                    # 工作副本（不动用户原文件；几何文件一并复制）
        for f in os.listdir(args.parts_dir):
            src = os.path.join(args.parts_dir, f)
            if os.path.isfile(src):
                shutil.copy(src, os.path.join(work, f))
    io_work, applied = _apply_supplement(work, answers, args.io)
    json.dump({"questions": questions, "answers": answers, "applied": applied},
              open(os.path.join(work, "supplement.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print(f"\nsupplement 已应用 {len(applied)} 项 → {os.path.join(work, 'supplement.json')}")

    # L3：调固定组装器
    cmd = [sys.executable, os.path.join(HERE, "aml_configurator.py"),
           "--io", io_work, "-o", args.out]
    cmd += ["--parts-dir", work] if args.parts_dir else ["--aml", args.aml]
    for k in ("adapter", "motion", "requirements"):
        if getattr(args, k):
            cmd += [f"--{k}", getattr(args, k)]
    r = subprocess.run(cmd)
    return r.returncode


if __name__ == "__main__":
    sys.exit(main())
