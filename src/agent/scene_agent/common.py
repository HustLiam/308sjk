"""Canonical records, finite arithmetic and explicit coordinate transforms."""
import ast
import hashlib
import json
import math
from pathlib import Path

import numpy as np


class SceneError(ValueError):
    def __init__(self, message, layer='component_model', details=None):
        super().__init__(message)
        self.layer, self.details = layer, details or {}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode('utf-8')).hexdigest()


def read_json(path):
    import time
    for attempt in range(30):
        try:return json.loads(Path(path).read_text(encoding='utf-8-sig'))
        except PermissionError:
            if attempt==29:raise
            time.sleep(.002+min(attempt,.01))


def write_json(path, value):
    import uuid
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    content=json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n'
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(content,encoding='utf-8')
        # Windows readers briefly deny rename/delete sharing. Keep the old
        # complete document available while polling clients close their handle.
        import time
        for attempt in range(30):
            try:temporary.replace(path);break
            except PermissionError:
                if attempt==29:raise
                time.sleep(.002+min(attempt,.01))
    finally:
        temporary.unlink(missing_ok=True)


def confined(root, name):
    root, path = Path(root).resolve(), (Path(root)/name).resolve()
    if not path.is_relative_to(root):
        raise SceneError('Path escapes task directory: '+str(name))
    return path


def finalize_visual_inertia(body):
    """Zero-inertia fixed visuals must not ask MuJoCo to divide by total mass 0."""
    import xml.etree.ElementTree as ET
    geoms=body.findall('geom')
    if (geoms and body.find('joint') is None and body.find('freejoint') is None and body.find('inertial') is None
            and all(g.get('mass')=='0' for g in geoms)):
        ET.SubElement(body,'inertial',pos='0 0 0',mass='0',diaginertia='0 0 0')


def expression(value, parameters):
    """Data DSL: $parameter or =finite arithmetic. No Python eval or attributes."""
    if isinstance(value, list):
        return [expression(v, parameters) for v in value]
    if isinstance(value, dict):
        return {k: expression(v, parameters) for k,v in value.items()}
    if not isinstance(value, str):
        return value
    if value.startswith('$'):
        if value[1:] not in parameters:
            raise SceneError('Missing parameter '+value[1:], 'device_description')
        return parameters[value[1:]]
    if not value.startswith('='):
        return value
    functions = {'min': min, 'max': max, 'abs': abs, 'sin': math.sin, 'cos': math.cos,
                 'sqrt': math.sqrt, 'radians': math.radians}
    def visit(n):
        if isinstance(n, ast.Expression): return visit(n.body)
        if isinstance(n, ast.Constant) and type(n.value) in (int,float): return n.value
        if isinstance(n, ast.Name) and n.id in parameters and type(parameters[n.id]) in (int,float):
            return parameters[n.id]
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.USub,ast.UAdd)):
            return -visit(n.operand) if isinstance(n.op,ast.USub) else visit(n.operand)
        if isinstance(n, ast.BinOp):
            a,b=visit(n.left),visit(n.right)
            if isinstance(n.op,ast.Add): return a+b
            if isinstance(n.op,ast.Sub): return a-b
            if isinstance(n.op,ast.Mult): return a*b
            if isinstance(n.op,ast.Div): return a/b
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id in functions and not n.keywords:
            return functions[n.func.id](*(visit(a) for a in n.args))
        raise SceneError('Unsupported parameter expression: '+value)
    try:
        result=visit(ast.parse(value[1:],mode='eval'))
        if not math.isfinite(result): raise ValueError('not finite')
        return result
    except (KeyError, TypeError, ZeroDivisionError, ValueError, SyntaxError) as exc:
        raise SceneError('Invalid expression '+value+': '+str(exc)) from exc


def transform(position=(0,0,0), rpy=(0,0,0)):
    x,y,z = rpy
    cx,cy,cz,sx,sy,sz=math.cos(x),math.cos(y),math.cos(z),math.sin(x),math.sin(y),math.sin(z)
    t=np.eye(4)
    t[:3,:3]=[[cz*cy,cz*sy*sx-sz*cx,cz*sy*cx+sz*sx],
               [sz*cy,sz*sy*sx+cz*cx,sz*sy*cx-cz*sx],[-sy,cy*sx,cy*cx]]
    t[:3,3]=position
    return t


def pose_matrix(pose):
    return transform(pose.get('position_m',[0,0,0]),pose.get('rpy_rad',[0,0,0]))


def quaternion(rotation):
    import mujoco
    q=np.zeros(4)
    mujoco.mju_mat2Quat(q,np.asarray(rotation,dtype=float).reshape(9))
    return q.tolist()


def vector(values, size=3):
    if not isinstance(values,(list,tuple)) or len(values)!=size or any(type(v) not in (int,float) or not math.isfinite(v) for v in values):
        raise SceneError('Expected finite vector of length '+str(size), 'device_description')
    return np.asarray(values,dtype=float)
