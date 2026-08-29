"""The agent, driving real OpenSTA on a real netlist."""

import json, os, sys
from dotenv import load_dotenv
from openai import OpenAI

import sta_parse
from sta_env import Design, AREAS, FAMILIES

load_dotenv()
client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                base_url="https://api.deepseek.com")
MODEL = "deepseek-chat"
if len(sys.argv) > 1 and sys.argv[1] == "--custom":
    VAR_PATH = sys.argv[2]
    TOP = sys.argv[3] if len(sys.argv) > 3 else "picorv32"
    EXTRA_MODE_SDCS = sys.argv[4:]   # zero or more additional SDC file paths
    VARIANT = f"custom:{TOP}"
    D = Design(top=TOP, var_path=VAR_PATH)
else:
    VARIANT = sys.argv[1] if len(sys.argv) > 1 else "p3.6"
    EXTRA_MODE_SDCS = []
    D = Design(VARIANT)
MAX_TURNS = 40
STALE_WNS_LIMIT = 6   # stop early if WNS hasn't improved in this many consecutive moves
AREA_WARN_PCT = 1.0   # log a caution if cumulative area growth exceeds this % of baseline

EXTRA_MODES = {f"mode_{i+2}": path for i, path in enumerate(EXTRA_MODE_SDCS)}

BASE = D.sta()
BASE_HOLD = D.hold()
BASE_MODES = {name: D.setup_under_sdc(name, path) for name, path in EXTRA_MODES.items()}
BEST = (BASE["wns_ns"], BASE["tns_ns"], BASE["area"], open(D.netlist).read())
_area_warned = False


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


def _violating_instance_scores():
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
    return score


def get_candidates():
    """Legacy: cells on violating paths ranked by summed delay, without move
    pairing. Prefer generate_candidate_moves unless you need the raw ranking."""
    score = _violating_instance_scores()
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
                cur_area = AREAS.get(s["cell"])
                opts = []
                for d in drives:
                    if cur is None or d > cur:
                        cname = f"{base}_{d}"
                        carea = AREAS.get(cname)
                        opts.append({
                            "cell": cname,
                            "area": carea,
                            "extra_area_vs_current": round(carea - cur_area, 3)
                                if (carea is not None and cur_area is not None) else None,
                        })
                if "clkbuf" in s["cell"]:
                    for d in FAMILIES["sky130_fd_sc_hd__buf"]:
                        cname = f"sky130_fd_sc_hd__buf_{d}"
                        carea = AREAS.get(cname)
                        opts.append({"cell": cname, "area": carea,
                                     "extra_area_vs_current": round(carea - cur_area, 3)
                                         if (carea is not None and cur_area is not None) else None})
                elif "clkinv" in s["cell"]:
                    for d in FAMILIES["sky130_fd_sc_hd__inv"]:
                        cname = f"sky130_fd_sc_hd__inv_{d}"
                        carea = AREAS.get(cname)
                        opts.append({"cell": cname, "area": carea,
                                     "extra_area_vs_current": round(carea - cur_area, 3)
                                         if (carea is not None and cur_area is not None) else None})
                return {"instance": instance, "current_cell": s["cell"],
                        "current_area": cur_area,
                        "fanout": s["fanout"],
                        "larger_cells_available_smallest_first": opts,
                        "note": ("this is a clock-tree cell sitting on a data "
                                 "path" if "clkbuf" in s["cell"] or
                                 "clkinv" in s["cell"] else None)}
    return {"error": f"{instance} not found on any reported path"}


