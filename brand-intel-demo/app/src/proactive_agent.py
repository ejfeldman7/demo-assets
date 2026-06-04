"""
Proactive Analysis Agent for Brand Intelligence.

Two-phase agent that autonomously finds anomalies, trend breaks, stockout risks,
and cannibalizations — without waiting for a human to ask.

Phase 1 (Sweep): Deterministic scan across all SKUs using analytical tools.
    Returns a ranked candidate list. Always runs — no LLM autonomy here.

Phase 2 (Drill-Down): Agentic investigation. LLM decides which Phase 1 candidates
    to investigate further, which tools to call, and in what order.

Design decision: Phase 1 uses direct SQL for performance and determinism.
Phase 2 uses Genie with constrained templates for flexible drill-down.
Metric views own the business definitions — the agent never encodes schema knowledge.
"""

import json
import logging
import time
import uuid
from datetime import datetime
from typing import Optional, Callable

from databricks.sdk import WorkspaceClient

from analytical_tools import (
    variance_baseline,
    forecast_vs_inventory,
    compare_periods,
    channel_decomposition,
    correlate,
    dispatch_tool,
    TOOL_DEFINITIONS,
)
from genie_templates import (
    genie_query,
    genie_demand,
    genie_inventory,
    GENIE_TOOL_DEFINITIONS,
    build_genie_tool_definitions,
)
from db import get_latest_memory, write_memory, get_active_genies

logger = logging.getLogger(__name__)
MODEL_CHAIN = [
    "databricks-claude-sonnet-4-5",
    "databricks-claude-sonnet-4",
]

MAX_TOOL_CALLS = 15
WALL_CLOCK_TIMEOUT_SECONDS = 180


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

PROACTIVE_AGENT_SYSTEM_PROMPT = """You are a Brand Intelligence Analyst running on a scheduled basis.
Your job is NOT to answer questions. Your job is to proactively find things worth
surfacing — anomalies, trend breaks, risks, and opportunities — that a brand manager
would want to know but did not think to ask about.

You will receive:
- MEMORY: What you found in prior runs and what you are watching
- PHASE_1_CANDIDATES: Anomalies detected by the deterministic sweep
- TOOLS: Functions you can call to investigate candidates further

Rules:
1. Do not repeat findings from prior runs unless the situation has materially
   changed (escalated, resolved, or reversed).
2. Every finding must include a recommended action.
3. Use Genie tools with the provided templates only — do not construct
   free-form questions.
4. If a Genie result fails validation, report it as 'unable to verify'
   rather than omitting it or stating it as fact.
5. Rank findings by business impact, not statistical novelty.
6. End every run by writing a MEMORY_UPDATE — a JSON object with
   'narrative' (≤200 words), 'watching' (updated list), and 'resolved'.
   This is mandatory even if you have no new findings.

Each watching item must have:
  topic, sku, metric, severity (notable|urgent|critical),
  first_flagged (ISO datetime), last_checked (ISO datetime),
  last_value (number), trend (worsening|stable|improving),
  runs_watched (integer — how many consecutive runs this item has been on the watch list)

Staleness rules — you MUST follow these:
- Increment runs_watched by 1 each run for every carried-over watching item.
- If an item has runs_watched >= 5 AND trend is "stable" or "improving", move it to resolved
  with resolution "Auto-resolved: stable for 5+ runs".
- If an item has runs_watched >= 10 regardless of trend, move it to resolved
  with resolution "Auto-resolved: watched for 10 runs without resolution".
- Do NOT keep items on the watch list indefinitely. Stale issues dilute attention
  from genuinely new problems.
- When re-adding a previously resolved topic, reset runs_watched to 1.

Output format:
1. First, provide your analysis as markdown text.
2. Then output a JSON block wrapped in ```json fences with key "MEMORY_UPDATE" containing:
   - "narrative": string (≤200 words summarizing current state)
   - "watching": list of watching items
   - "resolved": list of {topic, resolved_at, resolution}
   - "findings": list of {sku, type, severity, description, recommended_action}
"""


