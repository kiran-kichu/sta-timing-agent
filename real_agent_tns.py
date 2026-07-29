"""The agent, driving real OpenSTA on a real netlist."""

import json, sys
from dotenv import load_dotenv
from anthropic import Anthropic

import sta_parse
from sta_env import Design, AREAS, FAMILIES

load_dotenv()
client = Anthropic()
MODEL = "claude-haiku-4-5-20251001"
VARIANT = sys.argv[1] if len(sys.argv) > 1 else "p3.6"
MAX_TURNS = 40

D = Design(VARIANT)
BASE = D.sta()
BEST = (BASE["wns_ns"], BASE["tns_ns"], BASE["area"], open(D.netlist).read())


# ------------------------- TOOLS -------------------------

def run_sta():
    return D.sta()


def get_critical_paths(n=5):
    paths = sta_parse.parse(D.report(8))
    out = []
    for p in sorted(paths, key=lambda p: p["slack"])[:n]:
        out.append({"endpoint": p["endpoint"], "slack_ns": p["slack"],
                    "logic_depth": len(p["stages"]),
                    "hot_stages": [{k: s[k] for k in
                                    ("instance", "cell", "total_delay",
                                     "delay_share", "fanout", "drive")}
                                   for s in p["stages"][:4]]})
    return {"paths": out}


def get_candidates():
    """Cells on violating paths, ranked by summed delay contribution.

    NOTE: this is a CANDIDATE LIST, not a recommendation. Measured evidence on
    this design shows the top-ranked candidate can degrade WNS. Always verify.
    """
    paths = [p for p in sta_parse.parse(D.report(8)) if p["slack"] < 0]
    score = {}
    for p in paths:
        for s in p["stages"]:
            r = score.setdefault(s["instance"],
                                 {"instance": s["instance"], "cell": s["cell"],
                                  "n_paths": 0, "summed_delay": 0.0,
                                  "drive": s["drive"], "max_fanout": 0})
            r["n_paths"] += 1
            r["summed_delay"] = round(r["summed_delay"] + s["total_delay"], 3)
            r["max_fanout"] = max(r["max_fanout"], s["fanout"] or 0)
    ranked = sorted(score.values(), key=lambda r: -r["summed_delay"])[:10]
    return {"candidates": ranked}


def inspect_instance(instance):
    paths = sta_parse.parse(D.report(8))
    for p in paths:
        for s in p["stages"]:
            if s["instance"] == instance:
                base = s["cell"].rsplit("_", 1)[0]
                drives = FAMILIES.get(base, [])
                cur = s["drive"]
                opts = [f"{base}_{d}" for d in drives if cur is None or d > cur]
                # A clkbuf/clkinv on a DATA path is the wrong cell type, not
                # merely the wrong size. Offer the ordinary equivalent too,
                # otherwise the agent can only ever upsize within the family.
                if "clkbuf" in s["cell"]:
                    opts += [f"sky130_fd_sc_hd__buf_{d}"
                             for d in FAMILIES["sky130_fd_sc_hd__buf"]]
                elif "clkinv" in s["cell"]:
                    opts += [f"sky130_fd_sc_hd__inv_{d}"
                             for d in FAMILIES["sky130_fd_sc_hd__inv"]]
                return {"instance": instance, "current_cell": s["cell"],
                        "current_area": AREAS.get(s["cell"]),
                        "fanout": s["fanout"],
                        "larger_cells_available": opts,
                        "note": ("this is a clock-tree cell sitting on a data "
                                 "path" if "clkbuf" in s["cell"] or
                                 "clkinv" in s["cell"] else None)}
    return {"error": f"{instance} not found on any reported path"}


def resize_cell(instance, new_cell):
    if new_cell not in AREAS:
        return {"error": f"{new_cell} is not in the liberty file"}

    info = inspect_instance(instance)
    if "error" in info:
        return info
    old_cell = info["current_cell"]

    before = D.sta()
    D.snapshot()
    n = D._swap(instance, old_cell, new_cell)
    if n != 1:
        D.revert()
        return {"error": f"swap matched {n} instances, expected exactly 1"}
    D.invalidate()
    after = D.sta()

    delta = round(after["wns_ns"] - before["wns_ns"], 3)
    dtns = round(after["tns_ns"] - before["tns_ns"], 3)
    D.history.append({"instance": instance, "from": old_cell, "to": new_cell,
                      "delta_wns": delta, "delta_tns": dtns, "kept": None})
    # Snapshot the netlist whenever it is the best WNS seen so far.
    global BEST
    if after["wns_ns"] > BEST[0]:
        BEST = (after["wns_ns"], after["tns_ns"], after["area"],
                open(D.netlist).read())
    return {"ok": True, "from": old_cell, "to": new_cell,
            "wns_before": before["wns_ns"], "wns_after": after["wns_ns"],
            "delta_wns": delta,
            "tns_before": before["tns_ns"], "tns_after": after["tns_ns"],
            "delta_tns": dtns,
            "area_delta": round(after["area"] - before["area"], 2),
            "improved": delta > 0}


