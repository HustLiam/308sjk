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
    "pen_length": 0.135,                # 笔尖到 z_carriage 原点的距离
    "pen_radius": 0.0075,
}


def _vec(v):
    return " ".join(f"{float(x):.6g}" for x in v)


def build_mjcf(spec: dict) -> str:
    """SceneSpec dict → MJCF XML 字符串。只支持 gantry_xyz 组件（当前 io 闭环唯一场景）。"""
    assets = spec.get("assets", [])
    gantries = [a for a in assets if a.get("type") == "gantry_xyz"]
    if not gantries:
        raise ValueError("spec 中找不到 type == gantry_xyz 的组件（当前仅支持龙门场景）")
    if len(gantries) > 1:
        raise ValueError("spec 含多个 gantry_xyz，MJCF 组装器暂只支持单龙门")
    a = gantries[0]
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
           根原点的布局缺陷，见 devlog 2026-09-07(3)；MJCF 侧已修正） -->
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
            <!-- 笔向下悬伸：q=0 时笔尖触纸（距纸面 ~1mm 静置位），抬笔 = +Z 行程 -->
            <geom name="pen" type="capsule" fromto="0 0 {-c['pen_length']:.6g} 0 0 0"
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
