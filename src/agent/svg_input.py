"""静态 SVG 轮廓 → 确定性 PLC 轨迹；仅使用标准库，不执行 SVG 内容。

几何：M/L/H/V/C/S/Q/T/A/Z、基础形状与仿射变换。按指定设备坐标框等比
适配；曲线按设备坐标容差展开，最后量化为现有 INT 坐标。源码只用于归档，
不会直接嵌入预览页面。颜色、填充和笔画宽度不转换为加工指令。
"""

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .trajectory import AREA, PEN_Z, VMAX

_NUM = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_IDENTITY = (1., 0., 0., 1., 0., 0.)
_UNITS = {"": 1., "px": 1., "mm": 96 / 25.4, "cm": 96 / 2.54,
          "in": 96., "pt": 96 / 72, "pc": 16., "q": 96 / 101.6}
_SKIP = {"defs", "metadata", "title", "desc"}
_SHAPES = {"path", "line", "polyline", "polygon", "rect", "circle", "ellipse"}
_MAX_BYTES = 2 * 1024 * 1024
_MAX_SEGMENTS = 20000


class SVGError(ValueError):
    """可直接显示给用户的 SVG 输入错误。"""


def _finite(value):
    if isinstance(value, bool):
        raise SVGError("SVG 坐标/尺寸不能是布尔值")
    try:
        value = float(value)
    except (TypeError, ValueError) as exc:
        raise SVGError("SVG 坐标/尺寸必须是数值") from exc
    if not math.isfinite(value) or abs(value) > 1e12:
        raise SVGError("SVG 数值必须有限，绝对值不超过 1e12")
    return value


def _mul(a, b):
    aa, ab, ac, ad, ae, af = a
    ba, bb, bc, bd, be, bf = b
    return tuple(_finite(v) for v in (aa*ba+ac*bb, ab*ba+ad*bb,
        aa*bc+ac*bd, ab*bc+ad*bd, aa*be+ac*bf+ae, ab*be+ad*bf+af))


def _point(matrix, point):
    a, b, c, d, e, f = matrix
    return (_finite(a*point[0]+c*point[1]+e), _finite(b*point[0]+d*point[1]+f))


class _Scanner:
    def __init__(self, text):
        self.text, self.pos = text, 0

    def peek(self):
        while self.pos < len(self.text) and self.text[self.pos] in " \t\r\n,":
            self.pos += 1
        return self.text[self.pos:self.pos+1]

    def number(self):
        self.peek()
        match = _NUM.match(self.text, self.pos)
        if not match:
            raise SVGError("SVG 路径在位置 %d 缺少数值" % self.pos)
        self.pos = match.end()
        return _finite(match[0])

    def flag(self):
        char = self.peek()
        if char not in ("0", "1"):
            raise SVGError("SVG A 命令的 large-arc/sweep 标志必须为 0 或 1")
        self.pos += 1
        return int(char)


def _numbers(text):
    scanner, result = _Scanner(text), []
    while scanner.peek():
        result.append(scanner.number())
    return result


def _transform(text):
    matrix, position = _IDENTITY, 0
    for match in re.finditer(r"([A-Za-z]+)\s*\(([^()]*)\)", text):
        if text[position:match.start()].strip(" ,\t\r\n"):
            raise SVGError("SVG transform 包含未识别内容")
        name, values = match[1], _numbers(match[2])
        if name == "matrix" and len(values) == 6:
            part = tuple(values)
        elif name == "translate" and len(values) in (1, 2):
            part = (1., 0., 0., 1., values[0], values[1] if len(values) == 2 else 0.)
        elif name == "scale" and len(values) in (1, 2):
            part = (values[0], 0., 0., values[-1], 0., 0.)
        elif name == "rotate" and len(values) in (1, 3):
            angle = math.radians(values[0])
            part = (math.cos(angle), math.sin(angle), -math.sin(angle), math.cos(angle), 0., 0.)
            if len(values) == 3:
                x, y = values[1:]
                part = _mul(_mul((1, 0, 0, 1, x, y), part), (1, 0, 0, 1, -x, -y))
        elif name in ("skewX", "skewY") and len(values) == 1:
            tangent = _finite(math.tan(math.radians(values[0])))
            part = (1, 0, tangent, 1, 0, 0) if name == "skewX" else (1, tangent, 0, 1, 0, 0)
        else:
            raise SVGError("不支持的 SVG transform 或参数：%s" % name)
        matrix, position = _mul(matrix, part), match.end()
    if text[position:].strip(" ,\t\r\n"):
        raise SVGError("SVG transform 无法解析")
    return matrix


