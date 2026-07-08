"""
app.py — AI/BI Dashboard Intake Builder

A Gradio app that lets business users describe the dashboard they need, then
uses an LLM agent to build a Lakeview dashboard against a Unity Catalog
catalog/schema you configure (see GOLD_CATALOG/GOLD_SCHEMA in agent.py / app.yaml).
Conversation memory and dashboard version control are persisted in Lakebase.

Required env vars (auto-injected when Lakebase resource is linked in app.yaml):
  PGHOST, PGDATABASE, PGUSER, PGPORT
Auto-provided by the platform:
  DATABRICKS_HOST, DATABRICKS_TOKEN (or M2M via DATABRICKS_CLIENT_ID)
"""

import os
import uuid
import json

import gradio as gr
from fastapi import FastAPI
import uvicorn

# ---------------------------------------------------------------------------
# Patch gradio_client bug: additionalProperties=False (a bool) is passed to
# _json_schema_to_python_type(), crashing with "argument of type 'bool' is
# not iterable". Wrap both the public and private entry-points to guard.
# ---------------------------------------------------------------------------
try:
    import gradio_client.utils as _gcu

    _orig_private = _gcu._json_schema_to_python_type

    def _safe_private(schema, defs=None):
        if not isinstance(schema, dict):
            return "any"
        return _orig_private(schema, defs)

    _gcu._json_schema_to_python_type = _safe_private

    _orig_get_type = _gcu.get_type

    def _safe_get_type(schema):
        if not isinstance(schema, dict):
            return "any"
        return _orig_get_type(schema)

    _gcu.get_type = _safe_get_type
except Exception as _patch_err:
    print(f"[WARN] gradio_client patch failed: {_patch_err}")

import db
import agent as ag

# ---------------------------------------------------------------------------
# Bootstrap Lakebase schema on startup
# ---------------------------------------------------------------------------
_db_ready = False
try:
    db.init_db()
    _db_ready = True