def generate_candidate_moves(n=8):
    """PREFERRED first move-finding tool. Python assembles complete,
    ready-to-test (instance, new_cell) move proposals in one call, ranked by
    delay contribution per unit of extra area. Static estimate only -- the
    real effect (including the hold safety check) is only known once
    resize_cell actually re-runs OpenSTA."""
    score = _violating_instance_scores()
    moves = []
    for inst, r in score.items():
        cell = r["cell"]
        base = cell.rsplit("_", 1)[0]
        cur = r["drive"]
        cur_area = AREAS.get(cell)
        drives = sorted(d for d in FAMILIES.get(base, []) if cur is None or d > cur)

        step_options = []
        if drives:
            new_cell = f"{base}_{drives[0]}"
            new_area = AREAS.get(new_cell)
            if new_area is not None and cur_area is not None:
                step_options.append((new_cell, round(new_area - cur_area, 3)))
        if "clkbuf" in cell:
            fam = FAMILIES.get("sky130_fd_sc_hd__buf", [])
            if fam:
                new_cell = f"sky130_fd_sc_hd__buf_{fam[0]}"
                new_area = AREAS.get(new_cell)
                if new_area is not None and cur_area is not None:
                    step_options.append((new_cell, round(new_area - cur_area, 3)))
        elif "clkinv" in cell:
            fam = FAMILIES.get("sky130_fd_sc_hd__inv", [])
            if fam:
                new_cell = f"sky130_fd_sc_hd__inv_{fam[0]}"
                new_area = AREAS.get(new_cell)
                if new_area is not None and cur_area is not None:
                    step_options.append((new_cell, round(new_area - cur_area, 3)))

        for new_cell, extra_area in step_options:
            safe_area = extra_area if extra_area and extra_area > 0 else 0.001
            moves.append({
                "instance": inst,
                "current_cell": cell,
                "suggested_new_cell": new_cell,
                "extra_area": extra_area,
                "summed_delay_on_violating_paths": r["summed_delay"],
                "n_violating_paths": r["n_paths"],
                "cost_effectiveness_score": round(r["summed_delay"] / safe_area, 4),
                "note": ("clock-tree cell sitting on a data path"
                         if "clkbuf" in cell or "clkinv" in cell else None),
            })

    moves.sort(key=lambda m: -m["cost_effectiveness_score"])
    return {"candidate_moves": moves[:n],
            "guidance": "Ranked by delay-contribution per unit extra area. "
                        "Static estimate only -- resize_cell verifies the real "
                        "effect, including a hold check at the fast corner."}