def _length(text):
    match = re.fullmatch(r"\s*(" + _NUM.pattern + r")\s*([A-Za-z]*)\s*", str(text))
    if not match or match[2].lower() not in _UNITS:
        raise SVGError("不支持的 SVG 长度 %r；请导出绝对坐标路径" % text)
    return _finite(_finite(match[1]) * _UNITS[match[2].lower()])


def _arc(start, end, rx, ry, rotation, large, sweep):
    try:
        return _arc_geometry(start, end, rx, ry, rotation, large, sweep)
    except (ArithmeticError, ValueError) as exc:
        raise SVGError("SVG 椭圆弧数值无法稳定计算，请调整半径或简化路径") from exc


def _arc_geometry(start, end, rx, ry, rotation, large, sweep):
    """SVG endpoint representation → center + two ellipse basis vectors."""
    if start == end:
        return None
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return ("L", start, end)
    phi = math.radians(rotation % 360)
    co, si = math.cos(phi), math.sin(phi)
    dx, dy = (start[0]-end[0])/2, (start[1]-end[1])/2
    x, y = co*dx+si*dy, -si*dx+co*dy
    ratio = (x/rx)**2 + (y/ry)**2
    if ratio > 1:
        factor = math.sqrt(ratio)
        rx, ry = rx*factor, ry*factor
    denom = (rx*y)**2 + (ry*x)**2
    factor = math.sqrt(max(0., ((rx*ry)**2-denom)/denom)) if denom else 0.
    if large == sweep:
        factor = -factor
    cx0, cy0 = factor*rx*y/ry, -factor*ry*x/rx
    center = (co*cx0-si*cy0+(start[0]+end[0])/2, si*cx0+co*cy0+(start[1]+end[1])/2)
    u0, v0 = (x-cx0)/rx, (y-cy0)/ry
    u1, v1 = (-x-cx0)/rx, (-y-cy0)/ry
    theta = math.atan2(v0, u0)
    delta = math.atan2(u0*v1-v0*u1, u0*u1+v0*v1)
    if sweep and delta < 0:
        delta += 2*math.pi
    elif not sweep and delta > 0:
        delta -= 2*math.pi
    return ("A", start, end, center, (rx*co, rx*si), (-ry*si, ry*co), theta, delta)


def _path_data(data):
    scanner, paths, path = _Scanner(data), [], []
    current, beginning, control = (0., 0.), None, None
    previous, command = None, None
    while scanner.peek():
        if scanner.peek().isalpha():
            command = scanner.peek()
            scanner.pos += 1
        if command is None or command.upper() not in "MLHVCSQTAZ":
            raise SVGError("不支持或缺少 SVG 路径命令：%r" % command)
        kind, relative = command.upper(), command.islower()
        if kind == "Z":
            if beginning is None:
                raise SVGError("Z 命令之前必须有 M 命令")
            if current != beginning:
                path.append(("L", current, beginning))
            current, control, previous, command = beginning, None, "Z", None
            continue
        if kind != "M" and beginning is None:
            raise SVGError("SVG 路径必须从 M/m 命令开始")

        def xy():
            x, y = scanner.number(), scanner.number()
            return (_finite(x+current[0]), _finite(y+current[1])) if relative else (x, y)

        segment, next_control = None, None
        if kind == "M":
            target = xy()
            if path:
                paths.append(path)
            path, current, beginning = [], target, target
            command = "l" if relative else "L"
        elif kind in ("L", "H", "V"):
            if kind == "L":
                target = xy()
            elif kind == "H":
                x = scanner.number()
                target = (_finite(current[0]+x) if relative else x, current[1])
            else:
                y = scanner.number()
                target = (current[0], _finite(current[1]+y) if relative else y)
            segment = ("L", current, target)
        elif kind in ("C", "S"):
            first = xy() if kind == "C" else ((2*current[0]-control[0], 2*current[1]-control[1])
                    if previous in ("C", "S") and control is not None else current)
            second, target = xy(), xy()
            segment, next_control = ("C", current, first, second, target), second
        elif kind in ("Q", "T"):
            first = xy() if kind == "Q" else ((2*current[0]-control[0], 2*current[1]-control[1])
                    if previous in ("Q", "T") and control is not None else current)
            target = xy()
            segment, next_control = ("Q", current, first, target), first
        elif kind == "A":
            rx, ry, rotation = scanner.number(), scanner.number(), scanner.number()
            large, sweep = scanner.flag(), scanner.flag()
            target = xy()
            segment = _arc(current, target, rx, ry, rotation, large, sweep)
        if segment is not None:
            path.append(segment)
        if kind != "M":
            current = target
        control, previous = next_control, kind
        if len(path) > _MAX_SEGMENTS or len(paths) > _MAX_SEGMENTS:
            raise SVGError("SVG 路径过于复杂，请先简化")
    if path:
        paths.append(path)
    return paths


