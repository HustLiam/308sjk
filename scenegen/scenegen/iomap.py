"""io_map 契约：富化 usd_prim 绑定 + OpenPLC Modbus 地址确定性分配。

地址分配规则（与文档 2.6 一致，按 io_map 声明顺序）：
- 仿真面（桥 :5020 / jog GUI，float32 大端 2 寄存器）：
  - 输出 bool → 线圈区（逐位）；输出 float → 保持寄存器（2 寄存器/值）
  - 输入 bool/float → 传感区块（bool 1 寄存器 0/1，float 2 寄存器）
- PLC 面（OpenPLC :502，2026-09-15 裁决：传感注入统一 %Q 区、定点 INT16，
  换算归桥——见 lx 文档 §6.2 与 changelog「桥接裁决 v1.0」）：
  - 所有 float（不分方向）→ %QW 1 寄存器（工程量定点 int16，
    scale 由 PLC spec io_list range 与本 io_map range 推导，plc_link 执行换算）
  - 所有 bool（不分方向）→ %QX 线圈（桥侧 FC15 整组读写纪律）
  - %QW/%QX 地址各自按声明顺序全局唯一编号（跨方向共用计数器）
"""

from typing import Any, Dict, List

# components 仅 check_consistency 需要（USD/pxr 依赖）；延迟到调用点导入，
# 使地址分配/声明导出在无 pxr 环境（MuJoCo 链路）可用。

_BIT_PER_BYTE = 8


def _plc_coil_addr(bit_index: int) -> str:
    return f"%QX{bit_index // _BIT_PER_BYTE}.{bit_index % _BIT_PER_BYTE}"


def enrich(io_map: List[Dict[str, Any]],
           quantity_prim_paths: Dict[str, Dict[str, str]]) -> List[Dict[str, Any]]:
    """把构建期得到的 (asset, quantity) -> prim 路径写回 io_map 的 usd_prim 字段。"""
    out = []
    for entry in io_map:
        e = dict(entry)
        bind = e["bind"]
        prims = quantity_prim_paths.get(bind["asset"], {})
        prim = prims.get(bind["quantity"])
        if prim is None:
            raise KeyError(f"io_map 绑定无 prim: {bind['asset']}.{bind['quantity']}")
        e["usd_prim"] = prim
        out.append(e)
    return out


