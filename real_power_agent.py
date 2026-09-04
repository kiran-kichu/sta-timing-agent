"""The Power/IR-drop agent, driving real OpenROAD power-grid analysis and
mutation on a real, routed design. Mirrors real_agent_tns.py's philosophy:
the LLM proposes, Python applies and verifies every move."""

import json, os, sys
from dotenv import load_dotenv
from openai import OpenAI

from power_env import PowerDesign

load_dotenv()
client = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"],
                base_url="https://api.deepseek.com")
MODEL = "deepseek-chat"

if len(sys.argv) < 3:
    print("Usage: real_power_agent.py <def_path> <spef_path> [design_name]")
    sys.exit(1)

DEF_PATH = sys.argv[1]
SPEF_PATH = sys.argv[2]
DESIGN_NAME = sys.argv[3] if len(sys.argv) > 3 else "picorv32"

D = PowerDesign(def_path=DEF_PATH, spef_path=SPEF_PATH)
MAX_TURNS = 15   # power moves are expensive (esp. regenerate_pdn) -- keep this tight

BASE = D.ir_drop()
history = []
_exp_counter = 0

def _worst_zone(ir):
    """Zone of the WORSE of VDD/VSS worst-case pct_drop."""
    pct = max(ir["VDD"]["pct_drop"], ir["VSS"]["pct_drop"])
    if pct > 5: return "must_fix"
    if pct >= 2: return "prioritize"
    if pct >= 1: return "opportunity"
    return "stop"


# ------------------------- TOOLS -------------------------

def measure_ir_drop():
    return D.ir_drop()


def try_decap(target_cap):
    """Insert decap cells targeting roughly target_cap of added capacitance.
    KNOWN INERT for this analysis, by physical principle, not just
    empirically: analyze_power_grid is a static DC analysis, and a
    capacitor is invisible to DC (blocks DC current entirely) -- so decap
    cannot affect ANY static IR-drop metric, average or worst-case.
    This tool isolates decap's true marginal effect (separate from the
    remove_fillers precondition it requires, which independently changes
    the measurement) and will correctly report ~zero effect and revert."""
    D.snapshot()
    post_fillers, post_decap, decap_def = D.add_decap_isolated(target_cap=target_cap)

    marginal = round(post_decap["VDD"]["avg_drop_v"] - post_fillers["VDD"]["avg_drop_v"], 6)
    kept = marginal < -1e-5   # only "keep" if decap shows a REAL marginal improvement
    if kept:
        D.accept_def(decap_def)
    else:
        D.revert()

    result = {"move": "try_decap", "target_cap": target_cap,
              "avg_drop_after_fillers_only": post_fillers["VDD"]["avg_drop_v"],
              "avg_drop_after_fillers_plus_decap": post_decap["VDD"]["avg_drop_v"],
              "decap_marginal_effect": marginal,
              "kept": kept,
              "note": ("decap contributed no measurable effect beyond remove_fillers "
                       "-- expected, since static IR-drop analysis cannot see "
                       "capacitance (a capacitor is invisible to DC)" if not kept
                       else "decap showed a real marginal effect (unexpected -- verify)")}
    history.append(result)
    return result


def try_via_repair():
    """Add redundant vias to weak/missing connections in the EXISTING PDN.
    Cheap (seconds). Fixes via-connectivity issues -- if the PDN already has
    sufficient vias (common in a well-generated design), this is correctly
    a no-op."""
    D.snapshot()
    before = D.ir_drop()
    out, new_def = D.repair_vias(net="VDD")
    D.accept_def(new_def)
    after = D.ir_drop()

    before_worst = before["VDD"]["worst_drop_v"]
    after_worst = after["VDD"]["worst_drop_v"]
    kept = after_worst < before_worst
    if not kept:
        D.revert()
        after = before

    result = {"move": "try_via_repair",
              "worst_drop_before": before_worst,
              "worst_drop_after": after_worst if kept else before_worst,
              "kept": kept,
              "no_op": "No vias removed" in out}
    history.append(result)
    return result