def _shape_paths(element, name):
    def value(key, default=0.):
        return _length(element.get(key, default))
    if name == "path":
        return _path_data(element.get("d", ""))
    if name in ("polyline", "polygon"):
        values = _numbers(element.get("points", ""))
        if len(values) % 2:
            raise SVGError("SVG points 必须包含成对坐标")
        points = list(zip(values[::2], values[1::2]))
        if name == "polygon" and points:
            points.append(points[0])
    elif name == "line":
        points = [(value("x1"), value("y1")), (value("x2"), value("y2"))]
    elif name == "rect":
        x, y, width, height = value("x"), value("y"), value("width"), value("height")
        if width < 0 or height < 0:
            raise SVGError("矩形宽高不能为负")
        if not width or not height:
            return []
        rx, ry = value("rx", element.get("ry", 0)), value("ry", element.get("rx", 0))
        if rx < 0 or ry < 0:
            raise SVGError("矩形圆角不能为负")
        rx, ry = min(rx, width/2), min(ry, height/2)
        if rx and ry:
            return _path_data("M %g %g H %g A %g %g 0 0 1 %g %g V %g A %g %g 0 0 1 %g %g H %g A %g %g 0 0 1 %g %g V %g A %g %g 0 0 1 %g %g Z" %
                (x+rx, y, x+width-rx, rx, ry, x+width, y+ry, y+height-ry, rx, ry,
                 x+width-rx, y+height, x+rx, rx, ry, x, y+height-ry, y+ry, rx, ry, x+rx, y))
        points = [(x, y), (x+width, y), (x+width, y+height), (x, y+height), (x, y)]
    else:
        cx, cy = value("cx"), value("cy")
        rx = ry = value("r") if name == "circle" else 0
        if name == "ellipse":
            rx, ry = value("rx"), value("ry")
        if rx < 0 or ry < 0:
            raise SVGError("圆/椭圆半径不能为负")
        if not rx or not ry:
            return []
        start, end = (cx+rx, cy), (cx-rx, cy)
        return [[_arc(start, end, rx, ry, 0, 0, 1), _arc(end, start, rx, ry, 0, 0, 1)]]
    return [[("L", a, b) for a, b in zip(points, points[1:])]] if len(points) > 1 else []


def _map_segment(segment, matrix):
    if segment[0] != "A":
        return (segment[0], *(_point(matrix, p) for p in segment[1:]))
    _, start, end, center, u, v, theta, delta = segment
    a, b, c, d, _, _ = matrix
    return ("A", _point(matrix, start), _point(matrix, end), _point(matrix, center),
            (_finite(a*u[0]+c*u[1]), _finite(b*u[0]+d*u[1])),
            (_finite(a*v[0]+c*v[1]), _finite(b*v[0]+d*v[1])), theta, delta)