def assign_modbus(io_map: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    coil_bit = 0        # 仿真面线圈位
    qw_word = 0         # 仿真面输出保持寄存器（float32，2 寄存器/值）
    iw_word = 0         # 仿真面传感区块寄存器
    plc_qw = 0          # PLC 面 %QW（int16，1 寄存器/值，全方向共用计数器）
    plc_coil = 0        # PLC 面 %QX（全方向共用计数器）
    for entry in io_map:
        e = dict(entry)
        if entry["dir"] == "output":
            if entry["type"] == "bool":
                e["modbus"] = {
                    "area": "coils", "address": coil_bit,
                    "plc_addr": _plc_coil_addr(plc_coil), "length": 1,
                }
                coil_bit += 1
                plc_coil += 1
            else:
                e["modbus"] = {
                    "area": "holding_registers", "address": qw_word,
                    "plc_addr": f"%QW{plc_qw}", "length": 2, "encoding": "float32_be",
                    "plc_encoding": "int16_scaled",
                }
                qw_word += 2
                plc_qw += 1
        else:  # input → 仿真面传感区块；PLC 面统一 %Q 区（2026-09-15 裁决）
            if entry["type"] == "bool":
                e["modbus"] = {
                    "area": "sensor_block", "server_register": iw_word,
                    "plc_addr": _plc_coil_addr(plc_coil), "length": 1, "encoding": "bool01",
                }
                iw_word += 1
                plc_coil += 1
            else:
                e["modbus"] = {
                    "area": "sensor_block", "server_register": iw_word,
                    "plc_addr": f"%QW{plc_qw}", "length": 2, "encoding": "float32_be",
                    "plc_encoding": "int16_scaled",
                }
                iw_word += 2
                plc_qw += 1
        out.append(e)
    return out


def modbus_summary(io_map: List[Dict[str, Any]]) -> Dict[str, Any]:
    """运行时/桥端需要的通道概览。

    plc_loop 描述 2026-09-15 裁决定稿的接线拓扑（lx 文档 §6.2）：仿真侧桥作
    Modbus 客户端轮询 OpenPLC(:502) 的 %Q 区；旧的 openplc_polling
    （OpenPLC 轮询 :5020 → %IW）已随裁决废弃。"""
    coils = [e for e in io_map if e.get("modbus", {}).get("area") == "coils"]
    qw = [e for e in io_map if e.get("modbus", {}).get("area") == "holding_registers"]
    sensors = [e for e in io_map if e.get("modbus", {}).get("area") == "sensor_block"]
    plc_out_qw = [e for e in qw if e["modbus"].get("plc_encoding") == "int16_scaled"]
    plc_in_qw = [e for e in sensors if e["modbus"].get("plc_encoding") == "int16_scaled"]
    return {
        "plc_output_coils": max((e["modbus"]["address"] + 1 for e in coils), default=0),
        "plc_output_registers": max((e["modbus"]["address"] + e["modbus"]["length"] for e in qw), default=0),
        "sensor_block_registers": max(
            (e["modbus"]["server_register"] + e["modbus"]["length"] for e in sensors), default=0),
        "plc_loop": {
            "peer": "tcp://127.0.0.1:502",
            "transport": "仿真侧桥作 Modbus 客户端轮询 OpenPLC %Q 区（lx 文档 §6.2）",
            "read_commands": {
                "request": "FC03 read holding registers",
                "start": min((int(e["modbus"]["plc_addr"][3:]) for e in plc_out_qw), default=0),
                "length": len(plc_out_qw),
                "encoding": "int16_scaled（工程量定点，换算归桥）",
            },
            "write_feedback": {
                "request": "FC16 write holding registers",
                "start": min((int(e["modbus"]["plc_addr"][3:]) for e in plc_in_qw), default=0),
                "length": len(plc_in_qw),
                "encoding": "int16_scaled（工程量定点，换算归桥）",
            },
            "coils": {
                "request": "FC01 读 / FC15 整组写（完整 16 位跨度，SafeCoilIO 纪律）",
                "bits": sum(1 for e in io_map if e["type"] == "bool"),
            },
        },
    }


def st_io_declaration(io_map: List[Dict[str, Any]]) -> str:
    """生成与地址分配一致的 ST 全局定位变量声明（供代码生成模块对齐）。

    PLC 面全部落 %Q 区（2026-09-15 裁决）：float 为工程量定点 INT（换算归桥），
    bool 为线圈；仿真面 float32 布局由桥内部处理，不出现在 PLC 声明里。"""
    lines = ["VAR_GLOBAL"]
    for e in io_map:
        m = e["modbus"]
        if e["type"] == "bool":
            direction = "输出" if m["area"] == "coils" else "输入反馈"
            lines.append(f"    {e['plc_var']} AT {m['plc_addr']} : BOOL;   (* {direction}：桥 FC15 整组读写 *)")
        else:
            direction = "输出" if m["area"] == "holding_registers" else "输入反馈"
            lines.append(f"    {e['plc_var']} AT {m['plc_addr']} : INT;   (* {direction}：工程量定点 int16，换算归桥 *)")
    lines.append("END_VAR")
    return "\n".join(lines)


def check_consistency(io_map: List[Dict[str, Any]]) -> List[str]:
    from . import components   # 延迟导入：USD/pxr 依赖，仅本函数需要
    errors = []
    seen = set()
    for e in io_map:
        if e["plc_var"] in seen:
            errors.append(f"io_map 重复 plc_var: {e['plc_var']}")
        seen.add(e["plc_var"])
    for e in io_map:
        q = components.quantity_of(e.get("_asset_type", ""), e["bind"]["quantity"]) if "_asset_type" in e else None
        if q is None:
            continue
        want_dir = "out" if e["dir"] == "input" else "in"
        if q.direction != want_dir:
            errors.append(
                f"{e['plc_var']}: dir={e['dir']} 与 quantity {e['bind']['quantity']} 方向({q.direction}) 不匹配")
        if q.dtype != e["type"]:
            errors.append(
                f"{e['plc_var']}: type={e['type']} 与 quantity {e['bind']['quantity']} 类型({q.dtype}) 不匹配")
        rng = e["bind"].get("range")
        if rng and rng[0] >= rng[1]:
            errors.append(f"{e['plc_var']}: range 下界须小于上界")
    return errors
