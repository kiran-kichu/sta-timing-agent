"""Baseline: delay-ranked greedy sizing, NO revert. The thing to beat."""
import sys, json, sta_parse
from sta_env import Design, AREAS, FAMILIES

V = sys.argv[1] if len(sys.argv)>1 else "p3.6"
D = Design(V)
base = D.sta()
print("baseline:", base)

for step in range(8):
    paths = [p for p in sta_parse.parse(D.report(8)) if p["slack"] < 0]
    if not paths:
        break
    score = {}
    for p in paths:
        for s in p["stages"]:
            r = score.setdefault(s["instance"], {"cell": s["cell"], "d": 0.0})
            r["d"] += s["total_delay"]

    # Greedy: take the highest summed-delay cell, jump to max drive, keep it.
    for inst, r in sorted(score.items(), key=lambda kv: -kv[1]["d"]):
        b = r["cell"].rsplit("_", 1)[0]
        drives = FAMILIES.get(b, [])
        cur = int(r["cell"].rsplit("_", 1)[-1]) if r["cell"].rsplit("_", 1)[-1].isdigit() else None
        bigger = [d for d in drives if cur is not None and d > cur]
        if not bigger:
            continue
        new = f"{b}_{max(bigger)}"
        if D._swap(inst, r["cell"], new) == 1:
            D.invalidate()
            after = D.sta()
            print(f"  {inst:28s} {r['cell'].split('__')[1]:12s} -> "
                  f"{new.split('__')[1]:12s} wns={after['wns_ns']:+.3f}")
            break
    else:
        break

final = D.sta()
print("\nGREEDY  final:", final)
print("ΔWNS :", round(final["wns_ns"] - base["wns_ns"], 3))
print("Δarea:", round(final["area"] - base["area"], 2))

print("RESULT_JSON " + json.dumps({
    "method":"greedy","repeat":0,"variant":V,
    "wns_base":base["wns_ns"],"tns_base":base["tns_ns"],
    "wns_final":final["wns_ns"],"tns_final":final["tns_ns"],
    "area_delta":round(final["area"]-base["area"],2)}))
