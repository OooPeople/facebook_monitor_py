"""Web route request payload helpers。"""

from __future__ import annotations

from fastapi import HTTPException
from fastapi import Request


async def json_object_payload(request: Request) -> dict[str, object]:
    """讀取 JSON object payload，壞格式以 400 回覆而不是冒成 500。"""

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON 格式不正確") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="JSON payload 必須是物件")
    return payload
