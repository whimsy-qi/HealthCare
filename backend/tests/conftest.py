import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.mysql import LONGTEXT
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
PYTEST_TMP = BACKEND_ROOT / ".pytest-tmp"
PYTEST_TMP.mkdir(exist_ok=True)

for path in (PROJECT_ROOT, BACKEND_ROOT):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

os.environ.setdefault("JWT_SECRET_KEY", "pytest-jwt-secret")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{(PYTEST_TMP / 'default.sqlite3').as_posix()}")
os.environ.setdefault("OPENAI_API_KEY", "pytest-openai-key")
os.environ.setdefault("OPENAI_API_BASE", "http://127.0.0.1")
os.environ.setdefault("NEO4J_URI", "bolt://127.0.0.1:7687")
os.environ.setdefault("NEO4J_USER", "neo4j")
os.environ.setdefault("NEO4J_PASSWORD", "pytest-neo4j-password")
os.environ.setdefault("QA_REVIEW_ADMIN_TOKEN", "pytest-qa-review-token")


@compiles(LONGTEXT, "sqlite")
def _compile_mysql_longtext_for_sqlite(_type, _compiler, **_kwargs):
    return "TEXT"


@pytest.fixture
def make_sqlite_session_factory():
    from core.models import Base

    engines = []

    def _make(tables):
        engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(bind=engine, tables=tables)
        engines.append(engine)
        return sessionmaker(autocommit=False, autoflush=False, bind=engine, expire_on_commit=False)

    yield _make

    for engine in engines:
        engine.dispose()
