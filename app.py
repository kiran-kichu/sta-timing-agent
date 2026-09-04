import os
import streamlit as stl
import ui_env

stl.set_page_config(page_title="STA Timing Agent", layout="wide")

ACCESS_CODE = os.environ.get("APP_ACCESS_CODE")


def check_access() -> bool:
    """Return True if the app should render normally. If APP_ACCESS_CODE is
    set in the environment, require it before showing anything -- this exists
    because every run costs real LLM API credits, and the app is publicly
    reachable. If the env var is unset (e.g. local dev), no gate at all."""
    if not ACCESS_CODE:
        return True
    if stl.session_state.get("access_granted"):
        return True

    stl.title("STA Timing-Closure Agent")
    stl.caption("This app runs real LLM-driven optimization and consumes real "
                "API credits per run. Enter the access code to continue.")
    code = stl.text_input("Access code", type="password")
    if stl.button("Enter"):
        if code == ACCESS_CODE:
            stl.session_state["access_granted"] = True
            stl.rerun()
        else:
            stl.error("Incorrect code.")
    return False


if not check_access():
    stl.stop()

stl.title("STA Timing-Closure Agent")
stl.caption("Upload your own design — OpenSTA + LLM agent for timing closure")

if ui_env.live():
    stl.success("LIVE — OpenSTA is available")
else:
    stl.info("REPLAY — no OpenSTA here; live runs are unavailable")

import tab_run, tab_analyze, tab_power

t_run, t_ana, t_pow = stl.tabs(["Run agent", "Analyze a report", "Power Agent"])

with t_run:
    tab_run.render()
with t_ana:
    tab_analyze.render()
with t_pow:
    tab_power.render()
