#!/usr/bin/env python
"""标准 AML 装配与 IO 绑定（《复杂仿真模型装配与IO绑定说明》的适配版）。

原说明的输入是"AML + DAE + 独立 IO 配置文件（io_list+binding）"；本模块把输入
收敛为**一份含 IO 的标准 AML**（308sjk.standard_aml/1，由 aml_configurator 组装）：

  1. 解析装配  parse_standard_aml()
     InternalElement 层级 + Frame → 组件树与世界位姿（逐层累积）；
     COLLADAInterface(explicit) → DAE 几何引用；
     Kinematics:* 属性 → 关节（规格：axis/anchor/range/velocity/torque）；
     Physical 属性 → 质量/质心/惯性；
     InternalLink + 端子 → 电气接线关系；关系不明确项记入 pending（待确认项）。
  2. MuJoCo 生成  build_scene()
     层级→body、可动→joint、几何→geom(mesh/OBJ)、IO 接口锚点→site；
     target/actual 信号 → actuator/sensor（命名=AML 信号名）；
     保存 AML 组件 ↔ 仿真节点映射 mapping.json 供前端选择/控制/绑定。
  3. IO 绑定     信号自带 binding 三元组（component_id + source_file + source_id），
     适配器 IOSession 双向读写 + 单位换算（raw × deg_per_count ↔ deg），
     %QW 地址 ↔ Modbus 寄存器布局（offset=地址数字部分）。
  4. IO 测试     固定生成规则 → PLCopen XML(io_test.xml) + plc_test_contract.json
     （经 xml2st 结构闸门验证并转 ST；OpenPLC 可用则真编译，未接入时为扫描替身）
     → 通信测试：发目标 → 伺服执行 → 读反馈 → 扫描确认，按 SimulationRequirements
       容差裁定。

CLI 冒烟：python tools/aml_assembly.py examples/aml/standard/parts_demo/standard.aml
"""

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))


# ================================================================ 1. 解析装配

def _local(tag):
    return tag.split("}")[-1]


def _attrs_of(el):
    """CAEX Attribute 组 → {name: value 文本}（嵌套组压平：Frame 等单独处理）。"""
    out = {}
    for a in el.findall("Attribute"):
        out[a.get("Name")] = a.findtext("Value")
    return out


def _group(el, gname):
    for a in el.findall("Attribute"):
        if a.get("Name") == gname:
            return {x.get("Name"): x.findtext("Value") for x in a.iter("Attribute")
                    if x is not a}
    return {}


def _quat_from_matrix(r):
    """3x3 旋转矩阵 → [x,y,z,w]（three.js 顺序）。"""
    tr = r[0][0] + r[1][1] + r[2][2]
    if tr > 0:
        s = math.sqrt(tr + 1.0) * 2
        w = 0.25 * s
        x = (r[2][1] - r[1][2]) / s
        y = (r[0][2] - r[2][0]) / s
        z = (r[1][0] - r[0][1]) / s
    elif r[0][0] > r[1][1] and r[0][0] > r[2][2]:
        s = math.sqrt(1.0 + r[0][0] - r[1][1] - r[2][2]) * 2
        w = (r[2][1] - r[1][2]) / s
        x = 0.25 * s
        y = (r[0][1] + r[1][0]) / s
        z = (r[0][2] + r[2][0]) / s
    elif r[1][1] > r[2][2]:
        s = math.sqrt(1.0 + r[1][1] - r[0][0] - r[2][2]) * 2
        w = (r[0][2] - r[2][0]) / s
        x = (r[0][1] + r[1][0]) / s
        y = 0.25 * s
        z = (r[1][2] + r[2][1]) / s
    else:
        s = math.sqrt(1.0 + r[2][2] - r[0][0] - r[1][1]) * 2
        w = (r[1][0] - r[0][1]) / s
        x = (r[0][2] + r[2][0]) / s
        y = (r[1][2] + r[2][1]) / s
        z = 0.25 * s
    return [x, y, z, w]


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _mv(r, v):
    return [sum(r[i][k] * v[k] for k in range(3)) for i in range(3)]


def _rpy_matrix(rpy_deg):
    rx, ry, rz = (math.radians(v) for v in rpy_deg)
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry),
                              math.sin(ry), math.cos(rz), math.sin(rz))
    return _mm([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]],
               _mm([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]],
                   [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]))


