# core/models.py
from sqlalchemy import Column, Integer, String, DateTime, ForeignKey, JSON, Text, Date, Boolean, UniqueConstraint, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from sqlalchemy.dialects.mysql import LONGTEXT  # 馃専 鏋佸害鍏抽敭锛氱敤浜庡瓨鍌ㄥ法澶х殑 Base64 鍥剧墖鏁版嵁
from .database import Base


# ==========================================
# 鍘熸湁琛細鐢ㄦ埛浣撶郴涓庡仴搴锋。妗?
# ==========================================
class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    role = Column(String(20), nullable=False, default="user", index=True)
    is_active = Column(Boolean, nullable=False, default=True, index=True)
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    profile = relationship("HealthProfile", back_populates="owner", uselist=False, cascade="all, delete-orphan")
    # 寤虹珛涓庝細璇濊褰曠殑涓€瀵瑰鍏崇郴
    sessions = relationship("ChatSession", back_populates="owner", cascade="all, delete-orphan")
    health_checkins = relationship("UserHealthCheckin", back_populates="user", cascade="all, delete-orphan")


class HealthProfile(Base):
    __tablename__ = "health_profiles"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=False)
    profile_data = Column(JSON, nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    owner = relationship("User", back_populates="profile")


# ==========================================
# 鍘熸湁琛細澶氫細璇濈鐞嗕笌娑堟伅璁板綍浣撶郴
# ==========================================
class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    title = Column(String(100), default="新健康咨询")
    current_slots = Column(JSON, nullable=True)
    current_route = Column(String(80), nullable=False, default="")
    turn_count = Column(Integer, nullable=False, default=1)
    active_run_id = Column(String(64), nullable=True, index=True)
    state_version = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # 姣忔鏈夋柊娑堟伅鏃讹紝鏇存柊姝ゆ椂闂达紝浠ヤ究鍓嶇灏嗕細璇濆垪琛ㄦ寜娲昏穬搴︽帓搴?
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    # 鍏崇郴鏄犲皠
    owner = relationship("User", back_populates="sessions")
    messages = relationship("ChatMessage", back_populates="session", cascade="all, delete-orphan")
    runs = relationship("ChatRun", back_populates="session", cascade="all, delete-orphan")


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False)
    run_id = Column(String(64), nullable=True, index=True)
    role = Column(String(20), nullable=False)  # 璁板綍韬唤锛?user' 鎴?'ai'

    content = Column(Text, nullable=False)
    image_url = Column(String(500), nullable=True)  # 瀛樻枃浠惰矾寰勶紙濡?/static/uploads/xxx.jpg锛夛紝涓嶅啀瀛?Base64
    uploaded_file_id = Column(Integer, ForeignKey("uploaded_files.id"), nullable=True, index=True)

    # 馃専 鏍稿績澧為噺锛氭柊澧?JSON 瀛楁锛岀敤浜庢案涔呬繚瀛?AI 鍥炲鏃剁殑骞曞悗鐘舵€侊紙濡傛函婧愭暟鎹€佽矾鐢辫建閬擄級
    meta_data = Column(JSON, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now())

    # 鍏崇郴鏄犲皠
    session = relationship("ChatSession", back_populates="messages")
    uploaded_file = relationship("UploadedFile")


class ChatRun(Base):
    __tablename__ = "chat_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    run_id = Column(String(64), unique=True, nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    status = Column(String(20), nullable=False, default="running", index=True)
    user_message_id = Column(Integer, nullable=True, index=True)
    ai_message_id = Column(Integer, nullable=True, index=True)
    error = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    session = relationship("ChatSession", back_populates="runs")