def try_wider_straps(strap_width_um):
    """Regenerate met4/met5 PDN straps at strap_width_um (baseline is
    typically 1.6um). EXPENSIVE: re-runs floorplan-through-routing, several
    MINUTES per call. This is the real structural fix for worst-case
    resistive IR drop. Cost-aware: each call reports an efficiency score
    (mV improvement per second spent) so you can judge whether a further
    candidate width would be worth its cost before calling it."""
    global _exp_counter
    before = D.ir_drop()
    result_regen = D.regenerate_pdn(strap_width_um=strap_width_um, design=DESIGN_NAME)
    if not result_regen.get("ok"):
        return {"move": "try_wider_straps", "error": result_regen.get("error"),
                "stderr_tail": result_regen.get("stderr_tail")}

    # Measure the candidate WITHOUT committing it -- point at the regen's own
    # output paths temporarily, never touching D's actual working files until
    # the keep/revert decision is made. This is the only safe way to "revert":
    # never mutate the working state for a candidate that might be rejected.
    saved_def, saved_spef = D.def_path, D.spef_path
    D.def_path, D.spef_path = result_regen["def_path"], result_regen["spef_path"]
    D.invalidate()
    after = D.ir_drop()
    D.def_path, D.spef_path = saved_def, saved_spef
    D.invalidate()

    before_worst = before["VDD"]["worst_drop_v"]
    after_worst = after["VDD"]["worst_drop_v"]
    kept = after_worst < before_worst
    if kept:
        D.accept_def(result_regen["def_path"], result_regen["spef_path"])
    else:
        after = before   # D's working files were never touched -- nothing to revert

    elapsed = result_regen["elapsed_s"]
    improvement_mv = round((before_worst - (after_worst if kept else before_worst)) * 1000, 3)
    efficiency = round(improvement_mv / elapsed, 5) if elapsed > 0 else None

    _exp_counter += 1
    result = {"experiment_id": f"pwr_exp_{_exp_counter:03d}",
              "move": "try_wider_straps", "strap_width_um": strap_width_um,
              "elapsed_s": elapsed,
              "worst_drop_before": before_worst,
              "worst_drop_after": after_worst if kept else before_worst,
              "improvement_mv": improvement_mv,
              "efficiency_mv_per_s": efficiency,
              "resulting_zone": _worst_zone(after if kept else before),
              "kept": kept}
    history.append(result)
    return result


DISPATCH = {"measure_ir_drop": measure_ir_drop, "try_decap": try_decap,
            "try_via_repair": try_via_repair, "try_wider_straps": try_wider_straps}

TOOLS = [
 {"type": "function", "function": {
   "name": "measure_ir_drop",
   "description": "Current worst-case and average IR drop on VDD/VSS.",
   "parameters": {"type": "object", "properties": {}}}},
 {"type": "function", "function": {
   "name": "try_decap",
   "description": "Insert decap cells (cheap, seconds). ONLY improves "
                  "average/dynamic drop, never worst-case. Auto keeps/"
                  "reverts based on average drop.",
   "parameters": {"type": "object",
                  "properties": {"target_cap": {"type": "number"}},
                  "required": ["target_cap"]}}},
 {"type": "function", "function": {
   "name": "try_via_repair",
   "description": "Repair weak/missing PDN vias (cheap, seconds). Correctly "
                  "a no-op if the PDN already has sufficient via redundancy. "
                  "Auto keeps/reverts based on worst-case drop.",
   "parameters": {"type": "object", "properties": {}}}},
 {"type": "function", "function": {
   "name": "try_wider_straps",
   "description": "Regenerate PDN straps at a new width (EXPENSIVE: several "
                  "minutes, re-runs floorplan through routing). The real "
                  "structural fix for worst-case resistive IR drop. Use "
                  "deliberately -- pick one considered width, do not try "
                  "many values in a row.",
   "parameters": {"type": "object",
                  "properties": {"strap_width_um": {"type": "number"}},
                  "required": ["strap_width_um"]}}},
]

