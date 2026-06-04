"""
SupervisorAgent for Brand Intelligence Brand Manager Forecasting Intelligence.

Orchestrates multi-Genie queries with memory, planning, execution,
and synthesis using Foundation Model API (Claude) and Lakebase persistence.
"""

import json
import time
import uuid
import logging
from typing import Any, Callable, Optional

import pandas as pd
from databricks.sdk import WorkspaceClient

from genie import ask_genie
from db import execute_query, execute_insert, get_active_genies

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
MODEL_CHAIN = [
    "databricks-gpt-oss-120b",
    "databricks-gpt-oss-20b",
]


class SupervisorAgent:
    """Multi-Genie orchestrator with conversational memory."""

    def __init__(
        self,
        workspace_client: Optional[WorkspaceClient] = None,
        genies: list[dict] = None,
    ):
        self.w = workspace_client or WorkspaceClient()
        if genies is None:
            df = get_active_genies()
            genies = df.to_dict("records")
        self.genies = genies
        self._genie_map = {g["name"]: g["space_id"] for g in genies}

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------
    def _call_llm(self, messages: list[dict], max_tokens: int = 4096) -> str:
        """Call Foundation Model API with automatic failover across MODEL_CHAIN."""
        last_err = None
        for model in MODEL_CHAIN:
            try:
                response = self.w.api_client.do(
                    "POST",
                    f"/serving-endpoints/{model}/invocations",
                    body={
                        "messages": messages,
                        "max_tokens": max_tokens,
                        "temperature": 0.3,
                    },
                )
                if isinstance(response, dict):
                    choices = response.get("choices", [])
                    if choices:
                        msg = choices[0].get("message", {})
                        content = msg.get("content", "")
                        # Some models return content as a list of blocks
                        if isinstance(content, list):
                            # Only keep text blocks, skip thinking/reasoning blocks
                            content = "".join(
                                c.get("text", "")
                                for c in content
                                if isinstance(c, dict) and c.get("type") not in ("thinking", "reasoning")
                            )
                        # Some models put reasoning in a separate field
                        return self._strip_reasoning(str(content) if content else "")
                return str(response)
            except Exception as e:
                logger.warning("LLM call to %s failed: %s — trying next model", model, e)
                last_err = e
        logger.error("All models in MODEL_CHAIN failed. Last error: %s", last_err)
        return f"[LLM Error: {last_err}]"

    @staticmethod
    def _strip_reasoning(text: str) -> str:
        """Remove reasoning/thinking blocks from model output."""
        import re
        # Strip matched <think>...</think>, <reasoning>...</reasoning>, etc.
        text = re.sub(r'<(think|reasoning|thought|internal_monologue)>.*?</\1>', '', text, flags=re.DOTALL)
        # Strip unclosed reasoning tags at start
        text = re.sub(r'^<(think|reasoning|thought|internal_monologue)>.*?(?:</\1>)', '', text, flags=re.DOTALL)
        # Strip any leading text before the first markdown heading (##, #) — catches untagged reasoning
        match = re.search(r'^(#+\s)', text, re.MULTILINE)
        if match and match.start() > 0:
            # Only strip if the preamble looks like reasoning (no markdown formatting)
            preamble = text[:match.start()]
            if '#' not in preamble and '**' not in preamble:
                text = text[match.start():]
        return text.strip()

    # ------------------------------------------------------------------
    # Memory
    # ------------------------------------------------------------------
    def build_memory_context(self, user_email: str) -> str:
        """Read the last 5 sessions and last 20 messages from Lakebase."""
        try:
            sessions_df = execute_query(
                """
                SELECT session_id, topic_tag, first_message_at, message_count, summary
                FROM bi_conversation_sessions
                WHERE user_email = %s
                ORDER BY last_message_at DESC
                LIMIT 5
                """,
                (user_email,),
            )

            if sessions_df.empty:
                return ""

            session_ids = sessions_df["session_id"].tolist()
            placeholders = ",".join(["%s"] * len(session_ids))
            messages_df = execute_query(
                f"""
                SELECT session_id, role, content, created_at
                FROM bi_conversation_messages
                WHERE session_id IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT 20
                """,
                tuple(session_ids),
            )

            parts = ["## Prior Conversation Memory\n"]
            for _, sess in sessions_df.iterrows():
                parts.append(f"### Session: {sess.get('topic_tag', 'Untitled')} ({str(sess.get('first_message_at', ''))[:10]})")
                summary = sess.get("summary")
                if summary:
                    parts.append(f"  Summary: {summary}")

            if not messages_df.empty:
                parts.append("\n### Recent Messages")
                for _, msg in messages_df.iterrows():
                    role = msg.get("role", "?")
                    content = str(msg.get("content", ""))[:500]
                    parts.append(f"  [{role}]: {content}")

            return "\n".join(parts)
        except Exception as e:
            logger.warning("Memory retrieval failed: %s", e)
            return ""

    # ------------------------------------------------------------------
    # Planning
    # ------------------------------------------------------------------
    def plan(self, question: str, memory_context: str) -> list[dict]:
        """Use LLM to decompose the question into Genie sub-queries."""
        # Build dynamic genie descriptions
        genie_lines = []
        genie_names = []
        for i, g in enumerate(self.genies, 1):
            genie_lines.append(f'{i}. "{g["name"]}" - {g["display_name"]}: {g["description"]}')
            genie_names.append(f'"{g["name"]}"')

        genie_list_str = "\n".join(genie_lines)
        genie_names_str = ", ".join(genie_names)
        first_genie = self.genies[0]["name"] if self.genies else "demand"

        system_prompt = f"""You are a planning agent for a Brand Manager Forecasting Intelligence system.
You have access to {len(self.genies)} Genie data space(s):

{genie_list_str}

Given a user question, create a plan of 1-4 steps. Each step queries ONE genie.
Return ONLY a JSON array of steps. Each step must have:
  - "step": integer step number
  - "genie": one of [{genie_names_str}]
  - "query": the natural language question to ask that genie

Think carefully about which genie to query and how to phrase sub-questions to get the data needed.
If the question only needs one genie, return just one step.
Return ONLY valid JSON, no markdown fences, no explanation."""

        user_content = question
        if memory_context:
            user_content = f"Context from prior conversations:\n{memory_context}\n\nCurrent question: {question}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        raw = self._call_llm(messages, max_tokens=1024)

        # Parse the JSON plan
        try:
            # Strip markdown fences if present
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()

            plan = json.loads(cleaned)
            if isinstance(plan, list):
                return plan
            return [plan]
        except json.JSONDecodeError:
            logger.warning("Could not parse plan JSON, using fallback single-step plan")
            return [{"step": 1, "genie": first_genie, "query": question}]

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------
    def execute_plan(self, plan: list[dict], progress_callback: Optional[Callable] = None) -> list[dict]:
        """Execute each step by calling the appropriate Genie space."""
        results = []
        for step in plan:
            step_num = step.get("step", len(results) + 1)
            genie_name = step.get("genie", "demand")
            query = step.get("query", "")

            if progress_callback:
                progress_callback(step_num, genie_name, query, "running")

            # Map genie name to space ID
            space_id = self._genie_map.get(genie_name)
            if not space_id:
                logger.warning("Unknown genie '%s', falling back to first genie", genie_name)
                space_id = self.genies[0]["space_id"] if self.genies else ""

            result = ask_genie(space_id, query)
            result["step"] = step_num
            result["genie"] = genie_name
            result["query"] = query
            results.append(result)

            if progress_callback:
                status = "error" if result.get("error") else "complete"
                progress_callback(step_num, genie_name, query, status)

        return results

    # ------------------------------------------------------------------
    # Synthesis
    # ------------------------------------------------------------------
    def synthesize(self, question: str, plan_results: list[dict], memory_context: str) -> str:
        """Use LLM to synthesize all Genie results into a coherent report."""
        system_prompt = """You are a senior analytics advisor for Brand Intelligence brand management.
You are synthesizing data from multiple queries into a comprehensive report for a Brand Manager.

Structure your response in clean markdown with these sections:
## Executive Summary
A concise 2-3 sentence overview of the key findings.

## Key Findings
Bullet-pointed insights drawn directly from the data. Include specific numbers,
percentages, customer names, SKUs, and time periods. Be precise.

## Data Highlights
Reference specific data points and tables from the query results.
If there are accuracy percentages, revenue figures, or inventory levels, call them out.

## Recommended Actions
3-5 actionable recommendations based on the data, prioritized by impact.
Be specific about what the brand manager should do and why.

Be data-driven, specific, and actionable. Do not make up data - only reference
what was returned in the query results. If a query returned an error, note it.

FORMATTING RULES:
- For dollar amounts, write them as plain text (e.g., "1.02 million dollars" or "USD 864,500") — never use the $ symbol, as it will be misinterpreted as LaTeX math.
- Use clean markdown: headers, bold, bullet points, numbered lists.
- Do not use LaTeX, math mode, or inline equations.
- Keep paragraphs short and scannable.

VISUALIZATIONS (REQUIRED):
You MUST include at least one Vega-Lite chart whenever the query results contain three
or more numeric data points — this is not optional. Include up to 3 charts total.
Embed each chart as a fenced code block tagged ```vega-lite, placed immediately after the
text that references it. Each spec must be self-contained JSON with the actual numbers from
the query results inlined under "data": {"values": [...]} (never a URL source). Only the
rare case of fewer than 3 data points across all results exempts you from adding a chart.

Good candidates for charts:
- Time series of accuracy or revenue over weeks/months (line chart)
- Category or region comparisons (bar chart)
- Forecast vs actuals side-by-side (grouped bar chart)
- Revenue gap by customer or SKU (horizontal bar chart)

Keep specs simple: use "mark", "encoding", and inline "values" in "data".
Set "width": 500 and "height": 300 for consistent sizing.
Do NOT include "$schema" — it will be added automatically.

Copy this exact structure, substituting the real values, field names, and mark type:
```vega-lite
{
  "width": 500,
  "height": 300,
  "data": {"values": [
    {"week": "2026-W01", "accuracy_pct": 82.4},
    {"week": "2026-W02", "accuracy_pct": 88.1},
    {"week": "2026-W03", "accuracy_pct": 79.6}
  ]},
  "mark": {"type": "line", "point": true},
  "encoding": {
    "x": {"field": "week", "type": "ordinal", "title": "Week"},
    "y": {"field": "accuracy_pct", "type": "quantitative", "title": "Forecast Accuracy (%)"}
  }
}
```"""

        # Build context from results
        results_context = []
        for r in plan_results:
            entry = f"### Step {r.get('step', '?')}: {r.get('genie', 'unknown')} Genie\n"
            entry += f"**Query:** {r.get('query', '')}\n"
            if r.get("error"):
                entry += f"**Error:** {r['error']}\n"
            else:
                entry += f"**Answer:** {r.get('answer_text', 'No answer')}\n"
                if r.get("result_df") is not None and not r["result_df"].empty:
                    df = r["result_df"]
                    entry += f"**Data ({len(df)} rows):**\n{df.head(25).to_markdown(index=False)}\n"
                if r.get("sql_query"):
                    entry += f"**SQL:** {r['sql_query']}\n"
            results_context.append(entry)

        user_content = f"**Original Question:** {question}\n\n"
        if memory_context:
            user_content += f"**Conversation Memory:**\n{memory_context[:1000]}\n\n"
        user_content += "**Query Results:**\n\n" + "\n".join(results_context)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        return self._call_llm(messages, max_tokens=4096)

    # ------------------------------------------------------------------
    # Conversation persistence
    # ------------------------------------------------------------------
    def write_conversation(
        self,
        user_email: str,
        question: str,
        report: str,
        plan_json: list[dict],
        key_entities: Optional[dict] = None,
        genie_calls: int = 0,
        execution_time: float = 0.0,
        session_id: Optional[str] = None,
    ) -> Optional[str]:
        """Write conversation to Lakebase. Returns the session_id."""
        try:
            if not session_id:
                # Create a new session
                topic = question[:100] if question else "Untitled"
                session_id = str(uuid.uuid4())
                execute_insert(
                    """
                    INSERT INTO bi_conversation_sessions (session_id, user_email, topic_tag, message_count)
                    VALUES (%s, %s, %s, 2)
                    RETURNING session_id
                    """,
                    (session_id, user_email, topic),
                )
            else:
                # Update existing session
                execute_insert(
                    """
                    UPDATE bi_conversation_sessions
                    SET last_message_at = now(), message_count = message_count + 2
                    WHERE session_id = %s
                    """,
                    (session_id,),
                )

            # Write user message
            execute_insert(
                """
                INSERT INTO bi_conversation_messages (message_id, session_id, role, content)
                VALUES (%s, %s, 'user', %s)
                """,
                (str(uuid.uuid4()), session_id, question),
            )

            # Write assistant message
            genie_calls_data = [{"step": s.get("step"), "genie": s.get("genie"), "query": s.get("query")} for s in plan_json]
            execute_insert(
                """
                INSERT INTO bi_conversation_messages (message_id, session_id, role, content, plan_json, genie_calls_json, key_entities_json)
                VALUES (%s, %s, 'agent', %s, %s, %s, %s)
                """,
                (
                    str(uuid.uuid4()),
                    session_id,
                    report,
                    json.dumps(plan_json),
                    json.dumps(genie_calls_data),
                    json.dumps(key_entities or {}),
                ),
            )

            return session_id
        except Exception as e:
            logger.warning("Failed to write conversation to Lakebase: %s", e)
            return session_id

    # ------------------------------------------------------------------
    # Write audit log
    # ------------------------------------------------------------------
    def write_audit_log(
        self,
        schedule_id: Optional[str],
        run_type: str,
        status: str,
        question: str,
        plan_json: list[dict],
        memory_used: bool,
        genie_calls: int,
        execution_time: float,
        report_html: str = "",
        alert_breached: bool = False,
        alert_breach_value: str = "",
    ) -> Optional[str]:
        """Write an entry to the bi_report_audit_log table."""
        try:
            return execute_insert(
                """
                INSERT INTO bi_report_audit_log
                    (run_id, schedule_id, run_started_at, run_completed_at, status,
                     agent_plan_json, alert_breached, alert_breach_value)
                VALUES (%s, %s, now(), now(), %s, %s, %s, %s)
                RETURNING run_id
                """,
                (
                    str(uuid.uuid4()),
                    schedule_id,
                    status,
                    json.dumps(plan_json),
                    alert_breached,
                    float(alert_breach_value) if alert_breach_value else None,
                ),
            )
        except Exception as e:
            logger.warning("Failed to write audit log: %s", e)
            return None

    # ------------------------------------------------------------------
    # Full pipeline
    # ------------------------------------------------------------------
    def run(
        self,
        question: str,
        user_email: str,
        session_id: Optional[str] = None,
        progress_callback: Optional[Callable] = None,
    ) -> dict:
        """Full pipeline: memory -> plan -> execute -> synthesize -> persist.

        Returns dict with keys: report, plan, results, memory_used, session_id,
        genie_calls, execution_time.
        """
        start_time = time.time()

        # 1. Build memory context
        memory_context = self.build_memory_context(user_email)
        memory_used = bool(memory_context)

        # 2. Plan
        plan = self.plan(question, memory_context)

        # 3. Execute
        results = self.execute_plan(plan, progress_callback=progress_callback)
        genie_calls = len(results)

        # 4. Synthesize
        report = self.synthesize(question, results, memory_context)

        execution_time = round(time.time() - start_time, 2)

        # 5. Evaluate whether findings warrant an alert
        alert_eval = self.evaluate_alert(report, question)
        alert_breached = alert_eval.get("breached", False)

        # 6. Extract key entities from the report using LLM
        key_entities = self._extract_entities(question, report)

        # 7. Persist conversation
        session_id = self.write_conversation(
            user_email=user_email,
            question=question,
            report=report,
            plan_json=plan,
            key_entities=key_entities,
            genie_calls=genie_calls,
            execution_time=execution_time,
            session_id=session_id,
        )

        # 8. Audit log
        self.write_audit_log(
            schedule_id=None,
            run_type="manual",
            status="success",
            question=question,
            plan_json=plan,
            memory_used=memory_used,
            genie_calls=genie_calls,
            execution_time=execution_time,
            report_html=report,
            alert_breached=alert_breached,
            alert_breach_value=str(alert_eval.get("key_metric_value", "")) if alert_eval.get("key_metric_value") is not None else "",
        )

        return {
            "report": report,
            "plan": plan,
            "results": results,
            "memory_used": memory_used,
            "session_id": session_id,
            "genie_calls": genie_calls,
            "execution_time": execution_time,
            "alert_breached": alert_breached,
            "alert_eval": alert_eval,
        }

    # ------------------------------------------------------------------
    # Alert evaluation
    # ------------------------------------------------------------------
    def evaluate_alert(self, report: str, alert_question: str, threshold_context: str = "") -> dict:
        """Ask the LLM to evaluate whether a report contains an actionable alert.

        Returns dict with keys: breached (bool), summary (str), severity (str).
        """
        system_prompt = """You are an alert evaluation system for Brand Intelligence brand management.
You are given a Supervisor Agent report that was generated from a monitoring question.
Your job is to determine whether the report contains findings that warrant alerting stakeholders.

Evaluate whether there is a genuine, actionable issue — not just normal business metrics.
An alert should fire when:
- A metric has degraded significantly (accuracy drop, revenue gap spike, stockout risk)
- There is an emerging trend that needs attention before it becomes a problem
- Something has changed materially from expected or prior performance

An alert should NOT fire when:
- Metrics are within normal/expected ranges
- The report shows stable or improving performance
- The findings are routine and don't require action

Return ONLY a JSON object with these keys:
  - "breached": true or false
  - "summary": 1-2 sentence summary of what triggered the alert (or why it didn't)
  - "severity": "critical", "high", "medium", or "low" (only meaningful if breached is true)
  - "key_metric_value": the most relevant numeric value from the report (as a number, or null)

Return ONLY valid JSON, no markdown fences."""

        user_content = f"**Monitoring Question:** {alert_question}\n\n"
        if threshold_context:
            user_content += f"**User-Defined Context:** {threshold_context}\n\n"
        user_content += f"**Agent Report:**\n{report[:3000]}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]

        raw = self._call_llm(messages, max_tokens=512)
        logger.info("Alert evaluation raw response: %s", raw[:500])
        try:
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
            result = json.loads(cleaned)
            logger.info("Alert evaluation result: breached=%s, severity=%s", result.get("breached"), result.get("severity"))
            return result
        except Exception as e:
            logger.warning("Could not parse alert evaluation (raw=%s): %s", raw[:200], e)
            return {"breached": False, "summary": "Could not evaluate alert", "severity": "low", "key_metric_value": None}

    def _extract_entities(self, question: str, report: str) -> dict:
        """Extract key entities (customers, SKUs, regions) from the report."""
        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "Extract key entities from this Q&A. Return a JSON object with keys: "
                        '"customers" (list), "skus" (list), "regions" (list), "metrics" (list), "time_periods" (list). '
                        "Only include entities that are explicitly mentioned. Return ONLY valid JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\n\nReport excerpt: {report[:1500]}",
                },
            ]
            raw = self._call_llm(messages, max_tokens=512)
            cleaned = raw.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned[3:]
                if cleaned.endswith("```"):
                    cleaned = cleaned[:-3]
                cleaned = cleaned.strip()
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:].strip()
            return json.loads(cleaned)
        except Exception:
            return {"customers": [], "skus": [], "regions": [], "metrics": [], "time_periods": []}
