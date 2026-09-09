"""链路 A shim 生成器：io_map（契约③）→ C shim 源码（确定性与版本无关）。

matiec iec2c 把定位变量生成为 C 外部符号（__QX0_0 / __QW0 风格，随版本可能微调——
本模块的 SYMBOL_RULES 即隔离点，符号风格变化只改这里）。生成的 shim 暴露稳定接口：

    void plc_init(void);
    void plc_run(unsigned long tick);
    void plc_write_image(const unsigned char* di, const short* ai);  /* 传感注入（dir=input） */
    void plc_read_image(unsigned char* dq, short* aq);               /* PLC 输出（dir=output） */

di/ai/dq/aq 为**紧凑镜像**：按 io_map 顺序（过滤方向后）逐变量排列——
bool 占 1 字节（di/dq，独立下标），analog/word 占 2 字节（ai/aq，独立下标）。
索引规则由 runtime/plc_binding.py 的 IOLayout 按同一规则实现，两侧共同 golden 测试锁定。

纯函数、无 IO：结构回归不依赖 matiec/gcc（L2 层）。
"""

SYMBOL_RULES = {
    "QX": "__QX{byte}_{bit}",   # %QX0.3 → __QX0_3
    "QW": "__QW{n}",            # %QW10  → __QW10
}
C_TYPE = {"bool": "BOOL", "analog": "INT", "word": "WORD"}   # matiec 生成码自带 typedef


def parse_addr(plc_addr: str):
    """'%QX0.3' → ('QX', 0, 3)；'%QW10' → ('QW', 10, None)。"""
    a = plc_addr.upper()
    if a.startswith("%QX"):
        byte, bit = a[3:].split(".")
        return "QX", int(byte), int(bit)
    if a.startswith("%QW"):
        return "QW", int(a[3:]), None
    raise ValueError(f"不支持的地址（契约②：仅 %QX/%QW，%QD 禁用）: {plc_addr}")


def symbol_for(var_type: str, plc_addr: str) -> str:
    kind, a, b = parse_addr(plc_addr)
    if kind == "QX":
        return SYMBOL_RULES["QX"].format(byte=a, bit=b)
    return SYMBOL_RULES["QW"].format(n=a)


def build_layout(io_map):
    """io_map entries → 通道表与紧凑镜像索引（shim 与 Python 侧共用的唯一规则）。

    layout["index"][plc_var] = (镜像名, 下标, 类型)；
    镜像名 ∈ {di, ai, dq, aq}：di/dq = bool 字节镜像，ai/aq = 16 位量镜像，
    各自按 io_map 中该方向变量的出现顺序独立编号。"""
    index = {}
    counters = {"di": 0, "ai": 0, "dq": 0, "aq": 0}
    for e in io_map:
        image = ("di", "ai") if e["dir"] == "input" else ("dq", "aq")
        image = image[0] if e["type"] == "bool" else image[1]
        index[e["plc_var"]] = (image, counters[image], e["type"])
        counters[image] += 1
    return {"index": index, "counters": dict(counters)}


def gen_shim_c(io_map) -> str:
    """生成完整 shim 源码（定位变量 glue + 稳定接口）。同 io_map 必产出同源码。

    定位变量 glue（OpenPLC 内置 matiec 2019 实测）：iec2c 生成码对定位变量的外部引用是
    **type\* 指针**（POUS 的 __INIT_LOCATED 做 name.value = location，OpenPLC 由 core/glue
    提供指针实体）——独立 DLL 没有 core，shim 充当 glue：每个 io_map 变量定义静态存储 +
    指针符号（一址一变量，重复即报错）。若日后升级 matiec 导致符号/语义变化，只改本函数。"""
    defs = []
    seen = set()
    lines = {"di": [], "ai": [], "dq": [], "aq": []}
    layout = build_layout(io_map)
    for e in io_map:
        plc_addr = e.get("modbus", {}).get("plc_addr")
        if not plc_addr:
            raise ValueError(f"{e['plc_var']}: 链路 A 需要 modbus.plc_addr（%QX/%QW 定位地址）")
        sym = symbol_for(e["type"], plc_addr)
        if sym in seen:
            raise ValueError(f"地址符号重复: {sym}（{e['plc_var']}）——%QX/%QW 一址一变量")
        seen.add(sym)
        ctype = C_TYPE[e["type"]]
        defs.append(f"static {ctype} st_{sym.lstrip('_')};  {ctype} *{sym} = &st_{sym.lstrip('_')};   /* {e['plc_var']} ({e['dir']}) */")
        image, idx, _ = layout["index"][e["plc_var"]]
        if image in ("di", "ai"):            # 输入镜像 → 写入定位变量
            lines[image].append(f"    *{sym} = {image}[{idx}];")
        else:                                # 输出镜像 ← 读出定位变量
            lines[image].append(f"    {image}[{idx}] = *{sym};")

    header = (
        "/* plc_shim.c —— 由 toolchain/shim_gen.py 按 io_map（契约③）自动生成，勿手改。\n"
        " * 隔离 matiec 生成码的符号/版本差异，上层只认稳定接口：\n"
        " *   plc_init / plc_run(tick) / plc_write_image(di,ai) / plc_read_image(dq,aq)\n"
        " * 镜像为紧凑排列：di/dq = bool 字节，ai/aq = int16 字（顺序=io_map 过滤方向后顺序）。\n"
        " * 定位变量为指针语义（见 gen_shim_c 文档串），shim 自带 glue 存储。 */\n"
        '#include "iec_std_lib.h"   /* BOOL/INT/WORD 类型（matiec lib/C，-I 由 build_dll 提供） */\n'
        "/* 入口符号恒为 config_init__/config_run__（配置头文件名随 CONFIGURATION 名变化，不依赖） */\n"
        "void config_init__(void);\n"
        "void config_run__(unsigned long tick);\n\n"
    )
    body = "\n".join(defs) + "\n\n"
    body += "void plc_init(void)            { config_init__(); }\n"
    body += "void plc_run(unsigned long t)  { config_run__(t); }\n\n"
    body += "void plc_write_image(const unsigned char* di, const short* ai) {\n"
    body += "\n".join(lines["di"] + lines["ai"]) + "\n}\n\n"
    body += "void plc_read_image(unsigned char* dq, short* aq) {\n"
    body += "\n".join(lines["dq"] + lines["aq"]) + "\n}\n"
    return header + body


def gen_shim_h(io_map) -> str:
    c = build_layout(io_map)["counters"]
    return (
        "/* plc_shim.h —— 自动生成（toolchain/shim_gen.py），勿手改。 */\n"
        "#ifndef PLC_SHIM_H\n#define PLC_SHIM_H\n\n"
        "#ifdef __cplusplus\nextern \"C\" {\n#endif\n\n"
        "void plc_init(void);\n"
        "void plc_run(unsigned long tick);\n"
        f"void plc_write_image(const unsigned char* di, const short* ai);  /* di[{c['di']}] ai[{c['ai']}] */\n"
        f"void plc_read_image(unsigned char* dq, short* aq);               /* dq[{c['dq']}] aq[{c['aq']}] */\n\n"
        "#ifdef __cplusplus\n}\n#endif\n#endif\n"
    )
