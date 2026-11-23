"""Measure observed forbidden-state intervals without hiding raw samples."""
import math


def bounded_violations(trace, mask, response_s=0, source=None):
    if len(trace) != len(mask) or not math.isfinite(response_s) or response_s < 0:
        raise ValueError("Invalid response window or sample mask")
    raw, confirmed, intervals = [], [], []
    pending = []
    anchors = []
    if source is not None and len(source) != len(trace):
        raise ValueError("Source condition and trace length differ")
    anchor = trace[0]["t"] if trace else 0
    for index, (sample, bad) in enumerate(zip(trace, mask)):
        if not isinstance(sample.get("t"), (int, float)) or not math.isfinite(sample["t"]):
            raise ValueError("Trace requires finite timestamps")
        if index and sample["t"] < trace[index - 1]["t"]:
            raise ValueError("Trace timestamps must not decrease")
        if source is not None and index and source[index] != source[index - 1]:
            anchor = sample["t"]
        anchors.append(anchor)
        if bad:
            raw.append(index)
            pending.append(index)
        elif pending:
            duration = sample["t"] - trace[pending[0]]["t"]
            source_at = anchors[pending[0]] if source is not None else trace[pending[0]]["t"]
            delay = sample["t"] - source_at
            violation = response_s == 0 or delay > response_s + 1e-9
            intervals.append({"start": trace[pending[0]]["t"], "end": sample["t"],
                              "duration_s": duration, "source_changed_at": source_at, "response_delay_s": delay,
                              "resolved": True, "violation": violation})
            if violation:
                confirmed.extend(pending)
            pending = []
    if pending:
        confirmed.extend(pending)
        intervals.append({"start": trace[pending[0]]["t"], "end": None,
                          "duration_s": trace[-1]["t"] - trace[pending[0]]["t"],
                          "source_changed_at": anchors[pending[0]],
                          "response_delay_s": trace[-1]["t"] - anchors[pending[0]],
                          "resolved": False, "violation": True})
    return {"raw_indices": raw, "confirmed_indices": confirmed, "intervals": intervals,
            "max_observed_duration_s": max((i["duration_s"] for i in intervals), default=0),
            "max_response_delay_s": max((i["response_delay_s"] for i in intervals), default=0),
            "unresolved": bool(pending)}
