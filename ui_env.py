"""What can this deployment actually do? Detect, don't assume."""
import os, csv, glob, json, shutil

ORFS = os.path.expanduser("~/OpenROAD-flow-scripts/flow")

def has_sta():
    p = os.path.expanduser(
        "~/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/sta")
    return (os.path.isfile(p) and os.access(p, os.X_OK)) or bool(shutil.which("sta"))

def variants():
    d = f"{ORFS}/results/sky130hd/picorv32"
    if not os.path.isdir(d):
        return []
    return sorted(v for v in os.listdir(d) if v.startswith("p"))

def live():
    return has_sta() and bool(variants())

def load_csv(fn):
    try:
        return list(csv.DictReader(open(fn)))
    except FileNotFoundError:
        return []

def greedy_rows():
    rows = []
    for fn in ("greedy_clean.txt", "sweep_results_greedy.txt"):
        if os.path.exists(fn):
            for line in open(fn):
                if line.startswith("RESULT_JSON "):
                    rows.append(json.loads(line[len("RESULT_JSON "):]))
            if rows:
                break
    return rows

def traces():
    return sorted(glob.glob("traces/*.json"))

if __name__ == "__main__":
    print("sta available :", has_sta())
    print("variants      :", variants())
    print("LIVE MODE     :", live())
    print("agent rows    :", len(load_csv("sweep_results.csv")))
    print("greedy rows   :", len(greedy_rows()))
    print("traces        :", len(traces()))
