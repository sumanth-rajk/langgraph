# main.py ────────────────────────────────────────────────────────────────
from typing import TypedDict, Dict, Any, List, Optional
import json, requests, logging, inspect, re
from urllib.parse import urlencode
from langgraph.graph import StateGraph
from langchain_core.runnables import RunnableLambda

# ───────────────────────── helper regexes ───────────────────────────────
_JS_LENGTH = re.compile(r"(\w+)\.length")
_JS_TRUE   = re.compile(r"\btrue\b",  flags=re.IGNORECASE)
_JS_FALSE  = re.compile(r"\bfalse\b", flags=re.IGNORECASE)


def js_to_py(expr: str) -> str:
    """Convert JS style (length, ||, &&, true/false) → Python."""
    expr = _JS_LENGTH.sub(r"len(\1)", expr)
    expr = expr.replace("||", " or ").replace("&&", " and ")
    expr = _JS_TRUE.sub("True",  expr)
    expr = _JS_FALSE.sub("False", expr)
    return expr

# add near the other helpers ────────────────────────────────────────────
_NUMERIC_RE = re.compile(r"^-?\d+(\.\d+)?$")

def _auto_convert(v: Any) -> Any:
    """Try to turn numeric strings into int/float."""
    if isinstance(v, str) and _NUMERIC_RE.match(v):
        return float(v) if "." in v else int(v)
    return v

# ───────────────────────── shared workflow state ────────────────────────
class WorkflowState(TypedDict, total=False):
    full_name: str
    card_number_or_last4: str
    authentication_info: dict
    cancellation_request_id: str
    authentication_status: str
    card_status: str
    restriction_reason: str
    outstanding_balance: float
    pending_transactions: list
    recurring_payments: list
    reward_points_balance: int
    all_criteria_met: bool
    unmet_requirements: List[str]
    closure_approved: bool
    closure_confirmation: str
    updated_balance: float
    residual_charge_detected: bool
    residual_charge_details: dict
    CONFIG_BALANCE_THRESHOLD: float


# ─────────── placeholder‑resolver to inject state values -----------------
_PLACEHOLDER = re.compile(r"<from ([\w:]+)>")

def _resolve(obj, state: dict):
    """Recursively replace <from xyz> placeholders with values from state."""
    if isinstance(obj, dict):
        return {k: _resolve(v, state) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_resolve(v, state) for v in obj]
    if isinstance(obj, str):
        return _PLACEHOLDER.sub(
            lambda m: str(state.get(m.group(1).split("::")[0],
                                    f"__MISSING:{m.group(1)}__")), obj
        )
    return obj


# ───────────────────────── node‑handler factory ─────────────────────────
def get_node_handler(activity):
    t = activity["type"]

    # USER‑INPUT: pull values from incoming state (or mock)
    if t == "user_input":
        def handler(state: WorkflowState) -> Dict[str, Any]:
            print(f"\n🏁 Processing {activity['id']} ({t})")
            updates = {o: state.get(o, f"mock_{o}") for o in activity.get("outputs", [])}
            if updates:
                print(f"[🔁 Step Updates] {updates}")
            return updates
        return handler

    # generic wrapper to print+return diffs
    def wrap(fn):
        def inner(state: WorkflowState) -> Dict[str, Any]:
            print(f"\n🏁 Processing {activity['id']} ({t})")
            delta = fn(activity) or {}
            if delta:
                print(f"[🔁 Step Updates] {delta}")
            return delta
        return inner

    if t == "api_call":     return wrap(handle_api_call)
    if t == "notification": return wrap(handle_notification)
    if t == "pause":        return wrap(handle_pause)
    if t == "user_choice":  return wrap(handle_user_choice)
    if t == "parallel":     return wrap(handle_parallel)
    if t == "end":          return wrap(handle_end)
    raise ValueError(f"Unsupported activity type {t}")


# ────────────────────────── node implementations ────────────────────────
def handle_api_call(activity) -> Dict[str, Any]:
    api   = activity["api_integration"]
    url   = api["endpoint"]
    meth  = api["method"].upper()

    state = inspect.currentframe().f_back.f_locals["state"]
    params  = _resolve(api.get("params",  {}), state)
    payload = _resolve(api.get("payload", {}), state)

    print(f"🔧 API Call → {meth} {url}")
    data: Dict[str, Any] = {}

    try:
        if meth == "GET":
            full = f"{url}?{urlencode(params)}" if params else url
            r = requests.get(full, timeout=10)
        elif meth == "POST":
            r = requests.post(url, params=params, json=payload, timeout=10)
        else:
            raise ValueError(f"Unsupported HTTP method {meth}")

        r.raise_for_status()
        if r.headers.get("content-type","").startswith("application/json"):
            data = r.json()
        print(f"→ HTTP {r.status_code}")
    except Exception as e:
        logging.warning("API call failed (%s); using mock values", e)
        print(f"⚠️ API call failed ({e}); using mock values")

    # convert numeric strings → numbers
    updates = {}
    for out in activity.get("outputs", []):
        raw = data.get(out, simulate_output(out))
        updates[out] = _auto_convert(raw)
        print(f"   ↳ {out} = {updates[out]}")
    return updates


