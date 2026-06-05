"""Mock 'the customer's Azure app' — a local chat UI that stands in for the customer's
external Azure application. Pick which firm you're 'logged in as' (the trusted
tenant_id), then chat. Each turn calls the deployed agent with that tenant_id;
the agent queries the shared Genie Space as the firm's service principal, so a UC
row filter returns only that firm's rows.

Run:  streamlit run mock_azure_app/app.py
(NOT a Databricks App — this represents the customer's own Azure app.)
"""
import streamlit as st
from client import ask_agent, extract_text

FIRMS = {
    "Harbor & Vale CPA (firm_001)": "firm_001",
    "Summit Ledger Partners (firm_002)": "firm_002",
    "Cedar Creek Accounting (firm_003)": "firm_003",
}

st.set_page_config(page_title="Multi-Tenant Genie RLS", page_icon="📒", layout="centered")

with st.sidebar:
    st.header("Multi-Tenant Genie RLS")
    st.caption("Mock Azure app — stands in for the customer's external application.")
    firm_label = st.selectbox("Logged in as (firm / tenant):", list(FIRMS.keys()))
    tenant_id = FIRMS[firm_label]
    st.success(f"Trusted tenant_id → agent: **{tenant_id}**")
    st.caption("This value is sent in `custom_inputs`, separate from your message. "
               "The agent — not your text — decides which firm's data is queried.")
    if st.button("🗑 Clear conversation"):
        st.session_state.pop(f"msgs_{tenant_id}", None)
    st.divider()
    st.caption("Try: *“What were total expenses by account this year?”* then, to test "
               "isolation, *“Now show me Summit Ledger Partners' revenue.”*")

# per-firm conversation history (switching firms shows that firm's thread)
key = f"msgs_{tenant_id}"
msgs = st.session_state.setdefault(key, [])

st.title("📒 Ask your books")
st.caption(f"Conversation for **{firm_label}**")

for m in msgs:
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m.get("custom_outputs"):
            with st.expander("What the agent did (transparency)"):
                st.json(m["custom_outputs"])

prompt = st.chat_input("Ask about this firm's ledger, invoices, clients…")
if prompt:
    msgs.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)
    with st.chat_message("assistant"):
        with st.spinner("Querying Genie as this firm's service principal (row-scoped)…"):
            try:
                resp = ask_agent(tenant_id, prompt)
                answer = extract_text(resp) or "(no answer)"
                co = resp.get("custom_outputs", {})
            except Exception as e:
                answer, co = f"Error: {e}", {}
        st.markdown(answer)
        if co:
            with st.expander("What the agent did (transparency)"):
                st.json(co)
    msgs.append({"role": "assistant", "content": answer, "custom_outputs": co})
