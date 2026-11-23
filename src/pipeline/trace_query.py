"""Select observed events and task windows from fully collected PLC traces."""


def edge_times(trace, reference):
    name = reference["signal"]
    if any(name not in sample for sample in trace):
        return []
    want = reference.get("edge", "rising") == "rising"
    return [b["t"] for a, b in zip(trace, trace[1:])
            if bool(a[name]) != want and bool(b[name]) == want]


def event_window(trace, start, end=None):
    """Initial levels never invent events; missing boundaries produce no window."""
    starts = edge_times(trace, start)
    if not starts:
        return []
    t0 = starts[-1 if start.get("occurrence", "first") == "last" else 0]
    if end:
        ends = [t for t in edge_times(trace, end) if t >= t0]
        if not ends:
            return []
        t1 = ends[-1 if end.get("occurrence", "first") == "last" else 0]
    else:
        t1 = trace[-1]["t"]
    return [sample for sample in trace if t0 <= sample["t"] <= t1]
