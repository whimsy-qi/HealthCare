from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from jose import jwt

import api_server
from core.models import (
    ChatMessage,
    ChatRun,
    ChatSession,
    HealthProfile,
    QaReviewCandidate,
    UploadedFile,
    User,
)


@pytest.fixture
def api_security_context(make_sqlite_session_factory, monkeypatch):
    tables = [
        User.__table__,
        HealthProfile.__table__,
        ChatSession.__table__,
        ChatMessage.__table__,
        ChatRun.__table__,
        UploadedFile.__table__,
        QaReviewCandidate.__table__,
    ]
    SessionLocal = make_sqlite_session_factory(tables)

    monkeypatch.setattr(api_server, "SessionLocal", SessionLocal)
    monkeypatch.setattr(api_server, "_ensure_chat_schema_migrated", lambda _db: None)
    monkeypatch.setattr(api_server, "_ensure_admin_schema_migrated", lambda _db: None)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    api_server.app.dependency_overrides[api_server.get_db] = override_get_db

    db = SessionLocal()
    alice = User(username="alice", password_hash="hash", role="user", is_active=True)
    bob = User(username="bob", password_hash="hash", role="user", is_active=True)
    admin = User(username="admin", password_hash="hash", role="admin", is_active=True)
    db.add_all([alice, bob, admin])
    db.flush()

    db.add_all(
        [
            HealthProfile(user_id=alice.id, profile_data={"name": "Alice", "age": 31}),
            HealthProfile(user_id=bob.id, profile_data={"name": "Bob", "age": 45}),
        ]
    )

    alice_session = ChatSession(user_id=alice.id, title="Alice session")
    bob_session = ChatSession(user_id=bob.id, title="Bob session")
    db.add_all([alice_session, bob_session])
    db.flush()

    db.add(
        ChatMessage(
            session_id=bob_session.id,
            role="user",
            content="Bob private message",
            meta_data={"scope": "bob-only"},
        )
    )
    db.add(
        QaReviewCandidate(
            status="pending",
            domain="general",
            safety_status="needs_review",
            question="QA review seed question",
            answer="QA review seed answer",
            hallucination_status={"action": "WARN"},
            user_id=alice.id,
            session_id=alice_session.id,
        )
    )
    db.commit()

    context = {
        "client": TestClient(api_server.app),
        "alice_headers": _headers_for("alice"),
        "bob_headers": _headers_for("bob"),
        "admin_headers": _headers_for("admin"),
        "alice_session_id": alice_session.id,
        "bob_session_id": bob_session.id,
    }

    db.close()
    try:
        yield context
    finally:
        context["client"].close()
        api_server.app.dependency_overrides.clear()


def _headers_for(username: str) -> dict:
    token = api_server.create_access_token({"sub": username})
    return {"Authorization": f"Bearer {token}"}


def _expired_headers_for(username: str) -> dict:
    token = jwt.encode(
        {"sub": username, "exp": datetime.utcnow() - timedelta(minutes=1)},
        api_server.SECRET_KEY,
        algorithm=api_server.ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


def test_profile_requires_login(api_security_context):
    response = api_security_context["client"].get("/api/profile")

    assert response.status_code == 401


def test_profile_rejects_expired_jwt(api_security_context):
    response = api_security_context["client"].get(
        "/api/profile",
        headers=_expired_headers_for("alice"),
    )

    assert response.status_code == 401


def test_profile_is_scoped_to_authenticated_user(api_security_context):
    client = api_security_context["client"]

    alice_response = client.get("/api/profile", headers=api_security_context["alice_headers"])
    bob_response = client.get("/api/profile", headers=api_security_context["bob_headers"])

    assert alice_response.status_code == 200
    assert bob_response.status_code == 200
    assert alice_response.json()["profile_data"]["name"] == "Alice"
    assert bob_response.json()["profile_data"]["name"] == "Bob"
    assert alice_response.json()["profile_data"] != bob_response.json()["profile_data"]


def test_user_cannot_read_other_users_session_messages(api_security_context):
    response = api_security_context["client"].get(
        f"/api/sessions/{api_security_context['bob_session_id']}/messages",
        headers=api_security_context["alice_headers"],
    )

    assert response.status_code == 404


def test_user_cannot_chat_in_other_users_session(api_security_context):
    response = api_security_context["client"].post(
        "/api/chat",
        json={
            "session_id": api_security_context["bob_session_id"],
            "query": "继续这个会话",
            "messages_history": [],
        },
        headers=api_security_context["alice_headers"],
    )

    assert response.status_code == 404


def test_user_cannot_upload_image_to_other_users_session(api_security_context):
    response = api_security_context["client"].post(
        "/api/upload_image",
        json={
            "session_id": api_security_context["bob_session_id"],
            "image_base64": "data:image/png;base64,AAAA",
        },
        headers=api_security_context["alice_headers"],
    )

    assert response.status_code == 404


def test_regular_user_cannot_list_qa_review_candidates(api_security_context):
    response = api_security_context["client"].get(
        "/api/admin/qa-review/candidates",
        headers=api_security_context["alice_headers"],
    )

    assert response.status_code == 403


def test_admin_can_list_qa_review_candidates(api_security_context):
    response = api_security_context["client"].get(
        "/api/admin/qa-review/candidates",
        headers=api_security_context["admin_headers"],
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["question"] == "QA review seed question"
