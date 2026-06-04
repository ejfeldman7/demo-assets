"""
Brand Manager Forecasting Intelligence
Main Streamlit Application

Seven tabs:
  0. Home - Landing page with guided overview
  1. Ask the Genies - Direct Genie Q&A
  2. Supervisor Agent - Multi-Genie orchestration with memory
  3. Schedules & Alerts - Create/manage scheduled reports and anomaly alerts
  4. Monitoring - Visual monitoring dashboard for runs, alerts, and agent insights
  5. Data Pipeline - Medallion architecture and orchestration
  6. Architecture - System design and services
"""

import os
import sys
import json
import uuid
import time
import logging
from datetime import datetime, date, timedelta

# Utility modules live in app/src/ — put it on the path so the flat imports below resolve.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "src"))

import streamlit as st
import pandas as pd

from db import (
    execute_query, execute_insert, LAKEBASE_AVAILABLE, init_schema,
    get_active_genies, get_all_genies, set_genie_active, sync_genie_spaces_from_workspace,
    get_run_health_summary, get_run_timeline, get_alert_activity,
    get_alert_summary_by_template, get_agent_memory_summary, get_schedule_performance,
)
from genie import ask_genie
from supervisor import SupervisorAgent
from report_runner import markdown_to_html, html_to_pdf, make_pdf_filename, save_to_volume
from email_utils import send_email
from viz_utils import render_in_streamlit

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Page config
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Brand Manager Forecasting Intelligence",
    page_icon="icon.svg",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# Schema init (creates tables on first run if Lakebase is available)
# ---------------------------------------------------------------------------
init_schema()
sync_genie_spaces_from_workspace()

# ---------------------------------------------------------------------------
# Custom CSS
# ---------------------------------------------------------------------------
st.markdown("""
<style>
    /* Brand colours */
    :root {
        --ad-blue: #003DA5;
        --ad-dark: #001F52;
        --ad-light: #E8F0FE;
    }
    .stApp > header { background-color: var(--ad-dark); }
    section[data-testid="stSidebar"] {
        background-color: #f0f4f8;
    }
    section[data-testid="stSidebar"] h1 {
        color: var(--ad-blue);
    }

    /* Metric card styling */
    div[data-testid="stMetricValue"] {
        font-size: 1.6rem;
        color: var(--ad-blue);
    }

    /* Executive summary highlight */
    .exec-summary {
        background: linear-gradient(135deg, #E8F0FE 0%, #f0f4f8 100%);
        border-left: 4px solid var(--ad-blue);
        padding: 1.2rem;
        border-radius: 0 8px 8px 0;
        margin-bottom: 1rem;
    }

    /* Step cards */
    .step-card {
        background: white;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 0.8rem 1rem;
        margin-bottom: 0.5rem;
    }

    /* Starter chip buttons */
    .stButton > button {
        border-radius: 20px;
    }

    /* Hide default streamlit footer */
    footer { visibility: hidden; }
</style>
""", unsafe_allow_html=True)

# ---------------------------------------------------------------------------
# Genie registry (loaded from Lakebase, with env-var fallback)
# ---------------------------------------------------------------------------
@st.cache_data(ttl=300)
def _load_genies():
    df = get_active_genies()
    return df.to_dict("records")

GENIES = _load_genies()

# ---------------------------------------------------------------------------
# User identity
# ---------------------------------------------------------------------------
def get_user_email() -> str:
    try:
        headers = st.context.headers
        return headers.get("X-Forwarded-Email", headers.get("x-forwarded-email", "demo@example.com"))
    except Exception:
        return "demo@example.com"

USER_EMAIL = get_user_email()

