"""工作台仿真场景构建：复用 MJCF 组装器，消费同一任务的冻结输入。"""

import copy
import importlib.util
import json
from .task_inputs import load_task_spec
from pathlib import Path
import re
import math
import struct
import xml.etree.ElementTree as ET
import zlib


def geometry_transforms(data):
    return [list(map(float,p))+list(map(float,r.ravel())) for p,r in zip(data.geom_xpos,data.geom_xmat)]


def scene_geometry(path,snapshot=None):
    """导出真实 MJCF 几何；浏览器仅负责显示，不参与物理求解。"""
    import mujoco
    path=Path(path)
    model=mujoco.MjModel.from_xml_path(str(path));data=mujoco.MjData(model)
    if snapshot is not None:
        qpos=snapshot['qpos']
        if len(qpos)!=model.nq or not all(math.isfinite(v) for v in qpos):raise ValueError('运动快照不匹配')
        data.qpos[:]=qpos
    mujoco.mj_forward(model,data)
    center=list(map(float,model.stat.center));span=float(model.stat.extent)
    scene=json.loads(path.with_name('scene.spec.json').read_text(encoding='utf-8'))
    for asset in scene.get('assets',[]):
        if asset['type']=='gantry_xyz':
            x,y,z=asset.get('pose',{}).get('position',[0,0,0]);p=asset['params']
            center=[x+p['travel_x']/2,y+p['travel_y']/2,z+.12]
            span=max(p['travel_x'],p['travel_y'],.3)
            break
    return {'center':center,'span':span,'geoms':[
        {'type':int(model.geom_type[i]),'size':model.geom_size[i].tolist(),
         'color':model.geom_rgba[i].tolist()} for i in range(model.ngeom)],
        'transforms':geometry_transforms(data)}


