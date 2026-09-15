#!/usr/bin/env python
"""scene.spec.json → MuJoCo MJCF 组装器（Isaac/USD 链路的轻量替代仿真面）。

与 scenegen/build_usd.py 消费同一份 SceneSpec（契约不变），把 gantry_xyz 组件
组装成 MJCF XML：三段滑动关节链 base→x→y→z，位置执行器 + 行程限位，
关节零位/行程/Z 轴语义与 USD 场景一致（q=0 = 作者位姿，Z 0=落笔、travel=抬笔）。
配合 mujoco_jog_runtime.py + gantry_bridge.py 即构成完整 Modbus 闭环。

用法：
    python mujoco_build.py [--spec ../scenegen/out/gantry/scene.spec.json]
                           [--out /tmp/gantry.xml]          # 缺省打印 XML
"""

import argparse
import json
import os

# gantry_xyz 组件的 MJCF 几何/动力学常数（与 scenegen/components.py 的 USD 侧保持同源数值）
GANTRY_CONST = {
    # 质量（kg）：x/y/z 滑块
    "mass": (4.0, 3.0, 0.4),
    # 位置执行器增益（kp）与关节阻尼：对齐 USD DriveAPI 的 stiffness/damping
    "kp": (6000.0, 6000.0, 4000.0),
    "damping": (250.0, 250.0, 80.0),
    # 底座/导轨外观几何（box 半尺寸，米）
    "base_half": (0.45, 0.35, 0.02),
    "saddle_half": (0.045, 0.21, 0.03),
    "head_half": (0.05, 0.05, 0.04),
    "slider_half": (0.03, 0.03, 0.045),
    "pen_length": 0.135,                # 笔尖（球面最低点）到 z_carriage 原点距离；
                                        # MJCF 轴长 = pen_length − pen_radius（球头半径补偿，
                                        # 否则 q=0 时球面悬空半径高度——2026-09-09 落笔 7mm 根因）
    "pen_radius": 0.0075,
}


def _vec(v):
    return " ".join(f"{float(x):.6g}" for x in v)


PLOTTER_TYPES = ("ground", "work_table", "linear_axis", "tool_head", "pen", "hmi_panel")
MJCF_TYPES = ("gantry_xyz",) + PLOTTER_TYPES   # 契约导出用（cli components）


