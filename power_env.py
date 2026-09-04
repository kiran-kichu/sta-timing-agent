"""Real IR-drop analysis environment: the tools a Power/PDN agent is allowed
to use. No LLM code in this file -- ordinary Python driving OpenROAD's PSM
(power grid analysis) module. Independently testable, same philosophy as
sta_env.py.
"""

import os, re, subprocess, shutil

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
LIB = f"{ORFS}/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"
TECH_LEF = f"{ORFS}/platforms/sky130hd/lef/sky130_fd_sc_hd.tlef"
MERGED_LEF = f"{ORFS}/platforms/sky130hd/lef/sky130_fd_sc_hd_merged.lef"

WORK = os.path.expanduser("~/sta-agent/power_work")

# Official SkyWater sky130 via resistances (ohms), sourced from the PDK's own
# documented parasitic extraction resistance table. The sky130 tech LEF does
# NOT include these -- without setting them explicitly, OpenROAD's power grid
# analysis reports zero via resistance and refuses to run.
VIA_RESISTANCE = {
    "mcon": 152.0,
    "via": 4.5,
    "via2": 3.41,
    "via3": 3.41,
    "via4": 0.38,
}

# Decap cells and their capacitance-per-unit-length values, taken directly
# from OpenROAD's own official regression test (psm/test/insert_decap1.tcl)
# for these exact sky130hd decap cells -- proven, tested values, not derived.
DECAP_CELLS = {
    "sky130_fd_sc_hd__decap_3": 0.93,
    "sky130_fd_sc_hd__decap_4": 0.124,
    "sky130_fd_sc_hd__decap_6": 0.186,
    "sky130_fd_sc_hd__decap_8": 0.248,
    "sky130_fd_sc_hd__decap_12": 0.362,
}

VDD_VOLTAGE = 1.80
GND_VOLTAGE = 0.00

def _find_openroad():
    cand = [
        os.path.expanduser("~/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad"),
        "/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad",
        "/root/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad",
        shutil.which("openroad"),
    ]
    for c in cand:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError("Could not find the `openroad` binary. Tried: " + str(cand))

OPENROAD_BIN = _find_openroad()

WORST_DROP_RE = re.compile(r"^Worstcase IR drop:\s*([\d.eE+-]+)\s*V")
PCT_DROP_RE = re.compile(r"^Percentage drop\s*:\s*([\d.eE+-]+)\s*%")
AVG_DROP_RE = re.compile(r"^Average IR drop\s*:\s*([\d.eE+-]+)\s*V")