except Exception as _db_err:
    print(f"[WARN] Lakebase init failed — history features disabled: {_db_err}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _db_op(fn, *args, fallback=None, **kwargs):
    """Run a db call; swallow errors if Lakebase is unavailable."""
    if not _db_ready:
        return fallback
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        print(f"[WARN] DB error: {e}")
        return fallback


def _render_build_progress(lines: list[str]) -> str:
    bullet_lines = "\n".join(f"* {line}" for line in lines)
    return "Building your dashboard...\n\n" + bullet_lines


def _render_dash_link(url: str) -> str:
    """Return an HTML anchor for the dashboard URL."""
    return (
        f'<a href="{url}" target="_blank" rel="noopener noreferrer" '
        f'style="font-size:1rem;font-weight:600;">🔗 Open Dashboard</a>'
        f'<br><span style="font-size:0.8rem;color:#666;">{url}</span>'
    )


def _get_user_email(request: gr.Request | None) -> str:
    """Best-effort requesting-user identity, forwarded by the Databricks Apps auth proxy.

    Used to scope Dashboard History per user. Falls back to "unknown" if the
    header is absent (e.g. running outside the Apps proxy) — that just means
    such sessions share one bucket rather than being individually isolated.
    """
    if request is None:
        return "unknown"
    return request.headers.get("x-forwarded-email") or "unknown"


# ---------------------------------------------------------------------------
# Event handlers
# ---------------------------------------------------------------------------
def on_submit_intake(
    report_name, biz_question, description, key_metrics, dimensions, time_period, session_id,
    request: gr.Request,
):
    if not report_name.strip():
        gr.Warning("Please provide a Report Name.")
        return gr.update(), session_id, "", "", "", "", "", ""

    if not session_id:
        session_id = str(uuid.uuid4())

    _db_op(
        db.save_conversation,
        session_id, report_name, biz_question, description, key_metrics, dimensions, time_period,
        _get_user_email(request),
    )

    initial_msg = (
        f"I've received your request for **{report_name}**.\n\n"
        f"**Business Question:** {biz_question}\n\n"
        "Click **Build Dashboard** to generate it, or ask me any questions first."
    )
    _db_op(db.save_message, session_id, "assistant", initial_msg)

    history = [[None, initial_msg]]
    gr.Info("Request submitted! Switch to the Build & Review tab to continue.")
    return (
        history,
        session_id,
        report_name, biz_question, description, key_metrics, dimensions, time_period,
    )


def on_build_dashboard(
    history, session_id,
    report_name, biz_question, description, key_metrics, dimensions, time_period,
    request: gr.Request,
):
    intake = {
        "report_name": report_name,
        "business_question": biz_question,
        "description": description,
        "key_metrics": key_metrics,
        "dimensions": dimensions,
        "time_period": time_period,
    }
    mem = _db_op(db.get_messages, session_id, fallback=[])

    progress_lines = ["Starting build. This may take 20-40 seconds."]
    history = list(history or []) + [[None, _render_build_progress(progress_lines)]]
    yield history, "<p style='color:#888'>Building...</p>"

    try:
        msg = url = spec = dash_id = None
        for event, payload in ag.build_dashboard_stream(intake, session_id, mem):
            if event == "progress":
                progress_lines.append(payload)
                history[-1] = [None, _render_build_progress(progress_lines)]
                yield history, "<p style='color:#888'>Building...</p>"
            elif event == "result":
                msg, url, spec, dash_id = payload

        if msg is None:
            raise RuntimeError("Dashboard build ended without a result.")

        _db_op(
            db.save_dashboard_version,
            session_id, report_name, dash_id, url, spec,
            f"Auto-generated from intake: {biz_question}",
            _get_user_email(request),
        )
        _db_op(db.save_message, session_id, "assistant", msg)

        history[-1] = [None, msg]
        yield history, _render_dash_link(url)

    except Exception as e:
        err = f"Error building dashboard: {e}"
        history[-1] = [None, err]
        yield history, ""


def on_chat(user_msg, history, session_id):
    if not user_msg.strip():
        return history, ""
    mem = _db_op(db.get_messages, session_id, fallback=[])
    reply = ag.chat_with_agent(user_msg, mem)
    _db_op(db.save_message, session_id, "user", user_msg)
    _db_op(db.save_message, session_id, "assistant", reply)
    history = list(history or []) + [[user_msg, reply]]
    return history, ""


def load_history(request: gr.Request):
    user_email = _get_user_email(request)
    rows = _db_op(db.get_all_dashboard_versions, user_email, fallback=[])
    return [
        [r["report_name"], r["version_num"], r["status"],
         r["created_at"], r.get("dashboard_url", ""), r["id"]]
        for r in rows
    ]


def on_row_select(history_data, evt: gr.SelectData, request: gr.Request):
    if evt is None or not history_data:
        return ""
    try:
        version_id = history_data[evt.index[0]][5]
        user_email = _get_user_email(request)
        return _db_op(db.get_dashboard_json, int(version_id), user_email, fallback="") or ""
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
_CSS = """
/* Remove Gradio's default max-width cap so panels fill the browser window */
.gradio-container { max-width: 100% !important; padding: 16px 32px !important; }
/* Hide the "Built with Gradio" footer to recover vertical space */
footer { display: none !important; }
/* Make the tab panels stretch to available height */
.tab-nav { border-bottom: 1px solid #e0e0e0; }
"""

with gr.Blocks(
    title="AI/BI Dashboard Builder",
    theme=gr.themes.Soft(),
    css=_CSS,
    fill_height=True,
) as demo:
    gr.Markdown("# 📊 AI/BI Dashboard Builder")
    gr.Markdown(
        "Describe the business dashboard you need. "
        f"The agent will design it against your `{ag.SCOPE_CATALOG}.{ag.SCOPE_SCHEMA}` "
        "data layer and publish a Lakeview dashboard."
    )

    session_state = gr.State(str(uuid.uuid4()))
    s_report = gr.State("")
    s_bq = gr.State("")
    s_desc = gr.State("")
    s_metrics = gr.State("")
    s_dims = gr.State("")
    s_tp = gr.State("Last 30 days")

    with gr.Tabs() as tabs:
        with gr.Tab("Request Dashboard", id=0):
            with gr.Row():
                with gr.Column(scale=2):
                    f_report = gr.Textbox(
                        label="Report Name *",
                        placeholder="e.g., Q2 Carrier Performance Overview",
                    )
                    f_bq = gr.Textbox(
                        label="Business Question",
                        placeholder="e.g., Which carriers have the highest claim rates?",
                    )
                    f_desc = gr.Textbox(
                        label="Description",
                        lines=3,
                        placeholder="Describe what insights you need...",
                    )
                    f_metrics = gr.Textbox(
                        label="Key Metrics",
                        lines=2,
                        placeholder="e.g., Total claims, Avg claim value, On-time delivery rate",
                    )
                    f_dims = gr.Textbox(
                        label="Dimensions & Filters",
                        lines=2,
                        placeholder="e.g., By carrier, By client, By region, By month",
                    )
                    f_tp = gr.Dropdown(
                        choices=["Last 30 days", "Last quarter", "Last 6 months", "Last year", "All time"],
                        value="Last 30 days",
                        label="Time Period",
                    )
                    submit_btn = gr.Button("Submit Request ➡", variant="primary", size="lg")
                with gr.Column(scale=1):
                    gr.Markdown("""
### Tips for a great dashboard

**Report Name** — give it a clear, business-friendly name.

**Business Question** — one crisp question the dashboard should answer.

**Key Metrics** — list the KPIs you need (e.g., claim rate, on-time %, cost per move).

**Dimensions & Filters** — how you want to slice the data (carrier, client tier, region, month).

**Time Period** — the default date range to show.

After submitting, switch to **Build & Review** to generate your dashboard.
                    """)

        with gr.Tab("Build & Review", id=1):
            with gr.Row():
                with gr.Column(scale=3):
                    chatbot = gr.Chatbot(
                        label="Agent",
                        height=560,
                        show_copy_button=True,
                        bubble_full_width=True,
                    )
                    dash_link = gr.HTML(label="Dashboard URL")
                    with gr.Row():
                        build_btn = gr.Button("Build Dashboard", variant="primary", scale=1)
                        rebuild_btn = gr.Button("🔄 Rebuild (new version)", variant="secondary", scale=1)
                    with gr.Row():
                        chat_input = gr.Textbox(
                            label="Ask a question or request changes",
                            placeholder="e.g., Add a section showing expense compliance trends",
                            scale=5,
                        )
                        send_btn = gr.Button("Send", scale=1)

        with gr.Tab("Dashboard History", id=2):
            refresh_btn = gr.Button("🔄 Refresh")
            history_tbl = gr.Dataframe(
                headers=["Report Name", "Version", "Status", "Created", "URL", "ID"],
                datatype=["str", "number", "str", "str", "str", "number"],
                label="All Dashboard Versions (click a row to preview JSON)",
                interactive=False,
                wrap=True,
                type="array",  # list-of-lists — on_row_select indexes rows positionally
            )
            json_box = gr.Code(label="Serialized Dashboard JSON", language="json", lines=25)

    submit_btn.click(
        fn=on_submit_intake,
        inputs=[f_report, f_bq, f_desc, f_metrics, f_dims, f_tp, session_state],
        outputs=[chatbot, session_state, s_report, s_bq, s_desc, s_metrics, s_dims, s_tp],
    )

    build_btn.click(
        fn=on_build_dashboard,
        inputs=[chatbot, session_state, s_report, s_bq, s_desc, s_metrics, s_dims, s_tp],
        outputs=[chatbot, dash_link],
    )

    # Rebuild reuses on_build_dashboard directly — it must keep the SAME session_id
    # so Phase 2 sees the chat history the user just gave it (see on_build_dashboard's
    # db.get_messages(session_id) call). A fresh session_id here previously discarded
    # all chat-driven refinement before every rebuild.
    rebuild_btn.click(
        fn=on_build_dashboard,
        inputs=[chatbot, session_state, s_report, s_bq, s_desc, s_metrics, s_dims, s_tp],
        outputs=[chatbot, dash_link],
    )

    send_btn.click(
        fn=on_chat,
        inputs=[chat_input, chatbot, session_state],
        outputs=[chatbot, chat_input],
    )
    chat_input.submit(
        fn=on_chat,
        inputs=[chat_input, chatbot, session_state],
        outputs=[chatbot, chat_input],
    )

    refresh_btn.click(fn=load_history, outputs=[history_tbl])
    history_tbl.select(fn=on_row_select, inputs=[history_tbl], outputs=[json_box])

    demo.load(fn=load_history, outputs=[history_tbl])


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
_PORT = int(os.environ.get("PORT", 8080))

_fastapi = FastAPI()
_fastapi = gr.mount_gradio_app(_fastapi, demo, path="/")

if __name__ == "__main__":
    uvicorn.run(_fastapi, host="0.0.0.0", port=_PORT)