def parse_svg(data, metadata=None):
    """解析静态几何；对会改变轮廓但未支持的可见内容明确拒绝。"""
    data = data.encode("utf-8") if isinstance(data, str) else data
    if len(data) > _MAX_BYTES:
        raise SVGError("SVG 文件超过 2 MiB，请先简化")
    upper = data.replace(b"\x00", b"").upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise SVGError("不支持 SVG DTD/实体定义，请导出普通静态 SVG")
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise SVGError("SVG XML 无法解析：%s" % exc) from exc
    if root.tag not in ("svg", "{http://www.w3.org/2000/svg}svg"):
        raise SVGError("输入文件根元素必须是 SVG")
    root_matrix = _IDENTITY
    view_box = _numbers(root.get("viewBox", ""))
    if view_box and (len(view_box) != 4 or view_box[2] <= 0 or view_box[3] <= 0):
        raise SVGError("viewBox 必须为 min-x min-y 正宽 正高")
    # meet/slice 均匀缩放和位移会被最终适配抵消；none 的长宽比变化必须保留。
    if root.get("preserveAspectRatio", "").strip() == "none" and view_box:
        width = _length(root.get("width", view_box[2]))
        height = _length(root.get("height", view_box[3]))
        if width <= 0 or height <= 0:
            raise SVGError("SVG viewport 宽高必须为正")
        root_matrix = (width/view_box[2], 0, 0, height/view_box[3],
                       -view_box[0]*width/view_box[2], -view_box[1]*height/view_box[3])
    paths, count = [], 0

    def visit(element, parent, inherited, depth):
        nonlocal count
        if depth > 64:
            raise SVGError("SVG 分组嵌套超过 64 层")
        if not isinstance(element.tag, str):
            return
        if element.tag.startswith("{") and not element.tag.startswith("{http://www.w3.org/2000/svg}"):
            return
        name = element.tag.rsplit("}", 1)[-1]
        if name in _SKIP:
            return
        style = dict(inherited)
        for key in ("display", "visibility"):
            if key in element.attrib:
                style[key] = element.attrib[key]
        inline = dict(part.split(":", 1) for part in element.get("style", "").split(";") if ":" in part)
        inline = {k.strip(): v.strip() for k, v in inline.items()}
        style.update({k: inline[k] for k in ("display", "visibility") if k in inline})
        opacity = inline.get("opacity", element.get("opacity", "1"))
        if style.get("display") == "none" or opacity in ("0", "0.0", "0%"):
            return
        for key in ("clip-path", "mask", "filter", "transform"):
            if key == "transform" and key not in inline:
                continue
            value = inline.get(key, element.get(key, "none"))
            if value != "none":
                raise SVGError("暂不支持 SVG %s，请先转换为普通路径" % key)
        matrix = _mul(parent, _transform(element.get("transform", "")))
        if name in _SHAPES and style.get("visibility") not in ("hidden", "collapse"):
            for path in _shape_paths(element, name):
                count += len(path)
                if count > _MAX_SEGMENTS:
                    raise SVGError("SVG 超过 20000 个几何段，请先简化")
                if path:
                    paths.append([_map_segment(s, matrix) for s in path])
                    if metadata is not None:
                        metadata.append({"id": len(paths), "element": name,
                                         "label": element.get("id") or name})
        elif name not in ("g", "svg") and name not in _SHAPES:
            raise SVGError("不支持可见 SVG 元素 <%s>；文本、引用和图片请先转为路径" % name)
        if name == "svg" and element is not root:
            raise SVGError("不支持嵌套 SVG viewport，请先展开为分组路径")
        for child in element:
            visit(child, matrix, style, depth+1)

    visit(root, root_matrix, {}, 0)
    if not paths:
        raise SVGError("SVG 中没有可绘制路径")
    return paths


def _lerp(a, b):
    return ((a[0]+b[0])/2, (a[1]+b[1])/2)


def _distance(point, a, b):
    dx, dy = b[0]-a[0], b[1]-a[1]
    t = max(0., min(1., ((point[0]-a[0])*dx+(point[1]-a[1])*dy)/(dx*dx+dy*dy))) if dx or dy else 0
    return math.hypot(point[0]-a[0]-t*dx, point[1]-a[1]-t*dy)


