"""Run agent + greedy across variants x repeats. Unattended. Dumps CSV."""
import csv, json, subprocess, sys, time

VARIANTS = ["p3.6", "p4.0", "p4.4", "p4.8", "p5.2", "p6.0"]
REPEATS = 3          # the API has no seed; this measures run-to-run variance
OUT = "sweep_results.csv"

rows = []
t0 = time.time()

for v in VARIANTS:
    for rep in range(REPEATS):
        print(f"[{time.time()-t0:6.0f}s] agent {v} rep {rep}", flush=True)
        try:
            p = subprocess.run([sys.executable, "real_agent_tns.py", v],
                               capture_output=True, text=True, timeout=1800)
            line = next((l for l in p.stdout.splitlines()
                         if l.startswith("RESULT_JSON ")), None)
            if line:
                d = json.loads(line[len("RESULT_JSON "):])
                d.update(method="agent", repeat=rep)
                rows.append(d)
                print(f"    wns {d['wns_base']} -> {d['wns_final']}  "
                      f"tns {d['tns_base']} -> {d['tns_final']}  "
                      f"area +{d['area_delta']}", flush=True)
            else:
                print("    NO RESULT_JSON -- run failed", flush=True)
                print(p.stdout[-500:], p.stderr[-500:], flush=True)
        except subprocess.TimeoutExpired:
            print("    TIMEOUT", flush=True)

        # write after every run so a crash never loses everything
        if rows:
            with open(OUT, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=sorted(
                    {k for r in rows for k in r}))
                w.writeheader(); w.writerows(rows)

print(f"\ndone in {(time.time()-t0)/60:.1f} min -> {OUT}")
print(f"{len(rows)} successful runs of {len(VARIANTS)*REPEATS} attempted")