def parse_standard_aml(aml_path, resource_root=None):
    """标准 AML → 装配模型（组件树/关节/信号/接线/要求/待确认项）。

    resource_root 给定时，额外从信号 source_file 指向的 DAE library_joints 提取
    关节规格（打包模式：官方运动学在 COLLADA 1.5 DAE 里，AML 无 Kinematics 属性）。
    返回 dict；pending 列出关系不明确项（原说明 1.2(3) 的"待确认项"）。
    """
    root = ET.parse(aml_path).getroot()
    ih = root.find("InstanceHierarchy")
    if ih is None:
        raise ValueError("标准 AML 无 InstanceHierarchy")
    resource_root = resource_root or os.path.dirname(os.path.abspath(aml_path))

    model = {"source": os.path.basename(aml_path),
             "components": [], "joints": [], "signals": [], "links": [],
             "requirements": {}, "kin_dae": None, "pending": []}
    by_id = {}

    def walk(ie, parent_id, rot, trans):
        cid = ie.get("ID") or ie.get("Name")
        frame = _group(ie, "Frame")
        pos = [float(frame.get(k, 0)) for k in "xyz"]
        rpy = [float(frame.get(k, 0)) for k in ("rx", "ry", "rz")]
        world_rot = _mm(rot, _rpy_matrix(rpy))
        world_pos = [a + b for a, b in zip(_mv(rot, pos), trans)]

        comp = {"id": cid, "name": ie.get("Name", cid), "parent": parent_id,
                "frame": {"pos": pos, "rpy_deg": rpy,
                          "quat": _quat_from_matrix(_rpy_matrix(rpy))},
                "world": {"pos": world_pos, "quat": _quat_from_matrix(world_rot)},
                "geom_uri": None, "role": None, "physical": _group(ie, "Physical"),
                "signals": [], "joints": []}

        rr = ie.find("RoleRequirements")
        if rr is not None:
            comp["role"] = rr.get("RefBaseRoleClassPath", "").rsplit("/", 1)[-1]

        for ei in ie.findall("ExternalInterface"):
            cls = ei.get("RefBaseClassPath") or ""
            ea = _attrs_of(ei)
            uri = ea.get("refURI", "")
            if "COLLADAInterface" in cls:
                if "#" in uri:                     # fragment=运动学模型引用（Attachment_COLLADA_*）
                    model["kin_dae"] = model["kin_dae"] or uri
                elif ea.get("refType", "explicit") == "explicit":
                    if comp["geom_uri"]:
                        model["pending"].append(
                            f"组件 {cid} 有多个显式几何接口（{ei.get('Name')}），取首个")
                    else:
                        comp["geom_uri"] = uri
                else:
                    model["kin_dae"] = uri         # 标准运动学载体引用（implicit）
            elif "input" in cls.rsplit("/", 1)[-1].lower() or \
                    "output" in cls.rsplit("/", 1)[-1].lower():
                seg = cls.rsplit("/", 1)[-1].lower()
                rng = None
                if ea.get("range_min") is not None:
                    rng = [float(ea["range_min"]), float(ea["range_max"])]
                sig = {"name": ei.get("Name"), "dir": "input" if "input" in seg else "output",
                       "type": "BOOL" if "digital" in seg else "INT",
                       "address": ea.get("address"), "role": ea.get("role"),
                       "scale": float(ea["scale"]) if ea.get("scale") else None,
                       "range": rng, "component_id": cid,
                       "source_file": ea.get("source_file") or "",
                       "source_id": ea.get("source_id") or "",
                       "description": ea.get("description", "")}
                model["signals"].append(sig)
                comp["signals"].append(sig["name"])

        for a in ie.findall("Attribute"):
            name = a.get("Name") or ""
            if name.startswith("Kinematics:"):
                k = {x.get("Name"): x.findtext("Value") for x in a.iter("Attribute")
                     if x is not a}
                model["joints"].append({
                    "name": name.split(":", 1)[1], "component": cid,
                    "type": k.get("type", "hinge"),
                    "axis": [float(x) for x in k["axis"].split()] if k.get("axis") else [0, 0, 1],
                    "anchor": ([float(x) for x in k["anchor"].split()]
                               if k.get("anchor") else [0, 0, 0]),
                    "range": ([float(k["range_min"]), float(k["range_max"])]
                              if k.get("range_min") is not None else None),
                    "velocity": float(k["velocity"]) if k.get("velocity") else None,
                    "torque": float(k["torque"]) if k.get("torque") else None})
                comp["joints"].append(name.split(":", 1)[1])

        req = ie.find("Attribute[@Name='SimulationRequirements']")
        if req is not None and not model["requirements"]:
            r = {}
            for x in req.iter("Attribute"):
                if x is not req and x.find("Value") is not None and \
                        not [c for c in x if _local(c.tag) == "Attribute"]:
                    v = x.findtext("Value")
                    try:
                        v = float(v) if "." in v or v.isdigit() else v
                    except (TypeError, ValueError):
                        pass
                    r[x.get("Name")] = v
            model["requirements"] = r

        model["components"].append(comp)
        by_id[cid] = comp
        for child in ie.findall("InternalElement"):
            walk(child, cid, world_rot, world_pos)

    for ie in ih.findall("InternalElement"):
        walk(ie, None, [[1, 0, 0], [0, 1, 0], [0, 0, 1]], [0, 0, 0])

    # 电气接线（信号 × PLC 端子；挂 Station 层）
    ifaces = {ei.get("ID"): (ei.get("Name"), ei.get("RefBaseClassPath") or "")
              for ei in root.iter("ExternalInterface")}
    for lk in root.iter("InternalLink"):
        a, b = lk.get("RefPartnerSideA"), lk.get("RefPartnerSideB")
        model["links"].append({
            "name": lk.get("Name"),
            "a": {"id": a, "name": (ifaces.get(a) or ("?", ""))[0]},
            "b": {"id": b, "name": (ifaces.get(b) or ("?", ""))[0]}})

    # 打包模式：信号溯源的 DAE library_joints → 关节规格（官方运动学在 COLLADA 1.5 里）
    have = {j["name"] for j in model["joints"]}
    dae_cache = {}
    for s in model["signals"]:
        if not (s.get("source_file") and s.get("source_id")) or s["source_id"] in have:
            continue
        path = os.path.join(resource_root, s["source_file"])
        if s["source_file"] not in dae_cache:
            try:
                kroot = ET.parse(path).getroot()
                dae_cache[s["source_file"]] = {
                    (e.get("id") or "", e.get("name") or ""): e
                    for e in kroot.iter() if _local(e.tag) == "joint"}
            except (ET.ParseError, OSError) as e:
                model["pending"].append(
                    f"信号 {s['name']} 的关节源 DAE 不可解析（{type(e).__name__}）")
                dae_cache[s["source_file"]] = {}
        je = dae_cache[s["source_file"]].get((s["source_id"], s["source_id"])) \
            or next((v for k, v in dae_cache[s["source_file"]].items()
                     if s["source_id"] in k), None)
        if je is None:
            model["pending"].append(
                f"信号 {s['name']} 的关节 {s['source_id']} 不在 {s['source_file']} 中")
            continue
        rev = next((c for c in je if _local(c.tag) in ("revolute", "prismatic")), None)
        kind = _local(rev.tag) if rev is not None else None
        axis_el = next((c for c in rev if _local(c.tag) == "axis"), None) if rev is not None else None
        lim = next((c for c in rev if _local(c.tag) == "limits"), None) if rev is not None else None
        rng = None
        if lim is not None:
            lo, hi = lim.find("{*}min"), lim.find("{*}max")
            if lo is not None and hi is not None:
                rng = [float(lo.text), float(hi.text)]
        model["joints"].append({
            "name": s["source_id"], "component": s["component_id"],
            "type": "hinge" if kind == "revolute" else "slide",
            "axis": ([float(x) for x in axis_el.text.split()]
                     if axis_el is not None else [0, 0, 1]),
            "anchor": [0, 0, 0], "range": rng,
            "velocity": None, "torque": None, "from_dae": s["source_file"]})
        have.add(s["source_id"])
    comp_by_id = {c["id"]: c for c in model["components"]}
    for j in model["joints"]:                    # 组件关节清单补齐（前端链构建用）
        c = comp_by_id.get(j["component"])
        if c and j["name"] not in c["joints"]:
            c["joints"].append(j["name"])

    # 待确认项（原说明 1.2(3)：关系不明确的项目记录）
    for c in model["components"]:
        if not c["geom_uri"] and c["joints"]:
            model["pending"].append(
                f"组件 {c['id']} 有关节但无显式几何（{','.join(c['joints'])}）")
    for s in model["signals"]:
        if s["type"] == "INT" and not s["source_id"]:
            model["pending"].append(
                f"信号 {s['name']} 无 source_id 绑定（无法定位模型关节）")
    seen = {}
    for s in model["signals"]:
        if s["address"] in seen:
            model["pending"].append(
                f"地址冲突 {s['address']}：{seen[s['address']]} vs {s['name']}")
        seen[s["address"]] = s["name"]
    return model