def _curve(points, tolerance, emit, depth=0):
    if max(_distance(p, points[0], points[-1]) for p in points[1:-1]) <= tolerance:
        emit(points[-1])
        return
    if depth >= 24:
        raise SVGError("曲线无法在当前容差内离散，请增大容差或简化 SVG")
    levels = [points]
    while len(levels[-1]) > 1:
        levels.append([_lerp(a, b) for a, b in zip(levels[-1], levels[-1][1:])])
    _curve([level[0] for level in levels], tolerance, emit, depth+1)
    _curve([level[-1] for level in reversed(levels)], tolerance, emit, depth+1)


def _flatten(paths, tolerance, max_points):
    result, count = [], 0
    for path in paths:
        points = [path[0][1]]

        def emit(point):
            nonlocal count
            if points[-1] != point:
                points.append(point)
                count += 1
                if count > max_points:
                    raise SVGError("SVG 离散点数超过预算 %d；请增大容差、简化图形或调整 --svg-max-points" % max_points)

        count += 1
        for segment in path:
            kind = segment[0]
            if kind == "L":
                emit(segment[-1])
            elif kind in ("C", "Q"):
                _curve(list(segment[1:]), tolerance, emit)
            else:
                _, _, end, center, u, v, theta, delta = segment
                radius = math.hypot(*u)+math.hypot(*v)  # transformed ellipse norm upper bound
                angle = 2*math.acos(max(-1., min(1., 1-tolerance/radius))) if radius else math.pi/2
                parts = max(1, math.ceil(abs(delta)/max(1e-9, min(math.pi/2, angle))))
                if parts > max_points-count:
                    raise SVGError("SVG 椭圆弧点数超过预算，请增大容差或点数预算")
                for k in range(1, parts+1):
                    angle = theta+delta*k/parts
                    emit(end if k == parts else (center[0]+u[0]*math.cos(angle)+v[0]*math.sin(angle),
                                                  center[1]+u[1]*math.cos(angle)+v[1]*math.sin(angle)))
        if count > max_points:
            raise SVGError("SVG 点数超过预算")
        result.append(points)
    return result


def _bounds(paths):
    """曲线解析极值决定源边界，避免短椭圆弧按完整椭圆缩小。"""
    points = []
    for path in paths:
        for segment in path:
            if segment[0] == "A":
                _, a, b, center, u, v, theta, delta = segment
                points += [a, b]
                for dimension in (0, 1):
                    angle = math.atan2(v[dimension], u[dimension])
                    for candidate in (angle, angle+math.pi):
                        swept = (candidate-theta) % (2*math.pi) if delta >= 0 else (theta-candidate) % (2*math.pi)
                        if swept <= abs(delta)+1e-10:
                            points.append((center[0]+u[0]*math.cos(candidate)+v[0]*math.sin(candidate),
                                           center[1]+u[1]*math.cos(candidate)+v[1]*math.sin(candidate)))
            else:
                control = segment[1:]
                points += [control[0], control[-1]]
                if segment[0] == "L":
                    continue
                roots = []
                for dimension in (0, 1):
                    p = [point[dimension] for point in control]
                    if len(p) == 3:
                        denom = p[0]-2*p[1]+p[2]
                        if denom:
                            roots.append((p[0]-p[1])/denom)
                    else:
                        aa, bb, cc = -p[0]+3*p[1]-3*p[2]+p[3], 2*(p[0]-2*p[1]+p[2]), p[1]-p[0]
                        if abs(aa) < 1e-14:
                            if bb:
                                roots.append(-cc/bb)
                        elif bb*bb-4*aa*cc >= 0:
                            disc = math.sqrt(bb*bb-4*aa*cc)
                            roots += [(-bb+disc)/(2*aa), (-bb-disc)/(2*aa)]
                for t in roots:
                    if 0 < t < 1:
                        work = list(control)
                        while len(work) > 1:
                            work = [((1-t)*a[0]+t*b[0], (1-t)*a[1]+t*b[1]) for a, b in zip(work, work[1:])]
                        points.append(work[0])
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


_DEFAULT_TOLERANCE = object()