SYSTEM = """You are a power-delivery signoff engineer working on a real,
routed design. Your job: reduce IR drop (voltage loss across the power grid).

Two distinct objectives, and each tool targets exactly one of them -- never
assume a tool helps an objective it doesn't:

| Tool             | Targets            | Cost              |
|------------------|---------------------|-------------------|
| try_decap        | KNOWN INERT for this analysis (see below) | seconds |
| try_via_repair   | via resistance/redundancy | seconds     |
| try_wider_straps | WORST-CASE resistive drop (the real structural fix) | several MINUTES |

try_decap is included for completeness but is EXPECTED to show zero
effect: this analysis is static DC IR drop, and a capacitor is invisible
to DC by basic circuit theory -- decoupling capacitance cannot influence
a static metric at all, average or worst-case. Call it once to confirm
this on the current design, note the expected no-op result, and do not
spend further turns on it.
try_via_repair is often correctly a no-op on a well-generated PDN; that is
a valid result, not a failure.

Reporting requirement: when you state a drop value, always give the
absolute number (in mV) alongside the percentage. Do not describe a
result as "well under" or "acceptable" using the percentage alone --
33.7 mV and 1.87% are the same fact, but only the mV number tells you
whether a large absolute improvement is actually available. Check
whether a structural fix would meaningfully help BEFORE characterizing
the design as fine.

Worst-case pct_drop determines your action, using these zones -- apply
whichever zone the WORSE of the two nets (VDD/VSS) falls into:

| Worst-case pct_drop | Required behavior |
|----------------------|--------------------|
| > 5%                 | MUST fix -- call try_wider_straps |
| 2-5%                 | STRONGLY prioritize try_wider_straps |
| 1-2%                 | Optimization opportunity -- try_wider_straps is worth a look; a 4-minute call for a possible 30-50%+ reduction is a reasonable trade even though not mandatory |
| < 1%                 | Stop the structural-fix line of investigation unless an improvement would be extremely cheap (it isn't -- try_wider_straps always costs several minutes, so at this zone do not call it) |

Method:
1. measure_ir_drop to see the baseline (note both mV and % for worst-case).
2. Try try_decap once, for completeness -- expect and report it as inert
   (see above); do not spend more than one call on it.
3. Try try_via_repair (cheap) -- worth checking even if you expect a no-op.
4. Re-check worst-case pct_drop for the worse of the two nets. Apply the
   zone table above.
5. If the zone calls for or permits try_wider_straps, make your first
   call with a width you believe is sufficient (e.g. double the likely
   baseline of ~1.6um).
6. Read the result's efficiency_mv_per_s and resulting_zone. If
   resulting_zone is already "stop" (<1%), you are done -- do NOT try a
   second, larger width just to see if it does even better; that is
   spending real minutes for no zone-relevant benefit.
   If resulting_zone is still "opportunity" or worse after your first
   call, a second, larger candidate width MAY be justified -- but only if
   the first call's efficiency_mv_per_s suggests real returns remain
   (a second call that only marginally improves efficiency is not worth
   another several minutes). Never try more than 2 strap-width candidates
   total in one run.
7. Stop once worst-case and average drop are both in the <1% zone
   ("stop"), or once you've applied the zone table's guidance for the
   current zone and tried at most 2 candidate widths.

Be concise. State your reasoning briefly before each tool call.
"""

messages = [
    {"role": "system", "content": SYSTEM},
    {"role": "user", "content": f"Reduce IR drop on this design. Baseline: {BASE}"},
]

print(f"=== baseline: {BASE} ===\n")

for turn in range(MAX_TURNS):
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
        print(f"      -> {tc.function.name}({args}) {out}")
        messages.append({"role": "tool", "tool_call_id": tc.id,
                         "content": json.dumps(out)})

    if msg.content and msg.content.strip():
        print(f"[{turn}] {msg.content.strip()[:200]}")

final = D.ir_drop()
print(f"\nBASELINE : {BASE}")
print(f"FINAL    : {final}")
print(f"\nmoves attempted: {len(history)}")
for h in history:
    print("   ", h)

import json as _json
print("RESULT_JSON " + _json.dumps({
    "design": DESIGN_NAME,
    "baseline": BASE,
    "final": final,
    "history": history,
}))
