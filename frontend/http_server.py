"""Agent Shell HTTP Server — 暴露 Api 类方法为 REST endpoints，供 Electron 渲染进程调用。"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Any

_ROOT_DIR = Path(__file__).resolve().parent.parent
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from aiohttp import web
from aiohttp.web import Request, Response

from frontend.bridge import Api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent_shell_http] %(levelname)s: %(message)s",
)
logger = logging.getLogger("agent_shell_http")

_api: Api | None = None

# per-thread lock for chat operations（防止同一 thread 并发请求）
_chat_locks: dict[str, asyncio.Lock] = {}
_chat_locks_mutex = asyncio.Lock()


async def _acquire_chat_lock(thread_id: str) -> asyncio.Lock:
    async with _chat_locks_mutex:
        if thread_id not in _chat_locks:
            _chat_locks[thread_id] = asyncio.Lock()
        return _chat_locks[thread_id]


def get_api() -> Api:
    global _api
    if _api is None:
        _api = Api()
    return _api


async def init_agent(app: web.Application) -> None:
    """在服务器启动时异步初始化 agent（on_startup 回调）。"""
    global _api
    root_dir: str = app.get("root_dir", str(_ROOT_DIR))
    try:
        import aiosqlite
        from src.core.config import Settings
        from src.core.llm import build_llm
        from src.agent.builder import build_supervisor_agent
        from src.agent.memory import MemoryManager
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        settings = Settings.from_env()
        llm = build_llm(settings)
        conn = await aiosqlite.connect(settings.db_path)
        checkpointer = AsyncSqliteSaver(conn)
        await checkpointer.setup()
        bundle = build_supervisor_agent(llm, checkpointer=checkpointer)
        from src.tracing.store import TraceStore
        from src.tracing.api import TracingAPI

        trace_store = TraceStore()
        trace_store.cleanup()
        tracing_api = TracingAPI(trace_store)
        memory_manager = MemoryManager(
            bundle.agent, llm, settings,
            profile_middleware=bundle.profile_middleware,
            memory_middleware=bundle.memory_middleware,
            memory_store=bundle.memory_store,
        )
        _api = Api(
            root_dir=root_dir,
            agent=bundle.agent,
            memory_manager=memory_manager,
            settings=settings,
            memory_store=bundle.memory_store,
            tools=bundle.tools,
            tracing_api=tracing_api,
        )
        app["_db_conn"] = conn
        logger.info("Agent initialized with AsyncSqliteSaver (db=%s)", settings.db_path)
    except Exception as exc:
        logger.warning("Agent initialization failed (chat disabled): %s", exc)
        _api = Api(root_dir=root_dir)


async def close_agent(app: web.Application) -> None:
    conn = app.get("_db_conn")
    if conn is not None:
        await conn.close()
        logger.info("Database connection closed")


def json_response(data: Any, *, status: int = 200) -> Response:
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return Response(body=body, status=status, content_type="application/json")


def json_request(req: Request) -> dict:
    try:
        return req.json()
    except Exception:
        raise web.HTTPBadRequest(text="Invalid JSON body")


@web.middleware
async def cors_middleware(request: Request, handler):
    if request.method == "OPTIONS":
        resp = Response(status=200)
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return resp
    return await handler(request)


# ── 路由处理函数 ──


async def health(request: Request) -> Response:
    return json_response({"status": "ok"})


async def get_sessions(request: Request) -> Response:
    return json_response(get_api().get_sessions())


async def post_sessions(request: Request) -> Response:
    result = await get_api().create_session()
    return json_response(result)


async def delete_session(request: Request) -> Response:
    thread_id = request.match_info["thread_id"]
    return json_response({"deleted": get_api().delete_session(thread_id)})


async def get_session_messages(request: Request) -> Response:
    thread_id = request.match_info["thread_id"]
    messages = await get_api().get_messages(thread_id)
    return json_response(messages)


async def post_session_compress(request: Request) -> Response:
    thread_id = request.match_info["thread_id"]
    result = await get_api().compress_session(thread_id)
    return json_response(result)


async def get_settings(request: Request) -> Response:
    return json_response(get_api().get_settings())


async def post_settings(request: Request) -> Response:
    body = await json_request(request)
    get_api().save_settings(body)
    return json_response({"saved": True})


async def get_soul(request: Request) -> Response:
    return json_response(get_api().get_soul())


async def post_soul(request: Request) -> Response:
    body = await json_request(request)
    get_api().save_soul(body)
    return json_response({"saved": True})


async def get_profile(request: Request) -> Response:
    return json_response(get_api().get_profile())


async def post_profile(request: Request) -> Response:
    body = await json_request(request)
    get_api().save_profile(body)
    return json_response({"saved": True})


async def get_tools(request: Request) -> Response:
    return json_response(get_api().get_tools())


# ── Skill 查询（只读，按需加载由 agent 的 load_skill 工具完成） ──


async def get_skills(request: Request) -> Response:
    return json_response(get_api().get_skills())


async def get_skill_detail(request: Request) -> Response:
    name = request.match_info["name"]
    detail = get_api().get_skill_detail(name)
    if detail is None:
        return json_response({"error": f"Skill '{name}' 不存在"}, status=404)
    return json_response(detail)


async def post_chat(request: Request) -> Response:
    body = await json_request(request)
    thread_id = body.get("thread_id", "")
    message = body.get("message", "")
    if not thread_id or not message:
        return json_response({"error": "thread_id 和 message 不能为空"}, status=400)
    lock = await _acquire_chat_lock(thread_id)
    async with lock:
        result = await get_api().chat(thread_id, message)
    return json_response(result)


async def post_chat_stream(request: Request) -> web.StreamResponse:
    body = await json_request(request)
    thread_id = body.get("thread_id", "")
    message = body.get("message", "")
    if not thread_id or not message:
        resp = web.StreamResponse(status=400)
        resp.headers["Content-Type"] = "application/json"
        await resp.prepare(request)
        await resp.write(
            json.dumps({"error": "thread_id 和 message 不能为空"}, ensure_ascii=False).encode("utf-8")
        )
        return resp

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await resp.prepare(request)

    lock = await _acquire_chat_lock(thread_id)
    async with lock:
        chat_gen = get_api().chat_stream(thread_id, message)
        try:
            async for event in chat_gen:
                line = f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                await resp.write(line.encode("utf-8"))
        except ConnectionResetError:
            logger.warning("Client disconnected during SSE stream — closing generator")
            await chat_gen.aclose()
        except Exception as exc:
            try:
                line = f"event: error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
                await resp.write(line.encode("utf-8"))
            except Exception:
                pass
            await chat_gen.aclose()

    return resp


async def post_resume(request: Request) -> Response:
    body = await json_request(request)
    thread_id = body.get("thread_id", "")
    approved = body.get("approved", False)
    if not thread_id:
        return json_response({"error": "thread_id 不能为空"}, status=400)
    lock = await _acquire_chat_lock(thread_id)
    async with lock:
        result = await get_api().resume_chat(thread_id, approved)
    return json_response(result)


async def post_resume_stream(request: Request) -> web.StreamResponse:
    body = await json_request(request)
    thread_id = body.get("thread_id", "")
    approved = body.get("approved", False)
    if not thread_id:
        resp = web.StreamResponse(status=400)
        resp.headers["Content-Type"] = "application/json"
        await resp.prepare(request)
        await resp.write(
            json.dumps({"error": "thread_id 不能为空"}, ensure_ascii=False).encode("utf-8")
        )
        return resp

    resp = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await resp.prepare(request)

    lock = await _acquire_chat_lock(thread_id)
    async with lock:
        resume_gen = get_api().chat_stream(thread_id, "", resume=approved)
        try:
            async for event in resume_gen:
                line = f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"
                await resp.write(line.encode("utf-8"))
        except ConnectionResetError:
            logger.warning("Client disconnected during SSE resume stream — closing generator")
            await resume_gen.aclose()
        except Exception as exc:
            line = f"event: error\ndata: {json.dumps({'error': str(exc)}, ensure_ascii=False)}\n\n"
            await resp.write(line.encode("utf-8"))
            await resume_gen.aclose()

    return resp


# ── Tracing 路由 ──


async def get_traces(request: Request) -> Response:
    q = request.query.get("q", "")
    status = request.query.get("status", "")
    limit = int(request.query.get("limit", "50"))
    offset = int(request.query.get("offset", "0"))
    return json_response(get_api().get_traces(q=q, status=status, limit=limit, offset=offset))


async def get_trace_detail(request: Request) -> Response:
    trace_id = request.match_info["trace_id"]
    return json_response(get_api().get_trace_detail(trace_id))


async def get_trace_spans(request: Request) -> Response:
    trace_id = request.match_info["trace_id"]
    return json_response(get_api().get_trace_spans(trace_id))


async def get_trace_stats(request: Request) -> Response:
    return json_response(get_api().get_trace_stats())


async def get_trace_daily_stats(request: Request) -> Response:
    return json_response(get_api().get_trace_daily_stats())


async def get_trace_cache_stats(request: Request) -> Response:
    return json_response(get_api().get_trace_cache_stats())


async def get_traces_by_session(request: Request) -> Response:
    session_id = request.match_info["session_id"]
    return json_response(get_api().get_traces_by_session(session_id))


# ── 路由注册 ──


def setup_routes(app: web.Application) -> None:
    app.router.add_route("GET", "/health", health)
    app.router.add_route("GET", "/api/sessions", get_sessions)
    app.router.add_route("POST", "/api/sessions", post_sessions)
    app.router.add_route("DELETE", "/api/sessions/{thread_id}", delete_session)
    app.router.add_route("GET", "/api/sessions/{thread_id}/messages", get_session_messages)
    app.router.add_route("POST", "/api/sessions/{thread_id}/compress", post_session_compress)
    app.router.add_route("POST", "/api/chat", post_chat)
    app.router.add_route("POST", "/api/chat/stream", post_chat_stream)
    app.router.add_route("GET", "/api/settings", get_settings)
    app.router.add_route("POST", "/api/settings", post_settings)
    app.router.add_route("GET", "/api/soul", get_soul)
    app.router.add_route("POST", "/api/soul", post_soul)
    app.router.add_route("GET", "/api/profile", get_profile)
    app.router.add_route("POST", "/api/profile", post_profile)
    app.router.add_route("GET", "/api/tools", get_tools)
    app.router.add_route("GET", "/api/skills", get_skills)
    app.router.add_route("GET", "/api/skills/{name}", get_skill_detail)
    app.router.add_route("POST", "/api/chat/resume", post_resume)
    app.router.add_route("POST", "/api/chat/resume/stream", post_resume_stream)
    app.router.add_route("GET", "/api/traces", get_traces)
    app.router.add_route("GET", "/api/traces/stats", get_trace_stats)
    app.router.add_route("GET", "/api/traces/stats/daily", get_trace_daily_stats)
    app.router.add_route("GET", "/api/traces/stats/cache", get_trace_cache_stats)
    app.router.add_route("GET", "/api/traces/{trace_id}", get_trace_detail)
    app.router.add_route("GET", "/api/traces/{trace_id}/spans", get_trace_spans)
    app.router.add_route("GET", "/api/traces/sessions/{session_id}", get_traces_by_session)


# ── 入口 ──


def main() -> None:
    parser = argparse.ArgumentParser(description="Agent Shell HTTP Server")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("AGENT_SHELL_PORT", "18794")),
        help="HTTP server port (default: 18794)",
    )
    parser.add_argument(
        "--root-dir",
        type=str,
        default=str(_ROOT_DIR),
        help="Project root directory",
    )
    args = parser.parse_args()

    app = web.Application(middlewares=[cors_middleware])
    setup_routes(app)
    app["root_dir"] = args.root_dir
    app.on_startup.append(init_agent)
    app.on_cleanup.append(close_agent)

    logger.info("Starting HTTP server on 127.0.0.1:%d", args.port)
    logger.info("API ready — root_dir=%s", args.root_dir)

    loop = asyncio.get_event_loop()
    web.run_app(app, host="127.0.0.1", port=args.port, print=None, access_log=None)


if __name__ == "__main__":
    main()
