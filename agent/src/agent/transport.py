"""SSE adapter with mandatory host-provided authentication/context dependency."""

from contextlib import closing
import json

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.concurrency import run_in_threadpool

from .schemas import UserContext


class StreamRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    message: str = Field(min_length=1, max_length=12000)
    thread_id: str = Field(min_length=1, max_length=200)


def encode_sse(event):
    name = event['event']
    if name not in {'status', 'delta', 'replace', 'sources', 'followups', 'done'}:
        raise ValueError('未知事件类型')
    return f"event: {name}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


def create_router(assistant, context_dependency):
    """Mount this router in the host app; dependency returns an authenticated context.

    No authentication defaults, no identity accepted from request JSON. The host
    must also serialize writes per user/thread if concurrent requests are allowed.
    """
    router = APIRouter(prefix='/agent')

    @router.post('/stream')
    async def stream(request: StreamRequest, context: UserContext = Depends(context_dependency)):
        from fastapi import HTTPException
        if not isinstance(context, UserContext):
            raise HTTPException(401, '缺少已认证的用户上下文')
        if not request.message.strip() or not request.thread_id.strip():
            raise HTTPException(422, '消息和会话 ID 不能为空')

        async def body():
            # Each next() may call a sync model/SQL client; keep it off the event loop.
            sentinel = object()
            with closing(assistant.stream(request.message, context=context, thread_id=request.thread_id)) as events:
                while True:
                    event = await run_in_threadpool(next, events, sentinel)
                    if event is sentinel:
                        break
                    yield encode_sse(event)

        return StreamingResponse(body(), media_type='text/event-stream', headers={
            'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no',
        })

    return router
