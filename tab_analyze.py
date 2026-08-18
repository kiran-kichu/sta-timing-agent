import streamlit as stl
import sta_parse

def render():
    stl.write("Paste or upload an OpenSTA `report_checks` output. "
              "This runs the parser only — no OpenSTA, no API calls, "
              "so it works anywhere.")

    up = stl.file_uploader("Report file", type=["txt", "log", "rpt"])
    txt = up.read().decode("utf-8", "replace") if up else stl.text_area(
        "…or paste the report here", height=180)

    if not txt or not txt.strip():
        return

    paths = sta_parse.parse(txt)
    if not paths:
        stl.error("No timing paths found. Expected `report_checks "
                  "-format full_clock_expanded` output.")
        return

    s = sta_parse.summary(paths)
    c = stl.columns(4)
    c[0].metric("WNS (ns)", s["wns"])
    c[1].metric("Paths parsed", s["n_paths"])
    c[2].metric("Violating", s["n_violating"])
    c[3].metric("Timing met", "yes" if s["timing_met"] else "no")

    stl.caption(f"{len(txt.splitlines())} lines of report text → "
                f"{s['n_paths']} structured paths. TNS shown here is only over "
                f"the paths present in this report, not the whole design.")

    worst = sorted(paths, key=lambda p: p["slack"])[0]
    stl.subheader(f"Worst path — slack {worst['slack']} ns, "
                  f"depth {len(worst['stages'])}")
    stl.write(f"`{worst['startpoint']}` → `{worst['endpoint']}`")
    stl.dataframe(worst["stages"][:10], hide_index=True)

    bn = sta_parse.shared_bottlenecks(paths)
    if bn:
        stl.subheader("Instances on multiple violating paths")
        stl.caption("A candidate list, not a recommendation — measured evidence "
                    "shows the top-ranked entry can degrade WNS.")
        stl.dataframe(bn[:10], hide_index=True)
