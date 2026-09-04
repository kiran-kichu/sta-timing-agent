import json, os, re, subprocess, sys, tempfile
import streamlit as stl
import power_report_builder

DROP_RE = re.compile(r"'worst_drop_v': ([\d.]+)")

def render():
    stl.caption("Upload a routed design (DEF + SPEF) to analyze and reduce "
                "power-grid IR drop. Currently supports sky130hd standard-"
                "cell-only designs (no hardened macros yet).")
    def_file = stl.file_uploader("Routed DEF file (.def)", type=["def"])
    spef_file = stl.file_uploader("Parasitics (SPEF, .spef)", type=["spef"])
    design_name = stl.text_input("Design name", value="picorv32")

    go = stl.button("Run power agent", type="primary", use_container_width=True)

    stl.caption("Decap and via-repair moves take seconds. Widening PDN "
                "straps re-runs floorplan-through-routing and can take "
                "several MINUTES -- the agent uses this only when the "
                "cheaper options aren't enough.")

    if not go:
        return

    if not (def_file and spef_file and design_name.strip()):
        stl.warning("Please provide a DEF file, an SPEF file, and a design name.")
        return

    tmpdir = tempfile.mkdtemp(prefix="power_upload_")
    def_path = os.path.join(tmpdir, "design.def")
    spef_path = os.path.join(tmpdir, "design.spef")
    with open(def_path, "wb") as f:
        f.write(def_file.getbuffer())
    with open(spef_path, "wb") as f:
        f.write(spef_file.getbuffer())

    log_box = stl.empty()
    chart_box = stl.empty()
    lines, drop_series = [], []
    result = None
    env = dict(os.environ, PYTHONUNBUFFERED="1")

    cmd = [sys.executable, "-u", "real_power_agent.py", def_path, spef_path,
           design_name.strip()]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env)
    with stl.spinner("Power agent working... (may take several minutes if "
                     "PDN regeneration is used)"):
        for line in proc.stdout:
            line = line.rstrip()
            if line.startswith("RESULT_JSON "):
                result = json.loads(line[len("RESULT_JSON "):])
                continue
            if not line:
                continue
            lines.append(line)
            log_box.code("\n".join(lines[-20:]), language="text")
            if (m := DROP_RE.search(line)):
                drop_series.append(float(m.group(1)) * 1000)  # V -> mV
                chart_box.line_chart({"Worst-case drop (mV)": drop_series})
    proc.wait()

    stl.subheader("Final log")
    stl.code("\n".join(lines[-60:]), language="text")

    if result:
        try:
            pdf_bytes = power_report_builder.build_power_report_pdf(result)
            stl.download_button(
                label="Download power signoff report (PDF)",
                data=pdf_bytes,
                file_name=f"power_signoff_report_{design_name.strip()}.pdf",
                mime="application/pdf",
            )
        except Exception as e:
            stl.caption(f"Report generation failed: {e}")