def resize_cell(instance, new_cell):
    if new_cell not in AREAS:
        return {"error": f"{new_cell} is not in the liberty file"}

    info = inspect_instance(instance)
    if "error" in info:
        return info
    old_cell = info["current_cell"]

    before = D.sta()
    before_hold = D.hold()
    before_modes = {name: D.setup_under_sdc(name, path)
                    for name, path in EXTRA_MODES.items()}
    D.snapshot()
    n = D._swap(instance, old_cell, new_cell)
    if n != 1:
        D.revert()
        return {"error": f"swap matched {n} instances, expected exactly 1"}
    D.invalidate()
    after = D.sta()
    after_hold = D.hold()
    after_modes = {name: D.setup_under_sdc(name, path)
                   for name, path in EXTRA_MODES.items()}

    delta = round(after["wns_ns"] - before["wns_ns"], 3)
    dtns = round(after["tns_ns"] - before["tns_ns"], 3)

    hold_delta = None
    hold_ok = True
    if after_hold["hold_wns_ns"] is not None and before_hold["hold_wns_ns"] is not None:
        hold_delta = round(after_hold["hold_wns_ns"] - before_hold["hold_wns_ns"], 4)
        hold_ok = hold_delta >= 0   # hold must not get WORSE because of this move

    # A mode "regresses" if this move made either its WNS or TNS strictly
    # worse than it was before the move -- full multi-mode signoff: every
    # mode must be no worse, not just the primary one.
    mode_regressions = []
    for name in EXTRA_MODES:
        b, a = before_modes[name], after_modes[name]
        if (b["wns_ns"] is not None and a["wns_ns"] is not None and a["wns_ns"] < b["wns_ns"]) or \
           (b["tns_ns"] is not None and a["tns_ns"] is not None and a["tns_ns"] < b["tns_ns"]):
            mode_regressions.append(name)
    modes_ok = len(mode_regressions) == 0

    setup_improved = delta > 0 or dtns > 0
    kept = setup_improved and hold_ok and modes_ok
    if not kept:
        D.revert()
        after = before
        after_hold = before_hold
        after_modes = before_modes

    area_delta = round(after["area"] - before["area"], 2) if kept else 0.0
    wns_efficiency = round(delta / area_delta, 4) if (kept and area_delta > 0) else None
    tns_efficiency = round(dtns / area_delta, 4) if (kept and area_delta > 0) else None

    D.history.append({"instance": instance, "from": old_cell, "to": new_cell,
                      "delta_wns": delta, "delta_tns": dtns, "kept": kept,
                      "area_delta": area_delta, "hold_delta": hold_delta,
                      "reverted_for_hold": setup_improved and hold_ok is False,
                      "reverted_for_mode": setup_improved and hold_ok and not modes_ok,
                      "regressed_modes": mode_regressions if mode_regressions else None})

    global BEST, _area_warned
    if kept and after["wns_ns"] > BEST[0]:
        BEST = (after["wns_ns"], after["tns_ns"], after["area"],
                open(D.netlist).read())

    warning = None
    if kept and BASE["area"] > 0:
        cumulative_pct = (after["area"] - BASE["area"]) / BASE["area"] * 100
        if cumulative_pct >= AREA_WARN_PCT and not _area_warned:
            _area_warned = True
            warning = (f"Cumulative area growth has reached "
                       f"{round(cumulative_pct, 2)}% of baseline area. "
                       f"Prefer smaller/cheaper resizes from here if any remain viable.")

    reverted_reason = None
    if not kept:
        if setup_improved and not hold_ok:
            reverted_reason = "hold_would_worsen"
        elif setup_improved and not modes_ok:
            reverted_reason = f"mode_would_regress: {', '.join(mode_regressions)}"
        else:
            reverted_reason = "no_setup_improvement"

    return {"ok": True, "from": old_cell, "to": new_cell,
            "wns_before": before["wns_ns"], "wns_after": after["wns_ns"],
            "delta_wns": delta,
            "tns_before": before["tns_ns"], "tns_after": after["tns_ns"],
            "delta_tns": dtns,
            "hold_wns_before": before_hold["hold_wns_ns"],
            "hold_wns_after": after_hold["hold_wns_ns"],
            "hold_delta": hold_delta,
            "regressed_modes": mode_regressions if mode_regressions else None,
            "area_delta": area_delta,
            "wns_efficiency_per_area": wns_efficiency,
            "tns_efficiency_per_area": tns_efficiency,
            "kept": kept,
            "reverted_reason": reverted_reason,
            "area_budget_warning": warning}


DISPATCH = {"run_sta": run_sta, "get_critical_paths": get_critical_paths,
            "get_candidates": get_candidates, "inspect_instance": inspect_instance,
            "generate_candidate_moves": generate_candidate_moves,
            "resize_cell": resize_cell}