# ---------------------------------------------------------------------------
# Session state initialisation
# ---------------------------------------------------------------------------
def init_session_state():
    defaults = {
        # Genie tab
        "genie_chat_history": [],
        "genie_selected": "demand",
        # Supervisor tab
        "supervisor_chat_history": [],
        "supervisor_session_id": None,
        "supervisor_plan": None,
        "supervisor_results": None,
        # Schedules tab
        "schedule_refresh": 0,
        # Alerts tab
        "alert_refresh": 0,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val

init_session_state()

# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------
with st.sidebar:
    st.image("icon-blue.svg", width=60)
    st.title("Forecasting Intelligence")
    st.caption(f"Signed in as **{USER_EMAIL}**")
    st.divider()

    tab_selection = st.radio(
        "Navigation",
        ["Home", "Ask the Genies", "Supervisor Agent", "Schedules & Alerts", "Monitoring", "Data Pipeline", "Architecture"],
        label_visibility="collapsed",
    )

    st.divider()
    st.caption("Brand Intelligence | Powered by Databricks")
    st.caption("Powered by Databricks Genie + Claude")

# ---------------------------------------------------------------------------
# Helper: colour-code accuracy columns in a DataFrame
# ---------------------------------------------------------------------------
def style_accuracy_df(df: pd.DataFrame):
    """Apply red/yellow/green colouring to accuracy_pct-like columns."""
    acc_cols = [c for c in df.columns if "accuracy" in c.lower() or "pct" in c.lower() or "confidence" in c.lower()]

    def _colour_cell(val):
        try:
            v = float(val)
        except (ValueError, TypeError):
            return ""
        if v < 70:
            return "background-color: #ffcccc; color: #b71c1c"
        elif v < 85:
            return "background-color: #fff9c4; color: #f57f17"
        else:
            return "background-color: #c8e6c9; color: #1b5e20"

    styler = df.style
    for col in acc_cols:
        styler = styler.map(_colour_cell, subset=[col])
    return styler


# ===================================================================
# HOME: Landing Page
# ===================================================================
def render_home_tab():
    st.markdown(
        """
        <div style="
            background: linear-gradient(135deg, #001F52 0%, #003DA5 60%, #2a5f9e 100%);
            padding: 2.5rem 2rem;
            border-radius: 12px;
            margin-bottom: 1.5rem;
            color: white;
        ">
            <h1 style="margin:0 0 0.3rem 0; font-size:2.2rem; color:white;">Brand Manager Forecasting Intelligence</h1>
            <p style="margin:0; font-size:1.1rem; opacity:0.9;">
                A compound AI system built entirely on Databricks — from data pipeline to proactive anomaly detection.
            </p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        "This application combines **Genie Spaces**, a **Supervisor Agent**, and a **Proactive Analysis Agent** "
        "to give brand managers AI-powered access to demand forecasts, inventory data, and automated anomaly detection. "
        "Use the sidebar to navigate between pages."
    )

    st.markdown("---")

    # --- Interactive pages ---
    st.subheader("Interactive Analysis")
    h1, h2 = st.columns(2)
    with h1:
        with st.container(border=True):
            st.markdown("#### Ask the Genies")
            st.markdown(
                "Talk directly to two AI-powered Genie Spaces in plain English. "
                "**Demand Forecast** covers accuracy, revenue, and seasonal patterns. "
                "**Inventory & Channel** covers stock levels, supply chain risk, and regional breakdowns."
            )
            st.markdown("**Try it:** *\"Which SKUs have the lowest forecast accuracy this month?\"*")
    with h2:
        with st.container(border=True):
            st.markdown("#### Supervisor Agent")
            st.markdown(
                "Ask complex, multi-faceted questions that span both Genie Spaces. "
                "The agent **plans** a sequence of queries, **executes** them across the right Genies, "
                "and **synthesizes** the results into an executive report."
            )
            st.markdown("**Try it:** *\"Compare forecast accuracy trends for Electronics vs Home & Kitchen, and flag any SKUs at stockout risk.\"*")

    st.markdown("---")

    # --- Automation pages ---
    st.subheader("Automated Intelligence")
    with st.container(border=True):
        st.markdown("#### Schedules & Alerts")
        st.markdown(
            "Set up recurring **Scheduled Reports** that generate a PDF and email it to your team, "
            "or configure **Anomaly Alerts** that only fire when key metrics cross a threshold. "
            "Both run on a cron schedule using the Supervisor Agent and Genie data pipeline."
        )
        st.markdown("**Examples:** A weekly accuracy summary emailed every Monday, "
                     "or an alert when any SKU's forecast accuracy drops below 70%.")

    st.markdown("---")

    # --- Proactive Agent ---
    st.subheader("Proactive Analysis Agent")
    with st.container(border=True):
        st.markdown(
            "The proactive agent runs autonomously on a schedule — **no human question required**. "
            "It scans all SKUs for anomalies, trend breaks, stockout risks, and forecast accuracy collapses, "
            "then drills down into the most impactful candidates using Genie and analytical tools."
        )

        pa1, pa2, pa3 = st.columns(3)
        with pa1:
            st.markdown("**Phase 1: Sweep**")
            st.markdown(
                "Deterministic SQL scan across all SKUs using variance baselines, "
                "forecast-vs-inventory coverage, and period comparisons. "
                "Returns a ranked candidate list — no LLM involved."
            )
        with pa2:
            st.markdown("**Phase 2: Drill-Down**")
            st.markdown(
                "The LLM investigates top candidates using function calling — "
                "channel decomposition, correlations, and Genie template queries. "
                "It decides which tools to call and in what order."
            )
        with pa3:
            st.markdown("**Output**")
            st.markdown(
                "Ranked findings with severity and recommended actions. "
                "Persistent memory tracks what the agent is watching across runs — "
                "it won't repeat old findings unless they escalate or resolve."
            )

    st.markdown("---")

    # --- Technical pages ---
    st.subheader("Under the Hood")
    h5, h6 = st.columns(2)
    with h5:
        with st.container(border=True):
            st.markdown("#### Data Pipeline")
            st.markdown(
                "See how raw data flows through the **Medallion Architecture** — "
                "Bronze ingestion, Silver enrichment with AI SKU resolution, "
                "Gold forecasting with `ai_forecast()`, and proactive anomaly detection."
            )
    with h6:
        with st.container(border=True):
            st.markdown("#### Architecture")
            st.markdown(
                "Explore the system design: Databricks Apps, Genie Spaces, Foundation Model API, "
                "Lakebase for operational state, and the two-agent architecture (Supervisor + Proactive)."
            )


# ===================================================================
# TAB 1: Ask the Genies
# ===================================================================
def render_genie_tab():
    st.header("Ask the Genies")
    st.markdown("Query your data directly through the Demand Forecast or Inventory & Channel Genie.")

    # Build radio options from registry
    genie_options = [g["display_name"] for g in GENIES]
    genie_map = {g["display_name"]: g for g in GENIES}

    col_sel, col_clear = st.columns([3, 1])
    with col_sel:
        genie_choice = st.radio(
            "Select Genie",
            genie_options,
            horizontal=True,
            key="genie_radio",
        )
    with col_clear:
        if st.button("Clear Chat", key="genie_clear"):
            st.session_state.genie_chat_history = []
            st.rerun()

    selected_genie = genie_map.get(genie_choice, GENIES[0])
    space_id = selected_genie["space_id"]
    is_demand = selected_genie["name"] == "demand"

    # Starter question chips
    if is_demand:
        starters = [
            "What is the forecast accuracy by customer for Electronics this quarter?",
            "Show me the top 10 revenue gaps where we under-forecasted last month",
            "How does ai_forecast() compare to manual forecasts by region?",
            "Which SKUs have accuracy below 70% for the last 4 weeks?",
        ]
    else:
        starters = [
            "Which SKUs are at critical stockout risk (< 5 days of supply)?",
            "Show inventory coverage by warehouse and product category",
            "What is the channel distribution breakdown for top 20 customers?",
            "List replenishment orders due this week with quantities",
        ]

    st.markdown("**Quick questions:**")
    chip_cols = st.columns(2)
    for i, starter in enumerate(starters):
        with chip_cols[i % 2]:
            if st.button(starter, key=f"genie_starter_{i}", use_container_width=True):
                st.session_state.genie_chat_history.append({"role": "user", "content": starter})
                with st.spinner(f"Querying {genie_choice}..."):
                    result = ask_genie(space_id, starter)
                st.session_state.genie_chat_history.append({
                    "role": "assistant",
                    "content": result.get("answer_text", ""),
                    "sql_query": result.get("sql_query"),
                    "result_df": result.get("result_df"),
                    "error": result.get("error"),
                })
                st.rerun()

    st.divider()

    # Chat history display
    for msg in st.session_state.genie_chat_history:
        with st.chat_message(msg["role"]):
            if msg["role"] == "user":
                st.markdown(msg["content"])
            else:
                if msg.get("error"):
                    st.error(f"Error: {msg['error']}")
                else:
                    st.markdown(msg.get("content", ""))

                if msg.get("result_df") is not None and not msg["result_df"].empty:
                    df = msg["result_df"]
                    with st.expander(f"Data Table ({len(df)} rows)", expanded=True):
                        # Check for accuracy columns
                        acc_cols = [c for c in df.columns if "accuracy" in c.lower() or "pct" in c.lower()]
                        if acc_cols:
                            # Convert accuracy columns to numeric
                            for col in acc_cols:
                                df[col] = pd.to_numeric(df[col], errors="coerce")
                            st.dataframe(style_accuracy_df(df), use_container_width=True, hide_index=True)
                        else:
                            st.dataframe(df, use_container_width=True, hide_index=True)

                if msg.get("sql_query"):
                    with st.expander("View SQL"):
                        st.code(msg["sql_query"], language="sql")

                # Send to Supervisor button
                if msg.get("content"):
                    if st.button("Ask Supervisor Agent", key=f"send_supervisor_{id(msg)}"):
                        st.session_state.supervisor_chat_history = []
                        st.session_state.supervisor_session_id = None
                        # Pre-fill the question
                        st.session_state["supervisor_prefill"] = (
                            f"Based on this Genie finding, go deeper: {msg['content'][:300]}"
                        )
                        st.rerun()

    # Chat input
    user_input = st.chat_input("Ask a question...", key="genie_chat_input")
    if user_input:
        st.session_state.genie_chat_history.append({"role": "user", "content": user_input})
        with st.spinner(f"Querying {genie_choice}..."):
            result = ask_genie(space_id, user_input)
        st.session_state.genie_chat_history.append({
            "role": "assistant",
            "content": result.get("answer_text", ""),
            "sql_query": result.get("sql_query"),
            "result_df": result.get("result_df"),
            "error": result.get("error"),
        })
        st.rerun()


# ===================================================================
# TAB 2: Supervisor Agent
# ===================================================================
def render_supervisor_tab():
    st.header("Supervisor Agent")
    st.markdown(
        "Multi-Genie orchestration with conversational memory. "
        "The Supervisor plans queries across both Genies and synthesizes a comprehensive report."
    )

    agent = SupervisorAgent()

    # Memory indicator
    memory_context = agent.build_memory_context(USER_EMAIL)
    if memory_context:
        try:
            session_count = memory_context.count("### Session:")
        except Exception:
            session_count = 0
        st.info(f"Memory: **{session_count}** prior session(s) available")
        with st.expander("View memory context"):
            st.markdown(memory_context[:2000])
    else:
        st.caption("Memory: No prior sessions found. Start a conversation to build memory.")

    # Example questions
    st.markdown("**Example questions:**")
    examples = [
        "Where are our biggest revenue opportunities from under-forecasting?",
        "Which customers should I prioritize this week based on forecast accuracy and inventory risk?",
        "How did ai_forecast() perform vs actuals for Electronics last quarter?",
        "Compare Northeast vs West region -- demand accuracy, inventory coverage, and revenue gap",
        "Based on what we discussed last time, how has the situation changed?",
    ]

    example_cols = st.columns(3)
    for i, ex in enumerate(examples):
        with example_cols[i % 3]:
            if st.button(ex, key=f"supervisor_ex_{i}", use_container_width=True):
                _run_supervisor(agent, ex)
                st.rerun()

    st.divider()

    # Display chat history
    for entry in st.session_state.supervisor_chat_history:
        if entry["role"] == "user":
            with st.chat_message("user"):
                st.markdown(entry["content"])
        else:
            with st.chat_message("assistant"):
                # Plan display
                if entry.get("plan"):
                    with st.expander("Execution Plan", expanded=False):
                        for step in entry["plan"]:
                            genie_label = "Demand Genie" if step.get("genie") == "demand" else "Inventory Genie"
                            st.markdown(
                                f"**Step {step.get('step', '?')}** - {genie_label}\n\n"
                                f"> {step.get('query', '')}"
                            )

                # Step results
                if entry.get("results"):
                    with st.expander("Genie Query Results", expanded=False):
                        for r in entry["results"]:
                            genie_label = "Demand Genie" if r.get("genie") == "demand" else "Inventory Genie"
                            st.markdown(f"**Step {r.get('step', '?')} - {genie_label}:** {r.get('query', '')}")
                            if r.get("error"):
                                st.error(r["error"])
                            else:
                                st.markdown(r.get("answer_text", ""))
                                if r.get("result_df") is not None and not r["result_df"].empty:
                                    df = r["result_df"]
                                    acc_cols = [c for c in df.columns if "accuracy" in c.lower() or "pct" in c.lower()]
                                    if acc_cols:
                                        for col in acc_cols:
                                            df[col] = pd.to_numeric(df[col], errors="coerce")
                                        st.dataframe(style_accuracy_df(df), use_container_width=True, hide_index=True)
                                    else:
                                        st.dataframe(df, use_container_width=True, hide_index=True)
                            st.divider()

                # Report — interleaves text and any LLM-generated Vega-Lite charts
                if entry.get("report"):
                    render_in_streamlit(entry["report"], highlight_exec_summary=True)

                # Alert evaluation indicator
                alert_eval = entry.get("alert_eval")
                if alert_eval:
                    breached = alert_eval.get("breached", False)
                    severity = alert_eval.get("severity", "low")
                    summary = alert_eval.get("summary", "")
                    if breached:
                        severity_colors = {"critical": "error", "high": "warning", "medium": "warning", "low": "info"}
                        getattr(st, severity_colors.get(severity, "warning"))(
                            f"**Alert: {severity.upper()}** — {summary}"
                        )
                    else:
                        st.success(f"**No Alert** — {summary}")

                # Metrics bar + PDF download
                metrics_col1, metrics_col2, metrics_col3, metrics_col4 = st.columns([1, 1, 1, 1])
                with metrics_col1:
                    st.metric("Genie Calls", entry.get("genie_calls", 0))
                with metrics_col2:
                    st.metric("Execution Time", f"{entry.get('execution_time', 0):.1f}s")
                with metrics_col3:
                    st.metric("Memory Used", "Yes" if entry.get("memory_used") else "No")
                with metrics_col4:
                    # Generate PDF on demand
                    raw_report = entry.get("report", "")
                    if raw_report and st.button("Export PDF", key=f"pdf_{idx}"):
                        pdf_title = "Supervisor Agent Report"
                        pdf = html_to_pdf("", report_md=raw_report, title=pdf_title)
                        if pdf:
                            fname = make_pdf_filename(pdf_title)
                            save_to_volume(pdf, pdf_title)
                            st.download_button(
                                "Download PDF",
                                data=pdf,
                                file_name=fname,
                                mime="application/pdf",
                                key=f"dl_pdf_{idx}",
                            )
                        else:
                            st.warning("PDF generation unavailable")

    # Check for prefill from Genie tab
    prefill = st.session_state.pop("supervisor_prefill", None)

    # Chat input
    user_input = st.chat_input("Ask the Supervisor Agent...", key="supervisor_chat_input")
    question = user_input or prefill
    if question:
        _run_supervisor(agent, question)
        st.rerun()


def _run_supervisor(agent: SupervisorAgent, question: str):
    """Execute the supervisor pipeline and store results in session state."""
    st.session_state.supervisor_chat_history.append({"role": "user", "content": question})

    with st.status("Supervisor Agent is working...", expanded=True) as status:
        def progress_callback(step_num, genie_name, query, step_status):
            label = "Demand Genie" if genie_name == "demand" else "Inventory Genie"
            icon = {"running": "...", "complete": "Done", "error": "Error"}.get(step_status, "")
            st.write(f"Step {step_num}: {label} - {query[:80]}... [{icon}]")

        result = agent.run(
            question=question,
            user_email=USER_EMAIL,
            session_id=st.session_state.supervisor_session_id,
            progress_callback=progress_callback,
        )
        status.update(label="Supervisor Agent complete", state="complete")

    st.session_state.supervisor_session_id = result.get("session_id")
    st.session_state.supervisor_chat_history.append({
        "role": "assistant",
        "content": result.get("report", ""),
        "report": result.get("report", ""),
        "plan": result.get("plan", []),
        "results": result.get("results", []),
        "memory_used": result.get("memory_used", False),
        "genie_calls": result.get("genie_calls", 0),
        "execution_time": result.get("execution_time", 0),
        "alert_breached": result.get("alert_breached", False),
        "alert_eval": result.get("alert_eval"),
    })


# ===================================================================
# TAB 3: Schedules & Alerts
# ===================================================================
def render_schedules_tab():
    st.header("Schedules & Alerts")

    st.markdown(
        "Both **Scheduled Reports** and **Anomaly Alerts** use the Supervisor Agent and Genie pipeline "
        "to gather data on a cron schedule. The difference is what happens with the results:"
    )
    st.markdown(
        "- **Scheduled Report** — always generates a PDF and emails it to recipients.\n"
        "- **Anomaly Alert** — evaluates the results against a threshold and only notifies if the condition is breached. "
        "Includes cooldown to prevent repeated alerts."
    )

    if not LAKEBASE_AVAILABLE:
        st.info("Schedules require the Lakebase operational database. "
                "The connection is currently unavailable — schedules will be stored once connectivity is restored.")

    schedule_mode = st.radio(
        "Type",
        ["Scheduled Report", "Anomaly Alert"],
        horizontal=True,
        key="schedule_mode_radio",
    )

    sched_tab1, sched_tab2, sched_tab3, sched_tab4 = st.tabs(["Create New", "My Schedules", "Report Library", "Genie Spaces"])

    # ---- Alert templates (used by create + edit) ----
    ALERT_TEMPLATES = {
        "Forecast Accuracy Drop": {
            "description": "Fires when any customer-SKU forecast accuracy drops below the threshold.",
            "question": "Show me all customer-SKU combinations where forecast accuracy is below {threshold}% this week",
            "default_threshold": 70,
            "threshold_unit": "%",
            "metric": "accuracy_pct",
        },
        "Revenue Opportunity Spike": {
            "description": "Fires when the weekly revenue gap from under-forecasting exceeds the threshold.",
            "question": "What is the total weekly revenue gap from under-forecasting, broken down by customer? Flag any over ${threshold}",
            "default_threshold": 50000,
            "threshold_unit": "$",
            "metric": "revenue_gap",
        },
        "Stockout Risk Critical": {
            "description": "Fires when any SKU's days of supply drops below the threshold.",
            "question": "Which SKUs have fewer than {threshold} days of supply remaining? Include warehouse and current inventory",
            "default_threshold": 5,
            "threshold_unit": "days",
            "metric": "days_of_supply",
        },
        "Confidence Interval Degradation": {
            "description": "Fires when the AI forecast model confidence drops below the threshold.",
            "question": "Show forecast confidence intervals for all models. Flag any with confidence below {threshold}%",
            "default_threshold": 80,
            "threshold_unit": "%",
            "metric": "confidence_pct",
        },
    }

    # ---- Create New ----
    with sched_tab1:
        if schedule_mode == "Scheduled Report":
            st.subheader("Create a New Scheduled Report")
            with st.form("new_schedule_form"):
                schedule_name = st.text_input("Schedule Name", placeholder="Weekly Accuracy Report")
                report_question = st.text_area(
                    "Report Question",
                    placeholder="What is the forecast accuracy by customer for Electronics this quarter?",
                    height=100,
                )

                col_cron, col_preset = st.columns([2, 1])
                with col_preset:
                    preset = st.selectbox(
                        "Preset",
                        [
                            "Custom",
                            "Daily 6am",
                            "Weekly Monday 6am",
                            "Weekly Friday 5pm",
                            "Bi-weekly Monday 6am",
                            "Monthly 1st 6am",
                        ],
                    )
                    preset_map = {
                        "Daily 6am": "0 6 * * *",
                        "Weekly Monday 6am": "0 6 * * 1",
                        "Weekly Friday 5pm": "0 17 * * 5",
                        "Bi-weekly Monday 6am": "0 6 1-7,15-21 * 1",
                        "Monthly 1st 6am": "0 6 1 * *",
                    }
                with col_cron:
                    default_cron = preset_map.get(preset, "0 6 * * 1")
                    cron_expression = st.text_input("Cron Expression", value=default_cron)

                recipients_input = st.text_input(
                    "Recipients (comma-separated emails)",
                    placeholder="manager@example.com, analyst@example.com",
                )

                # Genie space multiselect
                all_genie_names = [g["display_name"] for g in GENIES]
                selected_genies = st.multiselect(
                    "Genie Spaces",
                    options=all_genie_names,
                    default=all_genie_names,
                    help="Select which Genie Spaces the agent can query. Defaults to all.",
                    key="sched_genie_multiselect",
                )

                btn_col1, btn_col2 = st.columns(2)
                with btn_col1:
                    submitted = st.form_submit_button("Create Schedule", type="primary", use_container_width=True)
                with btn_col2:
                    preview = st.form_submit_button("Preview Report", use_container_width=True)

                if preview:
                    if not report_question:
                        st.error("Please provide a report question to preview.")
                    else:
                        # Build genies list from selection
                        _genie_name_map = {g["display_name"]: g for g in GENIES}
                        preview_genies = [_genie_name_map[n] for n in selected_genies if n in _genie_name_map] or GENIES
                        agent = SupervisorAgent(genies=preview_genies)
                        with st.status("Running preview...", expanded=True) as status:
                            def preview_progress(step_num, genie_name, query, step_status):
                                label = "Demand Genie" if genie_name == "demand" else "Inventory Genie"
                                icon = {"running": "...", "complete": "Done", "error": "Error"}.get(step_status, "")
                                st.write(f"Step {step_num}: {label} - {query[:80]}... [{icon}]")

                            result = agent.run(
                                question=report_question,
                                user_email=USER_EMAIL,
                                progress_callback=preview_progress,
                            )
                            status.update(label="Preview complete", state="complete")

                        preview_recipients = [r.strip() for r in recipients_input.split(",") if r.strip()] if recipients_input else []
                        st.session_state["schedule_preview"] = {
                            "report": result.get("report", ""),
                            "genie_calls": result.get("genie_calls", 0),
                            "execution_time": result.get("execution_time", 0),
                            "title": schedule_name or "Report Preview",
                            "recipients": preview_recipients,
                        }

                if submitted:
                    if not schedule_name or not report_question:
                        st.error("Please provide a schedule name and report question.")
                    else:
                        recipients = [r.strip() for r in recipients_input.split(",") if r.strip()] if recipients_input else []
                        # Resolve selected genie space IDs (NULL = all)
                        _genie_name_map = {g["display_name"]: g for g in GENIES}
                        _sel_genies = [_genie_name_map[n] for n in selected_genies if n in _genie_name_map]
                        genie_ids = [g["space_id"] for g in _sel_genies] if len(_sel_genies) < len(GENIES) else None
                        try:
                            schedule_id = execute_insert(
                                """
                                INSERT INTO bi_report_schedules
                                    (schedule_id, report_name, schedule_type, cron_expression, report_question,
                                     recipients, genie_space_ids, is_active, created_by)
                                VALUES (%s, %s, 'report', %s, %s, %s, %s, TRUE, %s)
                                RETURNING schedule_id
                                """,
                                (str(uuid.uuid4()), schedule_name, cron_expression, report_question, recipients, genie_ids, USER_EMAIL),
                            )
                            if schedule_id:
                                st.success(f"Schedule **{schedule_name}** created successfully.")
                                st.session_state.schedule_refresh += 1
                            else:
                                st.warning("Schedule created but could not confirm (Lakebase may be unavailable).")
                        except Exception as e:
                            st.error(f"Failed to create schedule: {e}")

            # Render preview outside the form (st.download_button can't be inside a form)
            preview_data = st.session_state.pop("schedule_preview", None)
            if preview_data:
                report = preview_data["report"]
                st.markdown("---")
                st.markdown("### Preview Result")
                render_in_streamlit(report)

                pdf_title = preview_data["title"]
                pdf = html_to_pdf("", report_md=report, title=pdf_title)
                fname = make_pdf_filename(pdf_title) if pdf else None

                if pdf:
                    save_to_volume(pdf, pdf_title)

                preview_recipients = preview_data.get("recipients", [])
                email_sent = False
                if preview_recipients and pdf:
                    html_report = markdown_to_html(report, title=pdf_title)
                    date_str = datetime.utcnow().strftime("%Y-%m-%d")
                    email_sent = send_email(
                        recipients=preview_recipients,
                        subject=f"[Preview] {pdf_title} -- {date_str}",
                        html_body=html_report,
                        pdf_bytes=pdf,
                        pdf_filename=fname,
                    )

                mcol1, mcol2, mcol3, mcol4 = st.columns(4)
                with mcol1:
                    st.metric("Genie Calls", preview_data["genie_calls"])
                with mcol2:
                    st.metric("Execution Time", f"{preview_data['execution_time']:.1f}s")
                with mcol3:
                    if pdf:
                        st.download_button(
                            "Download PDF",
                            data=pdf,
                            file_name=fname,
                            mime="application/pdf",
                        )
                    else:
                        st.caption("PDF export unavailable")
                with mcol4:
                    if preview_recipients:
                        if email_sent:
                            st.success(f"Email sent to {', '.join(preview_recipients)}")
                        else:
                            st.warning("Email failed — check SMTP config")
                    else:
                        st.caption("No recipients — email skipped")

        else:
            # ---- Create Alert ----
            st.subheader("Create a New Anomaly Alert")

            template_name = st.selectbox(
                "Alert Template",
                list(ALERT_TEMPLATES.keys()),
            )
            template = ALERT_TEMPLATES[template_name]

            st.info(f"**{template_name}:** {template['description']}")

            with st.form("new_alert_form"):
                alert_name = st.text_input("Alert Name", value=f"{template_name} Alert")

                col1, col2 = st.columns(2)
                with col1:
                    threshold_value = st.number_input(
                        f"Threshold ({template['threshold_unit']})",
                        value=template["default_threshold"],
                        step=1,
                    )
                with col2:
                    cooldown_days = st.number_input(
                        "Cooldown (days between alerts)",
                        value=1,
                        min_value=0,
                        max_value=30,
                    )

                scope_filter_input = st.text_input(
                    "Scope Filter (optional, e.g., region=Northeast, customer=Walmart)",
                    placeholder="region=Northeast",
                )

                cron_preset = st.selectbox(
                    "Check Frequency",
                    ["Every 6 hours", "Daily 6am", "Every 12 hours", "Weekly Monday 6am"],
                    key="alert_cron_preset",
                )
                alert_cron_map = {
                    "Every 6 hours": "0 */6 * * *",
                    "Daily 6am": "0 6 * * *",
                    "Every 12 hours": "0 */12 * * *",
                    "Weekly Monday 6am": "0 6 * * 1",
                }

                recipients_input = st.text_input(
                    "Recipients (comma-separated emails)",
                    placeholder="manager@example.com",
                    key="alert_recipients",
                )

                # Genie space multiselect for alerts
                all_genie_names_alert = [g["display_name"] for g in GENIES]
                selected_genies_alert = st.multiselect(
                    "Genie Spaces",
                    options=all_genie_names_alert,
                    default=all_genie_names_alert,
                    help="Select which Genie Spaces the agent can query.",
                    key="alert_genie_multiselect",
                )

                submitted = st.form_submit_button("Create Alert", type="primary", use_container_width=True)
                if submitted:
                    if not alert_name:
                        st.error("Please provide an alert name.")
                    else:
                        scope_filter = {}
                        if scope_filter_input:
                            for pair in scope_filter_input.split(","):
                                pair = pair.strip()
                                if "=" in pair:
                                    k, v = pair.split("=", 1)
                                    scope_filter[k.strip()] = v.strip()

                        threshold_json = {
                            "metric": template["metric"],
                            "value": threshold_value,
                            "unit": template["threshold_unit"],
                            "operator": "less_than" if template["metric"] in ("accuracy_pct", "days_of_supply", "confidence_pct") else "greater_than",
                        }

                        recipients = [r.strip() for r in recipients_input.split(",") if r.strip()] if recipients_input else []
                        question = template["question"].replace("{threshold}", str(threshold_value))

                        # Resolve selected genie space IDs (NULL = all)
                        _alert_genie_map = {g["display_name"]: g for g in GENIES}
                        _alert_sel = [_alert_genie_map[n] for n in selected_genies_alert if n in _alert_genie_map]
                        alert_genie_ids = [g["space_id"] for g in _alert_sel] if len(_alert_sel) < len(GENIES) else None

                        try:
                            alert_id = execute_insert(
                                """
                                INSERT INTO bi_report_schedules
                                    (schedule_id, report_name, schedule_type, cron_expression, report_question,
                                     alert_template, alert_threshold, alert_scope_json, recipients,
                                     alert_cooldown_days, genie_space_ids, is_active, created_by)
                                VALUES (%s, %s, 'alert', %s, %s, %s, %s, %s, %s, %s, %s, TRUE, %s)
                                RETURNING schedule_id
                                """,
                                (
                                    str(uuid.uuid4()),
                                    alert_name,
                                    alert_cron_map.get(cron_preset, "0 6 * * *"),
                                    question,
                                    template_name,
                                    threshold_value,
                                    json.dumps(scope_filter) if scope_filter else None,
                                    recipients,
                                    cooldown_days,
                                    alert_genie_ids,
                                    USER_EMAIL,
                                ),
                            )
                            if alert_id:
                                st.success(f"Alert **{alert_name}** created successfully.")
                                st.session_state.alert_refresh += 1
                            else:
                                st.warning("Alert created but could not confirm (Lakebase may be unavailable).")
                        except Exception as e:
                            st.error(f"Failed to create alert: {e}")

    # ---- My Schedules (unified list of reports + alerts) ----
    with sched_tab2:
        st.subheader("My Schedules")
        schedules_df = execute_query(
            """
            SELECT schedule_id, report_name, schedule_type, report_type, cron_expression,
                   report_question, is_active, created_at, updated_at, recipients,
                   alert_template, alert_threshold, alert_scope_json,
                   alert_cooldown_days, last_alert_sent_at, genie_space_ids
            FROM bi_report_schedules
            WHERE created_by = %s
            ORDER BY created_at DESC
            """,
            (USER_EMAIL,),
        )

        if schedules_df.empty:
            st.info("No schedules found. Create one in the 'Create New' tab.")
        else:
            for _, row in schedules_df.iterrows():
                sid = str(row["schedule_id"])
                sched_type = row.get("schedule_type", "report")
                is_alert = sched_type == "alert"
                type_label = "Alert" if is_alert else "Report"

                with st.container(border=True):
                    col1, col2, col3 = st.columns([3, 2, 1])
                    with col1:
                        status_icon = "🔵" if row.get("is_active") else "---"
                        st.markdown(f"**{status_icon} {row.get('report_name', 'Unnamed')}**")
                        st.caption(f"Type: {type_label} | Cron: `{row.get('cron_expression', '')}`")
                        if is_alert:
                            st.caption(f"Template: {row.get('alert_template', 'Custom')}")
                    with col2:
                        st.caption(f"Created: {str(row.get('created_at', ''))[:16]}")
                        last_update = row.get("updated_at")
                        st.caption(f"Last updated: {str(last_update)[:16] if last_update else 'Never'}")
                        if is_alert:
                            threshold = row.get("alert_threshold")
                            if threshold is not None:
                                st.caption(f"Threshold: {threshold} | Cooldown: {row.get('alert_cooldown_days', 1)} day(s)")
                            last_alert = row.get("last_alert_sent_at")
                            if last_alert:
                                st.caption(f"Last alert sent: {str(last_alert)[:16]}")
                            scope = row.get("alert_scope_json")
                            if scope:
                                if isinstance(scope, str):
                                    try:
                                        scope = json.loads(scope)
                                    except Exception:
                                        pass
                                if isinstance(scope, dict) and scope:
                                    st.caption(f"Scope: {', '.join(f'{k}={v}' for k, v in scope.items())}")
                    with col3:
                        is_active = row.get("is_active", True)
                        new_state = st.toggle(
                            "Active",
                            value=bool(is_active),
                            key=f"sched_toggle_{sid}",
                        )
                        if new_state != is_active:
                            execute_insert(
                                "UPDATE bi_report_schedules SET is_active = %s, updated_at = now() WHERE schedule_id = %s",
                                (new_state, sid),
                            )
                            st.rerun()

                    # Run Now button
                    run_now_key = f"run_now_{sid}"
                    if st.button("Run Now", key=run_now_key, use_container_width=True):
                        st.session_state[f"run_now_trigger_{sid}"] = True
                        st.rerun()

                    if st.session_state.pop(f"run_now_trigger_{sid}", False):
                        report_type = row.get("report_type", "qa") if "report_type" in row.index else "qa"
                        with st.status(f"Running **{row.get('report_name', 'Unnamed')}**...", expanded=True) as run_status:
                            try:
                                if sched_type == "proactive" or report_type == "proactive":
                                    from report_runner import run_proactive_schedule
                                    result = run_proactive_schedule(sid)
                                else:
                                    from report_runner import run_schedule
                                    result = run_schedule(sid)
                                status_str = result.get("status", "unknown")
                                if status_str == "success":
                                    run_status.update(label="Run complete", state="complete")
                                    if is_alert:
                                        breached = result.get("alert_breached", False)
                                        if breached:
                                            st.warning(f"Alert BREACHED. {result.get('alert_summary', '')}")
                                        else:
                                            st.success("Alert checked — no breach detected.")
                                    else:
                                        st.success(f"Report generated and sent. Run ID: {result.get('run_id', 'N/A')}")
                                elif status_str == "no_breach":
                                    run_status.update(label="Run complete", state="complete")
                                    st.success("Alert checked — no breach detected.")
                                elif status_str == "cooldown":
                                    run_status.update(label="Skipped — cooldown", state="complete")
                                    st.info("Alert is in cooldown period — skipped.")
                                else:
                                    run_status.update(label="Run finished with issues", state="error")
                                    st.warning(f"Status: {status_str}. {result.get('error', '')}")
                            except Exception as e:
                                run_status.update(label="Run failed", state="error")
                                st.error(f"Run failed: {e}")

                    # For alerts: show last breach info
                    if is_alert:
                        last_breach = execute_query(
                            """
                            SELECT alert_breached, alert_breach_value, run_started_at
                            FROM bi_report_audit_log
                            WHERE schedule_id = %s
                            ORDER BY run_started_at DESC
                            LIMIT 1
                            """,
                            (sid,),
                        )
                        if not last_breach.empty:
                            breach_row = last_breach.iloc[0]
                            with st.expander("Last Check"):
                                if breach_row.get("alert_breached"):
                                    st.warning(f"BREACHED: {breach_row.get('alert_breach_value', 'N/A')} at {str(breach_row.get('run_started_at', ''))[:16]}")
                                else:
                                    st.success(f"OK at {str(breach_row.get('run_started_at', ''))[:16]}")

                    with st.expander("Edit / Details"):
                        with st.form(f"edit_sched_{sid}"):
                            edit_name = st.text_input(
                                "Name",
                                value=row.get("report_name", ""),
                                key=f"edit_name_{sid}",
                            )
                            edit_question = st.text_area(
                                "Question",
                                value=row.get("report_question", ""),
                                key=f"edit_question_{sid}",
                                height=100,
                            )

                            ecol1, ecol2 = st.columns(2)
                            with ecol1:
                                edit_cron = st.text_input(
                                    "Cron Expression",
                                    value=row.get("cron_expression", ""),
                                    key=f"edit_cron_{sid}",
                                )
                            with ecol2:
                                current_recipients = row.get("recipients", [])
                                if isinstance(current_recipients, list):
                                    current_recipients_str = ", ".join(current_recipients)
                                else:
                                    current_recipients_str = current_recipients or ""
                                edit_recipients = st.text_input(
                                    "Recipients (comma-separated)",
                                    value=current_recipients_str,
                                    key=f"edit_recipients_{sid}",
                                )

                            # Alert-specific edit fields
                            if is_alert:
                                acol1, acol2 = st.columns(2)
                                with acol1:
                                    edit_threshold = st.number_input(
                                        "Threshold",
                                        value=float(row.get("alert_threshold", 0) or 0),
                                        key=f"edit_athreshold_{sid}",
                                    )
                                with acol2:
                                    edit_cooldown = st.number_input(
                                        "Cooldown (days)",
                                        value=int(row.get("alert_cooldown_days", 1) or 1),
                                        min_value=0,
                                        max_value=30,
                                        key=f"edit_acooldown_{sid}",
                                    )

                            # Genie space multiselect
                            _all_genie_display = [g["display_name"] for g in GENIES]
                            _genie_id_to_display = {g["space_id"]: g["display_name"] for g in GENIES}
                            _current_genie_ids = row.get("genie_space_ids")
                            if _current_genie_ids and isinstance(_current_genie_ids, list):
                                _current_genie_display = [_genie_id_to_display.get(gid, gid) for gid in _current_genie_ids if gid in _genie_id_to_display]
                            else:
                                _current_genie_display = _all_genie_display
                            edit_genies = st.multiselect(
                                "Genie Spaces",
                                options=_all_genie_display,
                                default=_current_genie_display,
                                key=f"edit_genies_{sid}",
                            )

                            btn_col1, btn_col2 = st.columns(2)
                            with btn_col1:
                                save_btn = st.form_submit_button("Save Changes", type="primary", use_container_width=True)
                            with btn_col2:
                                delete_btn = st.form_submit_button("Delete", use_container_width=True)

                            if save_btn:
                                new_recipients = [r.strip() for r in edit_recipients.split(",") if r.strip()] if edit_recipients else []
                                # Resolve genie IDs from display names
                                _edit_genie_display_map = {g["display_name"]: g["space_id"] for g in GENIES}
                                _edit_genie_ids = [_edit_genie_display_map[n] for n in edit_genies if n in _edit_genie_display_map]
                                _edit_genie_ids_val = _edit_genie_ids if len(_edit_genie_ids) < len(GENIES) else None
                                if is_alert:
                                    execute_insert(
                                        """
                                        UPDATE bi_report_schedules
                                        SET report_name = %s, report_question = %s, cron_expression = %s,
                                            recipients = %s, alert_threshold = %s, alert_cooldown_days = %s,
                                            genie_space_ids = %s, updated_at = now()
                                        WHERE schedule_id = %s
                                        """,
                                        (edit_name, edit_question, edit_cron, new_recipients,
                                         edit_threshold, edit_cooldown, _edit_genie_ids_val, sid),
                                    )
                                else:
                                    execute_insert(
                                        """
                                        UPDATE bi_report_schedules
                                        SET report_name = %s, report_question = %s, cron_expression = %s,
                                            recipients = %s, genie_space_ids = %s, updated_at = now()
                                        WHERE schedule_id = %s
                                        """,
                                        (edit_name, edit_question, edit_cron, new_recipients, _edit_genie_ids_val, sid),
                                    )
                                st.success("Updated.")
                                st.rerun()

                            if delete_btn:
                                execute_insert(
                                    "DELETE FROM bi_report_audit_log WHERE schedule_id = %s",
                                    (sid,),
                                )
                                execute_insert(
                                    "DELETE FROM bi_report_schedules WHERE schedule_id = %s",
                                    (sid,),
                                )
                                st.success("Deleted.")
                                st.rerun()

    # ---- Report Library ----
    with sched_tab3:
        st.subheader("Report Library")
        reports_df = execute_query(
            """
            SELECT ral.run_id, ral.status, ral.run_started_at,
                   ral.alert_breached, ral.report_volume_path,
                   rs.report_name
            FROM bi_report_audit_log ral
            LEFT JOIN bi_report_schedules rs ON ral.schedule_id = rs.schedule_id
            ORDER BY ral.run_started_at DESC
            LIMIT 50
            """,
        )

        if reports_df.empty:
            st.info("No reports generated yet. Run a Supervisor Agent query to generate your first report.")
        else:
            for _, row in reports_df.iterrows():
                with st.container(border=True):
                    col1, col2, col3 = st.columns([3, 2, 1])
                    with col1:
                        sched_name = row.get("report_name", "")
                        label = sched_name if sched_name else "Manual Run"
                        st.markdown(f"**{label}**")
                    with col2:
                        st.caption(f"Run: {str(row.get('run_started_at', ''))[:16]}")
                    with col3:
                        status = row.get("status", "unknown")
                        if status == "success":
                            st.success("Success")
                        elif status == "error":
                            st.error("Error")
                        else:
                            st.warning(status)


    # ---- Genie Spaces admin ----
    with sched_tab4:
        st.subheader("Genie Spaces")
        st.markdown(
            "Manage which Genie Spaces are available for reports, alerts, and the interactive agent. "
            "Spaces are auto-discovered from the workspace on app startup. Toggle **Active** to include or exclude a space."
        )

        if st.button("Refresh from Workspace", key="resync_genies"):
            n = sync_genie_spaces_from_workspace()
            st.success(f"Synced {n} Genie space(s) from workspace.")
            _load_genies.clear()
            st.rerun()

        all_genies_df = get_all_genies()
        if all_genies_df.empty:
            st.info("No Genie spaces found. Click **Refresh from Workspace** to discover spaces.")
        else:
            for _, grow in all_genies_df.iterrows():
                gsid = str(grow["space_id"])
                with st.container(border=True):
                    gc1, gc2, gc3 = st.columns([3, 4, 1])
                    with gc1:
                        active_icon = "Active" if grow.get("is_active") else "Inactive"
                        st.markdown(f"**{grow.get('display_name', grow.get('name', 'Unknown'))}**")
                        st.caption(f"Name: `{grow.get('name', '')}` | {active_icon}")
                    with gc2:
                        desc = grow.get("description", "")
                        st.caption(desc[:200] if desc else "No description")
                        st.caption(f"ID: `{gsid}`")
                    with gc3:
                        current_active = bool(grow.get("is_active", True))
                        new_active = st.toggle(
                            "Active",
                            value=current_active,
                            key=f"genie_active_{gsid}",
                        )
                        if new_active != current_active:
                            set_genie_active(gsid, new_active)
                            _load_genies.clear()
                            st.rerun()


# ===================================================================
# TAB 4: Monitoring
# ===================================================================
def render_monitoring_tab():
    st.header("Monitoring")

    if not LAKEBASE_AVAILABLE:
        st.info("Monitoring requires the Lakebase operational database. Connection is currently unavailable.")
        return

    # -- Filters --------------------------------------------------------
    filter_cols = st.columns([2, 1, 1])
    with filter_cols[0]:
        date_range = st.date_input(
            "Date Range",
            value=(date.today() - timedelta(days=30), date.today() + timedelta(days=1)),
            key="monitoring_date_range",
        )
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date = datetime.combine(date_range[0], datetime.min.time())
        end_date = datetime.combine(date_range[1], datetime.min.time())
    else:
        start_date = datetime.combine(date.today() - timedelta(days=30), datetime.min.time())
        end_date = datetime.combine(date.today() + timedelta(days=1), datetime.min.time())

    with filter_cols[1]:
        status_filter = st.multiselect(
            "Status Filter",
            options=["success", "failed", "cooldown", "no_breach"],
            default=["success", "failed", "cooldown", "no_breach"],
            key="monitoring_status_filter",
        )
    with filter_cols[2]:
        schedule_names_df = execute_query(
            "SELECT DISTINCT report_name FROM bi_report_schedules ORDER BY report_name"
        )
        schedule_options = ["All"] + (schedule_names_df["report_name"].tolist() if not schedule_names_df.empty else [])
        schedule_filter = st.selectbox("Schedule", options=schedule_options, key="monitoring_schedule_filter")

    st.divider()

    # ================================================================
    # SECTION 1: Run Health Overview
    # ================================================================
    st.subheader("Run Health Overview")
    st.caption("Source: `bi_report_audit_log` — scheduled reports and alerts")

    summary_df = get_run_health_summary(start_date, end_date)
    if summary_df.empty or summary_df.iloc[0]["total_runs"] == 0:
        st.info("No runs recorded in the selected period.")
    else:
        row = summary_df.iloc[0]
        kpi_cols = st.columns(4)
        kpi_cols[0].metric("Total Runs", int(row["total_runs"]))
        kpi_cols[1].metric("Success Rate", f"{row['success_rate']}%")
        kpi_cols[2].metric("Failed", int(row["failed_count"]))
        kpi_cols[3].metric("Avg Duration", f"{row['avg_duration_sec']}s" if row["avg_duration_sec"] else "N/A")

    # Run status timeline
    timeline_df = get_run_timeline(start_date, end_date)
    if not timeline_df.empty:
        pivot = timeline_df.pivot_table(
            index="run_date", columns="status", values="run_count", fill_value=0
        ).reset_index()
        pivot["run_date"] = pd.to_datetime(pivot["run_date"])
        y_cols = [c for c in pivot.columns if c != "run_date"]
        if y_cols:
            st.bar_chart(pivot, x="run_date", y=y_cols)

    st.divider()

    # ================================================================
    # SECTION 2: Alert Activity
    # ================================================================
    st.subheader("Alert Activity")
    st.caption("Source: `bi_report_audit_log` + `bi_report_schedules` (alert-type schedules only)")

    template_df = get_alert_summary_by_template(start_date, end_date)
    if template_df.empty:
        st.info("No alert checks recorded in the selected period.")
    else:
        card_cols = st.columns(min(len(template_df), 4))
        for i, (_, trow) in enumerate(template_df.iterrows()):
            with card_cols[i % len(card_cols)]:
                with st.container(border=True):
                    st.markdown(f"**{trow['alert_template']}**")
                    st.metric("Breaches", int(trow["breaches"]))
                    st.caption(f"Checks: {int(trow['total_checks'])} · Cooldowns: {int(trow['cooldowns'])}")
                    if trow["last_breach_at"]:
                        st.caption(f"Last breach: {str(trow['last_breach_at'])[:16]}")

    # Alert breach timeline
    alert_df = get_alert_activity(start_date, end_date)
    if not alert_df.empty:
        breached_df = alert_df[alert_df["alert_breached"] == True].copy()  # noqa: E712
        if not breached_df.empty:
            breached_df["date"] = pd.to_datetime(breached_df["run_started_at"]).dt.date
            alert_timeline = breached_df.groupby(["date", "alert_template"]).size().reset_index(name="count")
            pivot = alert_timeline.pivot_table(
                index="date", columns="alert_template", values="count", fill_value=0
            ).reset_index()
            pivot["date"] = pd.to_datetime(pivot["date"])
            y_cols = [c for c in pivot.columns if c != "date"]
            if y_cols:
                st.markdown("**Breached Alerts Over Time**")
                st.bar_chart(pivot, x="date", y=y_cols)
        else:
            st.success("No alerts breached in the selected period.")

    # Cooldown status
    with st.expander("Cooldown Status"):
        cooldown_df = execute_query(
            """
            SELECT report_name, alert_template, alert_cooldown_days,
                   last_alert_sent_at,
                   last_alert_sent_at + (alert_cooldown_days || ' days')::INTERVAL AS cooldown_until
            FROM bi_report_schedules
            WHERE schedule_type = 'alert' AND is_active = TRUE AND last_alert_sent_at IS NOT NULL
            ORDER BY cooldown_until DESC
            """
        )
        if cooldown_df.empty:
            st.info("No active cooldowns.")
        else:
            st.dataframe(cooldown_df, use_container_width=True, hide_index=True)

    st.divider()

    # ================================================================
    # SECTION 3: Proactive Agent Insights
    # ================================================================
    st.subheader("Proactive Agent Insights")
    st.caption("Source: `bi_agent_memory` — proactive agent runs")

    memory_df = get_agent_memory_summary(start_date, end_date)

    total_watching = 0
    total_resolved = 0
    all_watching_items = []
    all_resolved_items = []

    if not memory_df.empty:
        for _, mrow in memory_df.iterrows():
            watching = mrow.get("watching", [])
            resolved = mrow.get("resolved", [])
            if isinstance(watching, str):
                watching = json.loads(watching)
            if isinstance(resolved, str):
                resolved = json.loads(resolved)
            if not isinstance(watching, list):
                watching = []
            if not isinstance(resolved, list):
                resolved = []
            total_watching += len(watching)
            total_resolved += len(resolved)
            all_watching_items.extend(watching)
            all_resolved_items.extend(resolved)

    agent_kpi_cols = st.columns(3)
    agent_kpi_cols[0].metric("Watching", total_watching)
    agent_kpi_cols[1].metric("Resolved", total_resolved)
    agent_kpi_cols[2].metric("Agent Runs", len(memory_df))

    # Watching vs resolved trend
    if not memory_df.empty:
        trend_data = []
        for _, mrow in memory_df.iterrows():
            w = mrow.get("watching", [])
            r = mrow.get("resolved", [])
            if isinstance(w, str):
                w = json.loads(w)
            if isinstance(r, str):
                r = json.loads(r)
            if not isinstance(w, list):
                w = []
            if not isinstance(r, list):
                r = []
            trend_data.append({
                "date": pd.to_datetime(mrow["created_at"]).date(),
                "watching": len(w),
                "resolved": len(r),
            })
        if trend_data:
            trend_df = pd.DataFrame(trend_data).groupby("date").sum().reset_index()
            trend_df["date"] = pd.to_datetime(trend_df["date"])
            st.line_chart(trend_df, x="date", y=["watching", "resolved"])

    # Escalating items
    with st.expander("Escalating Items", expanded=True):
        high_items = [
            i for i in all_watching_items
            if isinstance(i, dict) and i.get("severity", "").lower() in ("critical", "high")
        ]
        if high_items:
            for item in high_items[:10]:
                severity = item.get("severity", "unknown")
                color = "#dc3545" if severity.lower() == "critical" else "#ffc107"
                st.markdown(
                    f'<div style="border-left: 4px solid {color}; padding: 0.5rem 1rem; '
                    f'margin-bottom: 0.5rem; background: #f9f9f9; border-radius: 0 4px 4px 0;">'
                    f'<strong>{item.get("topic", "Unknown")}</strong> '
                    f'<span style="color:{color}; font-weight:bold;">({severity})</span><br/>'
                    f'Trend: {item.get("trend", "N/A")} · SKU: {item.get("sku", "N/A")}</div>',
                    unsafe_allow_html=True,
                )
        else:
            st.success("No critical or high severity items being watched.")

    # Recent resolutions
    with st.expander("Recent Resolutions"):
        if all_resolved_items:
            resolved_display = pd.DataFrame(all_resolved_items[:20])
            st.dataframe(resolved_display, use_container_width=True, hide_index=True)
        else:
            st.info("No resolved items in the selected period.")

    st.divider()

    # ================================================================
    # SECTION 4: Schedule Performance
    # ================================================================
    st.subheader("Schedule Performance")
    st.caption("Source: `bi_report_schedules` + `bi_report_audit_log`")

    perf_df = get_schedule_performance(start_date, end_date)
    if perf_df.empty:
        st.info("No schedules found.")
    else:
        perf_df["success_rate_pct"] = (
            100 * perf_df["successes"] / perf_df["total_runs"].replace(0, 1)
        ).round(1)
        display_cols = [
            "report_name", "schedule_type", "is_active", "total_runs",
            "successes", "failures", "success_rate_pct", "avg_duration_sec", "last_run_at",
        ]
        available_cols = [c for c in display_cols if c in perf_df.columns]
        st.dataframe(
            style_accuracy_df(perf_df[available_cols]),
            use_container_width=True,
            hide_index=True,
        )

    # Filtered report library
    with st.expander("Report Library (Filtered)"):
        library_df = execute_query(
            """
            SELECT ral.run_id, ral.status, ral.run_started_at, ral.run_completed_at,
                   ral.alert_breached, ral.report_volume_path, ral.error_message,
                   rs.report_name, rs.schedule_type
            FROM bi_report_audit_log ral
            LEFT JOIN bi_report_schedules rs ON ral.schedule_id = rs.schedule_id
            WHERE ral.run_started_at >= %s AND ral.run_started_at < %s
            ORDER BY ral.run_started_at DESC
            LIMIT 100
            """,
            (start_date, end_date),
        )
        if not library_df.empty:
            if status_filter:
                library_df = library_df[library_df["status"].isin(status_filter)]
            if schedule_filter and schedule_filter != "All":
                library_df = library_df[library_df["report_name"] == schedule_filter]

        if library_df.empty:
            st.info("No reports match the current filters.")
        else:
            st.dataframe(library_df, use_container_width=True, hide_index=True)


# ===================================================================
# TAB 5: Data Pipeline
# ===================================================================
def render_pipeline_tab():
    st.header("Data Pipeline")
    st.markdown("How raw data flows through the Databricks Lakehouse to produce governed, AI-enriched analytics for brand managers.")

    MERMAID_CSS = """
    <style>
    .mermaid-container { overflow-x: auto; padding: 10px 0; }
    .mermaid-container svg { max-width: 100%; height: auto; }
    </style>
    """
    MERMAID_INIT = """
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({startOnLoad:true, theme:'base', themeVariables:{
        primaryColor:'#1b3a5c', primaryTextColor:'#fff', primaryBorderColor:'#2a5f9e',
        lineColor:'#555', secondaryColor:'#f0f2f6', tertiaryColor:'#e8ecf1',
        fontSize:'14px'
    }});</script>
    """

    # --- Medallion Architecture ---
    st.subheader("Medallion Architecture")
    PIPELINE_MERMAID = f"""
    <div class="mermaid-container">
    {MERMAID_INIT}
    <pre class="mermaid" style="background:transparent;">
    flowchart LR
        SRC["fa:fa-database Raw Sources<br/><i>Transactions, Inventory,<br/>SKU Catalogs</i>"]

        subgraph BRONZE["Bronze"]
            direction TB
            B1["Streaming Ingestion<br/><i>Auto Loader</i>"]
            B2["Schema Evolution<br/>&amp; Validation"]
        end

        subgraph SILVER["Silver — Cleaned &amp; Enriched"]
            direction TB
            S1["AI-Powered SKU Resolution<br/><i>ai_similarity&#40;&#41;</i>"]
            S2["Cleaned Sales Actuals"]
            S3["Inventory with Risk Levels"]
            S4["Data Quality Quarantine"]
        end

        subgraph GOLD["Gold — Analytics Ready"]
            direction TB
            F1["AI Demand Forecasts<br/><i>ai_forecast&#40;&#41;</i>"]
            MV["Governed Metric Views<br/><i>Demand, Revenue, Seasonal,<br/>Inventory Risk</i>"]
        end

        subgraph SERVE["Serving"]
            DG["Demand Forecast<br/>Genie Space"]
            IG["Inventory &amp; Channel<br/>Genie Space"]
        end

        SRC --> BRONZE --> SILVER --> GOLD --> SERVE
    </pre>
    </div>
    {MERMAID_CSS}
    """
    st.components.v1.html(PIPELINE_MERMAID, height=300, scrolling=True)

    # --- Stage descriptions ---
    p1, p2, p3, p4 = st.columns(4)
    with p1:
        with st.container(border=True):
            st.markdown("**Bronze — Ingestion**")
            st.markdown(
                "Auto Loader continuously ingests new files as they arrive. "
                "Schema is inferred and evolves automatically — no manual DDL needed."
            )
    with p2:
        with st.container(border=True):
            st.markdown("**Silver — Enrichment**")
            st.markdown(
                "**AI SKU Resolution** matches messy product aliases to canonical names using `ai_similarity()`. "
                "Data quality rules quarantine bad records automatically."
            )
    with p3:
        with st.container(border=True):
            st.markdown("**Gold — Forecasting**")
            st.markdown(
                "`ai_forecast()` generates demand predictions per customer-SKU pair. "
                "Results are published as governed **Metric Views** with business definitions."
            )
    with p4:
        with st.container(border=True):
            st.markdown("**Serving — Genie**")
            st.markdown(
                "Two Genie Spaces let brand managers ask questions in plain English. "
                "Genie translates to SQL against the governed metric layer."
            )

    st.markdown("---")

    # --- AI Functions highlight ---
    st.subheader("Built-in AI Functions")
    ai1, ai2 = st.columns(2)
    with ai1:
        with st.container(border=True):
            st.markdown("**`ai_similarity()`** — SKU Resolution")
            st.markdown(
                "Customers reference products by informal names — "
                "*\"BT Speaker Portable\"* instead of *\"Wireless Bluetooth Speaker Large\"*. "
                "The AI function scores every alias against the canonical catalog and picks the best match. "
                "Scores above 0.85 auto-resolve; 0.70–0.84 are flagged for review."
            )
    with ai2:
        with st.container(border=True):
            st.markdown("**`ai_forecast()`** — Demand Prediction")
            st.markdown(
                "Generates time-series forecasts directly in SQL — no external ML infrastructure needed. "
                "Each customer-SKU combination gets its own forecast with confidence intervals. "
                "Results power the Metric Views that Genie Spaces query."
            )

    st.markdown("---")

    # --- Proactive Analysis ---
    st.subheader("Proactive Anomaly Detection")
    st.markdown("The Proactive Analysis Agent runs autonomously on a schedule, scanning all SKUs without a human question.")

    PROACTIVE_MERMAID = f"""
    <div class="mermaid-container">
    {MERMAID_INIT}
    <pre class="mermaid" style="background:transparent;">
    flowchart LR
        subgraph PHASE1["Phase 1 — Deterministic Sweep"]
            direction TB
            V1["variance_baseline<br/><i>accuracy_pct</i>"]
            V2["variance_baseline<br/><i>unit_variance</i>"]
            V3["forecast_vs_inventory<br/><i>60-day horizon</i>"]
        end

        RANK["Rank &amp; Deduplicate<br/><i>Top 20 candidates</i>"]

        subgraph PHASE2["Phase 2 — LLM Drill-Down"]
            direction TB
            T1["channel_decomposition"]
            T2["compare_periods"]
            T3["correlate"]
            T4["Genie template queries"]
        end

        OUTPUT["Findings + Memory Update<br/><i>PDF &amp; Email</i>"]

        PHASE1 --> RANK --> PHASE2 --> OUTPUT
    </pre>
    </div>
    {MERMAID_CSS}
    """
    st.components.v1.html(PROACTIVE_MERMAID, height=260, scrolling=True)

    pr1, pr2, pr3 = st.columns(3)
    with pr1:
        with st.container(border=True):
            st.markdown("**Phase 1: SQL Sweep**")
            st.markdown(
                "Three deterministic scans run directly via the SQL Warehouse — no LLM involved. "
                "Variance baselines flag SKUs beyond 1.5 sigma on accuracy and unit variance. "
                "Forecast-vs-inventory checks flag stockout risk within 60 days."
            )
    with pr2:
        with st.container(border=True):
            st.markdown("**Phase 2: Agentic Investigation**")
            st.markdown(
                "The LLM picks top candidates and investigates using function calling — "
                "channel decomposition, period comparisons, metric correlations, "
                "and constrained Genie queries. Up to 15 tool calls per run."
            )
    with pr3:
        with st.container(border=True):
            st.markdown("**Persistent Memory**")
            st.markdown(
                "Each run writes a structured memory update to Lakebase — "
                "what the agent is watching, what resolved, and new findings. "
                "The next run loads this memory to avoid repeating old findings."
            )

    st.markdown("---")

    # --- Job Orchestration ---
    st.subheader("Automated Orchestration")
    JOB_MERMAID = f"""
    <div class="mermaid-container">
    {MERMAID_INIT}
    <pre class="mermaid" style="background:transparent;">
    flowchart LR
        subgraph DAILY["Daily Pipeline · 5:00 AM UTC"]
            direction LR
            J1["Ingest New Data"] --> J2["Run Lakeflow<br/>Declarative Pipeline"]
            J2 --> J3["Generate<br/>AI Forecasts"]
            J3 --> J4["Publish<br/>Metric Views"]
        end

        subgraph HOURLY["Report Dispatcher · Every Hour"]
            direction LR
            D1["Check Schedules<br/>in Lakebase"] --> D2["Route by Type"]
            D2 --> D3["QA Reports<br/><i>Supervisor Agent</i>"]
            D2 --> D4["Proactive Reports<br/><i>Proactive Agent</i>"]
            D3 --> D5["Generate PDFs<br/>&amp; Send Emails"]
            D4 --> D5
        end

        J4 -. "Fresh data ready" .-> D1
    </pre>
    </div>
    {MERMAID_CSS}
    """
    st.components.v1.html(JOB_MERMAID, height=260, scrolling=True)

    j1, j2 = st.columns(2)
    with j1:
        with st.container(border=True):
            st.markdown("**Daily Forecast Pipeline**")
            st.markdown(
                "Runs every morning. Incrementally processes only new data — "
                "a typical daily run completes in ~30 minutes. "
                "The Lakeflow Declarative Pipeline handles Bronze/Silver automatically, "
                "then AI forecasts are regenerated and metric views are refreshed."
            )
    with j2:
        with st.container(border=True):
            st.markdown("**Report Dispatcher**")
            st.markdown(
                "Uses atomic claim-based scheduling to prevent double-sends. "
                "Routes each schedule by type: **QA reports** go to the Supervisor Agent, "
                "**Proactive reports** go to the Proactive Analysis Agent. "
                "Due reports run in parallel (up to 4 concurrent), each generating a PDF and emailing recipients."
            )


# ===================================================================
# TAB 6: Architecture
# ===================================================================
def render_architecture_tab():
    st.header("Architecture")
    st.markdown("A compound AI system built entirely on Databricks — from data platform to proactive anomaly detection.")

    MERMAID_CSS = """
    <style>
    .mermaid-container { overflow-x: auto; padding: 10px 0; }
    .mermaid-container svg { max-width: 100%; height: auto; }
    </style>
    """
    MERMAID_INIT = """
    <script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>
    <script>mermaid.initialize({startOnLoad:true, theme:'base', themeVariables:{
        primaryColor:'#1b3a5c', primaryTextColor:'#fff', primaryBorderColor:'#2a5f9e',
        lineColor:'#555', secondaryColor:'#f0f2f6', tertiaryColor:'#e8ecf1',
        fontSize:'14px'
    }});</script>
    """

    # --- System Architecture ---
    st.subheader("System Overview")
    ARCH_MERMAID = f"""
    <div class="mermaid-container">
    {MERMAID_INIT}
    <pre class="mermaid" style="background:transparent;">
    flowchart TB
        USER["fa:fa-user Brand Manager"]

        subgraph APP["Databricks App"]
            UI["Interactive UI"]
            AGENT["Supervisor Agent<br/><i>Q&amp;A &amp; Reports</i>"]
            PROACTIVE["Proactive Agent<br/><i>Autonomous Anomaly Detection</i>"]
        end

        subgraph PLATFORM["Databricks Platform"]
            direction TB

            subgraph AI_LAYER["AI Services"]
                LLM["Foundation Model API<br/><i>Planning, Synthesis, Tool Calling</i>"]
                GENIE["Genie Spaces<br/><i>Natural Language &rarr; SQL</i>"]
                AI_FN["AI Functions<br/><i>ai_similarity, ai_forecast</i>"]
            end

            subgraph DATA_LAYER["Data Platform"]
                DLT["Lakeflow Declarative Pipelines<br/><i>Medallion Architecture</i>"]
                UC["Unity Catalog<br/><i>Governed Tables &amp; Metric Views</i>"]
                WH["SQL Warehouse<br/><i>Analytical Tools &amp; Query Execution</i>"]
            end

            subgraph OPS["Operational Services"]
                LB["Lakebase<br/><i>Schedules, Agent Memory, Audit Log</i>"]
                JOBS["Databricks Jobs<br/><i>Pipeline, Dispatcher, Proactive Runs</i>"]
                SECRETS["Secret Management"]
            end
        end

        EMAIL["fa:fa-envelope Email Delivery<br/><i>Reports &amp; Anomaly Alerts</i>"]

        USER --> UI
        UI --> AGENT
        AGENT --> LLM
        AGENT --> GENIE
        PROACTIVE --> LLM
        PROACTIVE --> WH
        PROACTIVE --> GENIE
        PROACTIVE --> LB
        GENIE --> WH --> UC
        DLT --> UC
        AI_FN --> UC
        AGENT --> LB
        JOBS --> AGENT
        JOBS --> PROACTIVE
        JOBS --> SECRETS
        AGENT --> EMAIL
        PROACTIVE --> EMAIL
    </pre>
    </div>
    {MERMAID_CSS}
    """
    st.components.v1.html(ARCH_MERMAID, height=620, scrolling=True)

    # --- Platform services ---
    st.markdown("---")
    st.subheader("Databricks Services Used")

    c1, c2, c3 = st.columns(3)
    with c1:
        with st.container(border=True):
            st.markdown("**Databricks Apps**")
            st.markdown(
                "Hosts the Streamlit UI and both agents as a managed web application. "
                "Handles OAuth, service principal identity, and auto-scaling — no infrastructure to manage."
            )
        with st.container(border=True):
            st.markdown("**Lakeflow Declarative Pipelines**")
            st.markdown(
                "Defines the entire Bronze/Silver pipeline declaratively. "
                "Auto Loader handles streaming ingestion, materialized views refresh incrementally, "
                "and built-in data quality expectations quarantine bad records."
            )
    with c2:
        with st.container(border=True):
            st.markdown("**Genie Spaces**")
            st.markdown(
                "Two governed Genie Spaces give brand managers natural language access to demand forecasts "
                "and inventory data. Used by both agents — the Supervisor for Q&A and the Proactive Agent "
                "for drill-down investigations via constrained templates."
            )
        with st.container(border=True):
            st.markdown("**Foundation Model API**")
            st.markdown(
                "Powers both agents. The Supervisor uses the LLM for planning and synthesis. "
                "The Proactive Agent uses function calling to autonomously investigate anomalies — "
                "the LLM decides which analytical tools and Genie queries to run."
            )
    with c3:
        with st.container(border=True):
            st.markdown("**Lakebase**")
            st.markdown(
                "Managed Postgres database for operational state — report schedules, "
                "conversation memory, agent memory (watching/resolved lists), and audit logs. "
                "The proactive agent's append-only memory enables run-over-run continuity."
            )
        with st.container(border=True):
            st.markdown("**Databricks Jobs**")
            st.markdown(
                "Three automated jobs: a daily pipeline refreshes data and forecasts, "
                "an hourly dispatcher routes due schedules to the right agent (Supervisor or Proactive), "
                "and atomic claim-based locking prevents double-sends."
            )

    st.markdown("---")

    # --- Two-Agent Architecture ---
    st.subheader("Two-Agent Architecture")
    st.markdown("The system uses two specialized agents with different triggers and capabilities.")

    ag1, ag2 = st.columns(2)
    with ag1:
        with st.container(border=True):
            st.markdown("#### Supervisor Agent")
            st.markdown("*Human-triggered — answers questions on demand*")
            st.markdown(
                "**Trigger:** User question or scheduled report cron.\n\n"
                "**Flow:** Plan (decompose question) -> Execute (route to Genies) -> Synthesize (combine into report).\n\n"
                "**Memory:** Conversation sessions in Lakebase — understands follow-up questions.\n\n"
                "**Output:** Executive report with data tables, optional PDF + email."
            )
    with ag2:
        with st.container(border=True):
            st.markdown("#### Proactive Agent")
            st.markdown("*Autonomous — finds what you didn't think to ask*")
            st.markdown(
                "**Trigger:** Scheduled cron — no human question needed.\n\n"
                "**Flow:** Phase 1 SQL sweep (deterministic) -> Phase 2 LLM drill-down (agentic, tool calling).\n\n"
                "**Memory:** Structured watching/resolved lists — tracks issues across runs.\n\n"
                "**Output:** Ranked findings with severity + recommended actions, PDF + email."
            )

    st.markdown("---")

    # --- Supervisor Agent Flow ---
    st.subheader("Supervisor Agent Flow")
    st.markdown("When a brand manager asks a question — or a scheduled report fires:")

    AGENT_MERMAID = f"""
    <div class="mermaid-container">
    {MERMAID_INIT}
    <pre class="mermaid" style="background:transparent;">
    sequenceDiagram
        participant U as Brand Manager
        participant A as Supervisor Agent
        participant LLM as Foundation Model
        participant G as Genie Spaces
        participant DB as Lakebase

        U->>A: Ask a question
        A->>DB: Load conversation memory
        A->>LLM: Plan — break question into steps
        LLM-->>A: Execution plan

        loop Each step (1-4 queries, routed to the best Genie)
            A->>G: Query in natural language
            G-->>A: Data + SQL + answer
        end

        A->>LLM: Synthesize results into report
        LLM-->>A: Executive summary with insights
        A->>DB: Save conversation &amp; audit log
        A-->>U: Report with data tables &amp; charts

        opt Scheduled Report
            A->>A: Generate PDF &amp; email to recipients
        end
    </pre>
    </div>
    {MERMAID_CSS}
    """
    st.components.v1.html(AGENT_MERMAID, height=520, scrolling=True)

    a1, a2, a3 = st.columns(3)
    with a1:
        with st.container(border=True):
            st.markdown("**Plan**")
            st.markdown(
                "The LLM decomposes the question into 1-4 concrete steps, "
                "each targeting the right Genie Space. Previous conversation context "
                "helps the agent understand follow-up questions."
            )
    with a2:
        with st.container(border=True):
            st.markdown("**Execute**")
            st.markdown(
                "Each step is routed to the best Genie Space for that question — "
                "often the same Genie multiple times for multi-faceted analyses. "
                "Genie generates SQL against the governed metric views and returns "
                "structured data with explanations."
            )
    with a3:
        with st.container(border=True):
            st.markdown("**Synthesize**")
            st.markdown(
                "The LLM combines all step results into a single executive report — "
                "highlighting trends, anomalies, and actionable recommendations. "
                "Alerts are evaluated and emails sent if thresholds are breached."
            )

    st.markdown("---")

    # --- Proactive Agent Flow ---
    st.subheader("Proactive Agent Flow")
    st.markdown("Runs autonomously on a schedule — no human input required:")

    PROACTIVE_MERMAID = f"""
    <div class="mermaid-container">
    {MERMAID_INIT}
    <pre class="mermaid" style="background:transparent;">
    sequenceDiagram
        participant CRON as Dispatcher Job
        participant PA as Proactive Agent
        participant WH as SQL Warehouse
        participant LLM as Foundation Model
        participant G as Genie Spaces
        participant DB as Lakebase

        CRON->>PA: Trigger scheduled run
        PA->>DB: Load prior memory (watching list)

        rect rgb(230, 240, 250)
            Note over PA,WH: Phase 1 — Deterministic Sweep
            PA->>WH: variance_baseline(accuracy_pct)
            PA->>WH: variance_baseline(unit_variance)
            PA->>WH: forecast_vs_inventory(60d)
            WH-->>PA: Top 20 ranked candidates
        end

        rect rgb(240, 235, 250)
            Note over PA,G: Phase 2 — LLM Drill-Down
            PA->>LLM: Candidates + memory + tools
            loop Up to 15 tool calls
                LLM->>PA: Call tool (channel_decomp, correlate, genie...)
                PA->>WH: Execute analytical SQL
                PA->>G: Constrained Genie query
                PA-->>LLM: Tool results
            end
            LLM-->>PA: Findings + memory update
        end

        PA->>DB: Write memory (watching, resolved, findings)
        PA->>PA: Generate PDF &amp; email
    </pre>
    </div>
    {MERMAID_CSS}
    """
    st.components.v1.html(PROACTIVE_MERMAID, height=620, scrolling=True)

    p1, p2, p3 = st.columns(3)
    with p1:
        with st.container(border=True):
            st.markdown("**Analytical Tools**")
            st.markdown(
                "Five SQL-based tools run directly against the warehouse: "
                "`variance_baseline`, `forecast_vs_inventory`, `compare_periods`, "
                "`channel_decomposition`, and `correlate`. These are registered as function-calling "
                "tools so the LLM can invoke them autonomously in Phase 2."
            )
    with p2:
        with st.container(border=True):
            st.markdown("**Genie Templates**")
            st.markdown(
                "The agent can't write free-form Genie questions — it fills constrained templates "
                "with validated slots. Seven templates cover variance checks, channel splits, "
                "stockout risk, category trends, top movers, and inventory positions. "
                "Every Genie result is validated before use."
            )
    with p3:
        with st.container(border=True):
            st.markdown("**Agent Memory**")
            st.markdown(
                "Append-only memory in Lakebase (`bi_agent_memory`). Each run writes: "
                "a narrative summary, watching items (topic, SKU, severity, trend), "
                "resolved items, and ranked findings. "
                "The next run loads this memory to avoid duplicate alerts."
            )


# ===================================================================
# Main routing
# ===================================================================
if tab_selection == "Home":
    render_home_tab()
elif tab_selection == "Ask the Genies":
    render_genie_tab()
elif tab_selection == "Supervisor Agent":
    render_supervisor_tab()
elif tab_selection == "Schedules & Alerts":
    render_schedules_tab()
elif tab_selection == "Monitoring":
    render_monitoring_tab()
elif tab_selection == "Data Pipeline":
    render_pipeline_tab()
elif tab_selection == "Architecture":
    render_architecture_tab()
