from __future__ import annotations

import argparse
import asyncio
import base64
import csv
import io
import json
import os
import sys
import zipfile
import html
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
for path in (PROJECT_ROOT, BACKEND_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("JWT_SECRET_KEY", "system-function-test-jwt")
os.environ.setdefault("DATABASE_URL", "sqlite://")
os.environ.setdefault("OPENAI_API_KEY", "system-function-test-openai-key")
os.environ.setdefault("OPENAI_API_BASE", "http://127.0.0.1")
os.environ.setdefault("CHAT_STORAGE_BACKEND", "local")
os.environ.setdefault("CHAT_LOCAL_OBJECT_ROOT", str(BACKEND_ROOT / ".system-function-object-storage"))
os.environ.setdefault("QA_REVIEW_ADMIN_TOKEN", "system-function-test-qa-token")

from fastapi.testclient import TestClient
from jose import jwt
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@compiles(LONGTEXT, "sqlite")
def _compile_mysql_longtext_for_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


import api_server
from core.models import (
    Article,
    Base,
    ChatMessage,
    ChatRun,
    ChatSession,
    HealthCheckinItem,
    HealthProfile,
    QaReviewCandidate,
    User,
)


def _tiny_png_data_url() -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (2, 2), (255, 255, 255)).save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return f"data:image/png;base64,{encoded}"


@dataclass
class Context:
    client: TestClient
    session_factory: Any
    alice_headers: dict[str, str]
    bob_headers: dict[str, str]
    admin_headers: dict[str, str]
    alice_session_id: int
    bob_session_id: int
    article_id: int
    qa_candidate_id: int
    checkin_code: str
    uploaded_file_id: int | None = None


class FakeNode(dict):
    def __init__(self, element_id: str, labels: list[str], **properties: Any):
        super().__init__(properties)
        self.element_id = element_id
        self.labels = set(labels)


class FakeRelationship(dict):
    def __init__(self, element_id: str, start_node: FakeNode, end_node: FakeNode, rel_type: str, **properties: Any):
        super().__init__(properties)
        self.element_id = element_id
        self.start_node = start_node
        self.end_node = end_node
        self.type = rel_type


def _headers_for(username: str) -> dict[str, str]:
    token = api_server.create_access_token({"sub": username})
    return {"Authorization": f"Bearer {token}"}


def _expired_headers_for(username: str) -> dict[str, str]:
    token = jwt.encode(
        {"sub": username, "exp": datetime.utcnow() - timedelta(minutes=1)},
        api_server.SECRET_KEY,
        algorithm=api_server.ALGORITHM,
    )
    return {"Authorization": f"Bearer {token}"}


def _json_response(response) -> Any:
    try:
        return response.json()
    except Exception:
        return response.text[:4000]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys()) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_simple_xlsx(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers = list(rows[0].keys()) if rows else []

    def cell_ref(col_idx: int, row_idx: int) -> str:
        letters = ""
        col = col_idx
        while col:
            col, rem = divmod(col - 1, 26)
            letters = chr(65 + rem) + letters
        return f"{letters}{row_idx}"

    def cell(value: Any, col_idx: int, row_idx: int) -> str:
        text = "" if value is None else str(value)
        ref = cell_ref(col_idx, row_idx)
        return f'<c r="{ref}" t="inlineStr"><is><t>{html.escape(text)}</t></is></c>'

    sheet_rows = [
        '<row r="1">' + "".join(cell(header, idx, 1) for idx, header in enumerate(headers, start=1)) + "</row>"
    ]
    for row_idx, row in enumerate(rows, start=2):
        sheet_rows.append(
            f'<row r="{row_idx}">'
            + "".join(cell(row.get(header, ""), idx, row_idx) for idx, header in enumerate(headers, start=1))
            + "</row>"
        )
    sheet_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        + "".join(sheet_rows)
        + "</sheetData></worksheet>"
    )
    workbook_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="runs" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="xl/workbook.xml"/></Relationships>'
    )
    workbook_rels_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
        'Target="worksheets/sheet1.xml"/></Relationships>'
    )
    content_types_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types_xml)
        zf.writestr("_rels/.rels", rels_xml)
        zf.writestr("xl/workbook.xml", workbook_xml)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels_xml)
        zf.writestr("xl/worksheets/sheet1.xml", sheet_xml)