def revert_last():
    ok = D.revert()
    if ok and D.history:
        D.history[-1]["kept"] = False
    return {"reverted": ok, "wns_now": D.sta()["wns_ns"]}


DISPATCH = {"run_sta": run_sta, "get_critical_paths": get_critical_paths,
            "get_candidates": get_candidates, "inspect_instance": inspect_instance,
            "resize_cell": resize_cell, "revert_last": revert_last}

TOOLS = [
 {"name": "run_sta", "description": "Current WNS, TNS, area from OpenSTA.",
  "input_schema": {"type": "object", "properties": {}}},
 {"name": "get_critical_paths",
  "description": "Worst violating paths with their slowest stages.",
  "input_schema": {"type": "object",
                   "properties": {"n": {"type": "integer"}}}},
 {"name": "get_candidates",
  "description": "Cells on violating paths ranked by summed delay. A candidate "
                 "list only -- the top entry is not guaranteed to help.",
  "input_schema": {"type": "object", "properties": {}}},
 {"name": "inspect_instance",
  "description": "Current cell, area, fanout, and which larger cells exist.",
  "input_schema": {"type": "object",
                   "properties": {"instance": {"type": "string"}},
                   "required": ["instance"]}},
 {"name": "resize_cell",
  "description": "Swap one instance to a different cell. Re-runs STA and "
                 "returns WNS before/after and area cost.",
  "input_schema": {"type": "object",
                   "properties": {"instance": {"type": "string"},
                                  "new_cell": {"type": "string"}},
                   "required": ["instance", "new_cell"]}},
 {"name": "revert_last",
  "description": "Undo the most recent resize.",
  "input_schema": {"type": "object", "properties": {}}},
]

SYSTEM = """You are a timing closure engineer working on a real netlist.
Goal: improve BOTH metrics while keeping area growth small.
  - WNS (worst negative slack): the single worst path.
  - TNS (total negative slack): the sum across ALL violating paths.

TNS matters as much as WNS. A fix that improves WNS but leaves TNS large has
only moved the problem. After the worst path stops improving, keep going and
attack the remaining violating paths to bring TNS down.

Method:
1. run_sta to see where you stand.
2. get_candidates and get_critical_paths to find things worth changing.
   The candidate ranking is a SUGGESTION LIST, not an answer. Do not assume the
   top entry is the best fix.
3. inspect_instance before every resize, so you use a cell that actually exists.
4. resize_cell, then READ the returned delta_wns.
   If BOTH delta_wns <= 0 and TNS did not improve, call revert_last.
   A change that improves TNS while leaving WNS flat is still worth keeping. A change that does not help
   costs area for nothing.
5. Stop when timing is met, or when you have tried the plausible candidates and
   several consecutive attempts failed. If WNS is still badly negative and the
   critical path has high logic depth, say so plainly and recommend pipelining
   instead of continuing to size cells.

Be concise. One change at a time.
"""

messages = [{"role": "user",
             "content": f"Improve setup timing on this design. Baseline: {BASE}"}]

print(f"=== {VARIANT} baseline: {BASE} ===\n")

for turn in range(MAX_TURNS):
    r = client.messages.create(model=MODEL, max_tokens=1200,
                               system=SYSTEM, tools=TOOLS, messages=messages)
    messages.append({"role": "assistant", "content": r.content})

    if r.stop_reason != "tool_use":
        print("\n=== AGENT DONE ===")
        print(r.content[0].text)
        break

    results = []
    for b in r.content:
        if b.type == "text" and b.text.strip():
            print(f"[{turn}] {b.text.strip()[:150]}")
        if b.type == "tool_use":
            out = DISPATCH[b.name](**b.input)
            brief = {k: out[k] for k in ("wns_after", "delta_wns", "improved",
                                         "wns_ns", "reverted", "error")
                     if k in out}
            print(f"      -> {b.name}({b.input}) {brief or 'ok'}")
            results.append({"type": "tool_result", "tool_use_id": b.id,
                            "content": json.dumps(out)})
    messages.append({"role": "user", "content": results})

# Restore the best netlist found -- never report where the loop stopped.
open(D.netlist, "w").write(BEST[3])
D.invalidate()
final = D.sta()
print(f"\nBASELINE : {BASE}")
print(f"FINAL    : {final}")
print(f"ΔWNS     : {round(final['wns_ns'] - BASE['wns_ns'], 3)}")
print(f"Δarea    : {round(final['area'] - BASE['area'], 2)}")
import json as _json
print("RESULT_JSON " + _json.dumps({
    "variant": VARIANT,
    "wns_base": BASE["wns_ns"], "tns_base": BASE["tns_ns"],
    "wns_final": final["wns_ns"], "tns_final": final["tns_ns"],
    "area_delta": round(final["area"] - BASE["area"], 2),
    "moves_attempted": len(D.history),
    "moves_kept": sum(1 for h in D.history if h["kept"] is None),
    "moves_reverted": sum(1 for h in D.history if h["kept"] is False),
    "harmful_kept": sum(1 for h in D.history
                        if h["kept"] is None and h["delta_wns"] < 0),
}))
print(f"\nmoves attempted: {len(D.history)}")
for h in D.history:
    print("   ", h)