TOOLS = [
 {"type": "function", "function": {
   "name": "run_sta", "description": "Current setup WNS, TNS, area from OpenSTA "
                  "at the typical corner.",
   "parameters": {"type": "object", "properties": {}}}},
 {"type": "function", "function": {
   "name": "generate_candidate_moves",
   "description": "PREFERRED first step after run_sta. Returns a ranked list "
                  "of complete, ready-to-test (instance, new_cell) move "
                  "proposals with area cost and a cost-effectiveness score.",
   "parameters": {"type": "object",
                  "properties": {"n": {"type": "integer"}}}}},
 {"type": "function", "function": {
   "name": "get_critical_paths",
   "description": "Worst violating paths with their slowest stages.",
   "parameters": {"type": "object",
                  "properties": {"n": {"type": "integer"}}}}},
 {"type": "function", "function": {
   "name": "get_candidates",
   "description": "Legacy: cells on violating paths ranked by summed delay, "
                  "without move pairing. Prefer generate_candidate_moves.",
   "parameters": {"type": "object", "properties": {}}}},
 {"type": "function", "function": {
   "name": "inspect_instance",
   "description": "Deeper look at ONE specific instance's available upsize "
                  "options, sorted smallest-first with area cost each.",
   "parameters": {"type": "object",
                  "properties": {"instance": {"type": "string"}},
                  "required": ["instance"]}}},
 {"type": "function", "function": {
   "name": "resize_cell",
   "description": "Swap one instance to a different cell. Re-runs STA at "
                  "BOTH the typical corner (setup) and the fast corner "
                  "(hold), then AUTOMATICALLY keeps the change only if it "
                  "improves setup WNS or TNS AND does not make hold worse. "
                  "Returns setup and hold deltas, area cost, an efficiency "
                  "score, whether the change was kept, and why it was "
                  "reverted if it was not.",
   "parameters": {"type": "object",
                  "properties": {"instance": {"type": "string"},
                                 "new_cell": {"type": "string"}},
                  "required": ["instance", "new_cell"]}}},
]

SYSTEM = f"""You are a timing closure engineer working on a real netlist.
Goal: improve BOTH setup metrics while keeping area growth small AND never
introducing a hold violation. This is a multi-corner, multi-objective problem:
  - WNS (worst negative slack): the single worst setup path, checked at the
    typical corner.
  - TNS (total negative slack): the sum across ALL violating setup paths.
  - Hold: checked automatically at the fast corner after every resize. A
    setup fix that breaks hold is rejected automatically -- you do not need
    to check hold yourself, but be aware it is happening, since it means a
    move that looks good on setup alone can still be reverted.

TNS matters as much as WNS. A fix that improves WNS but leaves TNS large has
only moved the problem.

Method:
1. run_sta to see where you stand.
2. generate_candidate_moves for a ready-to-test, ranked list of complete move
   proposals. Try the top-ranked moves first via resize_cell directly.
   get_candidates and inspect_instance are available for deeper investigation
   of one specific instance if the top suggestions don't help.
3. resize_cell automatically keeps the change only if setup improves AND hold
   does not get worse -- read the "kept" and "reverted_reason" fields to see
   what happened. "reverted_reason": "hold_would_worsen" means the setup fix
   was good but had to be rejected because it would have broken hold; try a
   different, smaller step on that instance, or a different instance, rather
   than repeating the same move.
4. Read "wns_efficiency_per_area" / "tns_efficiency_per_area" to judge cost-
   effectiveness. If you see "area_budget_warning", prefer the smallest
   remaining viable option for further moves.
5. Stop when timing is met, or when you have tried the plausible candidates and
   several consecutive attempts failed. If WNS is still badly negative and the
   critical path has high logic depth, recommend pipelining instead of
   continuing to size cells.
   Note: if WNS has not improved for {STALE_WNS_LIMIT} consecutive resize_cell
   calls, the run stops automatically even if TNS-only improvements are still
   happening.

Be concise. One change at a time.
"""

messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": f"Improve setup timing on this design. "
                                f"Setup baseline: {BASE}. Hold baseline: {BASE_HOLD}."},
]

print(f"=== {VARIANT} baseline: {BASE} | hold baseline: {BASE_HOLD} ===")
if EXTRA_MODES:
    for name, m in BASE_MODES.items():
        print(f"    extra mode '{name}' ({EXTRA_MODES[name]}) baseline: {m}")
print()

stale_wns_moves = 0
plateaued = False

