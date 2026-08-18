import json, os, re, subprocess, sys
import streamlit as stl
import ui_env

WNS_RE = re.compile(r"'wns_after': (-?[\d.]+)")

def render():
    if not ui_env.live():
        stl.warning("Live runs need OpenSTA. Not available here.")
        return

    vs = ui_env.variants()
    c1, c2 = stl.columns([3, 1])
    variant = c1.selectbox("Design variant (picorv32)", vs,
                           index=vs.index("p3.6") if "p3.6" in vs else 0)
    go = c2.button("Run agent", type="primary", use_container_width=True)

    stl.caption("Each move re-runs OpenSTA. A hard variant takes 1-2 minutes "
                "and costs roughly EUR 0.17 in API calls.")

    if not go:
        return

    log_box = stl.empty()
    chart_box = stl.empty()
    lines, wns_series = [], []

    env = dict(os.environ, PYTHONUNBUFFERED="1")
    proc = subprocess.Popen([sys.executable, "-u", "real_agent_tns.py", variant],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, env=env)

    result = None
    with stl.spinner(f"Agent working on {variant}..."):
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

    stl.success("Done")
    m = stl.columns(4)
    m[0].metric("WNS", result["wns_final"],
                round(result["wns_final"] - result["wns_base"], 3))
    m[1].metric("TNS", result["tns_final"],
                round(result["tns_final"] - result["tns_base"], 2))
    m[2].metric("Area", f"+{result['area_delta']}")
    m[3].metric("Moves kept / tried",
                f"{result['moves_kept']} / {result['moves_attempted']}")
    stl.caption(f"{result['moves_reverted']} moves reverted after measuring "
                f"no improvement. Harmful moves kept: {result['harmful_kept']}.")
