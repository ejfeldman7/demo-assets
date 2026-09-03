"""
Genie conversation proxy for the popup chat panel.

The frontend (GeniePanel.tsx) makes a single call to POST /api/genie/ask and awaits the
final answer directly -- no separate poll call from the client -- so this endpoint does
the full start/continue-conversation + poll-until-complete cycle server-side before
responding. See fe-internal-tools:genie-rooms skill for the underlying REST pattern
(start-conversation / messages / poll message / query-result).

The Genie Space itself (space_id below) is built ONLY on 3 narrow, pre-scoped views --
see setup/create_scoped_views.py + setup/create_genie_space.py for the actual data
model / access boundary. This proxy is just plumbing; it has no bearing on Genie's scope.
"""
import asyncio
import re
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from ..config import get_genie_space_id, get_workspace_client
from ..ratelimit import genie_rate_limit
from ..retry import is_transient, retry_transient

router = APIRouter(prefix="/genie", tags=["genie"], dependencies=[Depends(genie_rate_limit)])

_POLL_INTERVAL_SECONDS = 2
_POLL_TIMEOUT_SECONDS = 60

# Genie always issues UUID-shaped conversation ids -- enforcing that shape here closes
# off a client from injecting extra path segments into the f-string-built REST path
# below, which is called with this app's own service-principal credentials.
_UUID_RE = re.compile(r"^[0-9a-fA-F-]{1,64}$")


class AskRequest(BaseModel):
    # Bounded so a client can't post an unbounded blob (each ask spends a Genie/warehouse
    # call); 4000 chars is far more than any real schema question.
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: Optional[str] = None

    @field_validator("conversation_id")
    @classmethod
    def _validate_conversation_id(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and not _UUID_RE.match(v):
            raise ValueError("conversation_id must be a UUID-shaped identifier")
        return v


def _extract_answer(message: dict) -> str:
    texts = [
        a["text"]["content"]
        for a in message.get("attachments", [])
        if "text" in a and a["text"].get("content")
    ]
    if texts:
        return "\n\n".join(texts)
    if message.get("status") == "FAILED":
        return "Genie couldn't answer that question."
    return "Genie didn't return a text answer for that question."


async def _poll_until_done(client, space_id: str, conversation_id: str, message_id: str) -> dict:
    """Poll for up to _POLL_TIMEOUT_SECONDS. The databricks-sdk client is synchronous, so
    each call is offloaded to a thread (asyncio.to_thread) and the wait between polls uses
    asyncio.sleep -- a blocking time.sleep() here would freeze the whole FastAPI event
    loop (and every other concurrent request) for the entire poll duration."""
    deadline = time.time() + _POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        try:
            message = await asyncio.to_thread(
                client.api_client.do,
                method="GET",
                path=f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}",
            )
        except Exception as exc:  # noqa: BLE001
            # A transient blip mid-poll shouldn't abort an answer that's still being
            # generated -- swallow it and let the next poll (within the same deadline) try
            # again. A non-transient error (e.g. the conversation vanished, an auth
            # failure) is real and re-raised so the caller sees a 500 rather than hanging
            # until the deadline.
            if not is_transient(exc):
                raise
            await asyncio.sleep(_POLL_INTERVAL_SECONDS)
            continue
        if message.get("status") in ("COMPLETED", "FAILED"):
            return message
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)
    raise HTTPException(status_code=504, detail="Genie timed out waiting for a response.")


@router.post("/ask")
async def ask(req: AskRequest):
    space_id = get_genie_space_id()
    if not space_id:
        raise HTTPException(
            status_code=503,
            detail="No Genie Space configured. Run setup/create_scoped_views.py and "
            "setup/create_genie_space.py, or set GENIE_SPACE_ID.",
        )

    # DELIBERATE SCOPING NOTE (OBO): Genie always runs as the app's own service principal
    # (get_workspace_client()), NOT the logged-in user's forwarded token -- even when the
    # rest of the app is in on_behalf_of_user mode, where the ERD graph IS privilege-filtered
    # per user (get_query_client()). This is an intentional, bounded exception, not an
    # oversight: the Genie Space is built over 3 narrow, pre-scoped metadata views
    # (setup/create_scoped_views.py) that expose only object names/comments/tags -- schema
    # shape, never row data -- so answering them as the shared SP leaks no customer data. The
    # alternative (Genie under each user's token) needs every end user granted SELECT on the
    # views + access to the Genie Space and is fragile through the conversations API; not worth
    # it for a metadata-exploration side panel. If a deployment ever needs Genie scoped
    # per-user, hide the panel in OBO mode rather than half-scoping it. See README "Auth modes".
    client = get_workspace_client()
    try:
        if req.conversation_id:
            # attempts=2: a transient blip before the message is created is safe to repeat;
            # the worst case (a request that landed but whose response was lost) is a
            # harmless duplicate question in the conversation, never data loss.
            resp = await asyncio.to_thread(
                lambda: retry_transient(
                    lambda: client.api_client.do(
                        method="POST",
                        path=f"/api/2.0/genie/spaces/{space_id}/conversations/{req.conversation_id}/messages",
                        body={"content": req.message},
                    ),
                    label="genie_message", attempts=2,
                )
            )
            conversation_id = req.conversation_id
            message_id = resp.get("id") or resp.get("message_id")
        else:
            resp = await asyncio.to_thread(
                lambda: retry_transient(
                    lambda: client.api_client.do(
                        method="POST",
                        path=f"/api/2.0/genie/spaces/{space_id}/start-conversation",
                        body={"content": req.message},
                    ),
                    label="genie_start", attempts=2,
                )
            )
            # .get() rather than direct indexing -- keeps this resilient the same way the
            # continue-conversation branch above already is, in case a future Genie API
            # revision nests these fields differently.
            conversation_id = resp.get("conversation_id") or (resp.get("conversation") or {}).get("id")
            message_id = resp.get("message_id") or (resp.get("message") or {}).get("id")
            if not conversation_id or not message_id:
                raise HTTPException(status_code=502, detail=f"Unexpected Genie response shape: {resp}")

        message = await _poll_until_done(client, space_id, conversation_id, message_id)
        return {
            "conversation_id": conversation_id,
            "message_id": message_id,
            "status": message.get("status"),
            "answer": _extract_answer(message),
        }
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))
