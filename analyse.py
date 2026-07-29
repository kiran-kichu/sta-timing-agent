"""Combine sweep + greedy CSVs into the comparison table with variance."""
import csv, statistics as st
from collections import defaultdict

def load(fn):
    try:
        return list(csv.DictReader(open(fn)))
    except FileNotFoundError:
        print(f"(missing {fn})")
        return []

rows = load("sweep_results.csv") + load("greedy_results.csv")
if not rows:
    raise SystemExit("no data yet")

g = defaultdict(list)
for r in rows:
    g[(r["variant"], r["method"])].append(r)

variants = sorted({r["variant"] for r in rows})
print(f"{'variant':8} {'base WNS':>9} {'method':7} {'n':>2} "
      f"{'WNS mean':>9} {'WNS sd':>7} {'WNS worst':>10} "
      f"{'TNS mean':>9} {'area mean':>10}")
print("-" * 88)

for v in variants:
    for m in ("greedy", "agent"):
        rs = g.get((v, m), [])
        if not rs:
            continue
        wns = [float(r["wns_final"]) for r in rs]
        tns = [float(r["tns_final"]) for r in rs]
        ar  = [float(r["area_delta"]) for r in rs]
        sd  = st.stdev(wns) if len(wns) > 1 else 0.0
        print(f"{v:8} {float(rs[0]['wns_base']):9.3f} {m:7} {len(rs):2d} "
              f"{st.mean(wns):9.3f} {sd:7.3f} {min(wns):10.3f} "
              f"{st.mean(tns):9.2f} {st.mean(ar):10.1f}")
    print()

# The number that matters most: is the agent's advantage bigger than its noise?
print("agent run-to-run spread in WNS (max - min), per variant:")
for v in variants:
    rs = g.get((v, "agent"), [])
    if len(rs) > 1:
        wns = [float(r["wns_final"]) for r in rs]
        print(f"  {v}: {max(wns) - min(wns):.3f} ns")
