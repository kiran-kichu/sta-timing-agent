import streamlit as stl
import ui_env

stl.set_page_config(page_title="STA Timing Agent", layout="wide")
stl.title("STA Timing-Closure Agent")
stl.caption("Upload your own design — OpenSTA + LLM agent for timing closure")

if ui_env.live():
    stl.success("LIVE — OpenSTA is available")
else:
    stl.info("REPLAY — no OpenSTA here; live runs are unavailable")

import tab_run, tab_analyze

t_run, t_ana = stl.tabs(["Run agent", "Analyze a report"])

with t_run:
    tab_run.render()
with t_ana:
    tab_analyze.render()
