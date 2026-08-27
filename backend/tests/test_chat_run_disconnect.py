import asyncio

import pytest

import api_server
import core.database as core_database
from core.models import ChatMessage, ChatRun, ChatSession, QaReviewCandidate, User


@pytest.mark.asyncio
async def test_chat_generator_persists_answer_after_client_disconnect(make_sqlite_session_factory, monkeypatch):
    SessionLocal = make_sqlite_session_factory([
        User.__table__,
        ChatSession.__table__,
        ChatMessage.__table__,
        ChatRun.__table__,
        QaReviewCandidate.__table__,
    ])
    monkeypatch.setattr(api_server, "SessionLocal", SessionLocal)
    monkeypatch.setattr(core_database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(api_server, "QA_REVIEW_ENABLED", False)

    db = SessionLocal()
    user = User(username="alice", password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.flush()
    session = ChatSession(user_id=user.id, title="Disconnect test")
    db.add(session)
    db.flush()
    run_id = "disconnect-run"
    run = ChatRun(run_id=run_id, session_id=session.id, user_id=user.id, status="running")
    session.active_run_id = run_id
    db.add(run)
    db.add(ChatMessage(session_id=session.id, run_id=run_id, role="user", content="测试问题"))
    db.commit()
    session_id = session.id
    db.close()

    async def fake_ainvoke(_initial_state):
        await asyncio.sleep(0.05)
        return {
            "final_answer": "这是后台完成的回复",
            "current_route": "general",
            "trace_data": {},
            "current_slots": {},
            "options": [],
            "is_finished": True,
        }

    monkeypatch.setattr(api_server.app_graph, "ainvoke", fake_ainvoke)

    async def consume_stream():
        async for _ in api_server._chat_sse_generator(
            {"turn_count": 1, "current_slots": {}, "query": "测试问题", "user_id": user.id},
            session_id,
            run_id,
        ):
            await asyncio.sleep(0)

    task = asyncio.create_task(consume_stream())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0.2)

    db = SessionLocal()
    ai_messages = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id,
        ChatMessage.run_id == run_id,
        ChatMessage.role == "ai",
    ).all()
    saved_run = db.query(ChatRun).filter(ChatRun.run_id == run_id).first()
    saved_session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    db.close()

    assert [message.content for message in ai_messages] == ["这是后台完成的回复"]
    assert saved_run.status == "succeeded"
    assert saved_run.ai_message_id == ai_messages[0].id
    assert saved_session.active_run_id is None


@pytest.mark.asyncio
async def test_chat_generator_tracks_disconnected_run_until_graph_finishes(make_sqlite_session_factory, monkeypatch):
    SessionLocal = make_sqlite_session_factory([
        User.__table__,
        ChatSession.__table__,
        ChatMessage.__table__,
        ChatRun.__table__,
        QaReviewCandidate.__table__,
    ])
    monkeypatch.setattr(api_server, "SessionLocal", SessionLocal)
    monkeypatch.setattr(core_database, "SessionLocal", SessionLocal)
    monkeypatch.setattr(api_server, "QA_REVIEW_ENABLED", False)
    api_server._background_chat_tasks.clear()

    db = SessionLocal()
    user = User(username="bob", password_hash="hash", role="user", is_active=True)
    db.add(user)
    db.flush()
    session = ChatSession(user_id=user.id, title="Tracked disconnect test")
    db.add(session)
    db.flush()
    run_id = "tracked-disconnect-run"
    run = ChatRun(run_id=run_id, session_id=session.id, user_id=user.id, status="running")
    session.active_run_id = run_id
    db.add(run)
    db.add(ChatMessage(session_id=session.id, run_id=run_id, role="user", content="测试问题"))
    db.commit()
    session_id = session.id
    db.close()

    finish_graph = asyncio.Event()

    async def fake_ainvoke(_initial_state):
        await finish_graph.wait()
        return {
            "final_answer": "这是断开后继续完成的回复",
            "current_route": "general",
            "trace_data": {},
            "current_slots": {},
            "options": [],
            "is_finished": True,
        }

    monkeypatch.setattr(api_server.app_graph, "ainvoke", fake_ainvoke)

    async def consume_stream():
        async for _ in api_server._chat_sse_generator(
            {"turn_count": 1, "current_slots": {}, "query": "测试问题", "user_id": user.id},
            session_id,
            run_id,
        ):
            await asyncio.sleep(0)

    task = asyncio.create_task(consume_stream())
    await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    await asyncio.sleep(0)
    background_tasks = list(api_server._background_chat_tasks)
    assert len(background_tasks) == 1

    db = SessionLocal()
    saved_run = db.query(ChatRun).filter(ChatRun.run_id == run_id).first()
    ai_count = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id,
        ChatMessage.run_id == run_id,
        ChatMessage.role == "ai",
    ).count()
    db.close()
    assert saved_run.status == "running"
    assert saved_run.ai_message_id is None
    assert ai_count == 0

    finish_graph.set()
    await asyncio.wait_for(asyncio.gather(*background_tasks), timeout=1)
    assert not api_server._background_chat_tasks

    db = SessionLocal()
    ai_messages = db.query(ChatMessage).filter(
        ChatMessage.session_id == session_id,
        ChatMessage.run_id == run_id,
        ChatMessage.role == "ai",
    ).all()
    saved_run = db.query(ChatRun).filter(ChatRun.run_id == run_id).first()
    saved_session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    db.close()

    assert [message.content for message in ai_messages] == ["这是断开后继续完成的回复"]
    assert saved_run.status == "succeeded"
    assert saved_run.ai_message_id == ai_messages[0].id
    assert saved_session.active_run_id is None