class PowerDesign:
    def __init__(self, def_path, spef_path, top=None):
        os.makedirs(WORK, exist_ok=True)
        self.def_path = f"{WORK}/design.def"
        shutil.copy(def_path, self.def_path)
        self.spef_path = spef_path  # SPEF is read-only input, never mutated
        self.snapshots = []
        self.history = []
        self._cache = None

    # ---------- measurement ----------

    def _via_rc_lines(self):
        return "\n".join(
            f"set_layer_rc -via {name} -resistance {r}"
            for name, r in VIA_RESISTANCE.items()
        )

    def _run(self, extra="", write_def_after=None):
        tcl = f"{WORK}/power.tcl"
        body = (f"read_liberty {LIB}\n"
                f"read_lef {TECH_LEF}\n"
                f"read_lef {MERGED_LEF}\n"
                f"read_def {self.def_path}\n"
                f"read_spef {self.spef_path}\n"
                f"{self._via_rc_lines()}\n"
                f"set_pdnsim_net_voltage -net VDD -voltage {VDD_VOLTAGE}\n"
                f"set_pdnsim_net_voltage -net VSS -voltage {GND_VOLTAGE}\n"
                f"{extra}\n")
        if write_def_after:
            body += f"write_def {write_def_after}\n"
        with open(tcl, "w") as f:
            f.write(body)
        return subprocess.run([OPENROAD_BIN, "-exit", tcl],
                              capture_output=True, text=True).stdout

    def _parse_ir_drop(self, out):
        results = {}
        current_net = None
        for line in out.splitlines():
            if line.strip().startswith("Net"):
                current_net = line.split(":", 1)[1].strip()
                results[current_net] = {}
            if current_net:
                if (m := WORST_DROP_RE.match(line.strip())):
                    results[current_net]["worst_drop_v"] = float(m.group(1))
                if (m := AVG_DROP_RE.match(line.strip())):
                    results[current_net]["avg_drop_v"] = float(m.group(1))
                if (m := PCT_DROP_RE.match(line.strip())):
                    results[current_net]["pct_drop"] = float(m.group(1))
        return results

    def ir_drop(self):
        """Worst-case IR drop (V and %) on VDD and VSS. Cached until
        invalidate() is called (i.e. after a mutation)."""
        if self._cache is None:
            out = self._run(
                f"analyze_power_grid -net VDD -voltage_file {WORK}/vdd.rpt\n"
                f"analyze_power_grid -net VSS -voltage_file {WORK}/vss.rpt\n"
            )
            self._cache = self._parse_ir_drop(out)
        return self._cache

    def invalidate(self):
        self._cache = None

    # ---------- mutation ----------

    def add_decap_isolated(self, target_cap):
        """Isolates decap's TRUE marginal effect from the remove_fillers
        precondition it requires. analyze_power_grid is a static DC
        analysis -- a capacitor is invisible to DC, so decap is expected
        to show ~zero marginal effect here on principle, not just
        empirically. Returns (post_fillers_ir_drop, post_decap_ir_drop,
        post_decap_def_path)."""
        cells_arg = " ".join(f'"{name}" {val}' for name, val in DECAP_CELLS.items())
        fillers_def = f"{WORK}/isolated_fillers.def"
        decap_def = f"{WORK}/isolated_decap.def"

        out1 = self._run("remove_fillers\n", write_def_after=fillers_def)
        old_def = self.def_path
        self.def_path = fillers_def
        self.invalidate()
        post_fillers = self.ir_drop()

        out2 = self._run(
            f"analyze_power_grid -net VDD -voltage_file {WORK}/iso_vdd.rpt\n"
            f"insert_decap -target_cap {target_cap} -cells {{{cells_arg}}}\n",
            write_def_after=decap_def)
        self.def_path = decap_def
        self.invalidate()
        post_decap = self.ir_drop()

        self.def_path = old_def
        self.invalidate()
        return post_fillers, post_decap, decap_def

    def add_decap(self, target_cap):
        """Ask OpenROAD to automatically place decap cells (from the proven
        sky130hd decap library) totalling roughly target_cap of added
        capacitance-per-length on the VDD net. OpenROAD picks the actual
        locations -- this tool only controls how much to add.

        remove_fillers is required first: a finished, routed design already
        has filler cells occupying the empty space decap cells need."""
        cells_arg = " ".join(f'"{name}" {val}' for name, val in DECAP_CELLS.items())
        out_def = f"{WORK}/after_decap.def"
        out = self._run(
            f"analyze_power_grid -net VDD -voltage_file {WORK}/pre_decap_vdd.rpt\n"
            f"remove_fillers\n"
            f"insert_decap -target_cap {target_cap} -cells {{{cells_arg}}}\n"
            f"check_placement\n",
            write_def_after=out_def,
        )
        return out, out_def

    def snapshot(self):
        self.snapshots.append(self.def_path + ".snap")
        shutil.copy(self.def_path, self.snapshots[-1])

    def revert(self):
        if not self.snapshots:
            return False
        last = self.snapshots.pop()
        shutil.copy(last, self.def_path)
        os.remove(last)
        self.invalidate()
        return True

    def repair_vias(self, net="VDD"):
        """Add redundant/repair vias to weak or missing via connections on
        the EXISTING power grid, without regenerating it from scratch. A
        structural fix, unlike decap (which only helps average/dynamic
        drop, not the resistive worst-case bottleneck)."""
        out_def = f"{WORK}/after_repair_vias.def"
        out = self._run(
            f"repair_pdn_vias -net {net}\n",
            write_def_after=out_def,
        )
        return out, out_def

    def accept_def(self, new_def_path, new_spef_path=None):
        """Replace the working DEF with a mutated one (e.g. after a
        successful add_decap or regenerate_pdn), and drop the cached
        measurement. If new_spef_path is given (PDN regeneration produces a
        genuinely different SPEF, unlike decap/via-repair which don't change
        parasitics), update that too."""
        shutil.copy(new_def_path, self.def_path)
        if new_spef_path:
            new_spef_local = f"{WORK}/design.spef"
            shutil.copy(new_spef_path, new_spef_local)
            self.spef_path = new_spef_local
        self.invalidate()

    # ---------- heavy mutation: PDN regeneration ----------
    # Unlike add_decap/repair_vias (sub-second, in-memory), this re-runs the
    # real ORFS flow from floorplan through routing -- ~4 minutes measured on
    # picorv32. This is a structural fix (confirmed to actually move worst-
    # case IR drop, unlike decap/via repair) but is expensive: an agent should
    # treat this as a single deliberate move, not something to retry rapidly.

    PDN_TCL = f"{ORFS}/platforms/sky130hd/pdn.tcl"

    def regenerate_pdn(self, strap_width_um, design="picorv32", base_variant="base",
                       flow_dir=None, test_variant_name="pdn_regen_test"):
        self._pre_regen_def = self.def_path
        self._pre_regen_spef = self.spef_path
        """Re-run floorplan-through-routing with met4/met5 PDN strap width set
        to strap_width_um (originally 1.6). Reuses the given base_variant's
        synthesis-stage outputs to save time. Returns (elapsed_seconds,
        new_def_path, new_spef_path) on success."""
        import time as _time
        flow_dir = flow_dir or ORFS
        results_base = f"{flow_dir}/results/sky130hd/{design}/{base_variant}"
        results_test = f"{flow_dir}/results/sky130hd/{design}/{test_variant_name}"

        backup = self.PDN_TCL + ".autobak"
        shutil.copy(self.PDN_TCL, backup)
        try:
            pdn_content = open(self.PDN_TCL).read()
            pdn_content = re.sub(
                r"-layer \{met4\} -width \{[\d.]+\}",
                f"-layer {{met4}} -width {{{strap_width_um:.3f}}}", pdn_content)
            pdn_content = re.sub(
                r"-layer \{met5\} -width \{[\d.]+\}",
                f"-layer {{met5}} -width {{{strap_width_um:.3f}}}", pdn_content)
            open(self.PDN_TCL, "w").write(pdn_content)

            os.makedirs(results_test, exist_ok=True)
            for f in os.listdir(results_base):
                if f.startswith("1_") or f in ("clock_period.txt", "mem.json"):
                    shutil.copy(f"{results_base}/{f}", f"{results_test}/{f}")

            import threading
            start = _time.time()
            proc = subprocess.Popen(
                ["make", f"DESIGN_CONFIG=./designs/sky130hd/{design}/config.mk",
                 f"RESULTS_DIR=./results/sky130hd/{design}/{test_variant_name}"],
                cwd=flow_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True,
            )

            # Heartbeat runs on its OWN thread with a real wall-clock sleep,
            # independent of whether the subprocess is producing output --
            # readline()-triggered checks silently fail to fire during a long
            # quiet stretch inside `make` itself (confirmed: a 38-minute gap
            # with zero heartbeats using the naive approach).
            stop_heartbeat = threading.Event()
            def _heartbeat():
                while not stop_heartbeat.wait(15):
                    elapsed_now = round(_time.time() - start)
                    print(f"      ... still regenerating PDN at {strap_width_um}um "
                          f"({elapsed_now}s elapsed)", flush=True)
            hb_thread = threading.Thread(target=_heartbeat, daemon=True)
            hb_thread.start()

            output_lines = []
            for line in proc.stdout:
                output_lines.append(line)
            proc.wait()
            stop_heartbeat.set()
            hb_thread.join(timeout=1)

            elapsed = _time.time() - start
            if proc.returncode != 0:
                return {"error": "make failed", "returncode": proc.returncode,
                        "stderr_tail": "".join(output_lines)[-2000:], "elapsed_s": elapsed}
        finally:
            shutil.copy(backup, self.PDN_TCL)
            os.remove(backup)

        return {"ok": True, "elapsed_s": round(elapsed, 1),
                "def_path": f"{results_test}/6_final.def",
                "spef_path": f"{results_test}/6_final.spef"}