def _disable_rate_limit() -> None:
    for obj in (api_server.limiter, getattr(api_server.app.state, "limiter", None)):
        if obj is None:
            continue
        for attr in ("enabled", "_enabled"):
            if hasattr(obj, attr):
                try:
                    setattr(obj, attr, False)
                except Exception:
                    pass


def _patch_external_services(session_factory) -> None:
    _disable_rate_limit()
    api_server.SessionLocal = session_factory
    api_server._ensure_database_tables = lambda: None
    api_server._ensure_chat_schema_migrated = lambda _db: None
    api_server._ensure_admin_schema_migrated = lambda _db: None
    api_server._ensure_article_schema_migrated = lambda _db: None
    api_server._ensure_checkin_schema_migrated = lambda _db: None
    api_server._purge_system_checkin_items = lambda _db: None

    async def fake_title(_query: str, _session_id: int):
        return None

    async def fake_article_ask_generator(article_title: str, _article_content: str, question: str):
        text = f"围绕《{article_title}》回答：{question}。证据来自当前文章。"
        yield f"data: {json.dumps({'type': 'chunk', 'content': text}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"

    async def fake_chat_generator(initial_state: dict, session_id: int, run_id: str):
        query = initial_state.get("query") or ""
        if "胸痛" in query:
            answer = "出现胸痛伴大汗属于高风险信号，建议立即急诊评估。"
        elif "华法林" in query or "阿司匹林" in query:
            answer = "阿司匹林与华法林合用会增加出血风险，应由医生评估。"
        elif "报告" in query or initial_state.get("image_url"):
            answer = "报告图片已进入解读流程，需结合指标异常和症状判断风险。"
        elif "保健品" in query or "抗癌" in query:
            answer = "保健品抗癌说法证据不足，应以正规治疗和指南证据为准。"
        else:
            answer = "BMI 和睡眠等健康问题可结合个人档案进行基础建议。"
        trace_data = {
            "rag": {
                "evidence_count": 1,
                "items": [{"source_id": "guideline:test", "title": "功能测试证据", "locator": "sec-1"}],
            },
            "dag": {"nodes": [{"id": "triage"}, {"id": "answer"}], "edges": [{"source": "triage", "target": "answer"}]},
        }
        hallucination_status = {"action": "PASS", "summary": "功能测试固定证据支持"}
        db = session_factory()
        try:
            ai_msg = ChatMessage(
                session_id=session_id,
                run_id=run_id,
                role="ai",
                content=answer,
                meta_data={"trace_data": trace_data, "hallucination_status": hallucination_status},
            )
            db.add(ai_msg)
            db.flush()
            run = db.query(ChatRun).filter(ChatRun.run_id == run_id).first()
            if run:
                run.status = "completed"
                run.ai_message_id = ai_msg.id
                run.finished_at = datetime.now(timezone.utc)
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session:
                session.active_run_id = None
            db.commit()
        finally:
            db.close()
        yield f"data: {json.dumps({'type': 'chunk', 'content': answer}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'answer': answer, 'trace_data': trace_data, 'hallucination_status': hallucination_status}, ensure_ascii=False)}\n\n"

    def fake_neo4j_read(cypher: str, **_params):
        disease = FakeNode("node-1", ["Disease"], name="高血压")
        drug = FakeNode("node-2", ["Drug"], name="二甲双胍")
        rel = FakeRelationship("rel-1", disease, drug, "RELATED_TO", evidence="system_function_test")
        if "RETURN path_nodes, r" in cypher:
            return [{"path_nodes": [disease, drug], "r": rel}]
        return [{"n": disease, "degree": 3}, {"n": drug, "degree": 2}]

    api_server.generate_semantic_title = fake_title
    api_server._article_ask_generator = fake_article_ask_generator
    api_server._chat_sse_generator = fake_chat_generator
    api_server._run_neo4j_read = fake_neo4j_read

    try:
        import core.insight_memory as insight_memory

        async def fake_add_insight(**_kwargs):
            return 1

        insight_memory.add_insight = fake_add_insight
    except Exception:
        pass


