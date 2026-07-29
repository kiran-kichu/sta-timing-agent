"""Greedy baseline across variants. No API calls, so this is fast."""
import csv, sta_parse
from sta_env import Design, FAMILIES

VARIANTS = ["p3.6", "p4.0", "p4.4", "p4.8", "p5.2", "p6.0"]
rows = []

for v in VARIANTS:
    D = Design(v)
    base = D.sta()
    print(f"{v}: baseline {base['wns_ns']}", flush=True)

    for _ in range(8):
        paths = [p for p in sta_parse.parse(D.report(8)) if p["slack"] < 0]
        if not paths:
            break
        score = {}
        for p in paths:
            for s in p["stages"]:
                r = score.setdefault(s["instance"], {"cell": s["cell"], "d": 0.0})
                r["d"] += s["total_delay"]
        for inst, r in sorted(score.items(), key=lambda kv: -kv[1]["d"]):
            b = r["cell"].rsplit("_", 1)[0]
            tail = r["cell"].rsplit("_", 1)[-1]
            cur = int(tail) if tail.isdigit() else None
            bigger = [d for d in FAMILIES.get(b, []) if cur is not None and d > cur]
            if not bigger:
                continue
            if D._swap(inst, r["cell"], f"{b}_{max(bigger)}") == 1:
                D.invalidate()
                break
        else:
            break

    f = D.sta()
    rows.append({"method": "greedy", "repeat": 0, "variant": v,
                 "wns_base": base["wns_ns"], "tns_base": base["tns_ns"],
                 "wns_final": f["wns_ns"], "tns_final": f["tns_ns"],
                 "area_delta": round(f["area"] - base["area"], 2)})
    print(f"   -> wns {f['wns_ns']}  tns {f['tns_ns']}  area +{rows[-1]['area_delta']}",
          flush=True)

with open("greedy_results.csv", "w", newline="") as fh:
    w = csv.DictWriter(fh, fieldnames=sorted({k for r in rows for k in r}))
    w.writeheader(); w.writerows(rows)
print("\n-> greedy_results.csv")
