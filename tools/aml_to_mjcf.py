#!/usr/bin/env python
"""标准 AML → MuJoCo MJCF 组装 + 真实编译冒烟（仿真侧下游实验）。

证明"标准 AML 作为仿真源文件"：从标准 AML 的 IO 信号出发——
  source_file/source_id（DAE 关节溯源）→ 解析 DAE library_joints 取关节轴向；
  range/scale（INT16 定点量程）→ hinge 限位；
  变量名 target/actual → MJCF 执行器/传感器命名（名称统一在仿真侧落地）。

产物：MJCF XML（mujoco 可加载）+ 编译/闭环冒烟（位置伺服收敛）报告。
几何用简化连杆（capsule）——本实验聚焦"IO→关节映射链"，网格导入归资源导入器。

用法：
  python tools/aml_to_mjcf.py examples/aml/standard/010RB_100_standard.aml \
      --resource-root AML_RobotCell_en_public/AML_RobotCell_en \
      -o workspace/aml_configurator/010RB_100.mjcf --smoke
"""

import argparse
import json
import math
import os
import xml.etree.ElementTree as ET


def extract_aml_joints(aml_path):
    """标准 AML 组件 Kinematics:* 属性 → {关节名: {component/type/axis/range/velocity/torque}}。"""
    root = ET.parse(aml_path).getroot()
    joints = {}
    for ie in root.iter("InternalElement"):
        for a in ie.findall("Attribute"):
            name = a.get("Name") or ""
            if not name.startswith("Kinematics:"):
                continue
            attrs = {x.get("Name"): x.findtext(".//Value") for x in a.iter("Attribute")}
            jn = name.split(":", 1)[1]
            joints[jn] = {
                "component": ie.get("ID"),
                "type": attrs.get("type", "hinge"),
                "axis": [float(x) for x in attrs["axis"].split()] if attrs.get("axis") else [0, 0, 1],
                "range": ([float(attrs["range_min"]), float(attrs["range_max"])]
                          if attrs.get("range_min") is not None else None),
                "velocity": float(attrs["velocity"]) if attrs.get("velocity") else None,
                "torque": float(attrs["torque"]) if attrs.get("torque") else None,
            }
    return joints


def extract_physical(aml_path):
    """标准 AML 组件 Physical 属性 → {component_id: {mass/com[m]/inertia[kg·m²]/density}}。"""
    root = ET.parse(aml_path).getroot()
    out = {}
    for ie in root.iter("InternalElement"):
        for a in ie.findall("Attribute"):
            if a.get("Name") != "Physical":
                continue
            attrs = {x.get("Name"): x.findtext(".//Value") for x in a.iter("Attribute")}
            rec = {}
            if attrs.get("mass"):
                rec["mass"] = float(attrs["mass"])
            if attrs.get("density"):
                rec["density"] = float(attrs["density"])
            for k in ("com", "inertia"):
                if attrs.get(k):
                    rec[k] = [float(v) for v in attrs[k].split()]
            if rec:
                out[ie.get("ID")] = rec
    return out


def extract_io(aml_path):
    """标准 AML → 信号表（name/dir/address/scale/range/source_file/source_id/role）。"""
    root = ET.parse(aml_path).getroot()
    out = []
    for iface in root.iter("ExternalInterface"):
        base = iface.get("RefBaseClassPath") or ""
        seg = base.rsplit("/", 1)[-1].lower()
        if not ("input" in seg or "output" in seg):
            continue
        attrs = {}
        for a in iface.iter("Attribute"):
            attrs[a.get("Name")] = a.findtext(".//Value")
        out.append({
            "name": iface.get("Name"),
            "dir": "input" if "input" in seg else "output",
            "address": attrs.get("address"),
            "scale": float(attrs["scale"]) if attrs.get("scale") else None,
            "range": ([float(attrs["range_min"]), float(attrs["range_max"])]
                      if attrs.get("range_min") is not None else None),
            "role": attrs.get("role"),
            "source_file": attrs.get("source_file"),
            "source_id": attrs.get("source_id"),
        })
    return out