def build_mjcf(spec: dict) -> str:
    """SceneSpec dict → MJCF XML 字符串。支持 gantry_xyz（单资产）
    与 plotter_cell 组件集（多资产装配）。"""
    supported = MJCF_TYPES
    assets = spec.get("assets", [])
    others = [a for a in assets if a.get("type") not in supported]
    if others:
        raise ValueError(f"spec 含未支持组件类型: {sorted({a.get('type') for a in others})}"
                         f"（当前支持 {supported}）")
    if any(a.get("type") in PLOTTER_TYPES for a in assets):
        return _plotter_xml(spec)
    picked = [a for a in assets if a.get("type") in supported]
    if not picked:
        raise ValueError(f"spec 中找不到受支持组件（{supported}）")
    if len(picked) > 1:
        raise ValueError("MJCF 组装器暂只支持单资产场景")
    a = picked[0]
    p = a.get("params", {})
    travel = (float(p.get("travel_x", 0.6)), float(p.get("travel_y", 0.4)),
              float(p.get("travel_z", 0.2)))
    speed = float(p.get("speed", 0.5))
    gx, gy, gz = a.get("pose", {}).get("position", (0, 0, 0))
    dt = float(spec.get("physics", {}).get("physics_dt", 1 / 60))
    grav = spec.get("physics", {}).get("gravity", (0, 0, -9.81))
    c = GANTRY_CONST

    # io_map 中 *_cmd 的 range 与 travel 不一致时以 travel 为准（与 USD 侧钳位语义一致）
    xml = f"""<mujoco model="{spec.get('scene_id', 'gantry')}">
  <!-- 由 scene.spec.json 组装（mujoco_build.py）；关节 q=0 = 作者位姿，Z: 0=落笔 {travel[2]}=抬笔 -->
  <option timestep="{dt:.6g}" gravity="{_vec(grav)}"/>
  <compiler angle="radian" autolimits="true"/>
  <default>
    <joint armature="0.01" frictionloss="0.1"/>
    <geom friction="0.8 0.02 0.001" condim="3"/>
  </default>
  <worldbody>
    <geom name="floor" type="plane" size="10 10 0.1" rgba="0.92 0.92 0.9 1"/>
    <body name="gantry_base" pos="{_vec((gx, gy, gz))}">
      <!-- 工作区布局：spec pose = 笔尖行程原点（左下角），纸张/底板以行程中心摆放，
           笔尖扫掠 [pose, pose+travel] 恰好铺满纸面（USD 侧同样存在纸张偏置在
           根原点的布局缺陷，见 devlog 2026-09-07(8)；MJCF 侧已修正） -->
      <geom name="base_plate" type="box" size="{travel[0] / 2 + 0.12:.6g} {travel[1] / 2 + 0.12:.6g} 0.02"
            pos="{travel[0] / 2:.6g} {travel[1] / 2:.6g} 0.02" rgba="0.75 0.76 0.78 1"/>
      <geom name="paper" type="box" size="{travel[0] / 2:.6g} {travel[1] / 2:.6g} 0.001"
            pos="{travel[0] / 2:.6g} {travel[1] / 2:.6g} 0.041" rgba="0.97 0.97 0.94 1"/>
      <!-- 立柱/导轨为结构装饰件：与滑座在作者位姿天然互穿（滑座"骑"在导轨上），
           关闭其碰撞以免顶死关节；功能接触（笔尖↔纸面、整体↔地面）不受影响 -->
      <geom name="column_l" type="box" size="0.03 0.03 0.275"
            pos="{travel[0] / 2 - 0.38:.6g} 0 0.275"
            contype="0" conaffinity="0" rgba="0.35 0.36 0.4 1"/>
      <geom name="column_r" type="box" size="0.03 0.03 0.275"
            pos="{travel[0] / 2 + 0.38:.6g} 0 0.275"
            contype="0" conaffinity="0" rgba="0.35 0.36 0.4 1"/>
      <geom name="x_rail" type="box" size="{travel[0] / 2 + 0.11:.6g} 0.035 0.035"
            pos="{travel[0] / 2:.6g} 0 0.55"
            contype="0" conaffinity="0" rgba="0.35 0.36 0.4 1"/>
      <body name="x_carriage" pos="0 0 0.5">
        <joint name="joint_x" type="slide" axis="1 0 0" range="0 {travel[0]:.6g}"
               damping="{c['damping'][0]:.6g}"/>
        <geom name="saddle" type="box" size="{_vec(c['saddle_half'])}" mass="{c['mass'][0]:.6g}"
              contype="0" conaffinity="0" rgba="0.2 0.55 0.85 1"/>
        <body name="y_carriage" pos="0 0 -0.01">
          <joint name="joint_y" type="slide" axis="0 1 0" range="0 {travel[1]:.6g}"
                 damping="{c['damping'][1]:.6g}"/>
          <geom name="head" type="box" size="{_vec(c['head_half'])}" mass="{c['mass'][1]:.6g}"
                contype="0" conaffinity="0" rgba="0.2 0.55 0.85 1"/>
          <body name="z_carriage" pos="0 0 -0.313">
            <joint name="joint_z" type="slide" axis="0 0 1" range="0 {travel[2]:.6g}"
                   damping="{c['damping'][2]:.6g}"/>
            <geom name="slider" type="box" size="{_vec(c['slider_half'])}" mass="{c['mass'][2]:.6g}"
                  pos="0 0 0.155" contype="0" conaffinity="0" rgba="0.16 0.42 0.66 1"/>
            <!-- 笔向下悬伸：q=0 时笔尖球面触纸（胶囊最低面 = 轴线端点 + 半径，轴长已补半径），
                 抬笔 = +Z 行程；伺服压纸的力平衡稳态由接触判据语义覆盖（csk 文档 §7.1） -->
            <geom name="pen" type="capsule" fromto="0 0 {-c['pen_length'] + c['pen_radius']:.6g} 0 0 0"
                  size="{c['pen_radius']:.6g}" mass="0.02" rgba="0.85 0.2 0.2 1"/>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>
    <!-- 位置伺服：ctrl = 关节目标（米），运行时按 axisSpeed={speed:.6g} m/s 速率限制写入 -->
    <position name="drive_x" joint="joint_x" kp="{c['kp'][0]:.6g}"
              ctrlrange="0 {travel[0]:.6g}"/>
    <position name="drive_y" joint="joint_y" kp="{c['kp'][1]:.6g}"
              ctrlrange="0 {travel[1]:.6g}"/>
    <position name="drive_z" joint="joint_z" kp="{c['kp'][2]:.6g}"
              ctrlrange="0 {travel[2]:.6g}"/>
  </actuator>
</mujoco>
"""
    return xml