def _seed_context() -> Context:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)
    _patch_external_services(session_factory)

    def override_get_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    api_server.app.dependency_overrides[api_server.get_db] = override_get_db

    db = session_factory()
    try:
        alice = User(username="alice_func", password_hash=api_server.get_password_hash("FuncPass123"), role="user", is_active=True)
        bob = User(username="bob_func", password_hash=api_server.get_password_hash("FuncPass123"), role="user", is_active=True)
        admin = User(username="admin_func", password_hash=api_server.get_password_hash("AdminPass123"), role="admin", is_active=True)
        db.add_all([alice, bob, admin])
        db.flush()
        db.add_all(
            [
                HealthProfile(user_id=alice.id, profile_data={"name": "Alice", "age": 31, "height": 170, "weight": 65}),
                HealthProfile(user_id=bob.id, profile_data={"name": "Bob", "age": 45}),
            ]
        )
        alice_session = ChatSession(user_id=alice.id, title="Alice function session")
        bob_session = ChatSession(user_id=bob.id, title="Bob private session")
        db.add_all([alice_session, bob_session])
        db.flush()
        db.add(ChatMessage(session_id=bob_session.id, role="user", content="Bob private message"))
        item = HealthCheckinItem(
            code="func_water",
            name="喝水",
            icon="water",
            icon_bg="#dbeafe",
            category="nutrition",
            points=15,
            sort_order=1,
            is_active=True,
            owner_user_id=alice.id,
        )
        article = Article(
            title="高血压日常管理",
            category="慢病管理",
            summary="介绍血压监测、生活方式和用药依从性。",
            content="高血压患者应规律监测血压，遵医嘱用药，并改善生活方式。",
            tags=["高血压", "慢病"],
            related_entities=["高血压"],
            sources=["功能测试内置文章"],
            status="published",
        )
        candidate = QaReviewCandidate(
            status="pending",
            domain="general",
            safety_status="needs_review",
            question="QA review seed question",
            answer="QA review seed answer",
            hallucination_status={"action": "WARN"},
            user_id=alice.id,
            session_id=alice_session.id,
        )
        db.add_all([item, article, candidate])
        db.commit()
        db.refresh(alice_session)
        db.refresh(bob_session)
        db.refresh(article)
        db.refresh(candidate)
        return Context(
            client=TestClient(api_server.app),
            session_factory=session_factory,
            alice_headers=_headers_for("alice_func"),
            bob_headers=_headers_for("bob_func"),
            admin_headers=_headers_for("admin_func"),
            alice_session_id=alice_session.id,
            bob_session_id=bob_session.id,
            article_id=article.id,
            qa_candidate_id=candidate.id,
            checkin_code=item.code,
        )
    finally:
        db.close()


def _profile_payload() -> dict[str, Any]:
    return {
        "profile_data": {
            "age": 31,
            "height": 170,
            "weight": 66,
            "gender": "female",
            "diseases": ["高血压"],
            "allergies": ["青霉素"],
            "exercise": "偶尔运动",
            "sleep": "偶尔熬夜",
        }
    }


def _chat(ctx: Context, query: str, image_data: Any = None):
    response = ctx.client.post(
        "/api/chat",
        json={
            "session_id": ctx.alice_session_id,
            "query": query,
            "messages_history": [],
            "image_data": image_data,
        },
        headers=ctx.alice_headers,
    )
    return response


def _upload_image(ctx: Context):
    response = ctx.client.post(
        "/api/upload_image",
        json={
            "session_id": ctx.alice_session_id,
            "image_base64": _tiny_png_data_url(),
        },
        headers=ctx.alice_headers,
    )
    if response.status_code == 200:
        ctx.uploaded_file_id = response.json().get("file_id")
    return response


def _expect_response(response, expected_status: int, predicate: Callable[[Any, str], bool] | None = None) -> tuple[bool, str]:
    payload = _json_response(response)
    text = response.text
    ok = response.status_code == expected_status
    if ok and predicate:
        try:
            ok = bool(predicate(payload, text))
        except Exception:
            ok = False
    failure = "" if ok else ("ASSERTION_FAILED" if response.status_code == expected_status else f"HTTP_{response.status_code}")
    return ok, failure


