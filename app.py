import statistics as st_
import pandas as pd
import streamlit as stl
import ui_env

stl.set_page_config(page_title="STA Timing Agent", layout="wide")
stl.title("STA Timing-Closure Agent")
stl.caption("picorv32 / sky130hd / OpenSTA — LLM agent vs delay-ranked greedy")

if ui_env.live():
    stl.success(f"LIVE — OpenSTA found, {len(ui_env.variants())} variants available")
else:
    stl.info("REPLAY — no OpenSTA here; showing recorded results only")

import tab_run, tab_analyze
t_res, t_run, t_ana = stl.tabs(["Results", "Run agent", "Analyze a report"])

with t_run:
    tab_run.render()
with t_ana:
    tab_analyze.render()

stl_page = t_res.container()
agent = ui_env.load_csv("sweep_results.csv")
greedy = ui_env.greedy_rows()

if not agent:
    stl.warning("No sweep_results.csv yet. Run: python sweep.py")
    stl.stop()

rows = []
for v in sorted({r["variant"] for r in agent}):
    a = [r for r in agent if r["variant"] == v]
    g = [r for r in greedy if r["variant"] == v]
    wns = [float(r["wns_final"]) for r in a]
    rows.append({
        "variant": v,
        "base WNS": float(a[0]["wns_base"]),
        "greedy WNS": float(g[0]["wns_final"]) if g else None,
        "agent WNS": round(st_.mean(wns), 3),
        "agent sd": round(st_.stdev(wns), 3) if len(wns) > 1 else 0.0,
        "greedy TNS": float(g[0]["tns_final"]) if g else None,
        "agent TNS": round(st_.mean([float(r["tns_final"]) for r in a]), 2),
        "greedy area": float(g[0]["area_delta"]) if g else None,
        "agent area": round(st_.mean([float(r["area_delta"]) for r in a]), 1),
        "n": len(a),
    })

df = pd.DataFrame(rows)
hard = df[df["base WNS"] < 0]

stl_page.subheader("Cases with real violations")
if len(hard):
    c = stl.columns(3)
    h = hard.iloc[0]
    c[0].metric("WNS", f"{h['agent WNS']}", f"{round(h['agent WNS']-h['greedy WNS'],3)} vs greedy")
    c[1].metric("TNS", f"{h['agent TNS']}", f"{round(h['agent TNS']-h['greedy TNS'],2)} vs greedy")
    c[2].metric("Area", f"+{h['agent area']}", f"{round(h['agent area']-h['greedy area'],1)} vs greedy",
                delta_color="inverse")
    stl.dataframe(hard, use_container_width=True, hide_index=True)

stl_page.subheader("All variants")
stl_page.dataframe(df, use_container_width=True, hide_index=True)

clean = df[df["base WNS"] == 0]
if len(clean):
    stl.subheader("Already-clean designs — false-positive check")
    stl.write(f"{len(clean)} variants started at WNS 0.00. "
              f"Agent added **{clean['agent area'].sum():.1f} µm²** across "
              f"{int(clean['n'].sum())} runs. Zero is correct.")
