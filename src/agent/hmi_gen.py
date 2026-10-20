# -*- coding: utf-8 -*-
"""HMI 描述产物生成器（2026-09-23 负责人设计：PLCopen XML 保持纯 61131-10
不变，HMI 与信号连接作为独立生成产物 hmi.json 交付，上位机解析渲染）。

确定性生成（非 LLM）：元素与信号连接全部由 spec.io_list 推导——
  · input BOOL  → BUTTON（type：trigger=按住有效/松开断开（io_list 的
    trigger:true 开跑刺激）；toggle=点击反转保持（自锁开关语义））
  · output BOOL → LAMP（状态指示灯，只读）
  · INT         → GAUGE（数值显示，只读；range 取 io_list 量程）
标签取 io_list 的 device 描述（去括号说明），地址逐字抄 io_list。

产物为任务级（随 spec 冻结，与迭代无关）：编排器每轮写入 iter_NNN/hmi.json，
final/ 随目录拷贝，runs/latest/ 由 _finalize 刷新。
"""


def build_hmi(spec):
    """spec → hmi 描述 dict（写入 hmi.json 的对象）。"""
    elements = []
    for e in spec.get("io_list", []):
        name, typ, d = e.get("name"), e.get("type"), e.get("dir")
        label = (e.get("device") or name).split("（")[0].strip() or name
        addr = e.get("address")
        if typ == "BOOL" and addr:
            if d == "input":
                # 按钮类型：trigger 标记 → 瞬动（按住有效）；但"使能"类主令
                # （device 描述含"使能"，如 run）是自锁开关语义 → toggle——
                # 使能要全程保持，且单鼠标无法同时按住两个按钮
                is_enable = "使能" in (e.get("device") or "")
                elements.append({"kind": "BUTTON", "signal": name, "label": label,
                                 "address": addr,
                                 "type": "toggle" if (is_enable or not e.get("trigger"))
                                 else "trigger"})
            else:
                elements.append({"kind": "LAMP", "signal": name, "label": label,
                                 "address": addr})
        elif typ == "INT":
            elements.append({"kind": "GAUGE", "signal": name, "label": label,
                             "address": addr, "range": e.get("range"),
                             "dir": d, "unit": e.get("unit")})
    return {"hmi_version": "1", "task_id": spec.get("task_id"), "elements": elements}
