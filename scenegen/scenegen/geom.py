"""纯 Python 几何工具：欧拉角、位姿复合、包围盒。无第三方依赖。"""

import math
from typing import Tuple

Vec3 = Tuple[float, float, float]
Mat3 = Tuple[Vec3, Vec3, Vec3]  # 行主序


def rpy_deg_matrix(rpy_deg: Vec3) -> Mat3:
    """ZYX 内旋顺序（roll-x, pitch-y, yaw-z），与 USD/机器人学惯例一致。"""
    r, p, y = (math.radians(a) for a in rpy_deg)
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(y), math.sin(y)
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp,     cp * sr,                cp * cr),
    )


def mat_vec(m: Mat3, v: Vec3) -> Vec3:
    return (
        m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
        m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
        m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2],
    )


def mat_mul(a: Mat3, b: Mat3) -> Mat3:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))  # type: ignore[misc]
        for i in range(3)
    )


def mat_transpose(m: Mat3) -> Mat3:
    return tuple(zip(*m))  # type: ignore[return-value]


def compose_pose(parent_pos: Vec3, parent_rot: Mat3,
                 child_pos: Vec3, child_rot: Mat3) -> Tuple[Vec3, Mat3]:
    """父位姿 ∘ 子位姿 → 世界位姿。"""
    wp = (
        parent_pos[0] + mat_vec(parent_rot, child_pos)[0],
        parent_pos[1] + mat_vec(parent_rot, child_pos)[1],
        parent_pos[2] + mat_vec(parent_rot, child_pos)[2],
    )
    return wp, mat_mul(parent_rot, child_rot)


def quat_from_matrix(m: Mat3) -> Tuple[float, float, float, float]:
    """旋转矩阵 → 四元数 (w, x, y, z)，Shepperd 法，数值稳定。"""
    tr = m[0][0] + m[1][1] + m[2][2]
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w, x, y, z = 0.25 * s, (m[2][1] - m[1][2]) / s, (m[0][2] - m[2][0]) / s, (m[1][0] - m[0][1]) / s
    elif m[0][0] > m[1][1] and m[0][0] > m[2][2]:
        s = math.sqrt(1.0 + m[0][0] - m[1][1] - m[2][2]) * 2.0
        w, x, y, z = (m[2][1] - m[1][2]) / s, 0.25 * s, (m[0][1] + m[1][0]) / s, (m[0][2] + m[2][0]) / s
    elif m[1][1] > m[2][2]:
        s = math.sqrt(1.0 + m[1][1] - m[0][0] - m[2][2]) * 2.0
        w, x, y, z = (m[0][2] - m[2][0]) / s, (m[0][1] + m[1][0]) / s, 0.25 * s, (m[1][2] + m[2][1]) / s
    else:
        s = math.sqrt(1.0 + m[2][2] - m[0][0] - m[1][1]) * 2.0
        w, x, y, z = (m[1][0] - m[0][1]) / s, (m[0][2] + m[2][0]) / s, (m[1][2] + m[2][1]) / s, 0.25 * s
    n = math.sqrt(w * w + x * x + y * y + z * z)
    return w / n, x / n, y / n, z / n


def axis_align_matrix(axis: str) -> Mat3:
    """构造正交基 R，使 R·e_z = 运动轴（用于把关节平移轴统一到关节系 Z）。"""
    w = {"x": (1.0, 0.0, 0.0), "y": (0.0, 1.0, 0.0), "z": (0.0, 0.0, 1.0)}[axis]
    ref = (0.0, 0.0, 1.0) if axis != "z" else (1.0, 0.0, 0.0)
    ux = cross(ref, w)
    u = normalize(ux)
    v = cross(w, u)
    cols = (u, v, w)  # 列向量 = e_x/e_y/e_z 的像
    return mat_transpose(cols)  # 转为行主序矩阵


def cross(a: Vec3, b: Vec3) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def normalize(v: Vec3) -> Vec3:
    n = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    return (v[0] / n, v[1] / n, v[2] / n)


def aabb_transform(mins: Vec3, maxs: Vec3, pos: Vec3, rot: Mat3) -> Tuple[Vec3, Vec3]:
    """局部 AABB 经位姿变换后的世界 AABB（8 角点投影）。"""
    corners = [
        (x, y, z)
        for x in (mins[0], maxs[0])
        for y in (mins[1], maxs[1])
        for z in (mins[2], maxs[2])
    ]
    world = [tuple(p + c for p, c in zip(pos, mat_vec(rot, cn))) for cn in corners]
    return (
        tuple(min(c[i] for c in world) for i in range(3)),  # type: ignore[return-value]
        tuple(max(c[i] for c in world) for i in range(3)),  # type: ignore[return-value]
    )


def aabb_overlap_depth(a: Tuple[Vec3, Vec3], b: Tuple[Vec3, Vec3]) -> Vec3:
    """两 AABB 各轴交叠深度（负值表示分离）。"""
    return tuple(  # type: ignore[return-value]
        min(a[1][i], b[1][i]) - max(a[0][i], b[0][i]) for i in range(3)
    )
