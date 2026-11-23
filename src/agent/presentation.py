"""面向工作台用户的简洁工艺摘要，不修改生成器消费的完整规格。"""
import re


def process_summary(spec, trajectory=None):
    def token(name):
        return r"(?<![A-Za-z0-9_])"+re.escape(name)+r"(?![A-Za-z0-9_])"
    labels = {"run":"使能", "cmd_draw":"启动按钮", "cmd_home":"回零按钮",
              "cmd_go":"定位按钮", "cmd_reset":"复位按钮", "quickstop":"急停",
              "cmd_halt":"停止按钮", "plot_done":"绘图完成信号", "pen_down":"落笔状态",
              "any_moving":"运动状态", "move_done":"定位完成信号", "fault_any":"故障信号",
              "all_oe":"轴使能状态", "jog_fwd":"正向点动", "jog_rev":"反向点动"}
    for axis in "xyz":
        for suffix, label in {"fb":"位置反馈","cmd":"位置目标","sp":"设定位置","v":"速度","sw":"状态"}.items():
            labels[axis+"_"+suffix] = axis.upper()+" 轴"+label
    names = {item.get("name", "") for item in spec.get("io_list", [])}|set(labels)
    goal = re.split(r'\n(?:已确认 SVG 轨迹|当前语言绘图轨迹|已确认几何)',str(spec.get('task_goal') or ''),maxsplit=1)[0]
    # 删除括号里的代码注解，保留中心坐标等纯几何内容。
    def annotation(match):
        text = match[0]
        return "" if any(re.search(token(name), text) for name in names if name) else text
    goal = re.sub(r"\([^()]*\)|（[^（）]*）", annotation, goal)
    for name in sorted((n for n in names if n), key=len, reverse=True):
        goal = re.sub(token(name), labels.get(name,"对应信号"), goal)
    goal = re.sub(r"%Q[XW]\d+(?:\.\d+)?", "", goal)
    goal = re.sub(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+(?![A-Za-z0-9_])", "", goal)
    goal = re.sub(r"\(\s*\)|（\s*）", "", goal)
    goal = goal.replace("TRUE","开启").replace("FALSE","关闭").replace("&&","且")
    goal = re.sub(r"[ \t]+", " ", goal).strip(" \n：:；;")
    goal = re.sub(r"（设备站：[^）]*）", "", goal).strip()
    if not goal:
        count = (trajectory or {}).get("params",{}).get("path_count")
        goal = "按所选"+(str(count)+"条" if count else "")+"轨迹依次绘制。"
    if len(goal)>240:
        goal = goal[:237].rstrip("，； ")+"…"
    return "工艺目标："+goal
