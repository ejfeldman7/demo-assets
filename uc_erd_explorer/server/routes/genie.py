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
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..config import get_genie_space_id, get_workspace_client

router = APIRouter(prefix="/genie", tags=["genie"])

_POLL_INTERVAL_SECONDS = 2
_POLL_TIMEOUT_SECONDS = 60


class AskRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


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


def _poll_until_done(client, space_id: str, conversation_id: str, message_id: str) -> dict:
    deadline = time.time() + _POLL_TIMEOUT_SECONDS
    while time.time() < deadline:
        message = client.api_client.do(
            method="GET",
            path=f"/api/2.0/genie/spaces/{space_id}/conversations/{conversation_id}/messages/{message_id}",
        )
        if message.get("status") in ("COMPLETED", "FAILED"):
            return message
        time.sleep(_POLL_INTERVAL_SECONDS)
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

    client = get_workspace_client()
    try:
        if req.conversation_id:
            resp = client.api_client.do(
                method="POST",
                path=f"/api/2.0/genie/spaces/{space_id}/conversations/{req.conversation_id}/messages",
                body={"content": req.message},
            )
            conversation_id = req.conversation_id
            message_id = resp.get("id") or resp.get("message_id")
        else:
            resp = client.api_client.do(
                method="POST",
                path=f"/api/2.0/genie/spaces/{space_id}/start-conversation",
                body={"content": req.message},
            )
            conversation_id = resp["conversation_id"]
            message_id = resp["message_id"]

        message = _poll_until_done(client, space_id, conversation_id, message_id)
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