class QaReviewCandidate(Base):
    __tablename__ = "qa_review_candidates"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    status = Column(String(20), nullable=False, default="pending", index=True)
    decision = Column(String(40), nullable=True, index=True)

    domain = Column(String(40), nullable=False, default="general", index=True)
    route = Column(String(80), nullable=True, index=True)
    intent = Column(String(80), nullable=True, index=True)
    sub_intent = Column(String(80), nullable=True, index=True)
    safety_status = Column(String(40), nullable=True, index=True)

    question = Column(Text, nullable=False)
    answer = Column(LONGTEXT, nullable=False)
    corrected_answer = Column(LONGTEXT, nullable=True)
    reviewer_note = Column(Text, nullable=True)
    feedback_tags = Column(JSON, nullable=True)
    reusable_scope = Column(String(20), nullable=False, default="shared")
    quality_score = Column(Float, nullable=True)

    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True, index=True)
    user_message_id = Column(Integer, nullable=True, index=True)
    ai_message_id = Column(Integer, nullable=True, index=True)
    run_id = Column(String(64), nullable=True, index=True)

    trace_summary = Column(JSON, nullable=True)
    evidence_refs = Column(JSON, nullable=True)
    kg_constraints = Column(JSON, nullable=True)
    external_sources = Column(JSON, nullable=True)
    hallucination_status = Column(JSON, nullable=True)
    meta_data = Column(JSON, nullable=True)

    reviewed_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    promoted_insight_id = Column(Integer, nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class UploadedFile(Base):
    __tablename__ = "uploaded_files"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    session_id = Column(Integer, ForeignKey("chat_sessions.id"), nullable=True, index=True)
    message_id = Column(Integer, nullable=True, index=True)
    run_id = Column(String(64), nullable=True, index=True)
    storage_bucket = Column(String(120), nullable=False)
    storage_key = Column(String(700), nullable=False)
    mime_type = Column(String(120), nullable=True)
    file_size = Column(Integer, nullable=False, default=0)
    purpose = Column(String(50), nullable=False, default="chat_image", index=True)
    meta_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    deleted_at = Column(DateTime(timezone=True), nullable=True, index=True)


# ==========================================
# 馃専 鏂板琛細鍋ュ悍鐭ヨ瘑涓撳尯 (AIGC 鍐呭寮曟搸搴?
# ==========================================
class Article(Base):
    __tablename__ = "articles"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    title = Column(String(255), nullable=False, index=True)  # 鏂囩珷鏍囬
    category = Column(String(50), nullable=False, index=True)  # 鎵€灞濼ab鏍忕洰 (濡? 杈熻埃绮夌鏈?
    summary = Column(Text, nullable=False)  # 鏍稿績鎽樿 (鐢ㄤ簬鍓嶇鍗＄墖灞曠ず锛屼娇鐢?Text 闃叉瓒呴暱)
    content = Column(LONGTEXT, nullable=False)  # Markdown 鏍煎紡鐨勬枃绔犳鏂囷紝浣跨敤 LONGTEXT 瀹圭撼闀跨瘒澶ц
    cover_image = Column(String(255), nullable=True)  # 灏侀潰鍥綰RL (棰勭暀鎵╁睍瀛楁)
    view_count = Column(Integer, default=0)  # 鐪熷疄闃呰閲?
    likes = Column(Integer, default=0)
    tags = Column(JSON, nullable=True)
    related_entities = Column(JSON, nullable=True)
    sources = Column(JSON, nullable=True)
    reading_time = Column(Integer, nullable=False, default=3)
    risk_level = Column(String(20), nullable=False, default="low")
    audience = Column(String(100), nullable=True)
    status = Column(String(20), nullable=False, default="published", index=True)
    is_hot = Column(Boolean, nullable=False, default=False, index=True)
    # 缁熶竴浣跨敤 sqlalchemy 鐨?func.now() 澶勭悊鏃堕棿锛屼繚鎸侀鏍间竴鑷?
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class ArticleFavorite(Base):
    __tablename__ = "article_favorites"
    __table_args__ = (
        UniqueConstraint("user_id", "article_id", name="uq_article_favorite_user_article"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ArticleLike(Base):
    __tablename__ = "article_likes"
    __table_args__ = (
        UniqueConstraint("user_id", "article_id", name="uq_article_like_user_article"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ArticleEvent(Base):
    __tablename__ = "article_events"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=True, index=True)
    event_type = Column(String(30), nullable=False, index=True)
    duration_ms = Column(Integer, nullable=True)
    query = Column(String(255), nullable=True)
    meta_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


# ==========================================
# 馃専 鏂板琛細鍋ュ悍鎵撳崱閰嶇疆涓庣敤鎴疯褰?
# ==========================================
class HealthCheckinItem(Base):
    __tablename__ = "health_checkin_items"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    icon = Column(String(20), nullable=False, default="activity")
    icon_bg = Column(String(20), nullable=False, default="#eaf4cc")
    category = Column(String(50), nullable=False, default="daily")
    points = Column(Integer, nullable=False, default=10)
    sort_order = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, nullable=False, default=True)
    # 馃啎 owner_user_id锛歂ULL=绯荤粺榛樿椤癸紙鎵€鏈変汉鍙銆佷笉鍙垹/鏀癸級锛屾暣鏁?璇ョ敤鎴风鏈夎嚜瀹氫箟椤?
    owner_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class UserHealthCheckin(Base):
    __tablename__ = "user_health_checkins"
    __table_args__ = (
        UniqueConstraint("user_id", "item_code", "checkin_date", name="uq_user_item_date"),
    )

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    item_code = Column(String(50), ForeignKey("health_checkin_items.code"), nullable=False, index=True)
    checkin_date = Column(Date, nullable=False, index=True)
    status = Column(String(20), nullable=False, default="done")
    value_json = Column(JSON, nullable=True)
    points_earned = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="health_checkins")
    item = relationship("HealthCheckinItem")


class AdminActionLog(Base):
    __tablename__ = "admin_action_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    admin_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    action = Column(String(80), nullable=False, index=True)
    target_type = Column(String(50), nullable=True, index=True)
    target_id = Column(String(80), nullable=True, index=True)
    detail = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class RagSourceFile(Base):
    __tablename__ = "rag_source_files"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    original_name = Column(String(255), nullable=False)
    stored_name = Column(String(255), nullable=False)
    storage_path = Column(String(500), nullable=False)
    file_hash = Column(String(64), nullable=False, index=True)
    file_size = Column(Integer, nullable=False, default=0)
    mime_type = Column(String(120), nullable=True)
    source_type = Column(String(50), nullable=False, default="guideline")
    target_collection = Column(String(80), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="uploaded", index=True)
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    meta_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())


class RagIngestTask(Base):
    __tablename__ = "rag_ingest_tasks"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    task_uid = Column(String(64), unique=True, nullable=False, index=True)
    celery_task_id = Column(String(128), nullable=True, index=True)
    source_file_id = Column(Integer, ForeignKey("rag_source_files.id"), nullable=True, index=True)
    task_type = Column(String(50), nullable=False, index=True)
    mode = Column(String(20), nullable=False, default="dry_run", index=True)
    collection = Column(String(80), nullable=False, index=True)
    status = Column(String(30), nullable=False, default="queued", index=True)
    progress = Column(Integer, nullable=False, default=0)
    summary = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now(), server_default=func.now())

    source_file = relationship("RagSourceFile")


class RagIngestTaskLog(Base):
    __tablename__ = "rag_ingest_task_logs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    task_id = Column(Integer, ForeignKey("rag_ingest_tasks.id"), nullable=False, index=True)
    level = Column(String(20), nullable=False, default="info")
    message = Column(Text, nullable=False)
    meta_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)


class RagEvalRun(Base):
    __tablename__ = "rag_eval_runs"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    run_uid = Column(String(64), unique=True, nullable=False, index=True)
    status = Column(String(30), nullable=False, default="queued", index=True)
    suite_path = Column(String(500), nullable=False)
    report_path = Column(String(500), nullable=True)
    metrics = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