MESH_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "workspace", "aml_assembly", "_meshcache")
MESH_FACES_SCENE = 4000      # mujoco/OBJ 每网格面数上限（降采样保持结构可辨识）
MESH_FACES_UI = 1000         # 前端载荷每网格上限
MESH_FACES_UI_TOTAL = 60000  # 前端载荷全局上限


def _mesh_cached(rel, resource_root, cap):
    """加载并降采样（stride 确定性）；磁盘缓存 key=uri+mtime+cap。返回 mesh 或 None。"""
    import hashlib
    import trimesh
    path = os.path.join(resource_root, rel)
    if not os.path.isfile(path):
        return None
    key = hashlib.sha1(f"{rel}|{os.path.getmtime(path):.0f}|{cap}".encode()).hexdigest()[:16]
    os.makedirs(MESH_CACHE, exist_ok=True)
    cf = os.path.join(MESH_CACHE, key + ".json")
    if os.path.isfile(cf):
        return json.load(open(cf, encoding="utf-8"))
    try:
        m = trimesh.load(path, force="mesh")
    except Exception:                                         # noqa: BLE001
        m = None
    if m is None or not len(m.faces):
        data = None
    else:
        stride = max(1, len(m.faces) // cap + 1)
        f = m.faces[::stride]
        seen, verts, faces = {}, [], []
        for tri in f:
            idx = []
            for v in tri:
                i = seen.get(int(v))
                if i is None:
                    i = len(verts)
                    seen[int(v)] = i
                    verts.append([round(float(x), 5) for x in m.vertices[v]])
                idx.append(i)
            faces.append(idx)
        data = {"vertices": verts, "faces": faces,
                "full_faces": int(len(m.faces))}
    json.dump(data, open(cf, "w", encoding="utf-8"))
    return data


def mesh_payload(model, resource_root):
    """组件几何 → 前端渲染载荷 {component_id: {vertices, faces}}（并行+缓存+全局面数上限）。"""
    from concurrent.futures import ThreadPoolExecutor
    targets = [(c["id"], c["geom_uri"]) for c in model["components"] if c["geom_uri"]]
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = list(ex.map(
            lambda t: (t[0], _mesh_cached(
                t[1].replace("file:///", "").replace("file://", ""),
                resource_root, MESH_FACES_UI)), targets))
    out = {cid: d for cid, d in results if d}
    total = sum(len(d["faces"]) for d in out.values())
    if total > MESH_FACES_UI_TOTAL:              # 全局超限 → 确定性再抽稀
        stride = total // MESH_FACES_UI_TOTAL + 1
        for d in out.values():
            d["faces"] = d["faces"][::stride]
    for cid, (rel, d) in zip([t[0] for t in targets],
                             [(t[1], out.get(t[0])) for t in targets]):
        if d is None:
            model["pending"].append(
                f"组件 {cid} 几何不可解析/为空（{rel.rsplit('/', 1)[-1]}；"
                f"COLLADA 1.5 Product 网格 pycollada 不支持）")
    return out


# ================================================================ 2. MuJoCo 生成

def build_scene(model, out_dir, resource_root):
    """装配模型 → scene.xml + meshes/ + mapping + mujoco 编译统计。

    层级→body（Frame→pos/euler）；可动→joint（axis/range/anchor）；同一组件多关节
    （打包模式 DAE 溯源）→ 链式子 body；几何→geom(mesh OBJ，降采样上限
    MESH_FACES_SCENE，几何缺失用 capsule 占位并记 pending)；IO 接口→site；
    target→position actuator、actual→jointpos sensor（命名=AML 信号名）。
    """
    os.makedirs(os.path.join(out_dir, "meshes"), exist_ok=True)
    mesh_name = {}
    decimated = 0
    for c in model["components"]:
        if not c["geom_uri"]:
            continue
        rel = c["geom_uri"].replace("file:///", "").replace("file://", "")
        d = _mesh_cached(rel, resource_root, MESH_FACES_SCENE)
        if d is None:
            model["pending"].append(
                f"组件 {c['id']} 几何不可解析/为空（{rel.rsplit('/', 1)[-1]}），用胶囊占位")
            continue
        obj = os.path.join(out_dir, "meshes", c["id"] + ".obj")
        with open(obj, "w", encoding="utf-8") as f:
            for v in d["vertices"]:
                f.write("v %g %g %g\n" % tuple(v))
            for t in d["faces"]:
                f.write("f " + " ".join(str(i + 1) for i in t) + "\n")
        mesh_name[c["id"]] = c["id"]
        if d["full_faces"] > MESH_FACES_SCENE:
            decimated += 1
    if decimated:
        model["pending"].append(
            f"{decimated} 个网格超过 {MESH_FACES_SCENE} 面，已确定性降采样用于编译"
            f"（全分辨率导入归 gc 资源导入器）")

    joints_by_comp = {}
    for j in model["joints"]:
        joints_by_comp.setdefault(j["component"], []).append(j)
    targets = [s for s in model["signals"] if s.get("role") == "target"]
    actuals = {s["name"]: s for s in model["signals"] if s.get("role") == "actual"}
    joint_names = {j["name"] for j in model["joints"]}

    m = ET.Element("mujoco", {"model": "standard_aml_assembly"})
    ET.SubElement(m, "compiler", {"angle": "degree", "coordinate": "local",
                                  "meshdir": "meshes"})
    ET.SubElement(m, "option", {"timestep": "0.002", "gravity": "0 0 -9.81",
                                "integrator": "implicitfast"})
    d = ET.SubElement(m, "default")
    ET.SubElement(d, "joint", {"damping": "50", "armature": "0.1"})
    if mesh_name:
        asset = ET.SubElement(m, "asset")
        for cid, name in mesh_name.items():
            ET.SubElement(asset, "mesh", {"name": name, "file": cid + ".obj"})

    world = ET.SubElement(m, "worldbody")
    ET.SubElement(world, "geom", {"type": "plane", "size": "4 4 0.1",
                                  "rgba": ".92 .92 .92 1"})
    ET.SubElement(world, "light", {"pos": "0 0 4", "dir": "0 0 -1", "directional": "true"})
    comp_by_id = {c["id"]: c for c in model["components"]}
    children = {}
    roots = []
    for c in model["components"]:
        (children.setdefault(c["parent"], []) if c["parent"] in comp_by_id
         else roots).append(c)

    chain_tail = {}                     # 多关节链的最深 body（子组件挂靠点=法兰端）

    def make_body(parent_el, c):
        attrs = {"name": c["id"], "pos": " ".join("%g" % v for v in c["frame"]["pos"])}
        if any(abs(v) > 1e-9 for v in c["frame"]["rpy_deg"]):
            attrs["euler"] = " ".join("%g" % v for v in c["frame"]["rpy_deg"])
        attrs["gravcomp"] = "1"          # IO/运动学验证不含重力效应（动力学保真归 gc 导入器）
        body = ET.SubElement(parent_el, "body", attrs)
        ph = c.get("physical") or {}
        if ph.get("mass"):
            ia = {"mass": ph["mass"]}
            if ph.get("com"):
                ia["pos"] = ph["com"]
            if ph.get("inertia"):
                ia["fullinertia"] = ph["inertia"]
            ET.SubElement(body, "inertial", ia)
        node = body
        for k, j in enumerate(joints_by_comp.get(c["id"], [])):
            if k > 0:                    # 同组件多关节（DAE 溯源）→ 链式子 body
                node = ET.SubElement(node, "body",
                                     {"name": j["name"] + "_link", "pos": "0 0 0.35",
                                      "gravcomp": "1"})
                ET.SubElement(node, "geom", {"type": "capsule",
                                             "fromto": "0 0 0 0 0 0.3", "size": "0.06",
                                             "rgba": ".55 .6 .66 1",
                                             "contype": "0", "conaffinity": "0"})
            ja = {"name": j["name"], "pos": " ".join("%g" % v for v in j["anchor"]),
                  "axis": " ".join("%g" % v for v in j["axis"])}
            if j["type"] == "slide":
                ja["type"] = "slide"
                if j["range"]:
                    ja["range"] = "%g %g" % tuple(j["range"])
            else:
                ja["type"] = "hinge"
                if j["range"]:
                    ja["range"] = "%g %g" % tuple(j["range"])
            ja["limited"] = "true" if j["range"] else "false"
            ET.SubElement(node, "joint", ja)
        chain_tail[c["id"]] = node
        if c["id"] in mesh_name:
            ET.SubElement(body, "geom", {"type": "mesh", "mesh": c["id"],
                                         "rgba": ".78 .45 .18 1",
                                         "contype": "0", "conaffinity": "0"})
        elif c["parent"] is not None:               # 根组件的地面平面已存在
            ET.SubElement(body, "geom", {"type": "capsule", "fromto": "0 0 0 0 0 0.25",
                                         "size": "0.05", "rgba": ".6 .6 .62 1",
                                         "contype": "0", "conaffinity": "0"})
        if c["signals"] or c["joints"]:             # IO 接口锚点 → site
            ET.SubElement(body, "site", {"name": "site_" + c["id"], "size": "0.008"})
        for ch in children.get(c["id"], []):
            make_body(chain_tail.get(c["id"], body), ch)

    for c in roots:
        make_body(world, c)

    act = ET.SubElement(m, "actuator")
    for t in sorted(targets, key=lambda s: s["source_id"] or s["name"]):
        if t["source_id"] in joint_names:
            ET.SubElement(act, "position",
                          {"name": t["name"], "joint": t["source_id"], "kp": "4000"})
    sen = ET.SubElement(m, "sensor")
    for t in sorted(targets, key=lambda s: s["source_id"] or s["name"]):
        a = actuals.get(t["name"].replace("target", "actual"))
        if a and t["source_id"] in joint_names:
            ET.SubElement(sen, "jointpos", {"name": a["name"], "joint": t["source_id"]})

    scene = os.path.join(out_dir, "scene.xml")
    tree = ET.ElementTree(m)
    ET.indent(tree, space="  ")
    tree.write(scene, encoding="utf-8", xml_declaration=True)

    mapping = {"component_to_body": {c["id"]: c["id"] for c in model["components"]},
               "joint_to_body": {j["name"]: j["component"] for j in model["joints"]},
               "signal_to_actuator": {t["name"]: t["source_id"] for t in targets
                                      if t["source_id"] in joint_names},
               "signal_to_sensor": {a["name"]: a["source_id"]
                                    for a in actuals.values()
                                    if a["source_id"] in joint_names}}
    json.dump(mapping, open(os.path.join(out_dir, "mapping.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)

    stats = {"scene": scene, "meshes": len(mesh_name),
             "actuators": len(mapping["signal_to_actuator"]),
             "sensors": len(mapping["signal_to_sensor"])}
    try:
        import mujoco
        mm = mujoco.MjModel.from_xml_path(scene)
        stats.update({"compile": True, "nbody": mm.nbody, "njnt": mm.njnt,
                      "nu": mm.nu, "nsensor": mm.nsensor})
    except Exception as e:                                      # noqa: BLE001
        stats.update({"compile": False, "error": str(e)[:300]})
    return stats


# ================================================================ 3. IO 绑定（适配器）

class IOSession:
    """适配器会话：PLC 变量（raw）↔ MuJoCo 关节（deg）双向读写 + 单位换算。

    raw = deg / deg_per_count（scale）；ctrl 目标 = radians(deg)。
    %QWn → Modbus offset n（与 aml_export.gen_modbus 通道表一致）。
    """

    def __init__(self, scene_xml, signals, joints, requirements=None):
        import mujoco
        self.model = mujoco.MjModel.from_xml_path(scene_xml)
        self.data = mujoco.MjData(self.model)
        self.signals = signals
        self.joints = {j["name"]: j for j in joints}
        self.requirements = requirements or {}
        self.act_idx = {}
        self.qpos_idx = {}
        for i in range(self.model.nu):
            nm = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_ACTUATOR, i)
            self.act_idx[nm] = i
        for i in range(self.model.njnt):
            nm = mujoco.mj_id2name(self.model, mujoco.mjtObj.mjOBJ_JOINT, i)
            self.qpos_idx[nm] = (self.model.jnt_qposadr[i], self.model.jnt_type[i])
        self.seq = 0
        self.targets_raw = {}

    def set_target(self, name, raw):
        sig = next(s for s in self.signals if s["name"] == name)
        if sig["range"]:
            raw = max(sig["range"][0], min(sig["range"][1], int(raw)))
        self.targets_raw[name] = int(raw)
        deg = raw * (sig["scale"] or 1.0)
        self.data.ctrl[self.act_idx[name]] = math.radians(deg)

    def _read_deg(self, joint):
        adr, jtype = self.qpos_idx[joint]
        v = self.data.qpos[adr]
        return v if jtype == 0 else math.degrees(v)      # slide 原值 / hinge rad→deg

    def state(self):
        actuals = {}
        for s in self.signals:
            if s.get("role") == "actual" and s["source_id"] in self.qpos_idx:
                deg = self._read_deg(s["source_id"])
                raw = round(deg / (s["scale"] or 1.0))
                if s["range"]:
                    raw = max(int(s["range"][0]), min(int(s["range"][1]), raw))
                actuals[s["name"]] = raw
        qpos_deg = {j: round(self._read_deg(j), 3) for j in self.joints}
        return {"t_s": round(self.data.time, 3), "seq": self.seq,
                "targets": dict(self.targets_raw), "actuals": actuals,
                "qpos_deg": qpos_deg}

    def step(self, n=1):
        import mujoco
        for _ in range(n):
            mujoco.mj_step(self.model, self.data)
        self.seq += 1
        return self.state()


def modbus_channels(signals):
    """%QW 地址 → Modbus 寄存器布局（FC03/06/16；BOOL %QX 线圈不进寄存器表）。"""
    chs = []
    for s in sorted(signals, key=lambda x: x["address"] or ""):
        if not (s["address"] or "").startswith("%QW"):
            continue
        chs.append({"plc_var": s["name"], "dir": s["dir"], "address": s["address"],
                    "offset": int(s["address"][3:]),
                    "raw_to_eng": s["scale"] or 1.0,
                    "range": s["range"], "fc_write": "06",
                    "binding": {"component_id": s["component_id"],
                                "source_file": s["source_file"],
                                "source_id": s["source_id"]}})
    return {"format": "308sjk.modbus_channels/1", "channels": chs}


# ================================================================ 4. IO 测试（固定规则）

def gen_plcopen_test(signals, requirements=None):
    """固定生成规则 → PLCopen XML（io_test.xml）文本。

    逻辑（原说明 2.2(2)）：接收目标位置 → 输出指令（使能门控）→ 读取反馈 →
    确认扫描（feedback_seq 递增 + prog_id 身份）。辅助寄存器自动分配：
    使能清零用 0 值；prog_id 定位 %QW20（契约②）。
    """
    outs = [s for s in signals if s["dir"] == "output"]
    ins = [s for s in signals if s["dir"] == "input"]
    have_prog = any(s["role"] == "prog_id" for s in signals)
    enable = next((s for s in signals if s["role"] == "enable"), None)

    var_lines, body = [], []

    def var(s, init="0"):
        var_lines.append(
            '        <variable name="%s" address="%s">\n'
            '          <type><%s /></type>\n'
            '          <initialValue><simpleValue value="%s" /></initialValue>\n'
            '        </variable>' % (s["name"], s["address"], s["type"], init))

    for s in outs + ins:
        var(s)
    if not have_prog:
        var_lines.append(
            '        <variable name="prog_id" address="%QW20">\n'
            '          <type><INT /></type>\n'
            '          <initialValue><simpleValue value="260" /></initialValue>\n'
            '        </variable>')

    body.append("(* 固定生成：io_test —— 接收目标→输出指令→读取反馈→确认扫描 *)")
    body.append("prog_id := 260; (* 程序身份：io_test v1 *)")
    if any(s["name"] == "feedback_seq" for s in outs + ins):
        body.append("feedback_seq := feedback_seq + 1; (* 扫描计数：通讯活性判定 *)")
    if enable:
        body.append("")
        body.append("IF %s THEN (* 使能门控：失能时指令清零 *)" % enable["name"])
        for s in outs:
            if s["role"] == "target":
                body.append("    (* %s 由测试驱动经 Modbus 写入，透传至适配器 *)"
                            % s["name"])
        body.append("ELSE")
        for s in outs:
            if s["role"] == "target":
                body.append("    %s := 0;" % s["name"])
        body.append("END_IF")
    body.append("")
    body.append("(* 反馈通道由适配器回写：actual_* / feedback_seq，此处仅声明 *)")

    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<project xmlns="http://www.plcopen.org/xml/tc6_0201">\n'
           '  <fileHeader companyName="308sjk" productName="aml_assembly" '
           'productVersion="1.0" creationDateTime="2026-11-23T00:00:00" '
           'description="io_test: fixed-rule IO binding test program generated '
           'from standard AML signals" />\n'
           '  <contentHeader name="io_test.project" modificationDateTime='
           '"2026-11-23T00:00:00">\n'
           '    <coordinateInfo><fbd><scaling x="1" y="1" /></fbd>'
           '<ld><scaling x="1" y="1" /></ld><sfc><scaling x="1" y="1" /></sfc>'
           '</coordinateInfo>\n'
           '  </contentHeader>\n'
           '  <types><dataTypes />\n    <pous>\n'
           '      <pou name="IO_TEST" pouType="program">\n'
           '        <interface>\n'
           '          <localVars>\n' + "\n".join(var_lines) +
           '\n          </localVars>\n'
           '        </interface>\n'
           '        <body>\n'
           '          <ST xmlns="http://www.plcopen.org/xml/tc6_0201">'
           '<xhtml xmlns="http://www.w3.org/1999/xhtml">'
           '<![CDATA[' + "\n".join(body) + ']]></xhtml></ST>\n'
           '        </body>\n'
           '      </pou>\n'
           '    </pous></types>\n'
           '  <instances><configurations /></instances>\n'
           '</project>\n')
    return xml


def gen_contract(signals, scene_stats=None):
    """plc_test_contract.json：辅助地址 + 程序身份 + 信号清单摘要。"""
    import hashlib
    have_prog = any(s["role"] == "prog_id" for s in signals)
    aux = []
    if not have_prog:
        aux.append({"name": "prog_id", "address": "%QW20", "init": 260,
                    "purpose": "程序身份（契约②）"})
    return {
        "format": "308sjk.plc_test_contract/1",
        "program": "IO_TEST", "source": "standard AML signals",
        "prog_id": {"address": "%QW20", "value": 260},
        "aux_registers": aux,
        "scan_logic": "receive target → gate by enable → read feedback → "
                      "confirm scan (feedback_seq + prog_id)",
        "signals_hash": hashlib.sha256(
            json.dumps([{k: s.get(k) for k in
                         ("name", "dir", "type", "address", "role", "source_id")}
                        for s in signals], sort_keys=True).encode()).hexdigest()[:16],
        "scene": scene_stats or {}}


def run_acceptance(session, out_json=None):
    """通信测试（原说明 2.2(5)）：逐关节发目标 → 伺服执行 → 反馈确认。

    裁定依据 SimulationRequirements（angle_tolerance_rad / response_timeout_s）。
    """
    tol_rad = float(session.requirements.get("angle_tolerance_rad", 0.001))
    timeout = float(session.requirements.get("response_timeout_s", 3))
    tol_deg = math.degrees(tol_rad)
    targets = sorted([s for s in session.signals if s.get("role") == "target"],
                     key=lambda s: s["source_id"] or s["name"])
    results = []
    for t in targets:
        rng = t["range"] or [-1000, 1000]
        raw = int(rng[0] + (rng[1] - rng[0]) * 0.4)
        deg = raw * (t["scale"] or 1.0)
        session.set_target(t["name"], raw)
        t0, err, settle = session.data.time, None, None
        steps = int(timeout / 0.002)
        for _ in range(steps):
            session.step(1)
            cur = session._read_deg(t["source_id"])
            err = abs(cur - deg)
            if err < tol_deg:
                settle = round(session.data.time - t0, 3)
                break
        actual = [a for a in session.signals
                  if a["name"] == t["name"].replace("target", "actual")]
        fb = None
        if actual:
            fb = session.state()["actuals"].get(actual[0]["name"])
        results.append({
            "signal": t["name"], "joint": t["source_id"],
            "cmd_raw": raw, "cmd_deg": round(deg, 3),
            "reached_deg": round(session._read_deg(t["source_id"]), 3),
            "error_deg": round(err, 4) if err is not None else None,
            "tolerance_deg": round(tol_deg, 4),
            "settle_s": settle, "feedback_raw": fb,
            "pass": settle is not None})
    rep = {"format": "308sjk.io_acceptance/1",
           "mode": "扫描替身（MuJoCo 真实伺服；OpenPLC 未接入时）",
           "tolerances": {"angle_deg": round(tol_deg, 4), "timeout_s": timeout},
           "joints": results,
           "ok": all(r["pass"] for r in results) and len(results) > 0,
           "final_seq": session.seq}
    if out_json:
        json.dump(rep, open(out_json, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=2)
    return rep


# ================================================================ main

def full_run(aml_path, out_dir, resource_root=None):
    """解析→装配→IO 测试 全链（Web/CLI/测试共用）。返回汇总 dict。"""
    resource_root = resource_root or os.path.dirname(os.path.abspath(aml_path))
    model = parse_standard_aml(aml_path, resource_root)
    os.makedirs(out_dir, exist_ok=True)
    stats = build_scene(model, out_dir, resource_root)
    meshes = mesh_payload(model, resource_root)

    sigs = model["signals"]
    json.dump(modbus_channels(sigs), open(os.path.join(out_dir, "modbus.json"), "w",
                                        encoding="utf-8"), ensure_ascii=False, indent=2)
    xml = gen_plcopen_test(sigs, model["requirements"])
    open(os.path.join(out_dir, "io_test.xml"), "w", encoding="utf-8").write(xml)

    st, st_problems = None, ["xml2st 不可用"]
    try:
        sys.path.insert(0, os.path.join(os.path.dirname(HERE), "src", "pipeline"))
        import xml2st
        xp = os.path.join(out_dir, "io_test.xml")
        st_problems, st_model = xml2st.parse(xp)
        if not st_problems:
            st = xml2st.to_st(st_model)
            open(os.path.join(out_dir, "io_test.st"), "w", encoding="utf-8").write(st)
    except ImportError:
        st_problems = ["src/pipeline/xml2st.py 导入失败"]
    json.dump(gen_contract(sigs, stats),
              open(os.path.join(out_dir, "plc_test_contract.json"), "w",
                   encoding="utf-8"), ensure_ascii=False, indent=2)

    acceptance = None
    if stats.get("compile"):
        session = IOSession(stats["scene"], sigs, model["joints"], model["requirements"])
        acceptance = run_acceptance(session, os.path.join(out_dir, "report.json"))

    return {"model": model, "meshes": meshes, "scene_stats": stats,
            "st_generated": st is not None, "st_problems": st_problems,
            "acceptance": acceptance}


def main(argv=None):
    p = argparse.ArgumentParser(description="标准 AML 装配与 IO 绑定（全链）")
    p.add_argument("aml", help="含 IO 的标准 AML")
    p.add_argument("-o", "--out", required=True, help="输出目录")
    p.add_argument("--resource-root", default=None, help="资源根（缺省=AML 所在目录）")
    args = p.parse_args(argv)
    r = full_run(args.aml, args.out, args.resource_root)
    s, a = r["scene_stats"], r["acceptance"]
    print(("OK " if s.get("compile") and (a or {}).get("ok") else "FAIL ") + args.out)
    print(f"  组件 {len(r['model']['components'])} | 关节 {len(r['model']['joints'])} "
          f"| 信号 {len(r['model']['signals'])} | mesh {s.get('meshes')} "
          f"| body={s.get('nbody')} joint={s.get('njnt')} "
          f"act={s.get('nu')} sen={s.get('nsensor')}")
    print(f"  待确认项 {len(r['model']['pending'])} | ST 生成={r['st_generated']}"
          + ("" if r["st_generated"] else f"（{r['st_problems']}）"))
    if a:
        print(f"  IO 验收 {'过' if a['ok'] else '不过'}（{sum(x['pass'] for x in a['joints'])}"
              f"/{len(a['joints'])} 关节收敛，seq={a['final_seq']}）")
    for item in r["model"]["pending"][:5]:
        print("  - 待确认:", item)
    return 0 if s.get("compile") else 1


if __name__ == "__main__":
    sys.exit(main())
