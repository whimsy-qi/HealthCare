# core/database.py
import os
from pathlib import Path
from dotenv import load_dotenv, find_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BACKEND_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BACKEND_DIR / ".env")
load_dotenv(find_dotenv(usecwd=True), override=False)

SQLALCHEMY_DATABASE_URL = os.getenv("DATABASE_URL")
if not SQLALCHEMY_DATABASE_URL:
    raise RuntimeError("DATABASE_URL is required. Set it in backend/.env, for example mysql+pymysql://user:password@localhost:3306/health_system")

# 创建数据库引擎
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,  # 自动重连机制
    pool_recycle=3600    # 连接池回收时间
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# 供 FastAPI 使用的依赖注入
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