# ---------------- 【csk 2026-09-08 新增】plotter_cell 多资产装配 ----------------
# 消费 gc 的 master:scene.spec.json（plotter_cell_square）。组装规则（歧义处已登记
# 反馈 gc，此处为确定性解释，不含 spec 外创作）：
#   R1 静态件：ground→地面平面（与顶层 ground 字段去重）；work_table→台体 box +
#      纸面（paper_area 按 work_table 尺寸的百分比、居中解释）；hmi_panel→库缺省
#      外观 box（spec 未给尺寸，仅视觉件）；
#   R2 轴链：linear_axis 按声明序 x→y→z 成链（spec 未声明轴间 parent）；轴 pose =
#      行程中心（水平）+ 安装高度（首轴 z）；q=0 = 行程起点（保持桥的非负寄存器
#      语义，与龙门一致）；
#   R3 工具：tool_head/pen 挂链尾（spec 的 plot_head.parent=y_axis 与 z_axis 并存，
#      链语义冲突，按 R2 链尾组装）；qz=0 = 笔尖距纸面 2mm 的作者静置位，抬笔 =
#      z 行程（与龙门 Z 语义一致）；
#   R4 动力学：spec 未提供质量/伺服参数，沿用 GANTRY_CONST 库值（同级别机构）。

def _parse_paper_area(text, size):
    """paper_area "20..80 x 20..80" → 纸面半尺寸 (hx, hy) 米（按 work_table 尺寸的百分比）。"""
    import re
    m = re.match(r"\s*(\d+(?:\.\d+)?)\.\.(\d+(?:\.\d+)?)\s*x\s*(\d+(?:\.\d+)?)\.\.(\d+(?:\.\d+)?)\s*$",
                 text or "")
    if not m:
        raise ValueError(f"paper_area 无法解析: {text!r}（期望形如 '20..80 x 20..80'）")
    x0, x1, y0, y1 = (float(v) / 100.0 for v in m.groups())
    if not (0 <= x0 < x1 <= 100 and 0 <= y0 < y1 <= 100):
        raise ValueError(f"paper_area 区间非法: {text!r}")
    return (size[0] * (x1 - x0) / 2, size[1] * (y1 - y0) / 2)