for turn in range(MAX_TURNS):
    llm_turns_used = turn + 1
    r = client.chat.completions.create(model=MODEL, messages=messages, tools=TOOLS)
    msg = r.choices[0].message
    messages.append(msg.model_dump(exclude_none=True))

    if not msg.tool_calls:
        print("\n=== AGENT DONE ===")
        print(msg.content or "")
        break

    for tc in msg.tool_calls:
        args = json.loads(tc.function.arguments or "{}")
        out = DISPATCH[tc.function.name](**args)
        brief = {k: out[k] for k in ("wns_after", "delta_wns", "delta_tns",
                                     "kept", "hold_delta", "reverted_reason",
                                     "wns_efficiency_per_area",
                                     "area_budget_warning", "error")
                 if k in out and out[k] is not None}
        print(f"      -> {tc.function.name}({args}) {brief or 'ok'}")
        results_content = json.dumps(out)
        messages.append({"role": "tool", "tool_call_id": tc.id,
                         "content": results_content})

        if tc.function.name == "resize_cell" and "delta_wns" in out:
            if out["delta_wns"] > 0:
                stale_wns_moves = 0
            else:
                stale_wns_moves += 1

    if msg.content and msg.content.strip():
        print(f"[{turn}] {msg.content.strip()[:150]}")

    if stale_wns_moves >= STALE_WNS_LIMIT:
        plateaued = True
        print(f"\n=== STOPPED: WNS plateaued for {STALE_WNS_LIMIT} "
              f"consecutive moves ===")
        break

# Restore the best netlist found -- never report where the loop stopped.
open(D.netlist, "w").write(BEST[3])
D.invalidate()
final = D.sta()
final_hold = D.hold()
print(f"\nBASELINE      : {BASE}")
print(f"BASELINE HOLD : {BASE_HOLD}")
print(f"FINAL         : {final}")
print(f"FINAL HOLD    : {final_hold}")
print(f"ΔWNS     : {round(final['wns_ns'] - BASE['wns_ns'], 3)}")
print(f"Δarea    : {round(final['area'] - BASE['area'], 2)}")
if BASE["area"] > 0:
    print(f"Area growth: {round((final['area'] - BASE['area']) / BASE['area'] * 100, 3)}% of baseline")
if plateaued:
    print(f"Stopped early: WNS plateaued for {STALE_WNS_LIMIT} consecutive moves.")
print(f"LLM API calls (turns) used: {llm_turns_used}")
hold_reverts = sum(1 for h in D.history if h.get("reverted_for_hold"))
print(f"Moves rejected specifically for breaking hold: {hold_reverts}")
import json as _json
print("RESULT_JSON " + _json.dumps({
    "variant": VARIANT,
    "wns_base": BASE["wns_ns"], "tns_base": BASE["tns_ns"],
    "hold_wns_base": BASE_HOLD["hold_wns_ns"],
    "wns_final": final["wns_ns"], "tns_final": final["tns_ns"],
    "hold_wns_final": final_hold["hold_wns_ns"],
    "area_delta": round(final["area"] - BASE["area"], 2),
    "area_growth_pct": round((final["area"] - BASE["area"]) / BASE["area"] * 100, 3)
                       if BASE["area"] > 0 else None,
    "moves_attempted": len(D.history),
    "moves_kept": sum(1 for h in D.history if h["kept"]),
    "moves_reverted": sum(1 for h in D.history if not h["kept"]),
    "moves_reverted_for_hold": hold_reverts,
    "harmful_kept": sum(1 for h in D.history
                        if h["kept"] and h["delta_wns"] < 0),
    "plateaued": plateaued,
    "llm_turns_used": llm_turns_used,
    "extra_modes": list(EXTRA_MODES.keys()),
    "hold_wns_before_run": BASE_HOLD["hold_wns_ns"],
    "hold_wns_after_run": final_hold["hold_wns_ns"],
    "history": D.history,
}))
print(f"\nmoves attempted: {len(D.history)}")
for h in D.history:
    print("   ", h)
