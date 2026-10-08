"""FAQ チャットの Web アプリ。

    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .chat import FaqChat
from .config import settings
from .index import FaqIndex
from .sync import index_path, sync_from_sharepoint

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("faq-chat")
STATIC = Path(__file__).resolve().parent.parent / "static"

MAX_MESSAGE_CHARS = 2000
MAX_TURNS = 20


class State:
    index: FaqIndex = FaqIndex([])
    chat: FaqChat | None = None


state = State()


async def _sync_loop() -> None:
    interval = settings.sync_interval_minutes * 60
    while True:
        try:
            state.index = await asyncio.to_thread(sync_from_sharepoint)
        except Exception:
            log.exception("SharePoint の同期に失敗しました。前回のインデックスで継続します")
        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(_: FastAPI):
    state.index = FaqIndex.load(index_path(settings))
    state.chat = FaqChat(settings)
    log.info("インデックス読み込み: %d チャンク", len(state.index.chunks))
    task = None
    if settings.sharepoint_configured and settings.sync_interval_minutes > 0:
        task = asyncio.create_task(_sync_loop())
    yield
    if task:
        task.cancel()


app = FastAPI(title="社内FAQチャット", lifespan=lifespan)


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=20000)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1)


@app.post("/api/chat")
async def chat(req: ChatRequest) -> StreamingResponse:
    messages = [m.model_dump() for m in req.messages][-MAX_TURNS * 2 :]
    if messages[0]["role"] != "user":
        messages = messages[1:]
    if not messages or messages[-1]["role"] != "user":
        raise HTTPException(400, "最後のメッセージはユーザーの質問である必要があります")
    if len(messages[-1]["content"]) > MAX_MESSAGE_CHARS:
        raise HTTPException(400, f"質問は{MAX_MESSAGE_CHARS}文字以内で入力してください")
    if not state.index.chunks:
        raise HTTPException(503, "FAQ データがまだ読み込まれていません。管理者に連絡してください")

    async def events():
        async for event in state.chat.stream_answer(state.index, messages):
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


@app.get("/api/status")
async def status() -> dict:
    return {
        "chunks": len(state.index.chunks),
        "pages": len({c.page_id for c in state.index.chunks}),
        "mode": "full_context" if 0 < state.index.total_chars <= settings.full_context_max_chars else "search",
    }


@app.get("/")
async def root() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
