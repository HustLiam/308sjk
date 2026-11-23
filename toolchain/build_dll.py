"""链路 A 构建编排：PLCopen XML → .st →（matiec iec2c）→ C →（gcc）→ 共享库 + shim。

流水线（csk 文档 §6.2.2，转换点唯一——两条链路编译同一份 xml2st 产物）：
    ① xml2st 校验+转换（复用 PLC 侧 src/pipeline/xml2st.py，子进程调用）
    ② matiec iec2c 把 .st 编译为 C（输入是 ST 文本；符号命名随版本变化 → shim 隔离）
    ③ shim 生成（toolchain/shim_gen.py，按 io_map 契约③）
    ④ gcc/clang 编译为共享库 plc_logic.dll/.so（含 matiec lib 头文件与运行时源）

工具链发现：环境变量 MATEC（iec2c 可执行）与 CC（编译器），否则查 PATH。
本机/CI 无工具链时构建失败并给出可操作提示（L3 层——分层测试策略，
协作指南 §3：L1 静态/L2 单测不依赖工具链，L3 在具备 matiec+gcc 的机器或
工具链 Docker（csk 文档 §8.5 锁版本）上执行）。

用法：
    python toolchain/build_dll.py <plc.xml 或 plc.st> --io-map io_map.json -o out/link_a
    python toolchain/build_dll.py <plc.xml> --io-map io_map.json -o out/link_a --matiec-root /opt/matiec
"""

import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(REPO, "runtime"))

from shim_gen import gen_shim_c, gen_shim_h  # noqa: E402


def _which(candidates, env_var):
    """按 环境变量 → 候选名列表 → PATH 的顺序解析可执行文件。"""
    env = os.environ.get(env_var)
    if env and (os.path.isfile(env) or shutil.which(env)):
        return env
    for c in candidates:
        p = shutil.which(c)
        if p:
            return p
    return None


def find_toolchain():
    iec2c = _which(["iec2c"], "MATEC")
    cc = _which(["gcc", "clang", "cc"], "CC")
    return iec2c, cc


def xml_to_st(xml_path: str, out_dir: str) -> str:
    """复用 lx 的 xml2st（校验 + 转换，转换点唯一）。子进程调用，解耦实现细节。"""
    st_path = os.path.join(out_dir, "plc.st")
    script = os.path.join(REPO, "src", "pipeline", "xml2st.py")
    r = subprocess.run([sys.executable, script, xml_path, "--out", st_path],
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"xml2st 失败（编译错误回喂 ② 重生成，不进入仿真）:\n{r.stdout}\n{r.stderr}")
    return st_path


def st_to_c(iec2c: str, st_path: str, out_dir: str, extra_args=None) -> list:
    """matiec iec2c：ST → C。生成文件落在 out_dir（子进程 cwd 切到 out_dir）。

    extra_args 为 None 时默认 `-f -l`；标准库目录由调用方经 extra_args 以
    `-I <matiec_root>/lib` 传入（iec2c 从该目录找 ieclib.txt，否则依赖 cwd/lib）。"""
    if extra_args is None:
        args = ["-f", "-l"]
    else:
        args = list(extra_args)
    args.append(os.path.abspath(st_path))
    r = subprocess.run([iec2c] + args, cwd=out_dir, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"iec2c 失败（matiec 版本参数差异见 csk 文档 §6.2.2）:\n{r.stdout}\n{r.stderr}")
    c_files = sorted(glob.glob(os.path.join(out_dir, "*.c")))
    return drop_textually_included(c_files)


def drop_textually_included(c_files: list) -> list:
    """剔除被其他生成 .c 文本包含（#include "xxx.c"）的文件。

    部分 matiec 版本（含 OpenPLC 内置 2019 版）把 POUS.c 直接 #include 进资源 .c
    （unity build，由包含方先提供头文件）——被包含者再单独编译必然重复符号。"""
    included = set()
    for path in c_files:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for m in re.finditer(r'^\s*#include\s+"([^"]+\.c)"', f.read(), re.MULTILINE):
                included.add(os.path.basename(m.group(1)))
    return [c for c in c_files if os.path.basename(c) not in included]


