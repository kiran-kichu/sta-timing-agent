import json
from dotenv import load_dotenv
from anthropic import Anthropic

load_dotenv()
client = Anthropic()
MODEL = "claude-haiku-4-5-20251001"

CLOCK_PERIOD, SETUP_TIME, CLK_Q = 2.2, 0.45, 0.32
LEGAL_DRIVES = [1, 2, 4, 8]

# A fake design. Bigger drive = faster cell, but more area.
DESIGN = {
    "u_alu/_0287_":  {"cell": "nand2", "drive": 1, "base_delay": 0.48, "fanout": 14},
    "u_add/_0132_":  {"cell": "xor2",  "drive": 1, "base_delay": 0.36, "fanout": 6},
    "u_mux/_0455_":  {"cell": "o21ai", "drive": 2, "base_delay": 0.44, "fanout": 3},
    "u_ctrl/_0091_": {"cell": "nor2",  "drive": 2, "base_delay": 0.40, "fanout": 2},
    "u_add/_0210_":  {"cell": "nand2", "drive": 2, "base_delay": 0.36, "fanout": 2},
    "u_alu/_0303_":  {"cell": "inv",   "drive": 4, "base_delay": 1.00, "fanout": 1},
    "u_ctrl/_0118_": {"cell": "and2",  "drive": 2, "base_delay": 0.42, "fanout": 2},
}


# ---------------- THE THREE TOOLS ----------------
def stage_delay(inst):
    return DESIGN[inst]["base_delay"] / DESIGN[inst]["drive"]

def run_sta():
    arrival = CLK_Q + sum(stage_delay(i) for i in DESIGN)
    wns = round(CLOCK_PERIOD - SETUP_TIME - arrival, 3)
    return {"wns_ns": wns, "timing_met": wns >= 0,
            "area": sum(d["drive"] for d in DESIGN.values())}

def get_worst_path():
    total = sum(stage_delay(i) for i in DESIGN)
    stages = [{"instance": i,
               "cell": f"sky130_fd_sc_hd__{DESIGN[i]['cell']}_{DESIGN[i]['drive']}",
               "delay_ns": round(stage_delay(i), 3),
               "delay_share": round(stage_delay(i) / total, 3),
               "drive": DESIGN[i]["drive"],
               "fanout": DESIGN[i]["fanout"]} for i in DESIGN]
    stages.sort(key=lambda s: -s["delay_ns"])
    return {"stages": stages}

def resize_cell(instance, new_drive):
    # VALIDATE IN PYTHON. Never trust the model's arguments.
    if instance not in DESIGN:
        return {"error": f"no such instance. valid: {list(DESIGN)}"}
    if new_drive not in LEGAL_DRIVES:
        return {"error": f"illegal drive {new_drive}. legal: {LEGAL_DRIVES}"}

    before = run_sta()["wns_ns"]
    DESIGN[instance]["drive"] = new_drive
    after = run_sta()["wns_ns"]
    return {"ok": True, "wns_before": before, "wns_after": after,
            "improved_by": round(after - before, 3)}


DISPATCH = {"run_sta": run_sta, "get_worst_path": get_worst_path,
            "resize_cell": resize_cell}

TOOLS = [
    {"name": "run_sta",
     "description": "Run STA. Returns WNS and whether timing is met.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "get_worst_path",
     "description": "Get the stages on the critical path, sorted slowest first.",
     "input_schema": {"type": "object", "properties": {}}},
    {"name": "resize_cell",
     "description": "Change a cell's drive strength. Returns WNS before and after.",
     "input_schema": {"type": "object",
        "properties": {"instance": {"type": "string"},
                       "new_drive": {"type": "integer"}},
        "required": ["instance", "new_drive"]}},
]

SYSTEM = """You are a timing closure engineer. Close setup timing on this design.

Method:
1. Run STA to see where you stand.
2. Look at the worst path. Find the stage with the highest delay_share.
3. Upsize that cell. Prefer cells with low drive and high fanout.
4. Re-run STA. If WNS did not improve, undo your thinking and try another cell.
5. Stop as soon as timing_met is true. Do not over-optimise; area costs money.
"""

messages = [{"role": "user", "content": "Close setup timing on this design."}]

for turn in range(12):
    resp = client.messages.create(model=MODEL, max_tokens=1000,
                                  system=SYSTEM, tools=TOOLS, messages=messages)
    messages.append({"role": "assistant", "content": resp.content})

    if resp.stop_reason != "tool_use":
        print("\n=== AGENT FINISHED ===")
        print(resp.content[0].text)
        break

    results = []
    for block in resp.content:
        if block.type == "text":
            print(f"[thinking] {block.text.strip()[:120]}")
        if block.type == "tool_use":
            out = DISPATCH[block.name](**block.input)
            print(f"  -> {block.name}({block.input}) = {out}")
            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": json.dumps(out)})

    messages.append({"role": "user", "content": results})

print("\nfinal:", run_sta())
