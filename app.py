# app.py ────────────────────────────────────────────────────────────────
"""
Interactive UI for the SyneBank credit‑card‑cancellation workflow
-----------------------------------------------------------------
* live log grouped per node (expanders, coloured updates)
* progress bar & sortable timeline table
* Mermaid diagram of the executed path
* pretty JSON view of the final state
"""
import builtins, json, time, re
from contextlib import contextmanager

import streamlit as st
import pandas as pd

# -- import your workflow loader (adjust module name if different) ------
from main import load_and_compile_workflow
# -----------------------------------------------------------------------

# ---------- helper to size the progress bar ----------------------------
def count_expected_steps() -> int:
    with open("workflow.json") as f:
        data = json.load(f)
    n = len(data["workflow"]["activities"])
    n += sum(len(a.get("branches", []))
             for a in data["workflow"]["activities"]
             if a.get("type") == "parallel")
    return n

EXPECTED_STEPS = count_expected_steps()

# ---------------- Streamlit page set‑up --------------------------------
st.set_page_config("SyneBank Cancellation Demo", "💳", layout="wide")
st.title("💳 SyneBank • Credit‑card Cancellation Workflow")

# ---------------- sidebar (input) --------------------------------------
with st.sidebar:
    st.header("Customer info")
    workflow_type = st.selectbox("Workflow type", ["Credit Card Cancellation"])  # ← NEW
    full_name = st.text_input("Full name", "")
    last4     = st.text_input("Card last‑4", "")
    otp       = st.text_input("One‑time passcode", "")
    run_btn   = st.button("▶ Run")
    result_msg = st.empty()

# ---------------- placeholders -----------------------------------------
log_container  = st.container()
pb             = st.empty()
state_box      = st.empty()
timeline_box   = st.empty()
diagram_box    = st.empty()

# make sure key exists even on first run
st.session_state.setdefault("timeline", [])

# ---------------- helper: capture print --------------------------------
@contextmanager
def capture_print(cb):
    orig = builtins.print
    builtins.print = lambda *a, **k: cb(" ".join(map(str, a)))
    try:
        yield
    finally:
        builtins.print = orig

STEP_RE = re.compile(r"\bProcessing\s+(\S+)\s+\((\w+)\)")

# ======================================================================
#                            RUN
# ======================================================================
if run_btn:
    raw_lines: list[str]         = []
    timeline_local: list[dict]   = []   # keep out of Streamlit while running

    def logger(line: str):
        raw_lines.append(line)
        m = STEP_RE.search(line)
        if m:
            node_id, node_type = m.groups()
            timeline_local.append({
                "Node": node_id,
                "Type": node_type,
                "Time": time.strftime("%H:%M:%S"),
            })

    with capture_print(logger):
        wf = load_and_compile_workflow()
        final_state = wf.invoke({
            "full_name": full_name,
            "card_number_or_last4": last4,
            "authentication_info": {"otp": otp},
            "cancellation_request_id": "req_ui_demo",
            "CONFIG_BALANCE_THRESHOLD": 0.0,
        })

    # ---------- back on Streamlit thread --------------------------------
    st.session_state.timeline = timeline_local  # store for future reruns
    pb.progress(1.0)

    # group log lines by node
    groups: dict[str, list[str]] = {}
    current = "startup"
    for ln in raw_lines:
        if (m := STEP_RE.search(ln)):
            current = m.group(1)
        groups.setdefault(current, []).append(ln)

    def colour(l: str) -> str:
        return (f"<span style='color:#43d9ad'>{l}</span>"
                if "[🔁 Step Updates]" in l else l)

    with log_container:
        st.subheader("Execution log")
        for node, lines in groups.items():
            with st.expander(f"📍 {node}",
                             expanded=node == "initiate_cancellation_request"):
                st.markdown("<br>".join(colour(l) for l in lines),
                            unsafe_allow_html=True)

    state_box.subheader("Final workflow state")
    state_box.json(final_state, expanded=False)

    timeline_box.subheader("Timeline")
    timeline_box.dataframe(pd.DataFrame(st.session_state.timeline),
                           use_container_width=True, hide_index=True)

    diagram_box.subheader("Path taken")
    nodes = [r["Node"] for r in st.session_state.timeline]
    edges = "\n".join(f"{a} --> {b}" for a, b in zip(nodes, nodes[1:]))
    diagram_box.markdown(f"```mermaid\ngraph TD\n{edges}\n```",
                         unsafe_allow_html=True)

    if final_state.get("closure_confirmation"):
        result_msg.success("✅ Card cancelled successfully.")
    elif final_state.get("card_status") == "legal_hold":
        result_msg.error("❌ Card cannot be cancelled due to unmet requirements.")
    elif final_state.get("closure_approved") is False:
        result_msg.warning("⚠️ Card closure was denied.")
    else:
        result_msg.info("ℹ️ Authentication failed.")
        