def compile_shared(cc: str, c_files, out_dir: str, matiec_lib=None,
                   lib_c_glob=None) -> str:
    """gcc → 共享库。matiec lib 头文件与标准库 C 一并编译（路径随 matiec-root 提供）。

    头文件位于 <matiec_root>/lib/C/（iec_std_lib.h 等），lib/ 与 lib/C/ 均加入 -I。"""
    ext = ".dll" if os.name == "nt" else ".so"
    out = os.path.join(out_dir, f"plc_logic{ext}")
    cmd = [cc, "-shared", "-fPIC", "-O2"]
    if matiec_lib:
        cmd += ["-I", matiec_lib, "-I", os.path.join(matiec_lib, "C")]
    cmd += c_files + [os.path.join(out_dir, "plc_shim.c")]
    if lib_c_glob:
        cmd += sorted(glob.glob(lib_c_glob))
    cmd += ["-o", out]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"编译共享库失败:\n{r.stdout}\n{r.stderr}")
    return out


def build(source_path: str, io_map_path: str, out_dir: str,
          matiec_root=None, extra_iec2c_args=None) -> dict:
    """完整流水线。返回 build_result.json 内容（供回喂/编排器消费）。"""
    t0 = time.time()
    os.makedirs(out_dir, exist_ok=True)
    iec2c, cc = find_toolchain()
    missing = [n for n, p in (("MATEC(iec2c)", iec2c), ("CC(gcc/clang)", cc)) if not p]
    result = {"ok": False, "steps": [], "source": source_path, "out_dir": out_dir,
              "toolchain": {"iec2c": iec2c, "cc": cc}}
    if missing:
        result["error"] = (
            "工具链缺失: " + ", ".join(missing)
            + " —— 链路 A 需 matiec+gcc（L3 层）。安装提示：设环境变量 MATEC/CC 或加入 PATH；"
              "或用 WSL/Docker（工具链锁定见 csk 文档 §6.2.2 / §8.5）")
        return result

    with open(io_map_path, encoding="utf-8") as f:
        io_map = json.load(f)
    io_map = io_map.get("entries", io_map)          # 契约③ 包装或裸数组皆可

    # ① xml → st（已是 .st 则跳过）
    if source_path.lower().endswith(".xml"):
        st_path = xml_to_st(source_path, out_dir)
        result["steps"].append({"step": "xml2st", "ok": True, "out": st_path})
    else:
        st_path = source_path
        result["steps"].append({"step": "xml2st", "ok": True, "skipped": "输入已是 .st"})

    # ② st → C（标准库经 -I 指向 <matiec_root>/lib；调用方显式覆盖参数时不追加）
    if extra_iec2c_args is None:
        extra_iec2c_args = ["-f", "-l"]
        lib_dir = os.path.join(matiec_root, "lib") if matiec_root else None
        if lib_dir and os.path.isdir(lib_dir):
            extra_iec2c_args = ["-f", "-l", "-I", lib_dir]
    c_files = st_to_c(iec2c, st_path, out_dir, extra_iec2c_args)
    result["steps"].append({"step": "iec2c", "ok": True, "files": [os.path.basename(c) for c in c_files]})

    # ③ shim（按 io_map 契约③ 生成，勿手改）
    with open(os.path.join(out_dir, "plc_shim.c"), "w", encoding="utf-8", newline="\n") as f:
        f.write(gen_shim_c(io_map))
    with open(os.path.join(out_dir, "plc_shim.h"), "w", encoding="utf-8", newline="\n") as f:
        f.write(gen_shim_h(io_map))
    result["steps"].append({"step": "shim_gen", "ok": True})

    # ④ 编译共享库
    matiec_lib = os.path.join(matiec_root, "lib") if matiec_root else None
    lib_c = os.path.join(matiec_root, "lib", "C", "*.c") if matiec_root else None
    dll = compile_shared(cc, c_files, out_dir, matiec_lib=matiec_lib, lib_c_glob=lib_c)
    result["steps"].append({"step": "cc", "ok": True, "out": dll})

    result["ok"] = True
    result["dll"] = dll
    result["elapsed_s"] = round(time.time() - t0, 2)
    with open(os.path.join(out_dir, "build_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    return result


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="链路 A 构建流水线（xml/st → DLL + shim）")
    p.add_argument("source", help="PLCopen XML（推荐，转换点唯一）或现成 .st")
    p.add_argument("--io-map", required=True, help="io_map.json（契约③，shim 地址表来源）")
    p.add_argument("-o", "--out", default="out/link_a")
    p.add_argument("--matiec-root", default=os.environ.get("MATEC_ROOT"),
                   help="matiec 根目录（含 lib/ 头文件与标准库 C；默认取 MATEC_ROOT）")
    p.add_argument("--iec2c-args", nargs="*", default=None,
                   help="覆盖 iec2c 参数（默认 -f -l；版本差异在此调，见 csk 文档 §6.2.2）")
    args = p.parse_args(argv)
    result = build(args.source, args.io_map, args.out,
                   matiec_root=args.matiec_root, extra_iec2c_args=args.iec2c_args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