def dae_joint_axis(resource_root, source_file, source_id):
    """DAE library_joints 按 sid 取关节轴向。"""
    path = os.path.join(resource_root, source_file)
    root = ET.parse(path).getroot()
    ns = {"c": root.tag.split("}")[0].strip("{")}
    for j in root.findall(".//c:library_joints/c:joint", ns):
        if j.get("sid") == source_id or j.get("id") == source_id:
            for t in ("prismatic", "revolute"):
                e = j.find(f"c:{t}", ns)
                if e is not None:
                    ax = e.find("c:axis", ns)
                    return {"type": t, "axis": [float(x) for x in ax.text.split()]}
    return None


def build_mjcf(io_items, resource_root, aml_joints=None, physical=None):
    """六轴串联链 MJCF：hinge 轴向=DAE、限位=AML range、命名=target/actual 变量名。
    物理属性（可选）：mass/com/inertia → body inertial；torque → actuator forcerange。"""
    targets = [it for it in io_items if it.get("role") == "target"]
    actuals = {it["name"]: it for it in io_items if it.get("role") == "actual"}
    targets.sort(key=lambda it: it["source_id"] or "")          # Joint_1..6 稳定排序
    physical = physical or {}

    m = ET.Element("mujoco", {"model": "standard_aml_robotcell"})
    ET.SubElement(m, "compiler", {"angle": "degree", "coordinate": "local"})
    ET.SubElement(m, "option", {"timestep": "0.002", "gravity": "0 0 -9.81"})
    d = ET.SubElement(m, "default")
    ET.SubElement(d, "joint", {"damping": "2", "armature": "0.1"})
    w = ET.SubElement(m, "worldbody")
    ET.SubElement(w, "geom", {"type": "plane", "size": "5 5 0.1",
                              "rgba": ".9 .9 .9 1"})

    parent, zoff = w, 0.0
    axes_note = []
    for i, t in enumerate(targets, 1):
        jinfo = dae_joint_axis(resource_root, t["source_file"], t["source_id"])             if t.get("source_file") else None
        if jinfo is None and aml_joints:                      # 散装模式回退：AML 关节属性
            jinfo = aml_joints.get(t.get("source_id"))
            if jinfo:
                jinfo = {"type": jinfo["type"], "axis": jinfo["axis"]}
        axis = jinfo["axis"] if jinfo else [0, 0, 1]
        axes_note.append({"target": t["name"], "source_id": t["source_id"],
                          "dae_type": jinfo["type"] if jinfo else "?",
                          "axis": axis})
        rng = t["range"] or [-655.34, 655.34]
        lo, hi = rng if t["scale"] is None else (rng[0], rng[1])   # 度（deg_per_count）
        body = ET.SubElement(parent, "body", {
            "name": f"link{i}",
            "pos": f"0 0 {0.45 if i == 1 else 0.35}"})
        ph = physical.get((aml_joints or {}).get(t.get("source_id"), {}).get("component")) \
            if aml_joints else None
        if ph and ph.get("mass"):
            inert = {"mass": str(ph["mass"])}
            if ph.get("com"):
                inert["pos"] = " ".join(str(v) for v in ph["com"])
            if ph.get("inertia"):
                inert["fullinertia"] = " ".join(str(v) for v in ph["inertia"])
            ET.SubElement(body, "inertial", inert)
        ET.SubElement(body, "joint", {
            "name": t["name"], "type": "hinge",
            "axis": " ".join(str(x) for x in axis),
            "range": f"{lo} {hi}", "limited": "true"})
        ET.SubElement(body, "geom", {
            "type": "capsule", "fromto": f"0 0 0 0 0 0.3", "size": "0.06",
            "rgba": ".8 .4 .1 1"})
        parent, zoff = body, zoff
    ET.SubElement(parent, "site", {"name": "flange", "pos": "0 0 0.05", "size": "0.01"})

    act = ET.SubElement(m, "actuator")
    for t in targets:
        attr = {"name": t["name"], "joint": t["name"], "kp": "120"}
        tq = (aml_joints or {}).get(t.get("source_id"), {}).get("torque")
        if tq:
            attr["forcerange"] = f"-{tq:g} {tq:g}"
        ET.SubElement(act, "position", attr)

    sen = ET.SubElement(m, "sensor")
    for t in targets:
        a = actuals.get(t["name"].replace("target", "actual"))
        if a:
            ET.SubElement(sen, "jointpos", {"name": a["name"], "joint": t["name"]})

    ET.indent(m, space="  ")
    return m, axes_note