def plan_svg(source, device_model=None, *, center=None, width=None, height=None,
             tolerance=_DEFAULT_TOLERANCE, max_points=1200, flip_y=True, selected_paths=None):
    """按路径次序适配到设备坐标框；闭合只跟随 Z/显式终点，不自动闭合开路径。"""
    source = Path(source)
    if source.stat().st_size > _MAX_BYTES:
        raise SVGError("SVG 文件超过 2 MiB")
    data = source.read_bytes()
    paths = parse_svg(data)
    source_bounds = _bounds(paths)
    selected = list(range(1, len(paths)+1))
    if selected_paths is not None:
        if (not isinstance(selected_paths, list) or not selected_paths or
                any(isinstance(i, bool) or not isinstance(i, int) or not 1 <= i <= len(paths)
                    for i in selected_paths) or len(set(selected_paths)) != len(selected_paths)):
            raise SVGError("请选择有效且不重复的 SVG 路径编号（从 1 开始）")
        selected = sorted(selected_paths)
        paths = [paths[i-1] for i in selected]
    axes = {a["axis"].removesuffix("_axis").lower(): a
            for a in (device_model or {}).get("kinematics", {}).get("axes", [])}
    area = {key: tuple(axes.get(key, {}).get("stroke") or value) for key, value in AREA.items()}
    xy_units = {axes[k].get("unit") for k in ("x", "y") if axes.get(k, {}).get("unit")}
    if len(xy_units) > 1:
        raise SVGError("SVG 等比适配要求 X/Y 使用相同坐标单位，请先对齐设备模型")
    center = [(sum(area[k])/2) for k in ("x", "y")] if center is None else center
    if not isinstance(center, (tuple, list)) or len(center) != 2:
        raise SVGError("SVG 中心必须是两个设备坐标")
    cx, cy = map(_finite, center)
    width = (area["x"][1]-area["x"][0])*.6 if width is None else _finite(width)
    height = (area["y"][1]-area["y"][0])*.6 if height is None else _finite(height)
    from .position_control import position_scales
    scales=position_scales((device_model or {}).get('io_points',[]))
    quantum=1/max(1,min(scales['x'],scales['y']))
    tolerance = _finite((.02 if quantum<1 else .25) if tolerance is _DEFAULT_TOLERANCE else tolerance)
    if not isinstance(flip_y, bool):
        raise SVGError("SVG flip_y 必须是布尔值")
    if min(width, height, tolerance) <= 0:
        raise SVGError("SVG 适配宽高和曲线容差必须为正")
    if isinstance(max_points, bool) or not isinstance(max_points, int) or not 2 <= max_points <= 4096:
        raise SVGError("SVG 点数预算必须是 2..4096 的整数")
    x0, y0, x1, y1 = source_bounds  # 取消路径不改变剩余路径相对原图的位置和比例。
    spans = [width/(x1-x0)] if x1 > x0 else []
    if y1 > y0:
        spans.append(height/(y1-y0))
    if not spans:
        raise SVGError("SVG 只有重合点，无法生成绘图轨迹")
    scale = min(spans)
    sy = -scale if flip_y else scale
    matrix = (scale, 0, 0, sy, cx-scale*(x0+x1)/2, cy-sy*(y0+y1)/2)
    paths = [[_map_segment(s, matrix) for s in p] for p in paths]
    sampled = _flatten(paths, tolerance, max_points)
    pen = {"down": int(round(PEN_Z["down"])),
           "up": min(int(round(PEN_Z["up"])), math.floor(area["z"][1]))}
    if not area["z"][0] <= pen["down"] < pen["up"] <= area["z"][1]:
        raise SVGError("Z 轴行程不支持本站落笔 0 与抬笔位置，请检查设备模型")
    steps, total, quantization = [], 0, 0.

    def point(xy, z):
        nonlocal quantization
        for key, value in zip("xyz", (*xy, z)):
            lo, hi = area[key]
            if not lo-1e-8 <= value <= hi+1e-8:
                raise SVGError("SVG 路点超出 %s 轴 AML 行程 %s..%s" % (key, lo, hi))
        target = ([round(round(xy[0]/quantum)*quantum,6),round(round(xy[1]/quantum)*quantum,6),int(round(z))]
                  if quantum<1 else [int(round(xy[0])),int(round(xy[1])),int(round(z))])
        for key, value in zip("xyz", target):
            if not -32768 <= value <= 32767:
                raise SVGError("SVG 路点超出 PLC INT 坐标域")
            if not area[key][0] <= value <= area[key][1]:
                raise SVGError("SVG 路点量化后超出 %s 轴行程" % key)
        quantization = max(quantization, math.dist(xy, target[:2]))
        return target

    def step(action, to, note, **extra):
        steps.append({"no": len(steps)+1, "action": action, "to": to, "note": note, **extra})

    for index, line in zip(selected, sampled):
        points = []
        for xy in line:
            p = point(xy, pen["down"])
            if not points or points[-1] != p:
                points.append(p)
        if len(points) < 2:
            raise SVGError("SVG 第 %d 条路径在 INT 坐标下退化，请放大图形" % index)
        total += len(points)
        step("pen_up_move", [*points[0][:2], pen["up"]], "抬笔定位路径 %d 起点" % index)
        step("pen_down_move", points[0], "路径 %d 落笔" % index)
        step("polyline_sweep", points[-1], "路径 %d 按序描画" % index,
             path=index, waypoints=points[1:])
        step("pen_up_move", [*points[-1][:2], pen["up"]], "路径 %d 完成后抬笔" % index)
    step("pen_up_move", point((cx, cy), pen["up"]), "全部路径完成，抬笔回中心")
    velocity = dict(VMAX)
    for key, names in (("xy", ("x", "y")), ("z", ("z",))):
        for name in names:
            limit = (axes.get(name, {}).get("limits") or {}).get("vmax")
            if isinstance(limit, (int, float)) and math.isfinite(limit) and limit > 0:
                velocity[key] = min(velocity[key], limit)
    return {"shape": "svg", "params": {"center": [cx, cy], "width": width, "height": height,
                "tolerance": tolerance, "max_points": max_points, "flip_y": flip_y,
                "path_count": len(sampled), "point_count": total,
                "selected_paths": selected},
            "pen_z": pen, "vmax": velocity, "steps": steps,
            "source": {"file": str(source.resolve()), "sha256": hashlib.sha256(data).hexdigest(),
                       "bytes": len(data), "bounds": [x0, y0, x1, y1]},
            "unit": next(iter(xy_units), "设备坐标"),
            "sampling": {"flatten_tolerance": tolerance, "coordinate_quantum": quantum,
                         "max_quantization_error": round(quantization, 6),
                         "fit_bounds": "curve_extrema"},
            "notes": ["按 SVG 几何轮廓描画，不执行填充、颜色、笔画宽度或虚线样式。",
                      "等比适配到指定设备坐标框；SVG 毫米或像素不会直接作为 PLC 坐标。",
                      "曲线容差仅控制折线展开；路点已量化为 INT，实际跟踪误差需在线验收。"]}