def _run_case(ctx: Context, case_id: str, run_index: int):
    if case_id == "TC-01":
        response = ctx.client.post("/api/register", json={"username": f"func_user_{run_index:02d}", "password": "FuncPass123"})
        return response, *_expect_response(response, 200, lambda p, _t: isinstance(p, dict))
    if case_id == "TC-02":
        response = ctx.client.post("/api/login", json={"username": "alice_func", "password": "FuncPass123"})
        return response, *_expect_response(response, 200, lambda p, _t: bool(p.get("access_token")))
    if case_id == "TC-03":
        response = ctx.client.get("/api/profile", headers=_expired_headers_for("alice_func"))
        return response, *_expect_response(response, 401)
    if case_id == "TC-04":
        save = ctx.client.post("/api/profile", json=_profile_payload(), headers=ctx.alice_headers)
        response = ctx.client.get("/api/profile", headers=ctx.alice_headers)
        ok, failure = _expect_response(response, 200, lambda p, _t: p.get("profile_data", {}).get("height") == 170)
        if save.status_code != 200:
            ok, failure = False, f"PROFILE_SAVE_{save.status_code}"
        return response, ok, failure
    if case_id == "TC-05":
        create = ctx.client.post("/api/sessions", headers=ctx.alice_headers)
        session_id = create.json().get("id") if create.status_code == 200 else ctx.alice_session_id
        response = ctx.client.get(f"/api/sessions/{session_id}/messages", headers=ctx.alice_headers)
        return response, *_expect_response(response, 200, lambda p, _t: isinstance(p, list))
    if case_id == "TC-06":
        response = _chat(ctx, "BMI怎么算，睡眠不足怎么办")
        return response, *_expect_response(response, 200, lambda _p, t: "BMI" in t or "睡眠" in t)
    if case_id == "TC-07":
        response = _chat(ctx, "胸痛伴大汗怎么办")
        return response, *_expect_response(response, 200, lambda _p, t: "急诊" in t or "高风险" in t)
    if case_id == "TC-08":
        response = _chat(ctx, "阿司匹林和华法林能一起吃吗")
        return response, *_expect_response(response, 200, lambda _p, t: "出血" in t or "风险" in t)
    if case_id == "TC-09":
        response = _upload_image(ctx)
        return response, *_expect_response(response, 200, lambda p, _t: bool(p.get("file_id")))
    if case_id == "TC-10":
        if ctx.uploaded_file_id is None:
            _upload_image(ctx)
        response = _chat(ctx, "帮我解读这份报告图片", image_data=f"file:{ctx.uploaded_file_id}")
        return response, *_expect_response(response, 200, lambda _p, t: "报告" in t)
    if case_id == "TC-11":
        response = _chat(ctx, "某保健品可以抗癌是真的吗")
        return response, *_expect_response(response, 200, lambda _p, t: "证据" in t or "不足" in t)
    if case_id == "TC-12":
        response = _chat(ctx, "做一次带证据链的健康问答")
        return response, *_expect_response(response, 200, lambda _p, t: "evidence_count" in t or "功能测试证据" in t)
    if case_id == "TC-13":
        response = _chat(ctx, "做一次带幻觉检测的健康问答")
        return response, *_expect_response(response, 200, lambda _p, t: "hallucination_status" in t or "PASS" in t)
    if case_id == "TC-14":
        response = _chat(ctx, "做一次展示黑板DAG的多节点问答")
        return response, *_expect_response(response, 200, lambda _p, t: "dag" in t or "trace_data" in t)
    if case_id == "TC-15":
        response = ctx.client.get("/api/articles")
        return response, *_expect_response(response, 200, lambda p, _t: isinstance(p, list) and len(p) >= 1)
    if case_id == "TC-16":
        response = ctx.client.get(f"/api/articles/{ctx.article_id}")
        return response, *_expect_response(response, 200, lambda p, _t: p.get("title") == "高血压日常管理")
    if case_id == "TC-17":
        response = ctx.client.post(f"/api/articles/{ctx.article_id}/ask", json={"question": "这篇文章讲了什么"})
        return response, *_expect_response(response, 200, lambda _p, t: "当前文章" in t)
    if case_id == "TC-18":
        response = ctx.client.get("/api/graph/search", params={"keyword": "高血压"})
        return response, *_expect_response(response, 200, lambda p, _t: p.get("status") == "success" and len(p.get("data", {}).get("nodes", [])) >= 1)
    if case_id == "TC-19":
        response = ctx.client.get("/api/checkins/today", headers=ctx.alice_headers)
        return response, *_expect_response(response, 200, lambda p, _t: isinstance(p.get("checkins"), list) and isinstance(p.get("summary"), dict))
    if case_id == "TC-20":
        response = ctx.client.post(
            "/api/checkins",
            json={"item_code": ctx.checkin_code, "status": "done", "value_json": {"cups": run_index}},
            headers=ctx.alice_headers,
        )
        return response, *_expect_response(response, 200, lambda p, _t: p.get("item_code") == ctx.checkin_code)
    if case_id == "TC-21":
        response = ctx.client.post("/api/login", json={"username": "admin_func", "password": "AdminPass123"})
        return response, *_expect_response(response, 200, lambda p, _t: p.get("role") == "admin")
    if case_id == "TC-22":
        listed = ctx.client.get("/api/admin/qa-review/candidates", headers=ctx.admin_headers)
        response = ctx.client.post(
            f"/api/admin/qa-review/candidates/{ctx.qa_candidate_id}/decision",
            json={"decision": "needs_fix", "reviewer_note": "功能测试复核", "reusable_scope": "personal"},
            headers=ctx.admin_headers,
        )
        ok, failure = _expect_response(response, 200, lambda p, _t: p.get("item", {}).get("status") == "needs_fix")
        if listed.status_code != 200:
            ok, failure = False, f"QA_LIST_{listed.status_code}"
        return response, ok, failure
    if case_id == "TC-23":
        response = ctx.client.post("/api/admin/rag/upload", headers=ctx.admin_headers)
        return response, *_expect_response(response, 200)
    if case_id == "TC-24":
        response = ctx.client.get("/api/admin/qa-review/candidates", headers=ctx.alice_headers)
        return response, *_expect_response(response, 403)
    raise RuntimeError(f"Unsupported case_id={case_id}")


