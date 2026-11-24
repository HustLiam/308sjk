"""散装几何基座构建：关系树 + STL/DAE + 位姿表 + 关节表 → CAEX 2.15 基座 AML 树。

替代"资源基座 AML"的细颗粒输入（四件等价于打包形态）：
  tree.json      [{id, name, parent, type?}]            结构（组件层级）
  geometry.json  {id: {file, unit: "mm"|"m"}}           几何（STL/DAE/OBJ，按组件）
  poses.json     {id: {pos:[x,y,z], rpy_deg:[rx,ry,rz]}} 位姿（组件在父坐标系）
  joints.json    [{name, component, type:"hinge"|"slide",
                   axis:[x,y,z], anchor:[x,y,z], range:[lo,hi], velocity?}]  运动学

产物为内存中的 CAEX 树（ElementTree），直接交给 aml_configurator 的
audit/assemble/verify——与"基座 AML 文件"完全同构（InternalElement ID=tree id，
io.json 的 component_id 即 tree id；Frame 属性照 AmlDocument 原生格式）。
STL→DAE 转换是确定性步骤（trimesh+pycollada），单位归一到 m。
"""

import os
import xml.etree.ElementTree as ET

SIG_LIB = "AutomationMLInterfaceClassLib/AutomationMLBaseInterface"
COLLADA_IFACE = SIG_LIB + "/ExternalDataConnector/COLLADAInterface"


def load_parts(parts_dir):
    """读四件 json（geometry/poses/joints 任一缺失按空表处理，缺失项由 L1/L2 层提问）。"""
    import json

    def _load(name, default):
        p = os.path.join(parts_dir, name)
        return json.load(open(p, encoding="utf-8")) if os.path.isfile(p) else default

    return {"tree": _load("tree.json", []),
            "geometry": _load("geometry.json", {}),
            "poses": _load("poses.json", {}),
            "joints": _load("joints.json", [])}


def stl_to_dae(src, dst, unit="m"):
    """STL/OBJ → COLLADA .dae（单位归一 m）。确定性：同输入同输出。"""
    import trimesh
    mesh = trimesh.load(src, force="mesh")
    if unit == "mm":
        mesh.apply_scale(0.001)
    mesh.export(dst)
    return dst


def convert_geometries(parts, parts_dir, out_dir):
    """geometry.json 中非 DAE 的文件转 DAE 落 out_dir；返回 {id: rel_dae_path}。"""
    os.makedirs(out_dir, exist_ok=True)
    conv = {}
    for cid, g in parts["geometry"].items():
        f = g.get("file")
        if not f:                      # L2 补充的条目可能只有 unit——按 id 找同名几何
            for ext in (".stl", ".dae", ".obj"):
                cand = os.path.join(parts_dir, cid + ext)
                if os.path.isfile(cand):
                    f = cid + ext
                    break
        if not f:                      # 目录里也没有 → 该组件无几何（可选），跳过
            continue
        base, ext = os.path.splitext(os.path.basename(f))
        if ext.lower() == ".dae":
            conv[cid] = f
            continue
        src = f if os.path.isabs(f) else os.path.join(parts_dir, f)
        dst = os.path.join(out_dir, base + ".dae")
        stl_to_dae(src, dst, unit=g.get("unit", "m"))
        conv[cid] = os.path.relpath(dst, os.path.dirname(out_dir)) if False else \
            os.path.join(os.path.basename(out_dir), base + ".dae")
    return conv


def _attr(parent, name, value):
    a = ET.SubElement(parent, "Attribute", {"Name": name})
    ET.SubElement(a, "Value").text = str(value)


def build_base(parts, dae_refs, root_name="Cell"):
    """四件 → CAEX 2.15 基座树（IH→IE 嵌套 + Frame + COLLADAInterface + 关节属性）。"""
    root = ET.Element("CAEXFile", {"FileName": "assembled_base.aml", "SchemaVersion": "2.15"})
    ih = ET.SubElement(root, "InstanceHierarchy", {"Name": root_name})

    nodes = {n["id"]: n for n in parts["tree"]}
    children = {}
    roots = []
    for n in parts["tree"]:
        pid = n.get("parent")
        if pid and pid in nodes:
            children.setdefault(pid, []).append(n)
        else:
            roots.append(n)

    elems = {}

    def make(n):
        ie = ET.Element("InternalElement", {"ID": n["id"], "Name": n.get("name", n["id"])})
        if n.get("type"):
            ie.set("RefBaseSystemUnitPath", str(n["type"]))
        # Frame（位姿，AmlDocument 原生属性格式）
        pose = parts["poses"].get(n["id"])
        if pose:
            fr = ET.SubElement(ie, "Attribute", {"Name": "Frame"})
            for k, v in zip("xyz", pose.get("pos", [0, 0, 0])):
                _attr(fr, k, v)
            for k, v in zip(("rx", "ry", "rz"), pose.get("rpy_deg", [0, 0, 0])):
                _attr(fr, k, v)
        # 几何引用（COLLADAInterface refURI）
        dae = dae_refs.get(n["id"])
        if dae:
            ei = ET.SubElement(ie, "ExternalInterface", {
                "Name": "geom", "ID": f"geo_{n['id']}",
                "RefBaseClassPath": COLLADA_IFACE})
            a = ET.SubElement(ei, "Attribute", {
                "Name": "refURI", "AttributeDataType": "xs:anyURI"})
            ET.SubElement(a, "Value").text = f"file:///{dae}"
        elems[n["id"]] = ie
        for c in children.get(n["id"], []):
            ie.append(make(c))
        return ie

    for n in roots:
        ih.append(make(n))

    # 关节属性挂对应组件（Kinematics 组：axis/anchor/range/velocity）
    # CAEX sequence：Attribute 须在 ExternalInterface/InternalElement 之前——按序插入
    def _ordered(parent, child):
        rank = {"Attribute": 0, "ExternalInterface": 1, "InternalElement": 2}.get(child.tag, 3)
        for e in parent:
            if {"Attribute": 0, "ExternalInterface": 1, "InternalElement": 2}.get(e.tag, 3) > rank:
                parent.insert(list(parent).index(e), child)
                return
        parent.append(child)

    for j in parts["joints"]:
        comp = elems.get(j["component"])
        if comp is None:
            raise KeyError(f"joints[{j.get('name')}]: component {j['component']} 不在 tree 中")
        k = ET.Element("Attribute", {"Name": f"Kinematics:{j['name']}"})
        _ordered(comp, k)
        _attr(k, "type", j.get("type", "hinge"))
        _attr(k, "axis", " ".join(str(x) for x in j["axis"]))
        _attr(k, "anchor", " ".join(str(x) for x in j.get("anchor", [0, 0, 0])))
        rng = j.get("range")
        if rng:
            _attr(k, "range_min", rng[0])
            _attr(k, "range_max", rng[1])
        if j.get("velocity"):
            _attr(k, "velocity", j["velocity"])
    return root
