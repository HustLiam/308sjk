#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
轨迹规划模块（轨迹参数化路线的确定性核心：图形参数 → 序列器步表）。

分工红线：LLM 只负责①识别形状与抽取用户显式数字、②按步表写 ST 代码；
几何计算与路点生成全部在本模块确定性完成（零幻觉面）。产物 trajectory
注入生成 prompt 作为权威约束——序列器步表必须精确实现这些路点，
坐标不得改动、不得自行发明几何。

站标准几何（与验收脚本 scenario_plotter3axis / scenario_plotter_circle
的硬编码口径逐字对齐——非标准参数可生成，但验收按标准几何判定）：
  · 方形：中心 (50,50)、边长 60（即 20..80），四边闭合，完成回中心
  · 圆：圆心 (50,50)、半径 25、24 段折线，起点 (50,25)（圆心正下方），
    整圆闭合后抬笔回圆心

用法:
    from agent.trajectory import plan_with_confirm, summarize_for_prompt
    traj = plan_with_confirm("circle", {"radius": 25})          # 缺参取默认
    traj = plan_with_confirm("square", {}, confirm=ask_user)    # 交互确认
    print(summarize_for_prompt(traj))                           # prompt 注入文本
"""

import math

# 站标准默认参数（缺参确认环节的建议值）
DEFAULTS = {
    "square": {"center": [50, 50], "size": 60},
    "circle": {"center": [50, 50], "radius": 25, "segments": 24},
}

PEN_Z = {"up": 10, "down": 0}                  # 笔行程 z 0..10（10=抬笔，0=落笔）
VMAX = {"xy": 40, "z": 20}                     # 30s 验收时限内的安全速度
AREA = {"x": (0, 100), "y": (0, 100), "z": (0, 10)}   # 行程域（路点合法性）


def _pt(x, y, z, name):
    for axis, v in zip("xyz", (x, y, z)):
        lo, hi = AREA[axis]
        if not (lo <= v <= hi):
            raise ValueError("路点 %s=(%.1f,%.1f,%.1f) 的 %s 轴超出行程 %s..%s"
                             % (name, x, y, z, axis, lo, hi))
    return [round(float(v), 1) for v in (x, y, z)]


def _step(no, action, to=None, note=None, **extra):
    s = {"no": no, "action": action}
    if to is not None:
        s["to"] = to
    if note:
        s["note"] = note
    s.update(extra)
    return s


def plan_square(center=None, size=None):
    """方形轨迹：抬笔定位起点 → 落笔 → 四边闭合 → 抬笔 → 回中心。"""
    center = center or DEFAULTS["square"]["center"]
    size = size if size is not None else DEFAULTS["square"]["size"]
    cx, cy = center
    half = size / 2.0
    x0, x1, y0, y1 = cx - half, cx + half, cy - half, cy + half
    steps = [
        _step(1, "pen_up_move", _pt(x0, y0, PEN_Z["up"], "定位起点"), note="抬笔定位"),
        _step(2, "pen_down_move", _pt(x0, y0, PEN_Z["down"], "落笔"), note="Z 下探落笔"),
        _step(3, "move", _pt(x1, y0, PEN_Z["down"], "边1"), note="沿 +X"),
        _step(4, "move", _pt(x1, y1, PEN_Z["down"], "边2"), note="沿 +Y"),
        _step(5, "move", _pt(x0, y1, PEN_Z["down"], "边3"), note="沿 -X"),
        _step(6, "move", _pt(x0, y0, PEN_Z["down"], "闭合"), note="沿 -Y 闭合回起点"),
        _step(7, "pen_up_move", _pt(x0, y0, PEN_Z["up"], "抬笔"), note="Z 抬起"),
        _step(8, "pen_up_move", _pt(cx, cy, PEN_Z["up"], "回中心"), note="完成位"),
    ]
    return {"shape": "square",
            "params": {"center": [cx, cy], "size": size},
            "pen_z": dict(PEN_Z), "vmax": dict(VMAX), "steps": steps}


def plan_circle(center=None, radius=None, segments=None):
    """圆轨迹：抬笔定位圆周起点 → 落笔 → N 段折线整圆 → 抬笔 → 回圆心。

    折线公式（k=0..N-1，从起点出发顺时针一周闭合）：
      x = cx + r*SIN(k*2π/N)，y = cy - r*COS(k*2π/N)
      （k=0 即起点 (cx, cy-r)；waypoints 同时给出全表，LLM 可逐点步进
        或按计数器公式实现，两者几何等价）
    """
    center = center or DEFAULTS["circle"]["center"]
    radius = radius if radius is not None else DEFAULTS["circle"]["radius"]
    segments = segments or DEFAULTS["circle"]["segments"]
    if segments < 8:
        raise ValueError("圆折线段数 %d 过少（≥8 才能保证轨迹判定）" % segments)
    cx, cy = center
    wp = []
    for k in range(segments):
        a = 2.0 * math.pi * k / segments
        wp.append(_pt(cx + radius * math.sin(a), cy - radius * math.cos(a),
                      PEN_Z["down"], "圆折线 k=%d" % k))
    formula = ("x = %g + %g*SIN(k*2π/%d)，y = %g - %g*COS(k*2π/%d)（k=0..%d，"
               "每步只改 X/Y 目标，z 恒 0；走完 N 段即闭合回起点）"
               % (cx, radius, segments, cy, radius, segments, segments - 1))
    steps = [
        _step(1, "pen_up_move", _pt(cx, cy - radius, PEN_Z["up"], "定位起点"),
              note="抬笔定位到圆周起点（圆心正下方）"),
        _step(2, "pen_down_move", _pt(cx, cy - radius, PEN_Z["down"], "落笔"),
              note="Z 下探落笔"),
        _step(3, "circle_sweep", to=wp[-1], note="N 段折线整圆（waypoints 全表见下）",
              formula=formula, waypoints=wp),
        _step(4, "pen_up_move", _pt(cx, cy - radius, PEN_Z["up"], "抬笔"), note="Z 抬起"),
        _step(5, "pen_up_move", _pt(cx, cy, PEN_Z["up"], "回圆心"), note="完成位"),
    ]
    return {"shape": "circle",
            "params": {"center": [cx, cy], "radius": radius, "segments": segments},
            "pen_z": dict(PEN_Z), "vmax": dict(VMAX), "steps": steps}


_PLANNERS = {"square": plan_square, "circle": plan_circle}


def missing_params(shape, user_params):
    """用户参数缺哪些必填项（用于确认问题生成）。"""
    user_params = user_params or {}
    if shape == "square":
        keys = ["center", "size"]
    elif shape == "circle":
        keys = ["center", "radius", "segments"]
    else:
        raise ValueError("暂不支持图形 %r（当前：square/circle）" % shape)
    return [k for k in keys if k not in user_params]


def plan_with_confirm(shape, user_params=None, confirm=None):
    """缺参确认 → 轨迹规划。

    confirm(questions) → {key: value}：交互回调（CLI 终端 input / chat 对话）；
    None（默认）= 非交互模式，全部缺参直接采纳站标准默认值（自动化测试口径）。
    """
    params = dict(user_params or {})
    missing = missing_params(shape, params)
    if missing and confirm is not None:
        questions = [{"key": k, "default": DEFAULTS[shape].get(k),
                      "question": _QUESTION.get(k, k)} for k in missing]
        answers = confirm(questions) or {}
        params.update({k: v for k, v in answers.items() if v is not None})
    for k in missing:  # 仍缺（无回调或回调没答）→ 站标准默认
        if k not in params:
            params[k] = DEFAULTS[shape][k]
    params = {k: params[k] for k in DEFAULTS[shape]}   # 键序规整
    return _PLANNERS[shape](**params)


_QUESTION = {
    "center": "绘图中心坐标（格式 x,y；默认 50,50）",
    "size": "正方形边长（默认 60，即 20..80；验收按此标准几何）",
    "radius": "圆半径（默认 25，即 25..75；验收按此标准几何）",
    "segments": "圆折线段数（默认 24）",
}


def summarize_for_prompt(traj):
    """轨迹 → 生成 prompt 注入文本（权威步表；LLM 不得改坐标/发明几何）。"""
    p = traj["params"]
    if traj["shape"] == "square":
        head = "正方形：中心 (%g,%g)、边长 %g" % (p["center"][0], p["center"][1], p["size"])
    else:
        head = "圆：圆心 (%g,%g)、半径 %g、%d 段折线" % (
            p["center"][0], p["center"][1], p["radius"], p["segments"])
    lines = [
        "## 轨迹规划参数（权威约束——序列器步表必须精确实现，坐标一个都不能改）",
        "图形：%s" % head,
        "笔位：up=%d（抬笔）down=%d（落笔）；速度上限 XY≤%d、Z≤%d" % (
            traj["pen_z"]["up"], traj["pen_z"]["down"], traj["vmax"]["xy"], traj["vmax"]["z"]),
        "步表（步号→动作→目标 (x,y,z)）：",
    ]
    for s in traj["steps"]:
        to = s.get("to")
        lines.append("  步%d %s → %s%s" % (
            s["no"], s["action"],
            "(%g,%g,%g)" % tuple(to) if to else "（见下）",
            "（%s）" % s["note"] if s.get("note") else ""))
        if s["action"] == "circle_sweep":
            lines.append("    折线公式：%s" % s["formula"])
            lines.append("    waypoints 全表（依次走完，末点闭合回起点）：")
            for k, w in enumerate(s["waypoints"]):
                lines.append("      k=%02d (%g,%g,%g)" % (k, w[0], w[1], w[2]))
    lines.append("完成语义：最后一步到位且全部插补空闲 → plot_done := TRUE；"
                 "急停/故障 → 序列清零且 plot_done := FALSE。")
    return "\n".join(lines)


def goal_text(traj):
    """轨迹 → 需求理解增强文本（拼进 request，让 spec 的 task_goal/acceptance 与几何一致）。"""
    p = traj["params"]
    if traj["shape"] == "square":
        c, sz = p["center"], p["size"]
        return ("画正方形：中心 (%d,%d)、边长 %d（%d..%d）；抬笔定位起点 → 落笔沿四边"
                "画完整方形并闭合 → 抬笔回中心 (%d,%d,10)"
                % (c[0], c[1], sz, c[0] - sz // 2, c[0] + sz // 2, c[0], c[1]))
    c, r, n = p["center"], p["radius"], p["segments"]
    return ("画整圆：圆心 (%d,%d)、半径 %d；抬笔定位圆周起点 (%d,%d) → 落笔沿 %d 段"
            "折线画整圆闭合 → 抬笔回圆心 (%d,%d,10)"
            % (c[0], c[1], r, c[0], c[1] - r, n, c[0], c[1]))