def _plotter_xml(spec: dict) -> str:
    assets = spec.get("assets", [])
    axes = [a for a in assets if a.get("type") == "linear_axis"]   # 声明序即链序（R2）
    letters = [a.get("params", {}).get("axis") for a in axes]
    if sorted(l for l in letters if l) != ["x", "y", "z"]:
        raise ValueError(f"linear_axis 的 axis 参数须为 x/y/z 各一根，得到 {letters}")
    travel, speed = {}, {}
    for a in axes:
        p = a.get("params", {})
        lo, hi = p["stroke"]
        s = float(p["scale_m_per_unit"])
        travel[p["axis"]] = (float(hi) - float(lo)) * s
        speed[p["axis"]] = float(p.get("vmax", 40.0)) * s        # m/s（米制换算）

    ax0 = axes[0].get("pose", {}).get("position", (0, 0, 0))
    cx, cy, z0 = float(ax0[0]), float(ax0[1]), float(ax0[2])     # 行程中心 + 安装高度（R2）

    table = next(a for a in assets if a.get("type") == "work_table")
    tp = table.get("params", {})
    tsize = tuple(float(v) for v in tp["size"])
    tpose = table.get("pose", {}).get("position", (0, 0, 0))
    th = tsize[2]
    # 静态件 pose = 落位面（底面高度）：spec 台面 pose z=0 即坐落于地面，顶面 = z+th
    table_center_z = float(tpose[2]) + th / 2
    table_top = float(tpose[2]) + th
    phx, phy = _parse_paper_area(tp.get("paper_area", "20..80 x 20..80"), tsize)
    paper_half_z = 0.001
    paper_center_z = table_top + paper_half_z               # 纸贴合台顶（geom pos = 中心）
    paper_top = paper_center_z + paper_half_z
    contact = paper_top + 0.002                             # qz=0 笔尖位（R3，距纸面 2mm）

    pen_asset = next((a for a in assets if a.get("type") == "pen"), None)
    tip_r = float(pen_asset["params"].get("tip_diameter_mm", 0.5)) / 2000.0 if pen_asset else 0.0075
    pen_len = z0 - contact                                         # 链根到笔尖（R3）

    ground = next((a for a in assets if a.get("type") == "ground"), None)
    gs = ground["params"]["size"] if ground else spec.get("ground", {}).get("size", (20, 20))
    gf = float(ground["params"].get("friction", 0.8)) if ground else \
        float(spec.get("ground", {}).get("friction", 0.8))
    dt = float(spec.get("physics", {}).get("physics_dt", 1 / 60))
    grav = spec.get("physics", {}).get("gravity", (0, 0, -9.81))
    c = GANTRY_CONST                                               # 动力学库值（R4）

    panel = next((a for a in assets if a.get("type") == "hmi_panel"), None)
    panel_xml = ""
    if panel is not None:
        pp = panel.get("pose", {}).get("position", (0, 0, 0))
        panel_xml = f"""    <geom name="hmi_panel" type="box" size="0.15 0.12 0.175"
          pos="{pp[0]:.6g} {pp[1]:.6g} {pp[2] + 0.175:.6g}" contype="0" conaffinity="0"
          rgba="0.25 0.28 0.32 1"/>   <!-- 库缺省外观（spec 未给尺寸） -->"""

    return f"""<mujoco model="{spec.get('scene_id', 'plotter_cell')}">
  <!-- 由 scene.spec.json 组装（mujoco_build.py _plotter_xml，组装规则 R1-R4 见源码） -->
  <option timestep="{dt:.6g}" gravity="{_vec(grav)}"/>
  <compiler angle="radian" autolimits="true"/>
  <default>
    <joint armature="0.01" frictionloss="0.1"/>
    <geom friction="{gf:.6g} 0.02 0.001" condim="3"/>
  </default>
  <worldbody>
    <geom name="floor" type="plane" size="{gs[0] / 2:.6g} {gs[1] / 2:.6g} 0.1" rgba="0.92 0.92 0.9 1"/>
    <geom name="work_table" type="box" size="{_vec((tsize[0] / 2, tsize[1] / 2, th / 2))}"
          pos="{tpose[0]:.6g} {tpose[1]:.6g} {table_center_z:.6g}" rgba="0.72 0.68 0.60 1"/>
    <geom name="paper" type="box" size="{phx:.6g} {phy:.6g} 0.001"
          pos="{tpose[0]:.6g} {tpose[1]:.6g} {paper_center_z:.6g}" rgba="0.97 0.97 0.94 1"/>
{panel_xml}
    <!-- 轴链锚点 = 行程起点（轴 pose 为行程中心，R2）；qz=0 笔尖距纸 2mm（R3） -->
    <body name="gantry_base" pos="{cx - travel['x'] / 2:.6g} {cy - travel['y'] / 2:.6g} {z0:.6g}">
      <body name="x_carriage" pos="0 0 0">
        <joint name="joint_x" type="slide" axis="1 0 0" range="0 {travel['x']:.6g}"
               damping="{c['damping'][0]:.6g}"/>
        <geom name="saddle" type="box" size="0.05 0.05 0.02" mass="{c['mass'][0]:.6g}"
              contype="0" conaffinity="0" rgba="0.2 0.55 0.85 1"/>
        <body name="y_carriage" pos="0 0 0">
          <joint name="joint_y" type="slide" axis="0 1 0" range="0 {travel['y']:.6g}"
                 damping="{c['damping'][1]:.6g}"/>
          <geom name="head" type="box" size="0.05 0.05 0.02" mass="{c['mass'][1]:.6g}"
                contype="0" conaffinity="0" rgba="0.2 0.55 0.85 1"/>
          <body name="z_carriage" pos="0 0 0">
            <joint name="joint_z" type="slide" axis="0 0 1" range="0 {travel['z']:.6g}"
                   damping="{c['damping'][2]:.6g}"/>
            <geom name="slider" type="box" size="0.04 0.04 0.015" mass="{c['mass'][2]:.6g}"
                  pos="0 0 0.02" contype="0" conaffinity="0" rgba="0.16 0.42 0.66 1"/>
            <!-- 笔（tool_head 载具为链尾滑块，R3）；笔尖有碰撞：任何故障下停在纸/台面 -->
            <geom name="pen" type="capsule" fromto="0 0 {-pen_len + tip_r:.6g} 0 0 0"
                  size="{tip_r:.6g}" mass="0.02" rgba="0.85 0.2 0.2 1"/>
          </body>
        </body>
      </body>
    </body>
  </worldbody>
  <actuator>
    <position name="drive_x" joint="joint_x" kp="{c['kp'][0]:.6g}" ctrlrange="0 {travel['x']:.6g}"/>
    <position name="drive_y" joint="joint_y" kp="{c['kp'][1]:.6g}" ctrlrange="0 {travel['y']:.6g}"/>
    <position name="drive_z" joint="joint_z" kp="{c['kp'][2]:.6g}" ctrlrange="0 {travel['z']:.6g}"/>
  </actuator>
</mujoco>
"""




def load_spec(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    p = argparse.ArgumentParser(description="SceneSpec → MuJoCo MJCF 组装器")
    p.add_argument("--spec", default=os.path.normpath(
        os.path.join(here, "..", "scenegen", "out", "gantry", "scene.spec.json")))
    p.add_argument("--out", default=None, help="写出 .xml 路径；缺省打印到 stdout")
    args = p.parse_args()
    xml = build_mjcf(load_spec(args.spec))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(xml)
        print(f"已写出 {args.out}")
    else:
        print(xml)


if __name__ == "__main__":
    main()
