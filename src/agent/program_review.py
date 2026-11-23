"""Collect scan-order evidence from ST without changing generated source.

This is a lexical review, not a complete IEC 61131 interpreter. Findings remain
advisory: branch feasibility must be checked against the complete program.
"""
import re


def scan_order_review(source):
    # Preserve line numbers while removing comments and string literals.
    clean = re.sub(r"\(\*[\s\S]*?\*\)|//[^\n]*|'(?:''|[^'])*'",
                   lambda m: "".join("\n" if c == "\n" else " " for c in m[0]), source)
    tokens = list(re.finditer(r"\bEND_CASE\b|\bCASE\s+([A-Za-z_]\w*)\s+OF\b", clean, re.I))
    stack, cases = [], []
    for token in tokens:
        if token.group(1):
            stack.append((token, token.group(1)))
        elif stack:
            start, selector = stack.pop()
            cases.append((start.start(), token.end(), selector))
    findings = []
    for start, end, selector in cases:
        # The same selector can be written by CASE and then a later IF in one scan.
        writes = list(re.finditer(r"\b" + re.escape(selector) + r"\s*:=\s*(-?\d+)\s*;",
                                  clean[start:end], re.I))
        if not writes:
            continue
        for guard in re.finditer(r"\bIF\s+" + re.escape(selector) + r"\s*=\s*(-?\d+)\s+THEN\b",
                                 clean[end:], re.I):
            entry = guard.group(1)
            incoming = [w for w in writes if w.group(1) == entry]
            if not incoming:
                continue
            pos = end + guard.end()
            # Limit review to this IF (nested IFs included), not unrelated later code.
            depth, stop = 1, len(clean)
            for block in re.finditer(r"\bIF\b|\bEND_IF\b", clean[pos:], re.I):
                depth += 1 if block[0].upper() == "IF" else -1
                if depth == 0:
                    stop = pos + block.start()
                    break
            later = list(re.finditer(r"\b" + re.escape(selector) + r"\s*:=\s*(-?\d+)\s*;",
                                     clean[pos:stop], re.I))
            later = [w for w in later if w.group(1) != entry]
            if later:
                line = lambda offset: source.count("\n", 0, offset) + 1
                findings.append({"kind": "same_scan_state_overwrite", "advisory": True,
                                 "selector": selector, "entry_value": int(entry),
                                 "case_write_lines": [line(start + w.start()) for w in incoming],
                                 "later_guard_line": line(end + guard.start()),
                                 "later_write_lines": [line(pos + w.start()) for w in later],
                                 "question": "CASE 写入该状态后，后续 IF 能否同一扫描覆盖它，使下一扫描无法执行该 CASE 分支？核对退出分支与所有写入路径。"})
    return {"scope": "lexical ST scan-order review", "complete_analysis": False,
            "findings": findings, "source_lines": len(source.splitlines())}