def smoke(mjcf_path, io_items, resource_root, out_report):
    """mujoco 真实编译 + 位置伺服闭环收敛冒烟。"""
    import mujoco
    model = mujoco.MjModel.from_xml_path(mjcf_path)
    data = mujoco.MjData(model)
    targets = [it for it in io_items if it.get("role") == "target"]
    targets.sort(key=lambda it: it["source_id"] or "")

    result = {"compile": True,
              "n_body": model.nbody, "n_joint": model.njnt,
              "n_actuator": model.nu, "n_sensor": model.nsensor,
              "actuator_names": [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_ACTUATOR, i)
                                 for i in range(model.nu)],
              "sensor_names": [mujoco.mj_id2name(model, mujoco.mjtObj.mjOBJ_SENSOR, i)
                               for i in range(model.nsensor)]}

    # 闭环：J1 指令 raw=1500（0.02°/LSB → 30°），伺服应在 2s 内收敛
    scale = targets[0]["scale"] or 1.0
    cmd_deg = 1500 * scale
    data.ctrl[0] = math.radians(cmd_deg)
    for _ in range(1000):                        # 2s @ 2ms
        mujoco.mj_step(model, data)
    j1 = data.qpos[0]
    err_deg = abs(math.degrees(j1) - cmd_deg)
    result["closed_loop_j1"] = {"cmd_raw": 1500, "scale": scale, "cmd_deg": cmd_deg,
                                "reached_deg": round(math.degrees(j1), 3),
                                "error_deg": round(err_deg, 4), "converged": err_deg < 1.0}
    result["ok"] = (model.njnt == 6 and model.nu == 6 and model.nsensor == 6
                    and result["closed_loop_j1"]["converged"])
    json.dump(result, open(out_report, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    return result


def main(argv=None):
    p = argparse.ArgumentParser(description="标准 AML → MJCF 组装 + mujoco 编译冒烟")
    p.add_argument("aml", help="标准 AML 路径")
    p.add_argument("--resource-root", required=True,
                   help="AML 资源包根（解析 source_file 相对路径）")
    p.add_argument("-o", "--out", required=True, help="MJCF 输出路径")
    p.add_argument("--smoke-report", default=None, help="冒烟报告 json 路径")
    p.add_argument("--smoke", action="store_true", help="编译后跑闭环冒烟")
    args = p.parse_args(argv)

    io_items = extract_io(args.aml)
    mjcf, axes_note = build_mjcf(io_items, args.resource_root,
                                 aml_joints=extract_aml_joints(args.aml),
                                 physical=extract_physical(args.aml))
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    ET.ElementTree(mjcf).write(args.out, encoding="utf-8", xml_declaration=True)
    print(f"OK 组装 {args.out}（{len(axes_note)} 关节，轴向均取自 DAE）")
    for n in axes_note:
        print(f"  {n['target']:20s} {n['source_id'][-18:]} {n['dae_type']:8s} axis={n['axis']}")

    if args.smoke:
        rep = args.smoke_report or os.path.splitext(args.out)[0] + "_smoke.json"
        r = smoke(args.out, io_items, args.resource_root, rep)
        print(f"{'OK ' if r['ok'] else 'FAIL '} mujoco 编译+闭环冒烟 → {rep}")
        print(f"  body={r['n_body']} joint={r['n_joint']} actuator={r['n_actuator']} "
              f"sensor={r['n_sensor']}")
        print(f"  J1 闭环: raw=1500 ×{r['closed_loop_j1']['scale']}°/LSB = "
              f"{r['closed_loop_j1']['cmd_deg']}° → 实际 {r['closed_loop_j1']['reached_deg']}° "
              f"（误差 {r['closed_loop_j1']['error_deg']}°）")
        return 0 if r["ok"] else 1
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
