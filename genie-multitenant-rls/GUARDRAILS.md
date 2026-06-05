# Guardrails — Multi-Tenant Genie RLS

Three layers, in order of importance for this use case:

## 1. Native RLS (primary blast-radius control) — ACTIVE
The UC row filter on `current_user()` means even a fully prompt-injected agent can only
ever return the calling firm's rows. This is the strongest control and it's enforced below
the LLM. Verified live.

## 2. In-agent input guardrail (LLM-judge) — ACTIVE
`agent.py` runs each user message through a fast LLM judge
(`databricks-meta-llama-3-1-8b-instruct`) that returns ALLOW/BLOCK and blocks prompt
injection, cross-tenant requests, credential probing, and unsafe content. Verified live
(the cross-tenant injection test returns `guardrail: BLOCK`). Plus a structural defense:
the firm is selected deterministically from the trusted `tenant_id`, never by the LLM.

## 3. AI Gateway Safety + PII guardrails — APPLY-READY (not on this endpoint type)
**Finding (confirmed live on <DATABRICKS_PROFILE>):** AI Gateway guardrails are **not supported on
Databricks-agent / custom-model serving endpoints** — the API returns *"AI Guardrails is
not currently supported for this endpoint type in this workspace."* They are supported on
**external-model** and **FMAPI pay-per-token** endpoints only (Public Preview), and require
a region with FMAPI pay-per-token.

The agent's LLM here is the shared `databricks-claude-sonnet-4-6` FMAPI endpoint (not
owned/configurable by us), so there's no guardrails-capable endpoint in this demo to attach
to. In **the customer's real Azure environment**, where the LLM is **Azure OpenAI as an
external model**, guardrails attach cleanly:

```bash
# Apply to the external-model (Azure OpenAI) endpoint the agent uses:
databricks serving-endpoints put-ai-gateway <AZURE_OPENAI_ENDPOINT> \
  --json @agent/ai_gateway_guardrails.json
```

This adds **Safety** (harmful-content) + **PII** (Block) on both input and output, plus
payload logging to an inference table. For coverage of the agent/custom surface that AI
Gateway doesn't reach, add **dedicated guardrail model endpoints** (Prompt Guard 2 for
jailbreak/injection, Llama Guard 4 for safety) and call them inline in the agent.

> Net: in this demo, layers 1 + 2 are active and verified; layer 3 is apply-ready for the
> customer's Azure OpenAI endpoint (the one place guardrails are supported), exactly as our
> analysis predicted.
