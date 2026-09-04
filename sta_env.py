"""Real STA environment: the tools the agent is allowed to use.

No LLM code in this file. Everything here is ordinary Python that runs
OpenSTA and edits a netlist. It is independently testable.
"""

import os, re, shutil, subprocess, collections

def _find_orfs_root():
    # ~/OpenROAD-flow-scripts is correct for local dev; deployments based on
    # the openroad/orfs Docker image place it at /OpenROAD-flow-scripts
    # instead (no leading /root), since that image's WORKDIR is root-level.
    candidates = [
        os.path.expanduser("~/OpenROAD-flow-scripts/flow"),
        "/OpenROAD-flow-scripts/flow",
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return candidates[0]  # fall through to the original default if neither exists yet

ORFS = _find_orfs_root()
LIB  = f"{ORFS}/platforms/sky130hd/lib/sky130_fd_sc_hd__tt_025C_1v80.lib"

# Fast corner (min delay analysis) for hold checking. Same folder as LIB so
# both resolve correctly whether running locally or inside the Docker image.
LIB_FAST = f"{ORFS}/platforms/sky130hd/lib/sky130_fd_sc_hd__ff_n40C_1v95.lib"

WORK = os.path.expanduser("~/sta-agent/work")

def _find_sta():
    # The full `openroad` binary is a superset that includes all of
    # OpenSTA's Tcl commands -- accept it as a valid substitute for the
    # standalone `sta` binary when that isn't present (e.g. deployments
    # based on the openroad/orfs image, which only ships `openroad`).
    cand = [
        os.path.expanduser("~/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/sta"),
        shutil.which("sta"),
        os.path.expanduser("~/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad"),
        "/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad",
        "/root/OpenROAD-flow-scripts/tools/install/OpenROAD/bin/openroad",
        shutil.which("openroad"),
    ]
    for c in cand:
        if c and os.path.isfile(c) and os.access(c, os.X_OK):
            return c
    raise RuntimeError("Could not find the `sta` or `openroad` binary. Tried: " + str(cand))

STA_BIN = _find_sta()

CELL_RE    = re.compile(r'cell \("?(sky130_fd_sc_hd__\w+)"?\)')
AREA_RE    = re.compile(r"^\s*area\s*:\s*([\d.]+)\s*;")
INST_RE    = re.compile(r"^\s*(sky130_fd_sc_hd__\w+)\s+")
WNS_RE     = re.compile(r"^wns max\s+(-?[\d.]+)")
TNS_RE     = re.compile(r"^tns max\s+(-?[\d.]+)")
WNS_MIN_RE = re.compile(r"^wns min\s+(-?[\d.]+)")
TNS_MIN_RE = re.compile(r"^tns min\s+(-?[\d.]+)")


def _load_liberty():
    areas, cur = {}, None
    with open(LIB) as f:
        for line in f:
            if (m := CELL_RE.search(line)):
                cur = m.group(1)
                areas.setdefault(cur, 0.0)
            elif cur and (m := AREA_RE.match(line)):
                areas[cur] = float(m.group(1))
    families = {}
    for name in areas:
        tail = name.rsplit("_", 1)[-1]
        if tail.isdigit():
            families.setdefault(name.rsplit("_", 1)[0], []).append(int(tail))
    for k in families:
        families[k].sort()
    return areas, families


AREAS, FAMILIES = _load_liberty()


class Design:
    def __init__(self, variant="p3.6", top="picorv32", design="picorv32", var_path=None):
        self.var = var_path or f"{ORFS}/results/sky130hd/{design}/{variant}"
        self.top = top
        os.makedirs(WORK, exist_ok=True)
        self.netlist = f"{WORK}/netlist.v"
        shutil.copy(f"{self.var}/1_2_yosys.v", self.netlist)
        self.default_sdc = f"{self.var}/1_synth.sdc"
        self.snapshots = []
        self.history = []
        self._cache = None
        self._hold_cache = None
        self._mode_cache = {}   # mode_name -> setup result, separate from the default cache

    # ---------- measurement ----------

    def _run(self, extra="", lib=None, sdc=None):
        lib = lib or LIB
        sdc = sdc or self.default_sdc
        tcl = f"{WORK}/sta.tcl"
        with open(tcl, "w") as f:
            f.write(f"read_liberty {lib}\n"
                    f"read_verilog {self.netlist}\n"
                    f"link_design {self.top}\n"
                    f"read_sdc {sdc}\n"
                    f"{extra}\n")
        return subprocess.run([STA_BIN, "-exit", tcl],
                              capture_output=True, text=True).stdout

    def sta(self):
        """Setup (max delay) WNS/TNS at the typical corner, using this
        design's default SDC. Unchanged behavior from before."""
        if self._cache is None:
            out = self._run("report_wns -digits 4\nreport_tns -digits 4\n")
            wns = tns = None
            for line in out.splitlines():
                if (m := WNS_RE.match(line)): wns = float(m.group(1))
                if (m := TNS_RE.match(line)): tns = float(m.group(1))
            self._cache = {"wns_ns": wns, "tns_ns": tns,
                           "timing_met": wns is not None and wns >= 0,
                           "area": round(self.area(), 1)}
        return self._cache

    def hold(self):
        """Hold (min delay) WNS/TNS at the fast corner, using this design's
        default SDC."""
        if self._hold_cache is None:
            out = self._run("report_wns -digits 4 -min\nreport_tns -digits 4 -min\n",
                            lib=LIB_FAST)
            wns = tns = None
            for line in out.splitlines():
                if (m := WNS_MIN_RE.match(line)): wns = float(m.group(1))
                if (m := TNS_MIN_RE.match(line)): tns = float(m.group(1))
            self._hold_cache = {"hold_wns_ns": wns, "hold_tns_ns": tns,
                                "hold_met": wns is not None and wns >= 0}
        return self._hold_cache

    def setup_under_sdc(self, mode_name, sdc_path):
        """Setup (max delay) WNS/TNS at the typical corner, under an
        ALTERNATE SDC file representing a different mode (e.g. a different
        clock period or case-analysis setting). Cached per mode_name,
        separate from the default sta() cache."""
        if mode_name not in self._mode_cache:
            out = self._run("report_wns -digits 4\nreport_tns -digits 4\n",
                            sdc=sdc_path)
            wns = tns = None
            for line in out.splitlines():
                if (m := WNS_RE.match(line)): wns = float(m.group(1))
                if (m := TNS_RE.match(line)): tns = float(m.group(1))
            self._mode_cache[mode_name] = {
                "mode": mode_name, "wns_ns": wns, "tns_ns": tns,
                "timing_met": wns is not None and wns >= 0}
        return self._mode_cache[mode_name]

    def area(self):
        total = 0.0
        with open(self.netlist) as f:
            for line in f:
                if (m := INST_RE.match(line)):
                    total += AREAS.get(m.group(1), 0.0)
        return total

    def report(self, n=8):
        return self._run(
            f"report_checks -path_delay max -sort_by_slack -group_path_count {n} "
            f"-format full_clock_expanded -fields {{slew cap input_pins fanout}}")

    # ---------- mutation ----------

    def _swap(self, instance, old_cell, new_cell):
        src = open(self.netlist).read()
        inst = r"(?:\\)?" + re.escape(instance) + r"(?=[\s(])"
        pat = re.compile(r"\b" + re.escape(old_cell) + r"(\s+" + inst + r")")
        new, n = pat.subn(new_cell + r"\1", src)
        if n == 1:
            open(self.netlist, "w").write(new)
        return n

    def snapshot(self):
        self.snapshots.append(open(self.netlist).read())

    def revert(self):
        if not self.snapshots:
            return False
        open(self.netlist, "w").write(self.snapshots.pop())
        self._cache = None
        self._hold_cache = None
        self._mode_cache = {}
        return True

    def invalidate(self):
        self._cache = None
        self._hold_cache = None
        self._mode_cache = {}
