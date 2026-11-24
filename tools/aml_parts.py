"""散装几何基座构建：关系树 + STL/DAE + 位姿表 + 关节表 → CAEX 2.15 基座 AML 树。

替代"资源基座 AML"的细颗粒输入（四件等价于打包形态）：
  tree.json      [{id, name, parent, type?}]            结构（组件层级）
  geometry.json  {id: {file, unit: "mm"|"m",
                       mass?, com?, inertia?, density?}} 几何（STL/DAE/OBJ）+ 可选物理
  poses.json     {id: {pos:[x,y,z], rpy_deg:[rx,ry,rz]}} 位姿（组件在父坐标系）
  joints.json    [{name, component, type:"hinge"|"slide",
                   axis:[x,y,z], anchor:[x,y,z], range:[lo,hi],
                   velocity?, torque?}]                 运动学（规格参数）

产物为内存中的 CAEX 树（ElementTree），直接交给 aml_configurator 的
audit/assemble/verify——与"基座 AML 文件"完全同构（InternalElement ID=tree id，
io.json 的 component_id 即 tree id；Frame 属性照 AmlDocument 原生格式）。
STL→DAE 转换是确定性步骤（trimesh+pycollada），单位归一到 m。

IEC 62714 符合性（2026-11-23 对官方 RobotCell 实例复查后补齐）：
  1. 运动学除 AML Kinematics:* 属性（项目内部约定，gc/仿真侧消费）外，
     另生成标准载体 kinematics.dae（COLLADA 1.5 library_joints/
     kinematics_models/articulated_systems/kinematics_scenes，结构照官方
     kin DAE），AML 侧经 COLLADAInterface refURI#fragment 引用（refType=implicit，
     不被 gc 资源导入器当作几何加载）；
  2. 自包含：产物内联声明其引用的全部 AutomationML 类库（官方 AmlDocument 同法），
     含官方库没有、本项目信号接口需要的 AnalogInput/AnalogOutput/DigitalInput/
     DigitalOutput/PortInterface；
  3. WriterHeader 声明实际写出工具；LastWritingDateTime 用固定纪元保持确定性
     （L3 同输入同输出，不写真实时间戳）。
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
            os.path.join(os.path.basename(out_dir), base + ".dae").replace(os.sep, "/")
    return conv


def _attr(parent, name, value):
    a = ET.SubElement(parent, "Attribute", {"Name": name})
    ET.SubElement(a, "Value").text = str(value)


# ---------------------------------------------------------------- IEC 62714 运动学
# 官方 RobotCell kin DAE 的结构蓝本（COLLADA 1.5）：
#   library_joints（revolute|prismatic + axis + limits，角度用度）
#   library_kinematics_models（instance_joint + link 树，关节子件经 attachment_full 挂接，
#     刚体子件折叠进最近关节祖先 link——官方 Festanteile 同法）
#   library_articulated_systems / library_kinematics_scenes（SIDREF/bind 参数链）
COLLADA15_NS = "http://www.collada.org/2008/03/COLLADASchema"
TOOL_NAME = "308sjk aml_configurator"
TOOL_VERSION = "1.1"
WRITER_EPOCH = "1970-01-01T00:00:00+00:00"   # 确定性纪元（不写真实时间戳）


def _mm(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)] for i in range(3)]


def _rpy_matrix(rpy_deg):
    """rpy（外旋 x-y-z，度）→ 3x3 旋转矩阵，与 MJCF euler xyz 同约定。"""
    import math
    rx, ry, rz = (math.radians(v) for v in rpy_deg)
    cx, sx, cy, sy, cz, sz = (math.cos(rx), math.sin(rx), math.cos(ry),
                              math.sin(ry), math.cos(rz), math.sin(rz))
    return _mm([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]],
               _mm([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]],
                   [[1, 0, 0], [0, cx, -sx], [0, sx, cx]]))


def _mv(r, v):
    return [sum(r[i][k] * v[k] for k in range(3)) for i in range(3)]


def _rot_axis_angle(r):
    """3x3 旋转矩阵 → (axis, angle_deg)；恒等返回 None。"""
    import math
    tr = r[0][0] + r[1][1] + r[2][2]
    ang = math.degrees(math.acos(max(-1.0, min(1.0, (tr - 1) / 2))))
    if ang < 1e-9:
        return None
    if 180.0 - ang < 1e-6:                     # 奇异角：由最大对角项重构轴
        i = max(range(3), key=lambda k: r[k][k])
        ax = [0.0, 0.0, 0.0]
        ax[i] = math.sqrt(max(0.0, (r[i][i] + 1) / 2))
        for j in range(3):
            if j != i:
                ax[j] = r[j][i] / (2 * ax[i]) if ax[i] > 1e-12 else 0.0
        n = math.sqrt(sum(x * x for x in ax)) or 1.0
        return [x / n for x in ax], 180.0
    s = math.sqrt(max(1e-30, (1 + tr) * 2))    # = 2*cos(ang/2)*? 标准公式 2*sin
    axis = [(r[2][1] - r[1][2]) / s, (r[0][2] - r[2][0]) / s, (r[1][0] - r[0][1]) / s]
    n = math.sqrt(sum(x * x for x in axis))
    if n < 1e-12:
        return None
    return [x / n for x in axis], ang


def _fmt(v):
    return "%g" % float(v)


def _place(parent, pos, rot):
    """COLLADA 节点放置：translate + rotate（axis-angle，度）。零变换省略。"""
    tag = lambda n: f"{{{COLLADA15_NS}}}{n}"
    if any(abs(v) > 1e-12 for v in pos):
        ET.SubElement(parent, tag("translate")).text = " ".join(_fmt(v) for v in pos)
    aa = _rot_axis_angle(rot)
    if aa:
        axis, ang = aa
        ET.SubElement(parent, tag("rotate")).text = \
            " ".join(_fmt(v) for v in axis) + " " + _fmt(ang)


def build_kin_dae(parts, out_dir, root_name="Cell", model_id="kinematics_model"):
    """joints+tree+poses → COLLADA 1.5 kinematics.dae（IEC 62714 Part 3 标准载体）。

    返回 {"rel": 相对路径, "model_id":…, "joints": n}（无关节返回 None）。
    rel 供 build_base 写 COLLADAInterface refURI=rel#model_id。
    """
    if not parts["joints"]:
        return None
    import math
    ET.register_namespace("", COLLADA15_NS)
    tag = lambda n: f"{{{COLLADA15_NS}}}{n}"
    root = ET.Element(tag("COLLADA"), {"version": "1.5.0"})

    asset = ET.SubElement(root, tag("asset"))
    contrib = ET.SubElement(asset, tag("contributor"))
    ET.SubElement(contrib, tag("authoring_tool")).text = TOOL_NAME
    ET.SubElement(asset, tag("created")).text = WRITER_EPOCH
    ET.SubElement(asset, tag("modified")).text = WRITER_EPOCH
    ET.SubElement(asset, tag("unit"), {"meter": "1", "name": "m"})
    ET.SubElement(asset, tag("up_axis")).text = "Z_UP"

    # ① library_joints：关节规格（轴向/限位；revolute 度、prismatic 米，与官方一致）
    lib_j = ET.SubElement(root, tag("library_joints"))
    for j in parts["joints"]:
        jid = f"{model_id}_{j['name']}"
        je = ET.SubElement(lib_j, tag("joint"), {"id": jid, "name": j["name"]})
        kind = "revolute" if j.get("type", "hinge") == "hinge" else "prismatic"
        re_ = ET.SubElement(je, tag(kind), {"sid": "axis"})
        ET.SubElement(re_, tag("axis")).text = " ".join(_fmt(v) for v in j["axis"])
        if j.get("range"):
            lim = ET.SubElement(re_, tag("limits"))
            ET.SubElement(lim, tag("min")).text = _fmt(j["range"][0])
            ET.SubElement(lim, tag("max")).text = _fmt(j["range"][1])

    # ② library_kinematics_models：link 树（关节子件 attachment_full；刚体折叠）
    lib_m = ET.SubElement(root, tag("library_kinematics_models"))
    km = ET.SubElement(lib_m, tag("kinematics_model"), {"id": model_id, "name": root_name})
    tc = ET.SubElement(km, tag("technique_common"))
    for j in parts["joints"]:
        ET.SubElement(tc, tag("instance_joint"),
                      {"sid": j["name"], "url": f"#{model_id}_{j['name']}"})

    nodes = {n["id"]: n for n in parts["tree"]}
    children = {}
    for n in parts["tree"]:
        pid = n.get("parent")
        if pid and pid in nodes:
            children.setdefault(pid, []).append(n)
    joint_by_comp = {j["component"]: j for j in parts["joints"]}
    eye = [[1, 0, 0], [0, 1, 0], [0, 0, 1]]

    def attach(parent_el, node, rot, trans):
        """node 子树挂 parent_el：有关节 → attachment_full；无关节 → 折叠传递累积变换。"""
        pose = parts["poses"].get(node["id"], {})
        pr = _rpy_matrix(pose.get("rpy_deg", [0, 0, 0]))
        pt = pose.get("pos", [0, 0, 0])
        n_rot, n_tr = _mm(rot, pr), [a + b for a, b in zip(_mv(rot, pt), trans)]
        if node["id"] in joint_by_comp:
            j = joint_by_comp[node["id"]]
            af = ET.SubElement(parent_el, tag("attachment_full"),
                               {"joint": f"{model_id}/{j['name']}"})
            _place(af, n_tr, n_rot)
            link = ET.SubElement(af, tag("link"),
                                 {"name": node["id"], "sid": node["id"]})
            for c in children.get(node["id"], []):
                attach(link, c, eye, [0, 0, 0])
        else:
            for c in children.get(node["id"], []):
                attach(parent_el, c, n_rot, n_tr)

    for n in parts["tree"]:                    # 树根：顶层 link（带自身位姿）
        if not (n.get("parent") and n["parent"] in nodes):
            pose = parts["poses"].get(n["id"], {})
            link = ET.SubElement(tc, tag("link"), {"name": n["id"], "sid": n["id"]})
            _place(link, pose.get("pos", [0, 0, 0]),
                   _rpy_matrix(pose.get("rpy_deg", [0, 0, 0])))
            for c in children.get(n["id"], []):
                attach(link, c, eye, [0, 0, 0])

    # ③ library_articulated_systems / ④ library_kinematics_scenes（官方参数链模式）
    art_id, inst = f"{model_id}_art", f"inst_{model_id}"
    lib_a = ET.SubElement(root, tag("library_articulated_systems"))
    art = ET.SubElement(lib_a, tag("articulated_system"), {"id": art_id})
    akins = ET.SubElement(art, tag("kinematics"))
    atc = ET.SubElement(akins, tag("technique_common"))
    ikm = ET.SubElement(atc, tag("instance_kinematics_model"),
                        {"sid": inst, "url": f"#{model_id}"})
    for j in parts["joints"]:
        np_ = ET.SubElement(ikm, tag("newparam"),
                            {"sid": f"{art_id}_{inst}_{j['name']}_axis"})
        ET.SubElement(np_, tag("SIDREF")).text = f"{art_id}/{inst}/{j['name']}/axis"
    lib_s = ET.SubElement(root, tag("library_kinematics_scenes"))
    ks = ET.SubElement(lib_s, tag("kinematics_scene"), {"id": f"{model_id}_scene"})
    stc = ET.SubElement(ks, tag("technique_common"))
    ias = ET.SubElement(stc, tag("instance_articulated_system"),
                        {"sid": f"inst_{art_id}", "url": f"#{art_id}"})
    for j in parts["joints"]:
        b = ET.SubElement(ias, tag("bind"),
                          {"symbol": f"{model_id}_scene_inst_{art_id}_{inst}_{j['name']}_axis"})
        ET.SubElement(b, tag("param"),
                      {"ref": f"{art_id}_{inst}_{j['name']}_axis"})
    scene = ET.SubElement(root, tag("scene"))
    ET.SubElement(scene, tag("instance_kinematics_scene"), {"url": f"#{model_id}_scene"})

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "kinematics.dae")
    tree = ET.ElementTree(root)
    ET.indent(tree, space="\t")
    tree.write(path, encoding="utf-8", xml_declaration=True)
    return {"rel": os.path.join(os.path.basename(out_dir), "kinematics.dae").replace(os.sep, "/"),
            "model_id": model_id, "joints": len(parts["joints"]),
            "revolute_deg": sum(1 for j in parts["joints"]
                                if j.get("type", "hinge") == "hinge")}


# ---------------------------------------------------------------- 自包含类库
def _std_interface_lib():
    """最小 AutomationMLInterfaceClassLib（照官方内联库裁剪 + 本项目信号类）。"""
    lib = ET.Element("InterfaceClassLib", {"Name": "AutomationMLInterfaceClassLib"})
    ET.SubElement(lib, "Description").text = \
        "Standard AutomationML Interface Class Library (minimal subset, inlined for self-containment)"
    ET.SubElement(lib, "Version").text = "2.2.2"
    base = ET.SubElement(lib, "InterfaceClass", {"Name": "AutomationMLBaseInterface"})

    def ic(parent, name, ref, attrs=()):
        e = ET.SubElement(parent, "InterfaceClass",
                          {"Name": name, "RefBaseClassPath": ref})
        for an, at in attrs:
            ET.SubElement(e, "Attribute", {"Name": an, "AttributeDataType": at})
        return e

    edc = ic(base, "ExternalDataConnector", "AutomationMLBaseInterface",
             [("refURI", "xs:anyURI")])
    ic(edc, "COLLADAInterface", "ExternalDataConnector",
       [("refType", "xs:string"), ("target", "xs:token")])
    plcx = ic(edc, "PLCopenXMLInterface", "ExternalDataConnector")
    li = ic(plcx, "LogicInterface", "PLCopenXMLInterface")
    full = "AutomationMLInterfaceClassLib/AutomationMLBaseInterface/ExternalDataConnector/PLCopenXMLInterface"
    ic(li, "SequencingLogicInterface", full + "/LogicInterface")
    ic(li, "BehaviourLogicInterface", full + "/LogicInterface")
    com = ic(base, "Communication", "AutomationMLBaseInterface")
    ic(com, "SignalInterface", "Communication")
    ic(base, "AttachmentInterface", "AutomationMLBaseInterface")
    ic(base, "PortInterface", "AutomationMLBaseInterface")
    # 本项目信号接口类（官方最小库未含；io 层按名称引用，须内联声明才可解析）
    for n in ("AnalogInput", "AnalogOutput", "DigitalInput", "DigitalOutput"):
        ic(base, n, "AutomationMLBaseInterface")
    return lib


def _std_role_lib():
    lib = ET.Element("RoleClassLib", {"Name": "AutomationMLBaseRoleClassLib"})
    ET.SubElement(lib, "Description").text = \
        "AutomationML Base Role Class Library (minimal subset, inlined for self-containment)"
    ET.SubElement(lib, "Version").text = "2.2.1"
    base = ET.SubElement(lib, "RoleClass", {"Name": "AutomationMLBaseRole"})
    ET.SubElement(base, "RoleClass",
                  {"Name": "Resource", "RefBaseClassPath": "AutomationMLBaseRole"})
    st = ET.SubElement(base, "RoleClass",
                       {"Name": "Structure", "RefBaseClassPath": "AutomationMLBaseRole"})
    ET.SubElement(st, "RoleClass",
                  {"Name": "ResourceStructure", "RefBaseClassPath": "Structure"})
    return lib


def append_std_libs(root):
    """补 Version/WriterHeader/内联类库，使散装产物自包含（幂等，按 CAEX 顶层序插入）。"""
    if root.find("Version") is None:
        v = ET.Element("Version")
        v.text = "2.0"
        root.insert(0, v)
    ai = ET.Element("AdditionalInformation", {"AutomationMLVersion": "2.0"})
    root.insert(list(root).index(root.find("Version")) + 1, ai)
    wh_wrap = ET.Element("AdditionalInformation")
    root.insert(list(root).index(ai) + 1, wh_wrap)
    wh = ET.SubElement(wh_wrap, "WriterHeader")
    for k, v in (("WriterName", TOOL_NAME), ("WriterID", TOOL_NAME),
                 ("WriterVendor", TOOL_NAME), ("WriterVendorURL",
                                               "https://github.com/HustLiam/308sjk"),
                 ("WriterVersion", TOOL_VERSION), ("WriterRelease", TOOL_VERSION),
                 ("LastWritingDateTime", WRITER_EPOCH)):
        ET.SubElement(wh, k).text = v
    # 类库排在 InstanceHierarchy 之后（CAEXFile sequence）
    ihs = [e for e in root if e.tag == "InstanceHierarchy"]
    anchor = list(root).index(ihs[-1]) + 1 if ihs else list(root).index(wh_wrap) + 1
    root.insert(anchor, _std_interface_lib())
    root.insert(anchor + 1, _std_role_lib())


def ensure_signal_classes(root):
    """打包基座的内联库补声明本项目信号类（AnalogInput 等，官方库未含）。

    幂等：遍历 InterfaceClassLib[AutomationMLInterfaceClassLib]→
    InterfaceClass[AutomationMLBaseInterface]，缺哪个补哪个。
    """
    for lib in root.iter("InterfaceClassLib"):
        if lib.get("Name") != "AutomationMLInterfaceClassLib":
            continue
        for base in lib.findall("InterfaceClass"):
            if base.get("Name") != "AutomationMLBaseInterface":
                continue
            have = {c.get("Name") for c in base.findall("InterfaceClass")}
            for n in ("PortInterface", "AnalogInput", "AnalogOutput",
                      "DigitalInput", "DigitalOutput"):
                if n not in have:
                    base.append(ET.Element(
                        "InterfaceClass", {"Name": n,
                                           "RefBaseClassPath": "AutomationMLBaseInterface"}))
    return root


def build_base(parts, dae_refs, root_name="Cell", kin_ref=None):
    """四件 → CAEX 2.15 基座树（IH→IE 嵌套 + Frame + COLLADAInterface + 关节属性
    + 可选物理属性 + RoleRequirements + 自包含类库 + kin DAE 引用）。"""
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
        # 物理属性（可选：mass kg / com m（组件系）/ inertia kg·m² / density kg/m³）
        g = parts["geometry"].get(n["id"], {})
        phys = {k: g[k] for k in ("mass", "com", "inertia", "density") if g.get(k) is not None}
        if phys:
            ph = ET.SubElement(ie, "Attribute", {"Name": "Physical"})
            for k, v in phys.items():
                _attr(ph, k, " ".join(str(x) for x in v) if isinstance(v, list) else v)
        # 几何引用（COLLADAInterface refURI）
        dae = dae_refs.get(n["id"])
        if dae:
            ei = ET.SubElement(ie, "ExternalInterface", {
                "Name": "geom", "ID": f"geo_{n['id']}",
                "RefBaseClassPath": COLLADA_IFACE})
            a = ET.SubElement(ei, "Attribute", {
                "Name": "refURI", "AttributeDataType": "xs:anyURI"})
            ET.SubElement(a, "Value").text = f"file:///{dae}"
        # 运动学标准载体引用（refType=implicit：gc 资源导入器仅加载 explicit 几何）
        if kin_ref and roots and n["id"] == roots[0]["id"]:
            ei = ET.SubElement(ie, "ExternalInterface", {
                "Name": "Kinematics", "ID": "kin_ref",
                "RefBaseClassPath": COLLADA_IFACE})
            a = ET.SubElement(ei, "Attribute", {
                "Name": "refURI", "AttributeDataType": "xs:anyURI"})
            ET.SubElement(a, "Value").text = \
                f"file:///{kin_ref['rel']}#{kin_ref['model_id']}"
            _attr(ei, "refType", "implicit")
        elems[n["id"]] = ie
        for c in children.get(n["id"], []):
            ie.append(make(c))
        # RoleRequirements（官方惯例：叶子=Resource，结构组=Structure/ResourceStructure）
        rr = ("AutomationMLBaseRoleClassLib/AutomationMLBaseRole/Structure/ResourceStructure"
              if children.get(n["id"]) else
              "AutomationMLBaseRoleClassLib/AutomationMLBaseRole/Resource")
        ET.SubElement(ie, "RoleRequirements", {"RefBaseRoleClassPath": rr})
        return ie

    for n in roots:
        ih.append(make(n))

    # 关节属性挂对应组件（Kinematics 组：type/axis/anchor/range/velocity/torque）
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
        if j.get("torque"):
            _attr(k, "torque", j["torque"])
    append_std_libs(root)
    return root
