"""验收容差（poswin）提取：spec → 各轴米制 in-position 窗。

契约④判据语义（csk 文档 §7.1 增补，2026-09-09 提案，共同议题待三方定稿）：
- 定位类目标（自由行程轴定位）：done = |feedback − target| ≤ poswin（in-position 窗，
  即 PLC 驱动器"到位"的工程语义）；
- 接触类目标（落笔压纸/夹爪压合/顶到负载端面）：判 **接触建立**（反馈进入接触带并
  稳定 / 接触传感器为真 / 功能量测如墨迹滴落），不以 |feedback − target| 判——伺服
  压紧存在力平衡稳态，"贴住"是工程本质，位置相等不是（根因与实验见 devlog
  2026-09-09(2)：修复前落笔稳态 7mm = 笔胶囊球头半径，几何补偿后 <0.05mm）。

poswin 来源（米制）：
- linear_axis: params.poswin × scale_m_per_unit（plotter spec 携带）；
- gantry_xyz:  spec 无 poswin 参数 → DEFAULT_POSWIN（0.005，文档化缺省；
  契约 v1.2 评估为 gantry 增加 poswin 参数）。

客户端/测试一律经本模块取容差，禁止硬编码等待阈值。
"""

DEFAULT_POSWIN = 0.005


def load_poswin(spec: dict, default: float = DEFAULT_POSWIN) -> dict:
    """SceneSpec dict → {"X": poswin_m, "Y": ..., "Z": ...}（米）。"""
    out = {}
    for a in spec.get("assets", []):
        p = a.get("params", {})
        if a.get("type") == "linear_axis":
            ax = str(p.get("axis", "")).upper()
            if ax:
                out[ax] = float(p.get("poswin", 0.0)) * float(p.get("scale_m_per_unit", 1.0))
        elif a.get("type") == "gantry_xyz":
            for ax in "XYZ":
                out.setdefault(ax, float(default))
    if not out:
        out = {ax: float(default) for ax in "XYZ"}
    return out
