"""Disable the local OpenPLC ST optimizer that changes CASE branch semantics.

Only the running Docker container publishing the requested local HTTP port is
managed. XML/ST are untouched; matiec remains the compiler. Remote runtimes
need the same configuration by their administrator.
"""
import base64
import hashlib
import json
import os
import re
import subprocess
from urllib.parse import urlsplit


SCRIPT = "/workdir/webserver/scripts/compile_program.sh"
MARKER = 'echo "ST optimizer bypassed: preserve source semantics (agent-guard-v1)"'
OPTIMIZER = re.compile(r'^\s*\./st_optimizer\s+[^\n]+$', re.MULTILINE)


class CompilerConfigurationError(RuntimeError):
    pass


def guarded_script(text):
    calls = list(OPTIMIZER.finditer(text))
    if MARKER in text and not calls:
        return text
    if len(calls) != 1 or MARKER in text:
        raise CompilerConfigurationError("OpenPLC 编译脚本结构未知，未修改；须人工核对 ST optimizer")
    return OPTIMIZER.sub(lambda _: MARKER, text, count=1)


# Python 2/3 compatible because the installed OpenPLC image uses Python 2.7.
INSTALL = r'''
import base64, hashlib, json, os, stat, sys
p = json.load(sys.stdin)
path = p['path']
current = open(path, 'rb').read()
if hashlib.sha256(current).hexdigest() != p['expected_sha256']:
    raise RuntimeError('Compile script changed during guard installation')
backup = path + '.pre_agent_optimizer_guard'
if not os.path.exists(backup):
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, stat.S_IMODE(os.stat(path).st_mode))
    with os.fdopen(fd, 'wb') as f:
        f.write(current)
replacement = base64.b64decode(p['replacement'])
tmp = path + '.agent_guard_pending'
with open(tmp, 'wb') as f:
    f.write(replacement)
os.chmod(tmp, stat.S_IMODE(os.stat(path).st_mode))
getattr(os, 'replace', os.rename)(tmp, path)
sys.stdout.write(json.dumps({'backup': backup, 'sha256': hashlib.sha256(replacement).hexdigest()}))
'''


def _docker(args, input=None):
    try:
        result = subprocess.run(["docker", *args], input=input, capture_output=True,
                                timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise CompilerConfigurationError("Docker 编译环境检查失败：%s" % exc) from exc
    if result.returncode:
        raise CompilerConfigurationError(result.stderr.decode("utf-8", errors="replace")[-1000:])
    return result.stdout


def ensure_local_compiler(url, container=None):
    """Idempotent configuration for the matching local Docker runtime only."""
    target = urlsplit(url)
    if target.hostname not in ("127.0.0.1", "localhost", "::1"):
        return {"status": "external_unverified", "url": url}
    container = container or os.environ.get("OPENPLC_CONTAINER", "openplc")
    try:
        info = json.loads(_docker(["inspect", container]))[0]
    except CompilerConfigurationError:
        return {"status": "unmanaged_unverified", "url": url}
    ports = info.get("NetworkSettings", {}).get("Ports", {}).get("8080/tcp") or []
    port = target.port or (443 if target.scheme == "https" else 80)
    bindings = [p for p in ports if int(p["HostPort"]) == port
                and p.get("HostIp") in ("0.0.0.0", "127.0.0.1", "::", "::1", "")]
    if not info.get("State", {}).get("Running") or not bindings or target.scheme != "http":
        return {"status": "unmanaged_unverified", "url": url}
    original = _docker(["exec", container, "cat", SCRIPT])
    replacement = guarded_script(original.decode("utf-8")).encode("utf-8")
    changed = replacement != original
    if changed:
        payload = {"path": SCRIPT, "expected_sha256": hashlib.sha256(original).hexdigest(),
                   "replacement": base64.b64encode(replacement).decode("ascii")}
        _docker(["exec", "-i", container, "python", "-c", INSTALL], json.dumps(payload).encode())
    actual = _docker(["exec", container, "cat", SCRIPT])
    if actual != replacement:
        raise CompilerConfigurationError("OpenPLC 编译脚本回读不符，停止部署")
    return {"status": "configured" if changed else "verified", "container": container,
            "container_id": info["Id"], "image_id": info["Image"], "optimizer": "bypassed",
            "compile_script_sha256": hashlib.sha256(actual).hexdigest(),
            "backup": SCRIPT + ".pre_agent_optimizer_guard"}


if __name__ == "__main__":
    print(json.dumps(ensure_local_compiler(os.environ.get("OPENPLC_URL", "http://127.0.0.1:8080")),
                     ensure_ascii=False, indent=2))