def add_svg_arguments(parser):
    parser.add_argument("--svg", metavar="FILE", help="将 SVG 几何路径作为权威绘图输入")
    parser.add_argument("--svg-width", type=float, help="等比适配框的宽（设备坐标，默认 X 行程的 60%%）")
    parser.add_argument("--svg-height", type=float, help="等比适配框的高（设备坐标，默认 Y 行程的 60%%）")
    parser.add_argument("--svg-center", type=float, nargs=2, metavar=("X", "Y"), help="绘图中心（默认 X/Y 行程中点）")
    parser.add_argument("--svg-tolerance", type=float, help="曲线展开容差（设备坐标，默认 .25；不含 INT 量化误差）")
    parser.add_argument("--svg-max-points", type=int, help="采样总点数预算（2..4096，默认 1200）")
    parser.add_argument("--svg-keep-y", action="store_true", help="保留 SVG 向下为正的 Y 方向（默认翻转以保持图形朝向）")
    parser.add_argument("--svg-paths", type=int, nargs="+", help="只绘制这些路径编号（从 1 开始，按源文件顺序）")


def prepare_svg_request(args, text, device_model=None, answers=None):
    from .trajectory import goal_text
    answers = answers or {}
    settings = {"center": args.svg_center, "width": args.svg_width, "height": args.svg_height,
                "tolerance": args.svg_tolerance, "max_points": args.svg_max_points,
                "flip_y": not args.svg_keep_y, "selected_paths": getattr(args, "svg_paths", None)}
    supplied = {key for key, value in settings.items() if value is not None and key != "flip_y"}
    if args.svg_keep_y:
        supplied.add("flip_y")
    from .position_control import position_scales
    scales=position_scales((device_model or {}).get('io_points',[]))
    settings["tolerance"] = (.02 if min(scales['x'],scales['y'])>1 else .25) if args.svg_tolerance is None else args.svg_tolerance
    settings["max_points"] = 1200 if args.svg_max_points is None else args.svg_max_points
    overrides = answers.get("svg", {})
    if not isinstance(overrides, dict) or set(overrides)-set(settings):
        raise SVGError("SVG 答案应为 svg 对象，仅支持 center/width/height/tolerance/max_points/flip_y/selected_paths")
    settings.update(overrides)
    trajectory = plan_svg(args.svg, device_model, **settings)
    return {"request_text": text+"\n已确认 SVG 轨迹（以步表为准）："+goal_text(trajectory),
            "trajectory": trajectory, "clarifications": [], "unit": trajectory["unit"],
            "sources": {key: "derived" if key in {"path_count", "point_count", "selected_paths"} else
                        "answer" if key in overrides else "explicit" if key in supplied else "default"
                        for key in trajectory["params"]}}


