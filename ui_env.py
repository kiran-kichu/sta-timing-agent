"""What can this deployment actually do? Detect, don't assume."""
import os, csv, glob, json, shutil

def _find_orfs_root():
    candidates = [
        os.path.expanduser("~/OpenROAD-flow-scripts/flow"),
        "/OpenROAD-flow-scripts/flow",
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[0]

ORFS = _find_orfs_root()

def has_sta():
    # Accept either a standalone `sta` binary or the full `openroad` binary
    # (a superset that includes all of OpenSTA's Tcl commands) -- the
    # openroad/orfs-based deployment only ships the latter. ORFS here
    # already points at the correct root (see _find_orfs_root above).
    root = ORFS.rsplit("/flow", 1)[0]  # strip the trailing /flow
    candidates = [
        f"{root}/tools/install/OpenROAD/bin/sta",
        f"{root}/tools/install/OpenROAD/bin/openroad",
    ]
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return True
    return bool(shutil.which("sta")) or bool(shutil.which("openroad"))

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
