"""
Vega-Lite visualization utilities for Brand Intelligence reports.

Extracts Vega-Lite specs from LLM-generated markdown, renders them to PNG
for PDF embedding, and provides helpers for Streamlit display.

Handles two LLM output patterns:
1. Fenced: ```vega-lite\n{...}\n```
2. Unfenced: raw JSON object with "mark" + "encoding" keys inline in markdown
"""

import io
import json
import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# Pattern 1: ```vega-lite fenced code blocks
# Capture everything between fences — JSON parsing handles brace matching
_VEGALITE_FENCED = re.compile(r"```(?:vega-lite|json)\s*\n(.*?)```", re.DOTALL)

# Pattern 2: unfenced JSON objects that span multiple lines starting with {
# We'll use bracket-counting in _find_unfenced_specs() instead of regex.

_VEGALITE_KEYS = {"mark", "encoding"}  # minimum keys for a Vega-Lite spec


def _is_vegalite_spec(obj: dict) -> bool:
    """Check if a parsed JSON dict looks like a Vega-Lite spec."""
    return isinstance(obj, dict) and _VEGALITE_KEYS.issubset(obj.keys())


def _find_all_specs(markdown: str) -> list[tuple[int, int, dict]]:
    """Find all Vega-Lite specs in markdown, returning (start, end, spec) tuples.

    Detects both fenced and unfenced JSON blocks.
    """
    found = []
    seen_ranges = set()

    # Strategy 1: fenced blocks
    for match in _VEGALITE_FENCED.finditer(markdown):
        try:
            spec = json.loads(match.group(1))
            if _is_vegalite_spec(spec):
                found.append((match.start(), match.end(), spec))
                seen_ranges.add((match.start(), match.end()))
        except json.JSONDecodeError:
            pass

    # Strategy 2: unfenced JSON — find { at line start, bracket-count to closing }
    for match in re.finditer(r'^\s*\{', markdown, re.MULTILINE):
        start = match.start()
        # Skip if already captured by a fenced block
        if any(s <= start < e for s, e in seen_ranges):
            continue
        depth = 0
        for i in range(start, len(markdown)):
            if markdown[i] == '{':
                depth += 1
            elif markdown[i] == '}':
                depth -= 1
                if depth == 0:
                    candidate = markdown[start:i + 1].strip()
                    try:
                        spec = json.loads(candidate)
                        if _is_vegalite_spec(spec):
                            found.append((start, i + 1, spec))
                            seen_ranges.add((start, i + 1))
                    except json.JSONDecodeError:
                        pass
                    break

    # Sort by position in document
    found.sort(key=lambda x: x[0])
    return found


def extract_vegalite_specs(markdown: str) -> list[dict]:
    """Extract all Vega-Lite specs from markdown."""
    return [spec for _, _, spec in _find_all_specs(markdown)]


def split_markdown_and_charts(markdown: str) -> list[dict]:
    """Split markdown into alternating text and chart segments.

    Returns a list of dicts:
      {"type": "text", "content": "..."}
      {"type": "vega-lite", "spec": {...}}
    """
    specs = _find_all_specs(markdown)
    if not specs:
        return [{"type": "text", "content": markdown}]

    segments = []
    last_end = 0
    for start, end, spec in specs:
        text_before = markdown[last_end:start].strip()
        if text_before:
            segments.append({"type": "text", "content": text_before})
        segments.append({"type": "vega-lite", "spec": spec})
        last_end = end
    remaining = markdown[last_end:].strip()
    if remaining:
        segments.append({"type": "text", "content": remaining})
    return segments


def strip_vegalite_blocks(markdown: str) -> str:
    """Remove all Vega-Lite specs from markdown."""
    specs = _find_all_specs(markdown)
    if not specs:
        return markdown
    result = []
    last_end = 0
    for start, end, _ in specs:
        result.append(markdown[last_end:start])
        last_end = end
    result.append(markdown[last_end:])
    return "".join(result).strip()


def render_in_streamlit(markdown_text: str, highlight_exec_summary: bool = False) -> None:
    """Render a markdown report in Streamlit, drawing any embedded Vega-Lite
    specs as native interactive charts via ``st.vega_lite_chart``.

    This is the single entry point the app uses to display LLM report output —
    text and charts interleaved in document order. Streamlit is imported lazily
    so this module stays importable in non-Streamlit (Databricks Job) contexts
    that only need the spec-extraction / PNG helpers above.
    """
    import streamlit as st

    for seg in split_markdown_and_charts(markdown_text):
        if seg["type"] == "vega-lite":
            try:
                st.vega_lite_chart(seg["spec"], use_container_width=True)
            except Exception as e:  # skip invalid specs rather than break the report
                logger.warning("Skipping invalid Vega-Lite spec: %s", e)
        else:
            # Escape $ so Streamlit doesn't treat it as LaTeX math
            text = seg["content"].replace("$", "\\$")
            if highlight_exec_summary and "## Executive Summary" in text:
                _render_with_exec_summary(st, text)
            else:
                st.markdown(text)


def _render_with_exec_summary(st, text: str) -> None:
    """Render report text, wrapping the Executive Summary section in a callout box."""
    parts = text.split("## Executive Summary", 1)
    if len(parts) <= 1:
        st.markdown(text)
        return
    pre = parts[0].strip()
    rest = parts[1]
    next_heading_pos = rest.find("\n## ")
    if next_heading_pos > 0:
        exec_summary = rest[:next_heading_pos].strip()
        remainder = rest[next_heading_pos:]
    else:
        exec_summary = rest.strip()
        remainder = ""
    if pre:
        st.markdown(pre)
    st.markdown(
        f'<div class="exec-summary"><h3>Executive Summary</h3>{exec_summary}</div>',
        unsafe_allow_html=True,
    )
    if remainder:
        st.markdown(remainder)


def spec_to_png(spec: dict, scale: int = 2) -> Optional[bytes]:
    """Render a Vega-Lite spec to PNG bytes using vl-convert.

    Returns None if vl-convert is not installed or rendering fails.
    """
    try:
        import vl_convert as vlc
    except ImportError:
        logger.warning("vl-convert-python not installed — skipping chart render")
        return None
    try:
        spec_str = json.dumps(spec)
        return vlc.vegalite_to_png(spec_str, scale=scale)
    except Exception as e:
        logger.warning("Vega-Lite to PNG failed: %s", e)
        return None
