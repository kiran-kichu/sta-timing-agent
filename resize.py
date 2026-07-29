"""Real cell resizing on a real netlist, with real STA measurement."""

import re, os, shutil, subprocess, sys

ORFS = os.path.expanduser("~/OpenROAD-flow-scripts/flow")
LIB  = f"{ORFS}/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
VAR  = f"{ORFS}/results/sky130hd/picorv32/p3.6"
WORK = os.path.expanduser("~/sta-agent/work")

# ---- which cells actually exist (so we never guess a name) ----
LEGAL = set(re.findall(r'cell \("?(sky130_fd_sc_hd__\w+)"?\)', open(LIB).read()))


def swap(text, instance, old_cell, new_cell):
    """Change the cell type of exactly ONE instance."""
    inst = r"(?:\\)?" + re.escape(instance) + r"(?=[\s(])"
    pat = re.compile(r"\b" + re.escape(old_cell) + r"(\s+" + inst + r")")
    return pat.subn(new_cell + r"\1", text)


def run_sta(netlist):
    tcl = f"{WORK}/sta.tcl"
    with open(tcl, "w") as f:
        f.write(f"""read_liberty {LIB}
read_verilog {netlist}
link_design picorv32
read_sdc {VAR}/1_synth.sdc
report_wns
report_tns
""")
    out = subprocess.run(["sta", "-exit", tcl], capture_output=True, text=True).stdout
    wns = tns = None
    for line in out.splitlines():
        if line.startswith("wns max"):
            wns = float(line.split()[-1])
        if line.startswith("tns max"):
            tns = float(line.split()[-1])
    return wns, tns


SWAPS = [
    ("_9108_",                      "sky130_fd_sc_hd__fa_1",     "sky130_fd_sc_hd__fa_4"),
    ("_9258_",                      "sky130_fd_sc_hd__ha_1",     "sky130_fd_sc_hd__ha_4"),
    ("_4712_",                      "sky130_fd_sc_hd__o21ai_0",  "sky130_fd_sc_hd__o21ai_4"),
    ("_4705_",                      "sky130_fd_sc_hd__clkbuf_1", "sky130_fd_sc_hd__buf_4"),
    ("_4704_",                      "sky130_fd_sc_hd__buf_1",    "sky130_fd_sc_hd__buf_4"),
    ("latched_branch$_SDFFE_PN0P_", "sky130_fd_sc_hd__dfxtp_1",  "sky130_fd_sc_hd__dfxtp_4"),
]

os.makedirs(WORK, exist_ok=True)
work_v = f"{WORK}/netlist.v"
shutil.copy(f"{VAR}/1_2_yosys.v", work_v)

wns0, tns0 = run_sta(work_v)
print(f"BASELINE          wns={wns0}  tns={tns0}\n")

for inst, old, new in SWAPS:
    if new not in LEGAL:
        print(f"SKIP {inst}: {new} not in liberty")
        continue

    src = open(work_v).read()
    src, n = swap(src, inst, old, new)
    if n != 1:
        print(f"SKIP {inst}: matched {n} times (expected 1)")
        continue
    open(work_v, "w").write(src)

    wns, tns = run_sta(work_v)
    print(f"{inst:30s} {old.split('__')[1]:12s} -> {new.split('__')[1]:12s} "
          f"wns={wns:+.3f} (d={wns - wns0:+.3f})  tns={tns:+.2f}")
    wns0 = wns

print(f"\nFINAL wns={wns0}  (started at {run_sta(f'{VAR}/1_2_yosys.v')[0]})")