def handle_notification(activity) -> Dict[str, Any]:
    print(f"📣 Notification: {activity['description']}")
    return {}


def handle_pause(activity) -> Dict[str, Any]:
    print(f"⏸️ Pause: {activity['description']}\n→ Resume condition simulated satisfied.")
    return {}


def handle_user_choice(activity) -> Dict[str, Any]:
    print(f"🤔 User Choice: {activity['description']}")
    if activity.get("choices"):
        sel = activity["choices"][0]
        print(f"   ↳ Auto‑selected: {sel['option']} → {sel['next']}")
        return {"next": sel["next"]}
    return {}


def handle_parallel(activity) -> Dict[str, Any]:
    print(f"🔀 Parallel block: {activity['description']}")
    for br in activity.get("branches", []):
        print(f"   ↪ branch {br.get('id')} – {br.get('description','')}")
    return {}


def handle_end(activity) -> Dict[str, Any]:
    print(f"✅ Workflow Complete: {activity['description']}")
    return {}


# ────────────────────────── mock generator ──────────────────────────────
def simulate_output(key: str) -> Any:
    preset = {
        "cancellation_request_id": "req_12345",
        "authentication_status": "success",
        "card_status": "clear",
        "restriction_reason": "",
        "outstanding_balance": 0.0,
        "pending_transactions": [],
        "recurring_payments": [],
        "reward_points_balance": 0,
        "all_criteria_met": True,
        "unmet_requirements": [],
        "closure_approved": True,
        "closure_confirmation": "conf_78910",
        "updated_balance": 0.0,
        "residual_charge_detected": False,
        "residual_charge_details": {},
    }
    return preset.get(key, f"mock_{key}")


# ───────────────────────── condition helper ─────────────────────────────
def make_condition_fn(expr: str):
    expr = js_to_py(expr)
    def fn(state):
        try:
            res = eval(expr, {}, dict(state))
            print(f"   ↳ Condition '{expr}' → {res}")
            return res
        except Exception as e:
            print(f"❌ Condition '{expr}' error: {e}")
            return False
    return RunnableLambda(fn, name=expr)


# ───────────────────────── graph builder ────────────────────────────────
def load_and_compile_workflow():
    with open("Workflowwithendpoints.json") as f:
        data = json.load(f)
    activities = data["workflow"]["activities"]

    # lift parallel branches into top‑level nodes
    lifted = []
    for act in activities:
        if act.get("type") == "parallel":
            for br in act["branches"]:
                br = br.copy()
                br.setdefault("id", br.get("id") or f"{act['id']}::{len(lifted)}")
                lifted.append(br)
    activities += lifted

    if not any(a["id"] == "end_process" for a in activities):
        activities.append({"id": "end_process", "type": "end",
                           "description": "End of workflow."})

    g = StateGraph(WorkflowState)

    # add nodes
    for act in activities:
        g.add_node(act["id"], get_node_handler(act))

    # add edges
    for act in activities:
        nid, typ, nxt = act["id"], act["type"], act.get("next")

        # fan‑out for parallel
        if typ == "parallel":
            for br in act.get("branches", []):
                g.add_edge(nid, br["id"])
            continue

        # simple str
        if isinstance(nxt, str):
            g.add_edge(nid, nxt)
            continue

        # conditional list
        if isinstance(nxt, list):
            branches, default = [], None
            for br in nxt:
                if "condition" in br:
                    branches.append((js_to_py(br["condition"]),
                                     br["activity"] or "end_process"))
                else:
                    default = br["activity"] or "end_process"

            fallback = default or "end_process"

            def router(st, _branches=branches, _fb=fallback):
                local = dict(st)
                for e, tgt in _branches:
                    if eval(e, {}, local):
                        return tgt
                return _fb

            targets = {tgt: tgt for _, tgt in branches}
            targets[fallback] = fallback
            g.add_conditional_edges(nid,
                                    RunnableLambda(router, name=f"{nid}_router"),
                                    targets)

    g.set_entry_point("initiate_cancellation_request")
    return g.compile()


# ───────────────────────── simple CLI runner ────────────────────────────
def run_happy_path():
    wf = load_and_compile_workflow()
    init = {
        "full_name": "Alice Smith",
        "card_number_or_last4": "1234",
        "authentication_info": {"otp": "0000"},
        "cancellation_request_id": "req_demo_cli",
        "CONFIG_BALANCE_THRESHOLD": 0.0,
    }
    print("🚀 Starting workflow execution …")
    final = wf.invoke(init)
    print("\n✅ Final state:")
    print(json.dumps(final, indent=2))


if __name__ == "__main__":
    run_happy_path()




