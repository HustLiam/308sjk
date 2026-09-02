"""io_map 契约：富化 usd_prim 绑定 + OpenPLC Modbus 地址确定性分配。

地址分配规则（与文档 2.6 一致，按 io_map 声明顺序）：
- 输出 bool → 线圈区 %QX（逐位）
- 输出 float → 保持寄存器 %QW（REAL32 = 2 寄存器，大端字序）
- 输入（bool/float）→ Isaac 传感区块 / OpenPLC %IW（bool 1 寄存器 0/1，float 2 寄存器）
"""

from typing import Any, Dict, List

from . import components

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
    out: List[Dict[str, Any]] = []
    coil_bit = 0
    qw_word = 0
    iw_word = 0
    for entry in io_map:
        e = dict(entry)
        if entry["dir"] == "output":
            if entry["type"] == "bool":
                e["modbus"] = {
                    "area": "coils", "address": coil_bit,
                    "plc_addr": _plc_coil_addr(coil_bit), "length": 1,
                }
                coil_bit += 1
            else:
                e["modbus"] = {
                    "area": "holding_registers", "address": qw_word,
                    "plc_addr": f"%QW{qw_word}", "length": 2, "encoding": "float32_be",
                }
                qw_word += 2
        else:  # input → Isaac 服务端传感区块，OpenPLC 轮询写入 %IW
            if entry["type"] == "bool":
                e["modbus"] = {
                    "area": "sensor_block", "server_register": iw_word,
                    "plc_addr": f"%IW{iw_word}", "length": 1, "encoding": "bool01",
                }
                iw_word += 1
            else:
                e["modbus"] = {
                    "area": "sensor_block", "server_register": iw_word,
                    "plc_addr": f"%IW{iw_word}", "length": 2, "encoding": "float32_be",
                }
                iw_word += 2
        out.append(e)
    return out


def modbus_summary(io_map: List[Dict[str, Any]]) -> Dict[str, Any]:
    """运行时/桥端需要的通道概览。"""
    coils = [e for e in io_map if e.get("modbus", {}).get("area") == "coils"]
    qw = [e for e in io_map if e.get("modbus", {}).get("area") == "holding_registers"]
    sensors = [e for e in io_map if e.get("modbus", {}).get("area") == "sensor_block"]
    return {
        "plc_output_coils": max((e["modbus"]["address"] + 1 for e in coils), default=0),
        "plc_output_registers": max((e["modbus"]["address"] + e["modbus"]["length"] for e in qw), default=0),
        "sensor_block_registers": max(
            (e["modbus"]["server_register"] + e["modbus"]["length"] for e in sensors), default=0),
        "openplc_polling": {
            "request": "FC03 read holding registers",
            "server": "127.0.0.1:5020",
            "start": 0,
            "length": max((e["modbus"]["server_register"] + e["modbus"]["length"] for e in sensors), default=0),
            "target": "%IW0",
        },
    }


def st_io_declaration(io_map: List[Dict[str, Any]]) -> str:
    """生成与地址分配一致的 ST 全局定位变量声明（供代码生成模块对齐）。"""
    lines = ["VAR_GLOBAL"]
    for e in io_map:
        m = e["modbus"]
        if m["area"] == "coils":
            lines.append(f"    {e['plc_var']} AT {m['plc_addr']} : BOOL;   (* 输出：Isaac 客户端 FC01 读 *)")
        elif m["area"] == "holding_registers":
            lines.append(f"    {e['plc_var']} AT {m['plc_addr']} : REAL;  (* 输出：Isaac 客户端 FC03 读，2 寄存器 *)")
        elif m["encoding"] == "bool01":
            lines.append(f"    {e['plc_var']} AT {m['plc_addr']} : WORD;  (* 输入：轮询写入，0/1 *)")
        else:
            lines.append(f"    {e['plc_var']} AT {m['plc_addr']} : REAL;  (* 输入：轮询写入，2 寄存器 *)")
    lines.append("END_VAR")
    return "\n".join(lines)


def check_consistency(io_map: List[Dict[str, Any]]) -> List[str]:
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