def refine_svg_request(args, previous, correction, device_model=None):
    """SVG 文件决定轮廓；自然语言仅调整适配框和工艺要求，不猜测新轮廓。"""
    settings = {k: v for k, v in previous["trajectory"]["params"].items()
                if k in {"center", "width", "height", "tolerance", "max_points", "flip_y", "selected_paths"}}
    for key, pattern in (("width", "宽度"), ("height", "高度"), ("tolerance", "容差")):
        matches = list(re.finditer(pattern+r"\s*[:：=]?\s*("+_NUM.pattern+r")", correction))
        if matches:
            tail = correction[matches[-1].end():]
            if re.match(r"\s*(mm|cm|毫米|厘米)", tail, re.I):
                raise SVGError("SVG 适配尺寸使用设备坐标，请先换算物理长度")
            settings[key] = _finite(matches[-1][1])
    matches = list(re.finditer(r"中心\s*[:：=]?\s*[（(\[]?\s*("+_NUM.pattern+r")\s*[,，]\s*("+_NUM.pattern+r")", correction))
    if matches:
        settings["center"] = [_finite(matches[-1][1]), _finite(matches[-1][2])]
    if re.search(r"边长|半径|(?:改成|换成|绘制\s*|画\s*|draw\s+)(?:一个|个)?\s*(?:正方形|方形|圆形?|square\b|circle\b)", correction, re.I):
        raise SVGError("SVG 轮廓以输入文件为准；可修改宽度、高度、中心或容差，改变图形请更换 SVG，或切换到“语言描述”输入绘图任务")
    result = prepare_svg_request(args, previous["request_text"]+"\n用户补充："+correction,
                                 device_model, {"svg": settings})
    return result


def svg_catalog(source):
    """返回经过解析的可选子路径；前端仅绘制点列，不插入上传的 SVG DOM。"""
    source = Path(source)
    if source.stat().st_size > _MAX_BYTES:
        raise SVGError("SVG 文件超过 2 MiB")
    metadata = []
    paths = parse_svg(source.read_bytes(), metadata)
    bounds = _bounds(paths)
    x0, y0, x1, y1 = bounds
    scale = min(560/max(x1-x0, 1e-9), 340/max(y1-y0, 1e-9))
    matrix = (scale, 0, 0, scale, 340-scale*(x0+x1)/2, 220-scale*(y0+y1)/2)
    # 每个子路径有独立预算，密集文件仍受总预览预算约束。
    flattened = _flatten([[_map_segment(s, matrix) for s in path] for path in paths], .65, 40000)
    return {"bounds": list(bounds), "paths": [dict(info, points=[
        [round(x, 3), round(y, 3)] for x, y in points])
        for info, points in zip(metadata, flattened)]}
