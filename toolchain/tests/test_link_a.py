"""链路 A 分层测试（协作指南 §3：L1 静态 / L2 单测 / L3 在线验收）。

L2（本机全绿，无工具链依赖）：shim 生成 golden、镜像索引双实现一致性、
    定点换算/打包、契约③ Schema 校验、工具链缺失降级。
L3（具备 matiec+gcc 的机器/工具链 Docker 上执行，否则 SKIP）：minimal.st → DLL →
    SoftPLC 写读回环（run 注入 → echo 直通 + prog_id 身份）。

运行：python toolchain/tests/test_link_a.py  或  python -m pytest toolchain/tests/ -v
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLCHAIN = os.path.dirname(HERE)
REPO = os.path.dirname(TOOLCHAIN)
sys.path.insert(0, TOOLCHAIN)
sys.path.insert(0, os.path.join(REPO, "runtime"))

from shim_gen import build_layout, gen_shim_c, gen_shim_h, symbol_for  # noqa: E402
from plc_binding import IOLayout, SoftPLC  # noqa: E402
import build_dll  # noqa: E402

FIXTURE = os.path.join(HERE, "fixtures", "io_map_motion3axis.json")
SCHEMA = os.path.join(REPO, "schemas", "io_map.schema.json")


def load_fixture():
    with open(FIXTURE, encoding="utf-8") as f:
        return json.load(f)


def test_layout_matches_shim_gen():
    """单一规则双实现：plc_binding.IOLayout 与 shim_gen.build_layout 必须逐变量一致。"""
    io_map = load_fixture()["entries"]
    a = IOLayout(io_map).index
    b = build_layout(io_map)["index"]
    assert a == b, "镜像索引双实现漂移——shim 与 Python 打包将错位，禁止放行"


def test_shim_golden():
    io_map = load_fixture()["entries"]
    c1, c2 = gen_shim_c(io_map), gen_shim_c(io_map)
    assert c1 == c2, "shim 生成必须确定（同 io_map 同源码）"
    # glue 定义与符号规则（指针语义：static 存储 + type* 符号，OpenPLC matiec 实测）
    assert 'static BOOL st_QX0_0;  BOOL *__QX0_0 = &st_QX0_0;   /* run (input) */' in c1
    assert 'static INT st_QW0;  INT *__QW0 = &st_QW0;   /* x_fb (input) */' in c1
    assert 'static WORD st_QW6;  WORD *__QW6 = &st_QW6;   /* x_sw (output) */' in c1
    assert 'static INT st_QW20;  INT *__QW20 = &st_QW20;   /* prog_id (output) */' in c1
    # 紧凑镜像排列：di=[run,cmd_home]，ai=[x_fb]，dq=[move_done,echo]，aq=[x_sw,x_sp,prog_id]
    assert "*__QX0_0 = di[0];" in c1 and "*__QX0_1 = di[1];" in c1
    assert "*__QW0 = ai[0];" in c1
    assert "dq[0] = *__QX1_1;" in c1 and "dq[1] = *__QX1_0;" in c1
    assert "aq[0] = *__QW6;" in c1 and "aq[1] = *__QW10;" in c1 and "aq[2] = *__QW20;" in c1
    assert "void plc_write_image(const unsigned char* di, const short* ai);" in gen_shim_h(io_map)
    # 地址冲突拒绝
    bad = [dict(io_map[0]), dict(io_map[0])]
    bad[1] = {**bad[1], "plc_var": "dup"}
    try:
        gen_shim_c(bad)
        assert False, "同址双变量应报错"
    except ValueError as e:
        assert "地址符号重复" in str(e)
    # %QD 拒绝（契约② 禁用）
    try:
        symbol_for("analog", "%QD0")
        assert False, "%QD 应拒绝"
    except ValueError as e:
        assert "仅 %QX/%QW" in str(e)


def test_layout_and_scaling():
    io_map = load_fixture()["entries"]
    lo = IOLayout(io_map)
    assert lo.sizes == {"di": 2, "ai": 1, "dq": 2, "aq": 3}
    # 定点换算：scale=0.1（0.1mm/LSB）→ 100mm = 1000 LSB；超程钳位 INT16
    assert lo.to_raw("x_fb", 100) == 1000
    assert lo.to_raw("x_fb", 99999) == 32767
    assert lo.to_raw("x_fb", -5) == -50
    assert lo.to_eng("x_fb", 1000) == 100.0
    # bool / word（word 无符号语义：-1 位型 → 65535）
    assert lo.to_raw("run", True) == 1 and lo.to_eng("run", 0) is False
    assert lo.to_eng("x_sw", -1) == 65535
    # 打包/解包（未指定变量置 0）
    di, ai = lo.pack_inputs({"run": True, "x_fb": 100})
    assert list(di) == [1, 0]
    import struct
    assert struct.unpack("<h", ai) == (1000,)
    out = lo.unpack_outputs(bytes([1, 0]), struct.pack("<3h", 0x1234, 1234, 1))
    assert out["move_done"] is True
    assert out["echo"] is False
    assert out["x_sw"] == 0x1234 & 0xFFFF
    assert out["x_sp"] == 123.4
    assert out["prog_id"] == 1


def test_schema():
    try:
        import jsonschema
    except ImportError:
        print("SKIP：未安装 jsonschema")
        return
    with open(SCHEMA, encoding="utf-8") as f:
        schema = json.load(f)
    jsonschema.validate(load_fixture(), schema)
    bad = load_fixture()
    bad["entries"][0]["dir"] = "sideways"          # 非法方向
    try:
        jsonschema.validate(bad, schema)
        assert False, "非法 dir 应被 Schema 拒绝"
    except jsonschema.ValidationError:
        pass


def test_build_dll_missing_toolchain():
    """无工具链时优雅降级：ok=False + 可操作提示（不抛异常、不半成品）。"""
    saved = build_dll.find_toolchain
    build_dll.find_toolchain = lambda: (None, None)
    try:
        r = build_dll.build(os.path.join(HERE, "fixtures", "minimal.st"),
                            FIXTURE, os.path.join(HERE, "out_l2"))
        assert r["ok"] is False and "工具链缺失" in r["error"]
        assert "MATEC" in r["error"] and "WSL" in r["error"]
    finally:
        build_dll.find_toolchain = saved


def test_build_dll_l3_end_to_end():
    """L3：真 matiec+gcc 编译 minimal.st → SoftPLC 回环。无工具链则 SKIP。"""
    iec2c, cc = build_dll.find_toolchain()
    matiec_root = os.environ.get("MATEC_ROOT")
    if not (iec2c and cc and matiec_root):
        print("SKIP：缺工具链（需 iec2c + gcc + MATEC_ROOT 指向 matiec 根目录）")
        return
    out = os.path.join(HERE, "out_l3")
    r = build_dll.build(os.path.join(HERE, "fixtures", "minimal.st"),
                        FIXTURE, out, matiec_root=matiec_root)
    assert r["ok"], f"L3 构建失败: {r.get('error')}"
    plc = SoftPLC(r["dll"], load_fixture()["entries"])
    plc.init()
    plc.write_inputs({"run": True})
    plc.run(0)
    o = plc.read_outputs()
    assert o["echo"] is True, "输入注入 → PLC 扫描 → 输出直通失败"
    assert o["prog_id"] == 1, "契约② v1.1 程序身份未读到"
    plc.write_inputs({"run": False})
    plc.run(1)
    assert plc.read_outputs()["echo"] is False


def main() -> int:
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"PASS：{t.__name__}")
    print(f"PASS：{len(tests)} 项链路 A 断言全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