# ---------------------------------------------------------------------------
# LLM helper
# ---------------------------------------------------------------------------

def _call_llm(w: WorkspaceClient, messages: list[dict], tools: list[dict] = None, max_tokens: int = 4096) -> dict:
    """Call Foundation Model API with automatic failover across MODEL_CHAIN."""
    body = {
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    if tools:
        body["tools"] = tools

    last_err = None
    for model in MODEL_CHAIN:
        try:
            response = w.api_client.do(
                "POST",
                f"/serving-endpoints/{model}/invocations",
                body=body,
            )
            return response if isinstance(response, dict) else {}
        except Exception as e:
            logger.warning("LLM call to %s failed: %s — trying next model", model, e)
            last_err = e
    logger.error("All models in MODEL_CHAIN failed. Last error: %s", last_err)
    return {"error": str(last_err)}


def _extract_content(response: dict) -> str:
    """Extract text content from LLM response."""
    choices = response.get("choices", [])
    if not choices:
        return ""
    msg = choices[0].get("message", {})
    content = msg.get("content", "")
    if isinstance(content, list):
        return "".join(
            c.get("text", "") for c in content
            if isinstance(c, dict) and c.get("type") not in ("thinking", "reasoning")
        )
    return str(content) if content else ""


def _extract_tool_calls(response: dict) -> list[dict]:
    """Extract tool calls from LLM response."""
    choices = response.get("choices", [])
    if not choices:
        return []
    msg = choices[0].get("message", {})
    return msg.get("tool_calls", [])


# ---------------------------------------------------------------------------
# Phase 1: Deterministic sweep
# ---------------------------------------------------------------------------

def run_phase1(w: WorkspaceClient) -> list[dict]:
    """Run deterministic sweep across all SKUs. Returns ranked candidates."""
    candidates = []

    # Sweep 1: variance_baseline on accuracy_pct
    logger.info("Phase 1: Sweeping accuracy_pct")
    accuracy_results = variance_baseline(w, metric="accuracy_pct", sigma_threshold=1.5)
    for r in accuracy_results:
        r["source"] = "variance_baseline"
        r["sweep_metric"] = "accuracy_pct"
        candidates.append(r)

    # Sweep 2: variance_baseline on unit_variance
    logger.info("Phase 1: Sweeping unit_variance")
    variance_results = variance_baseline(w, metric="unit_variance", sigma_threshold=1.5)
    for r in variance_results:
        r["source"] = "variance_baseline"
        r["sweep_metric"] = "unit_variance"
        candidates.append(r)

    # Sweep 3: forecast_vs_inventory
    logger.info("Phase 1: Sweeping forecast_vs_inventory")
    inventory_results = forecast_vs_inventory(w, horizon_days=60)
    for r in inventory_results:
        r["source"] = "forecast_vs_inventory"
        r["sweep_metric"] = "days_of_supply"
        r["sigma"] = 0  # Not sigma-based, but needed for ranking
        # Assign a pseudo-sigma based on severity
        if r.get("severity") == "critical":
            r["sigma"] = 3.0
        elif r.get("severity") == "urgent":
            r["sigma"] = 2.0
        elif r.get("severity") == "notable":
            r["sigma"] = 1.5
        candidates.append(r)

    # Deduplicate by SKU (keep highest sigma)
    seen = {}
    for c in candidates:
        sku = c.get("sku", "")
        existing_sigma = abs(seen.get(sku, {}).get("sigma", 0))
        new_sigma = abs(c.get("sigma", 0))
        if sku not in seen or new_sigma > existing_sigma:
            seen[sku] = c

    # Sort by absolute sigma descending, take top 20
    ranked = sorted(seen.values(), key=lambda x: abs(x.get("sigma", 0)), reverse=True)
    return ranked[:20]


# ---------------------------------------------------------------------------
# Phase 2: LLM-driven drill-down
# ---------------------------------------------------------------------------

def run_phase2(
    w: WorkspaceClient,
    candidates: list[dict],
    memory: Optional[dict],
    progress_callback: Optional[Callable] = None,
    genies: list[dict] = None,
) -> tuple[str, list[dict]]:
    """Run LLM-driven investigation of Phase 1 candidates.

    Returns (analysis_text, tool_call_log).
    """
    # Build the context message
    memory_text = "No prior memory — this is the first run."
    if memory:
        narrative = memory.get("narrative", "")
        watching = memory.get("watching", [])
        resolved = memory.get("resolved", [])
        memory_text = f"**Narrative:** {narrative}\n\n"
        if watching:
            memory_text += "**Watching:**\n"
            for item in watching:
                memory_text += f"- {item.get('topic', '?')}: severity={item.get('severity', '?')}, trend={item.get('trend', '?')}, last_value={item.get('last_value', '?')}\n"
        if resolved:
            memory_text += "\n**Recently Resolved:**\n"
            for item in resolved[:5]:
                memory_text += f"- {item.get('topic', '?')}: {item.get('resolution', '?')}\n"

    # Annotate candidates with memory context
    for c in candidates:
        sku = c.get("sku", "")
        if memory and memory.get("watching"):
            for w_item in memory["watching"]:
                if w_item.get("sku") == sku:
                    c["prior_severity"] = w_item.get("severity")
                    c["prior_trend"] = w_item.get("trend")
                    c["first_flagged"] = w_item.get("first_flagged")
                    break

    candidates_text = json.dumps(candidates, indent=2, default=str)

    messages = [
        {"role": "system", "content": PROACTIVE_AGENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"## MEMORY\n{memory_text}\n\n"
                f"## PHASE_1_CANDIDATES ({len(candidates)} anomalies detected)\n"
                f"```json\n{candidates_text}\n```\n\n"
                "Investigate the most impactful candidates. Use tools to drill down. "
                "Then write your findings and MEMORY_UPDATE."
            ),
        },
    ]

    # Build genie map for dynamic dispatch
    _genie_map = {g["name"]: g["space_id"] for g in genies} if genies else {}

    # Combine all tool definitions
    genie_tools = build_genie_tool_definitions(genies) if genies else GENIE_TOOL_DEFINITIONS
    all_tools = TOOL_DEFINITIONS + genie_tools

    tool_call_log = []
    tool_call_count = 0
    start_time = time.time()

    # Tool-calling loop
    while tool_call_count < MAX_TOOL_CALLS:
        # Check wall clock timeout
        elapsed = time.time() - start_time
        if elapsed > WALL_CLOCK_TIMEOUT_SECONDS:
            logger.warning("Phase 2 wall clock timeout after %.0fs", elapsed)
            messages.append({
                "role": "user",
                "content": "TIME LIMIT REACHED. Write your findings and MEMORY_UPDATE now with what you have.",
            })
            response = _call_llm(w, messages, max_tokens=4096)
            return _extract_content(response), tool_call_log

        response = _call_llm(w, messages, tools=all_tools, max_tokens=4096)
        tool_calls = _extract_tool_calls(response)

        if not tool_calls:
            # No more tool calls — LLM is done
            return _extract_content(response), tool_call_log

        # Add assistant message with tool calls
        assistant_msg = response.get("choices", [{}])[0].get("message", {})
        messages.append(assistant_msg)

        # Execute each tool call
        for tc in tool_calls:
            tool_call_count += 1
            fn = tc.get("function", {})
            tool_name = fn.get("name", "")
            try:
                args = json.loads(fn.get("arguments", "{}"))
            except json.JSONDecodeError:
                args = {}

            if progress_callback:
                progress_callback(tool_call_count, tool_name, args)

            logger.info("Phase 2 tool call %d/%d: %s(%s)", tool_call_count, MAX_TOOL_CALLS, tool_name, json.dumps(args)[:100])

            # Dispatch to the right tool
            if tool_name.startswith("genie_"):
                genie_name = tool_name[len("genie_"):]
                space_id = _genie_map.get(genie_name)
                if not space_id:
                    logger.warning("Unknown genie tool '%s', falling back to first genie", tool_name)
                    space_id = genies[0]["space_id"] if genies else ""
                result = genie_query(
                    template_name=args.get("template_name", ""),
                    slots=args.get("slots", {}),
                    space_id=space_id,
                )
                result_text = _serialize_genie_result(result)
            else:
                # Analytical tool
                result = dispatch_tool(w, tool_name, args)
                result_text = json.dumps(result[:10], indent=2, default=str) if result else "No results"

            tool_call_log.append({
                "tool": tool_name,
                "args": args,
                "result_preview": result_text[:500],
            })

            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", ""),
                "content": result_text,
            })

    # Hit max tool calls — force final response
    messages.append({
        "role": "user",
        "content": "TOOL CALL LIMIT REACHED. Write your findings and MEMORY_UPDATE now.",
    })
    response = _call_llm(w, messages, max_tokens=4096)
    return _extract_content(response), tool_call_log


