"""Parse OpenSTA report_checks output into clean Python data."""

import re

# A pin row: some numbers, then ^ or v, then pin (cell)
#   "     7    0.02    0.55    0.95    1.26 ^ _4701_/Y (sky130_fd_sc_hd__nand2_1)"
ROW = re.compile(r"^\s*([-\d.\s]*?)\s*([\^v])\s+(\S+)\s+\(([^)]+)\)\s*$")
SLACK = re.compile(r"^\s*(-?[\d.]+)\s+slack\s+\((\w+)\)")
CLOCKED_BY = re.compile(r"clocked by (\S+?)\)")

OUT_PINS = {"Y", "X", "Q", "Q_N", "Z", "SUM", "COUT", "CO", "HI", "LO"}


def parse(text):
    paths, cur = [], None
    pending_net = 0.0

    for line in text.splitlines():
        if line.startswith("Startpoint:"):
            cur = {"startpoint": line.split(":", 1)[1].strip(),
                   "endpoint": "", "clock": None, "slack": None, "stages": []}
            pending_net = 0.0
        if cur is None:
            continue

        if line.startswith("Endpoint:"):
            cur["endpoint"] = line.split(":", 1)[1].strip()

        # In real reports the clock name is on a CONTINUATION line
        if cur["clock"] is None and (m := CLOCKED_BY.search(line)):
            cur["clock"] = m.group(1)

        if (m := ROW.match(line)):
            nums = [float(x) for x in m.group(1).split()]
            if len(nums) >= 2:
                pin, cell = m.group(3), m.group(4)
                port = pin.rsplit("/", 1)[-1]
                delay = nums[-2]
                if port in OUT_PINS:
                    cur["stages"].append({
                        "instance": pin.rsplit("/", 1)[0],
                        "cell": cell,
                        "cell_delay": delay,
                        "net_delay": pending_net,
                        "fanout": int(nums[-5]) if len(nums) >= 5 else None,
                    })
                    pending_net = 0.0
                else:
                    pending_net += delay

        if (m := SLACK.match(line)):
            cur["slack"] = float(m.group(1))
            add_derived(cur)
            paths.append(cur)
            cur = None

    return paths


def add_derived(path):
    for s in path["stages"]:
        s["total_delay"] = round(s["cell_delay"] + s["net_delay"], 3)
    total = sum(s["total_delay"] for s in path["stages"]) or 1.0
    for s in path["stages"]:
        s["delay_share"] = round(s["total_delay"] / total, 3)
        tail = s["cell"].rsplit("_", 1)[-1]
        s["drive"] = int(tail) if tail.isdigit() else None
    path["stages"].sort(key=lambda s: -s["total_delay"])


def summary(paths):
    bad = [p for p in paths if p["slack"] < 0]
    return {"wns": min([p["slack"] for p in paths], default=0.0),
            "tns": round(sum(p["slack"] for p in bad), 3),
            "n_paths": len(paths), "n_violating": len(bad),
            "timing_met": not bad}


def shared_bottlenecks(paths, top=10):
    bad = sorted([p for p in paths if p["slack"] < 0],
                 key=lambda p: p["slack"])[:top]
    counts = {}
    for p in bad:
        for s in p["stages"]:
            r = counts.setdefault(s["instance"],
                                  {"instance": s["instance"], "cell": s["cell"],
                                   "n_paths": 0, "max_share": 0.0})
            r["n_paths"] += 1
            r["max_share"] = max(r["max_share"], s["delay_share"])
    return sorted([r for r in counts.values() if r["n_paths"] > 1],
                  key=lambda r: (-r["n_paths"], -r["max_share"]))


if __name__ == "__main__":
    import json, sys
    paths = parse(open(sys.argv[1]).read())
    print("SUMMARY:", json.dumps(summary(paths)))
    print()
    print("SHARED BOTTLENECKS:", json.dumps(shared_bottlenecks(paths), indent=2))
    print()
    worst = sorted(paths, key=lambda p: p["slack"])[0]
    print("WORST PATH:")
    print(json.dumps({"startpoint": worst["startpoint"],
                      "endpoint": worst["endpoint"],
                      "clock": worst["clock"], "slack": worst["slack"],
                      "depth": len(worst["stages"]),
                      "hot_stages": worst["stages"][:3]}, indent=2))
