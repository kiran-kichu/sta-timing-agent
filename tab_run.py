import json, os, re, subprocess, sys, tempfile
import streamlit as stl
import ui_env
import report_builder

WNS_RE = re.compile(r"'wns_after': (-?[\d.]+)")

def render():
    if not ui_env.live():
        stl.warning("Live runs need OpenSTA. Not available here.")
        return

    stl.caption("Uses the built-in sky130hd liberty file. Upload a synthesized "
                "Verilog netlist and matching SDC constraints.")
    netlist_file = stl.file_uploader("Netlist (Verilog, .v)", type=["v"])
    sdc_file = stl.file_uploader("Constraints (SDC, .sdc)", type=["sdc"])
    top = stl.text_input("Top module name")

    with stl.expander("Additional modes (optional) — multi-mode signoff"):
        stl.caption("Upload extra SDC files representing other operating modes "
                    "(e.g. a tighter clock period, a scan/test mode). A move "
                    "will only be kept if it does not regress ANY mode, not "
                    "just the primary one above.")
        extra_mode_files = stl.file_uploader(
            "Extra mode SDC file(s)", type=["sdc"], accept_multiple_files=True)

    go = stl.button("Run agent", type="primary", use_container_width=True)

    stl.caption("Each move re-runs OpenSTA at setup + hold + every extra mode. "
                "A hard design takes 1-2 minutes and costs roughly EUR 0.17 "
                "in API calls; extra modes add real OpenSTA runtime but not "
                "extra API cost.")

    if not go:
        return

    if not (netlist_file and sdc_file and top.strip()):
        stl.warning("Please provide a netlist, an SDC file, and a top module name.")
        return

    tmpdir = tempfile.mkdtemp(prefix="sta_upload_")
    netlist_path = os.path.join(tmpdir, "1_2_yosys.v")
    sdc_path = os.path.join(tmpdir, "1_synth.sdc")
    with open(netlist_path, "wb") as f:
        f.write(netlist_file.getbuffer())
    with open(sdc_path, "wb") as f:
        f.write(sdc_file.getbuffer())

    extra_mode_paths = []
    for i, ef in enumerate(extra_mode_files or []):
        p = os.path.join(tmpdir, f"mode_extra_{i}.sdc")
        with open(p, "wb") as f:
            f.write(ef.getbuffer())
        extra_mode_paths.append(p)

    log_box = stl.empty()
    chart_box = stl.empty()
    lines, wns_series = [], []
    env = dict(os.environ, PYTHONUNBUFFERED="1")

    cmd = ([sys.executable, "-u", "real_agent_tns.py", "--custom", tmpdir, top.strip()]
           + extra_mode_paths)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env)
    result = None
    with stl.spinner("Agent working..."):
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("RESULT_JSON "):
                result = json.loads(line[len("RESULT_JSON "):])
                continue
            if not line:
                continue
            lines.append(line)
            log_box.code("\n".join(lines[-18:]), language="text")
            if (m := WNS_RE.search(line)):
                wns_series.append(float(m.group(1)))
                chart_box.line_chart({"WNS (ns)": wns_series})
    proc.wait()
    if not result:
        stl.error("Run produced no result. Full output:")
        stl.code("\n".join(lines[-40:]))
        return

    timing_closed = result["wns_final"] >= 0 and result["tns_final"] >= 0
    wns_improvement = round(result["wns_final"] - result["wns_base"], 4)

    if timing_closed:
        stl.success(f"TIMING CLOSED — Final WNS: {result['wns_final']:+.4f} ns, "
                    f"Final TNS: {result['tns_final']:+.4f} ns")
    else:
        stl.warning("⚠ Optimization finished — timing not closed")
        stl.write(f"Final WNS: **{result['wns_final']:+.4f} ns**")
        stl.write(f"Final TNS: **{result['tns_final']:+.4f} ns**")
        stl.write(f"Best improvement: **{wns_improvement:+.4f} ns WNS**")

    m = stl.columns(4)
    m[0].metric("WNS", f"{result['wns_final']:+.4f}", f"{wns_improvement:+.4f}")
    m[1].metric("TNS", f"{result['tns_final']:+.4f}",
                f"{round(result['tns_final'] - result['tns_base'], 4):+.4f}")
    m[2].metric("Area", f"+{result['area_delta']}")
    m[3].metric("Moves kept / tried",
                f"{result['moves_kept']} / {result['moves_attempted']}")
    stl.caption(f"{result['moves_reverted']} moves reverted after measuring "
                f"no improvement. Harmful moves kept: {result['harmful_kept']}.")
    if extra_mode_paths:
        stl.caption(f"{len(extra_mode_paths)} additional mode(s) checked on "
                    f"every move — a move was rejected if it regressed any of them.")

    try:
        pdf_bytes = report_builder.build_report_pdf(result)
        stl.download_button(
            label="Download signoff report (PDF)",
            data=pdf_bytes,
            file_name=f"sta_signoff_report_{top.strip()}.pdf",
            mime="application/pdf",
        )
    except Exception as e:
        stl.caption(f"Report generation failed: {e}")