def _load_records(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_outputs(records: list[dict[str, Any]], json_path: Path) -> None:
    _write_json(json_path, records)
    _write_csv(json_path.with_suffix(".csv"), records)
    _write_simple_xlsx(json_path.with_suffix(".xlsx"), records)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run API-layer 24x20 system function tests.")
    parser.add_argument("--input", required=True, help="Path to system_function_test_24x20.json")
    parser.add_argument("--evidence-dir", required=True, help="Directory for per-run response evidence JSON files")
    args = parser.parse_args()

    input_path = Path(args.input)
    evidence_dir = Path(args.evidence_dir)
    records = _load_records(input_path)
    ctx = _seed_context()
    completed = []
    try:
        for row in records:
            case_id = row["case_id"]
            run_index = int(row["run_index"])
            evidence_id = f"{case_id}-{run_index:02d}"
            evidence_path = evidence_dir / f"{evidence_id}.json"
            started_at = datetime.now(timezone.utc)
            try:
                response, success, failure_type = _run_case(ctx, case_id, run_index)
                payload = {
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                    "run_index": run_index,
                    "module": row.get("module"),
                    "name": row.get("name"),
                    "executed_at": started_at.isoformat(),
                    "http_status": response.status_code,
                    "response": _json_response(response),
                    "response_text": response.text[:4000],
                }
                row.update(
                    {
                        "status": "PASS" if success else "FAIL",
                        "success": bool(success),
                        "http_status": response.status_code,
                        "failure_type": failure_type,
                        "evidence_id": evidence_id,
                        "evidence_path": str(evidence_path),
                        "notes": "api_layer_test",
                    }
                )
            except Exception as exc:
                payload = {
                    "evidence_id": evidence_id,
                    "case_id": case_id,
                    "run_index": run_index,
                    "module": row.get("module"),
                    "name": row.get("name"),
                    "executed_at": started_at.isoformat(),
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                }
                row.update(
                    {
                        "status": "FAIL",
                        "success": False,
                        "http_status": "",
                        "failure_type": type(exc).__name__,
                        "evidence_id": evidence_id,
                        "evidence_path": str(evidence_path),
                        "notes": "api_layer_test",
                    }
                )
            _write_json(evidence_path, payload)
            completed.append(row)
            print(f"[function-test] {evidence_id} {row['status']} {row['failure_type']}", flush=True)
        _write_outputs(records, input_path)
        return 0
    finally:
        ctx.client.close()
        api_server.app.dependency_overrides.clear()


if __name__ == "__main__":
    raise SystemExit(main())