def build_scene(directory):
    from runtime.mujoco_build import build_mjcf
    import mujoco

    # 包入口仍会导入旧 USD 组件；直接加载现有纯 Python 地址分配器，免除 pxr 依赖。
    module_spec = importlib.util.spec_from_file_location("workbench_iomap",
        Path(__file__).resolve().parents[2]/"scenegen/scenegen/iomap.py")
    iomap = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(iomap)

    directory = Path(directory)
    scene = json.loads((directory/"final/scene.spec.json").read_text(encoding="utf-8"))
    spec = load_task_spec(directory)
    physics = json.loads((directory/"assets/physics.json").read_text(encoding="utf-8"))
    xml = build_mjcf(scene, physics)
    model = mujoco.MjModel.from_xml_string(xml)
    # 仿真侧布局由现有分配器生成；PLC 面地址必须取自最终 XML，禁止按顺序重排。
    root = ET.parse(directory/"final/plcopen.xml").getroot()
    addresses = {v.get("name"):v.get("address") for v in root.iter()
                 if v.tag.rsplit("}",1)[-1]=="variable" and v.get("address")}
    ranges = {v["name"]:v.get("range") for v in spec.get("io_list", [])}
    io_map = iomap.assign_modbus(copy.deepcopy(scene["io_map"]))
    problems = []
    for channel in io_map:
        name = channel["plc_var"]
        address = addresses.get(name)
        if not address or not re.fullmatch(r"%QW\d+", address):
            channel["modbus"].pop("plc_addr", None)
            problems.append(name+" 缺少可连接的位置通道地址")
        else:
            channel["modbus"]["plc_addr"] = address
        value_range = ranges.get(name)
        if not isinstance(value_range, list) or len(value_range)!=2 or value_range[0]>=value_range[1]:
            problems.append(name+" 缺少工程量范围")
    for axis in "xyz":
        for suffix, direction in (("pos","input"),("cmd","output")):
            if not any(c["dir"]==direction and c["bind"]["quantity"]==axis+"_"+suffix for c in io_map):
                problems.append(axis+" 轴缺少"+("位置反馈" if direction=="input" else "位置指令"))
    target = directory/"simulation"
    target.mkdir(exist_ok=True)
    def save(name, content):
        path = target/name
        temporary = path.with_suffix(path.suffix+".tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    save("scene.xml", xml)
    mapped = dict(scene, io_map=io_map)
    save("scene.spec.json", json.dumps(mapped, ensure_ascii=False, indent=2))
    save("io_map.json", json.dumps(io_map, ensure_ascii=False, indent=2))
    result = {"status":"ready", "bodies":model.nbody, "joints":model.njnt,
              "actuators":model.nu, "channels":len(io_map),
              "runtime_ready":not problems, "runtime_problems":problems}
    save("build.json", json.dumps(result, ensure_ascii=False, indent=2))
    return result


def render_scene(path, azimuth=135, elevation=-35, distance=1.8, pan_x=0, pan_y=0, snapshot=None):
    """MuJoCo 离屏画面；可显示运行线程的真实位置快照，不在渲染线程推进物理。"""
    import mujoco

    if not all(math.isfinite(v) for v in (azimuth, elevation, distance, pan_x, pan_y)):
        raise ValueError("相机参数必须为有限数值")
    if not -360<=azimuth<=360 or not -89<=elevation<=-5 or not .3<=distance<=8 or not -4<=pan_x<=4 or not -4<=pan_y<=4:
        raise ValueError("相机参数超出范围")
    model = mujoco.MjModel.from_xml_path(str(path))
    data = mujoco.MjData(model)
    if snapshot is not None:
        values = snapshot["qpos"]
        if len(values)!=model.nq or not all(math.isfinite(v) for v in values):
            raise ValueError("运动快照与场景不匹配")
        data.qpos[:] = values
    mujoco.mj_forward(model, data)
    camera = mujoco.MjvCamera()
    camera.lookat[:] = model.stat.center
    scene = json.loads(Path(path).with_name("scene.spec.json").read_text(encoding="utf-8"))
    span = model.stat.extent
    for asset in scene.get("assets", []):
        if asset["type"]=="gantry_xyz":
            x,y,z = asset.get("pose",{}).get("position",[0,0,0])
            params = asset["params"]
            camera.lookat[:] = [x+params["travel_x"]/2,y+params["travel_y"]/2,z+.15]
            span = max(params["travel_x"],params["travel_y"],.3)
            break
    camera.azimuth, camera.elevation, camera.distance = azimuth, elevation, distance*span*1.3
    theta, tilt = math.radians(azimuth), math.radians(elevation)
    camera.lookat[0] += span*(-pan_x*math.sin(theta)-pan_y*math.cos(theta)*math.sin(tilt))
    camera.lookat[1] += span*(pan_x*math.cos(theta)-pan_y*math.sin(theta)*math.sin(tilt))
    camera.lookat[2] += span*pan_y*math.cos(tilt)
    model.vis.headlight.active = 1
    model.vis.headlight.ambient[:] = [.5,.5,.5]
    model.vis.headlight.diffuse[:] = [.8,.8,.8]
    model.vis.global_.offwidth = 960
    model.vis.global_.offheight = 640
    # GL 上下文在同一线程创建、渲染和销毁，沿用 CSP Studio 的生命周期。
    with mujoco.Renderer(model, height=640, width=960) as renderer:
        renderer.update_scene(data, camera=camera)
        if snapshot is not None:
            import numpy as np
            for position in snapshot.get("ink",[]):
                if renderer.scene.ngeom>=renderer.scene.maxgeom:
                    break
                geom = renderer.scene.geoms[renderer.scene.ngeom]
                mujoco.mjv_initGeom(geom,mujoco.mjtGeom.mjGEOM_SPHERE,
                    np.array([.002,.002,.002]),np.array(position),np.eye(3).flatten(),
                    np.array([.08,.12,.55,1],dtype=np.float32))
                renderer.scene.ngeom += 1
        rgb = renderer.render()
    def chunk(kind, data):
        return struct.pack(">I",len(data))+kind+data+struct.pack(">I",zlib.crc32(kind+data)&0xffffffff)
    pixels = b"".join(b"\0"+row.tobytes() for row in rgb)
    return (b"\x89PNG\r\n\x1a\n"+chunk(b"IHDR",struct.pack(">IIBBBBB",960,640,8,2,0,0,0))+
            chunk(b"IDAT",zlib.compress(pixels))+chunk(b"IEND",b""))