def _serialize_genie_result(result: dict) -> str:
    """Serialize a Genie result dict for the LLM."""
    parts = []
    if not result.get("valid", True):
        parts.append(f"[VALIDATION FAILED: {result.get('validation_reason', 'unknown')}]")
    if result.get("answer_text"):
        parts.append(f"Answer: {result['answer_text']}")
    if result.get("result_df") is not None and hasattr(result["result_df"], "empty"):
        df = result["result_df"]
        if not df.empty:
            parts.append(f"Data ({len(df)} rows):\n{df.head(15).to_markdown(index=False)}")
    if result.get("error"):
        parts.append(f"Error: {result['error']}")
    return "\n".join(parts) if parts else "No data returned"


# ---------------------------------------------------------------------------
# Parse findings and memory update from LLM output
# ---------------------------------------------------------------------------

def _parse_memory_update(text: str) -> Optional[dict]:
    """Extract the MEMORY_UPDATE JSON from the LLM's response text."""
    import re

    # Strategy 1: Find JSON block in ```json fences (greedy — handles nested braces)
    json_match = re.search(r'```json\s*(\{.+\})\s*```', text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1))
            # If the JSON has a MEMORY_UPDATE wrapper, unwrap it
            if "MEMORY_UPDATE" in data:
                data = data["MEMORY_UPDATE"]
            if "narrative" in data or "watching" in data or "findings" in data:
                return data
        except json.JSONDecodeError:
            pass

    # Strategy 2: Find the largest JSON object containing "narrative"
    # Use a bracket-counting approach instead of regex
    start_indices = [m.start() for m in re.finditer(r'\{', text)]
    best = None
    for start in start_indices:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    candidate = text[start:i+1]
                    try:
                        data = json.loads(candidate)
                        if isinstance(data, dict):
                            if "MEMORY_UPDATE" in data:
                                data = data["MEMORY_UPDATE"]
                            if "narrative" in data or "findings" in data:
                                if best is None or len(candidate) > len(best[1]):
                                    best = (data, candidate)
                    except json.JSONDecodeError:
                        pass
                    break

    if best:
        return best[0]

    # Log the first 500 chars of text to help debug
    logger.warning("Could not parse MEMORY_UPDATE from LLM output. First 500 chars: %s", text[:500])
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_proactive_agent(
    schedule_id: str,
    run_id: str,
    workspace_client: Optional[WorkspaceClient] = None,
    progress_callback: Optional[Callable] = None,
    genies: list[dict] = None,
) -> tuple[list[dict], dict]:
    """Run the full proactive analysis pipeline.

    Returns (findings: list[dict], memory_update: dict).
    """
    w = workspace_client or WorkspaceClient()
    if genies is None:
        df = get_active_genies()
        genies = df.to_dict("records")
    start_time = time.time()

    # 1. Load memory
    memory = get_latest_memory(schedule_id)
    if memory:
        logger.info("Loaded memory: %d watching items, narrative=%s...",
                     len(memory.get("watching", [])),
                     (memory.get("narrative", "") or "")[:50])
    else:
        logger.info("No prior memory — first run for schedule %s", schedule_id)

    # 2. Run Phase 1 sweep
    logger.info("Starting Phase 1 sweep")
    candidates = run_phase1(w)
    logger.info("Phase 1 complete: %d candidates", len(candidates))

    # 3. Run Phase 2 drill-down
    logger.info("Starting Phase 2 drill-down")
    analysis_text, tool_call_log = run_phase2(w, candidates, memory, progress_callback, genies=genies)
    logger.info("Phase 2 complete: %d tool calls, %.0fs elapsed",
                len(tool_call_log), time.time() - start_time)
    logger.info("Phase 2 analysis text length: %d chars", len(analysis_text) if analysis_text else 0)
    if analysis_text:
        logger.info("Phase 2 analysis text (last 1000 chars): %s", analysis_text[-1000:])

    # 4. Parse memory update from LLM output
    memory_update = _parse_memory_update(analysis_text)
    if not memory_update:
        memory_update = {
            "narrative": "Agent run completed but no structured memory update was produced.",
            "watching": [],
            "resolved": [],
            "findings": [],
        }

    findings = memory_update.get("findings", [])

    # 4b. Enforce staleness rules deterministically (LLM may not comply)
    #     Also auto-resolve items whose SKU no longer appears in Phase 1 candidates.
    candidate_skus = {c.get("sku") for c in candidates if c.get("sku")}
    watching = memory_update.get("watching", [])
    resolved = memory_update.get("resolved", [])
    enforced_watching = []
    for item in watching:
        sku = item.get("sku", "")
        if sku and sku not in candidate_skus:
            resolved.append({
                "topic": item.get("topic", "unknown"),
                "resolved_at": datetime.utcnow().isoformat(),
                "resolution": f"Auto-resolved: {sku} no longer flagged by Phase 1 sweep",
            })
            logger.info("Auto-resolved absent item: %s (sku=%s not in Phase 1)", item.get("topic"), sku)
            continue
        runs = item.get("runs_watched", 1)
        trend = item.get("trend", "stable")
        if runs >= 10:
            resolved.append({
                "topic": item.get("topic", "unknown"),
                "resolved_at": datetime.utcnow().isoformat(),
                "resolution": f"Auto-resolved: watched for {runs} runs without resolution",
            })
            logger.info("Auto-resolved stale item: %s (runs_watched=%d)", item.get("topic"), runs)
        elif runs >= 5 and trend in ("stable", "improving"):
            resolved.append({
                "topic": item.get("topic", "unknown"),
                "resolved_at": datetime.utcnow().isoformat(),
                "resolution": f"Auto-resolved: {trend} for {runs} runs",
            })
            logger.info("Auto-resolved stable item: %s (trend=%s, runs=%d)", item.get("topic"), trend, runs)
        else:
            enforced_watching.append(item)
    memory_update["watching"] = enforced_watching
    memory_update["resolved"] = resolved

    # 5. Write memory
    write_memory(
        schedule_id=schedule_id,
        run_id=run_id,
        narrative=memory_update.get("narrative", ""),
        watching=memory_update.get("watching", []),
        resolved=memory_update.get("resolved", []),
        findings=findings,
    )
    logger.info("Memory written: %d findings, %d watching",
                len(findings), len(memory_update.get("watching", [])))

    return findings, {
        "analysis_text": analysis_text,
        "tool_call_log": tool_call_log,
        "candidates_count": len(candidates),
        "execution_time": round(time.time() - start_time, 2),
        **memory_update,
    }
