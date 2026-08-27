# api_server.py
import os
os.environ["HTTP_PROXY"] = ""
os.environ["HTTPS_PROXY"] = ""

import sys
import os
import hashlib
import json
import re
import shutil
import unicodedata
import requests as _requests
from urllib.parse import quote

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
import random
import asyncio
import copy
import uuid
import base64
import binascii
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Optional
from agents.memory_agent import (
    extract_health_updates,
    persist_health_updates,
    merge_updates_into_profile,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.append(BASE_DIR)

from fastapi import FastAPI, Depends, File, HTTPException, UploadFile, status, Query
from fastapi.responses import Response, StreamingResponse, FileResponse
from pydantic import BaseModel
from typing import List, Dict, Optional, Any
from fastapi.middleware.cors import CORSMiddleware
import uvicorn
import logging
from openai import AsyncOpenAI
from dotenv import load_dotenv, find_dotenv
from fastapi.staticfiles import StaticFiles
from PIL import Image

from sqlalchemy import or_, text, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from core.database import Base, engine, get_db, SessionLocal
from core.models import (
    User,
    HealthProfile,
    ChatSession,
    ChatMessage,
    ChatRun,
    QaReviewCandidate,
    UploadedFile,
    Article,
    ArticleFavorite,
    ArticleLike,
    ArticleEvent,
    AdminActionLog,
    HealthCheckinItem,
    RagEvalRun,
    RagIngestTask,
    RagIngestTaskLog,
    RagSourceFile,
    UserHealthCheckin,
)
from core.request_schemas import (
    GRAPH_QUERY_TIMEOUT_SEC,
    LoginUserParams,
    ProfilePayload,
    RegisterUserParams,
    clamp_graph_depth,
)
from core.security import get_password_hash, verify_password, create_access_token, SECRET_KEY, ALGORITHM
from core.storage import LocalStorageService, get_storage_service, verify_object_url_signature
from core.llm_client import DEFAULT_MODEL, FAST_MODEL, shared_client
from jose import jwt, JWTError
from fastapi.security import OAuth2PasswordBearer

from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from graph_engine import app_graph, report_agent_instance
from core.sse_emitter import set_queue as set_sse_queue, reset_queue as reset_sse_queue
from core.sse_emitter import set_collector as set_sse_collector, reset_collector as reset_sse_collector
from question_pool import RECOMMEND_QUESTIONS
from neo4j import GraphDatabase, Query as Neo4jQuery

load_dotenv(find_dotenv(usecwd=True))
NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD", "")
neo4j_driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))
logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)])
# 缂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛銈呭閺屾盯顢曢敐鍡欘槰闂佽鍨抽崑銈夌嵁閺嶎灔搴敆閳ь剚淇婃總鍛婄厽妞ゆ挾鍣ュ▓婊勵殽閻愬澧懣鎰亜閹哄棗浜炬繝寰枫倕浜圭紒杈ㄥ浮楠炲洭顢樿閻や線姊洪崫鍕効缂佺粯绻傞悾鐑藉醇閺囩偟鍘告繛杈剧到濞村嫮寰婇崸妤€鐒?stdout 闂?UTF-8
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
logger = logging.getLogger("APIGateway")
_background_chat_tasks: set[asyncio.Task] = set()


def _track_background_chat_task(task: asyncio.Task) -> asyncio.Task:
    _background_chat_tasks.add(task)

    def _cleanup(done_task: asyncio.Task) -> None:
        _background_chat_tasks.discard(done_task)
        if done_task.cancelled():
            return
        try:
            done_task.exception()
        except Exception:
            pass

    task.add_done_callback(_cleanup)
    return task

def _stable_dedupe_list(items):
    """Keep first occurrence order while removing duplicate trace strings."""
    seen = set()
    out = []
    for item in items or []:
        key = str(item).strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out

limiter = Limiter(key_func=get_remote_address)

@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    _ensure_database_tables()
    db = SessionLocal()
    try:
        _ensure_checkin_schema_migrated(db)
        _ensure_chat_schema_migrated(db)
        _purge_system_checkin_items(db)
        _ensure_article_schema_migrated(db)
        logger.info("Database schema is ready")
    finally:
        db.close()

    async def _warmup():
        result = await get_hot_realtime_articles(refresh=False)
        if result.get("fallback"):
            logger.info("Realtime article cache warmed from local fallback")
        else:
            logger.info("Realtime article cache warmed")

    asyncio.create_task(_warmup())
    asyncio.create_task(_chat_upload_cleanup_loop())
    yield


app = FastAPI(title="Health System API Gateway (With LangGraph)", lifespan=lifespan)
app.state.limiter = limiter
STATIC_UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
STATIC_COVERS_DIR = os.path.join(BASE_DIR, "covers")
STATIC_NEW_COVERS_DIR = os.path.join(BASE_DIR, "new_covers")
os.makedirs(STATIC_UPLOADS_DIR, exist_ok=True)
os.makedirs(STATIC_COVERS_DIR, exist_ok=True)
os.makedirs(STATIC_NEW_COVERS_DIR, exist_ok=True)
app.mount("/static/uploads", StaticFiles(directory=STATIC_UPLOADS_DIR), name="static_uploads")
app.mount("/static/covers", StaticFiles(directory=STATIC_COVERS_DIR), name="static_covers")
app.mount("/static/new_covers", StaticFiles(directory=STATIC_NEW_COVERS_DIR), name="static_new_covers")

@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"detail": "????????????"})

app.add_middleware(SlowAPIMiddleware)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"],
)

@app.middleware("http")
async def disable_legacy_admin_api(request: Request, call_next):
    if request.url.path.startswith("/api/admin") and not request.url.path.startswith("/api/admin/qa-review"):
        return JSONResponse(
            status_code=410,
            content={
                "detail": "Legacy admin API has been retired. Use medical-graphrag admin at http://localhost:3026/login."
            },
        )
    return await call_next(request)

fast_llm = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"), base_url=os.getenv("OPENAI_API_BASE"))

QA_REVIEW_ENABLED = os.getenv("QA_REVIEW_ENABLED", "true").lower() in {"1", "true", "yes", "on"}
QA_REVIEW_CAPTURE_SCOPE = os.getenv("QA_REVIEW_CAPTURE_SCOPE", "all").strip().lower()
QA_REVIEW_ADMIN_TOKEN = (
    os.getenv("QA_REVIEW_ADMIN_TOKEN")
    or os.getenv("MEDICAL_RAG_SERVICE_TOKEN")
    or "health-rag-local-dev-token"
)


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/login_form")


async def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="????????????????",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username: str = payload.get("sub")
        if username is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(User).filter(User.username == username).first()
    if user is None:
        raise credentials_exception
    if hasattr(user, "is_active") and not user.is_active:
        raise HTTPException(status_code=403, detail="????")
    return user


def get_current_admin(current_user: User = Depends(get_current_user)):
    if getattr(current_user, "role", "user") != "admin":
        raise HTTPException(status_code=403, detail="????")
    return current_user


class CheckinPayload(BaseModel):
    item_code: str
    status: str = "done"
    value_json: Optional[dict] = None
    checkin_date: Optional[str] = None


class CheckinItemCreatePayload(BaseModel):
    name: str
    icon: Optional[str] = "activity"
    icon_bg: Optional[str] = "#eaf4cc"
    category: Optional[str] = "custom"
    points: Optional[int] = 10


class QaReviewDecisionPayload(BaseModel):
    decision: str
    reviewer_note: Optional[str] = ""
    corrected_answer: Optional[str] = ""
    feedback_tags: Optional[List[str]] = None
    reusable_scope: Optional[str] = "shared"
    quality_score: Optional[float] = None


# 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛銈呭閺屾盯顢曢敐鍡欘槬闂佺琚崝搴ㄥ箟閹间礁绫嶉柛顐ｆ儕閵夆晜鐓曢柟鑸妽濞呭棝鏌涙惔锝呮灈闁哄本娲濈粻娑氣偓锝庝簽閸旀潙鈹戦悙璺虹毢妞ゎ厼鐗撻崺鐐哄箣閿旇棄浜归柣鐘叉厂閸愌呯煑闂傚倷鑳剁划顖炪€冮崨瀛樻櫇妞ゅ繐瀚弳锕傛煙鏉堝墽鐣遍柣鎾寸洴閺屾稑鈽夐崡鐐寸亾闂佸憡眉缁瑥顫忓ú顏呯劵婵炴垶锚缁侇喖鈹戦悙鏉垮皟闁告洦鍓氶悵閿嬬箾鐎电甯堕柣掳鍔戦幃锟犲礃椤忓棛锛濇繛杈剧秬閸嬪倿骞嬮悩鐢电劶闂侀€炲苯澧い顏勫暣婵¤埖鎯旈垾宕囧摋婵犵數鍋涢ˇ鏉棵洪悢鑲╁祦闁硅揪绠戠粈瀣亜閹烘垵鈧骞婂┑鍡╂富闁靛牆妫涙晶顒勬煠閸︻厼浜剧紒鏃傚枛瀹曞ジ濡烽敂瑙勫濠电偠鎻徊鍧楀箠閹惧瓨娅犳い鏍嚔閻熼偊鐓ラ柛娑卞幒濡叉劙鎮楀▓鍨珮闁稿鎳愰幑銏犫攽閸♀晜鍍靛銈嗗笒閸燁垶骞夐悧鍫㈢瘈闁汇垽娼ф禒锕傛煕閵娿儳鍩ｉ柍銉畵瀹曟帡鎮欓懠顒傛綁闂備胶顭堥張顒勩€冮崨顔绢洸濡わ絽鍟悡銉︾節闂堟稒顥㈡い搴㈩殔椤儻顦遍柛妤佸▕瀵鏁愭径瀣珖闂侀€炲苯澧撮柟顔ㄥ洤绠婚柟棰佺劍缂嶅骸鈹戦悙鍙夆枙濞存粍绻堣棢闁割偆鍠撶粻楣冩煙鐎电浠ч柟铏姈娣囧﹪顢曢妶鍛埛缂備浇椴搁幐濠氬箯閸涙潙绀堥柛娆忥紞閿斿墽纾介柛灞炬皑瀛濋梺鎸庢处娴滎亪骞冩导鎼晪闁逞屽墮閻ｇ兘宕￠悙鈺傤潔濠碘槅鍨抽埛鍫澪ｉ悧鍫滅箚闁绘劦浜滈埀顒佺墪铻炲〒姘ｅ亾鐎规洘鍨块獮鍥礂椤愩垺鍠樼€殿喛娉涢埢搴ㄦ倷椤掆偓閻︽粓姊绘笟鈧褔鎮ч崱妞㈡稑螖閸愵亞鐣堕梺绋跨灱閸嬬偤鎮￠悩缁樼厱闁归偊鍨伴惃娲煕閳哄鎮奸柍褜鍓濋～澶娒鸿箛娑樺瀭濞寸姴顑囧畵渚€鏌涢妷顔煎⒒闁轰礁娲弻锝呂熼崗鍏兼瘎濠?濠电姷鏁告慨鐑藉极閸涘﹥鍙忛柣鎴ｆ閺嬩線鏌熼梻瀵割槮缁炬儳娼￠弻鐔衡偓鐢殿焾瀛濈紓浣界堪閸婃繈寮婚敃鈧灒濞撴凹鍨遍敍鍡涙偡濠婂懎顣奸悽顖涘笧婢规洘绺介崨濠勫幗闂佸綊鍋婇崹浼存儍濞差亝鐓熸繛鎴炵墪閸旀岸鏌嶇憴鍕仸妤犵偛锕弻娑欑節閸愨晝顦板Δ鐘靛仜閿曘儵骞嗛弮鍫澪╅柕澶堝劚濞堛倕鈹戦悙瀛樺鞍闁糕晛鍟村畷鎴﹀箻缂佹鍘搁柣蹇曞仩椤曆囧焵椤掍焦绀嬫繝鈧笟鈧弻锝嗘償椤栨粎校闂佺顑呴幊姗€骞冮悽绋垮嵆闁靛骏绱曢崢浠嬫煙閸忚偐鏆橀柛銊ヮ煼閵嗗倿鎳犻钘変壕闁稿繐顦禍鍓х磽娴ｅ壊鍎愭い鎴炵懃椤洭寮介妸褏顔曢悗鐟板閸犳洜鑺辨繝姘厱闁靛鍎遍埀顒€缍婃俊鐢稿礋椤栨艾宓嗛梺缁樺姈濞兼瑥鈻嶉敐澶嬧拺闁告稑锕ョ亸顓㈡煕閻旈攱鍋ラ柛鈺冨仱楠炲鏁傞挊澶夋睏婵＄偑鍊栭崝锕€顭块埀顒€顭跨憴鍕婵﹦绮幏鍛村川婵犲啫鍓垫俊鐐€х紓姘跺础閸愯尙鏆?class CheckinItemCreatePayload(BaseModel):
class CheckinItemUpdatePayload(BaseModel):
    name: Optional[str] = None
    icon: Optional[str] = None
    icon_bg: Optional[str] = None
    category: Optional[str] = None
    points: Optional[int] = None
    is_active: Optional[bool] = None


class ArticleTrackPayload(BaseModel):
    event_type: str
    article_id: Optional[int] = None
    duration_ms: Optional[int] = None
    query: Optional[str] = None
    meta_data: Optional[dict] = None


class MessageItem(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    session_id: int
    query: str
    messages_history: List[MessageItem] = []
    turn_count: int = 1
    current_slots: Dict[str, str] = {}
    current_route: str = ""
    image_data: Optional[Any] = None
    vision_context: Optional[str] = None
    # 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧湱鈧懓瀚崳纾嬨亹閹烘垹鍊炲銈嗗笒閿曪妇绮欒箛鏃傜瘈闁靛骏绲剧涵鐐亜閹存繃鍠橀柕鍡楁嚇楠炴捇骞戝Δ鈧紞濠囧箖閳轰緡鍟呮い鏃傚帶婢瑰牏绱撴担鍝勪壕闁稿骸鍟块…鍥晸閻樿尪鎽曢梺鎸庣☉鐎氼亜鈻介鍫熷仯闁搞儯鍔岀徊璇测攽椤斿ジ鍙勬慨濠冩そ瀹曨偊宕熼棃娑樺闂備胶鍘ч崯鍧楁偉婵傚摜宓佸鑸靛姇缁犵懓霉閿濆洦鍤€濞存粓浜跺娲礈閹绘帊绨肩紓浣筋嚙鐎氫即骞冮悜钘壩╃憸蹇曞閽樺褰掓晲閸℃绁锋繛瀛樼矒缁犳牕顫忓ú顏勭闁圭粯甯婄花顕€姊洪悷鏉挎毐婵炲樊鍘奸悾鐑藉箣閿曗偓缁犲鏌ゆ慨鎰偓鏇犵矈閿曞倹鈷戦柛鎾瑰皺閸樻盯鏌涢悩宕囧闁逛究鍔嶉幆鏃堝煢閳ь剟寮ㄦ禒瀣厽闁归偊鍓涢幗鐘电磽瀹ュ棗鐏撮柡灞剧⊕缁绘繈宕掑☉婊咁攨闂備線鈧偛鑻晶瀛樼箾娴ｅ啿娲ゅ洿缂備礁顑堝▔鏇㈠汲閿曞倹鐓欓柣鎴灻В鍕煙閻戞﹩娈㈤柡浣告喘閺岋綁骞囬鑺ユ瘎婵°倖妫冨缁樻媴娓氼垱鏁┑鐐叉噺濞茬喖骞冭閹晝鎷犻崣澶嬓氶梻浣规灱閺呮盯宕娑樺灁闁圭虎鍠楅悡鏇㈡煏婢跺棙娅撴俊顐ｅ灩缁辨帞鈧綆鍘奸崥鍦磼鏉堛劌绗х紒杈ㄥ笒铻ｉ柛蹇曞帶閸ㄩ亶姊绘担渚敯婵炲懏娲熼幃褔鎮╃拠鍙夋К闂佽法鍠撴慨瀵稿閸忚偐绠鹃柟瀵稿仧閹冲懏淇婇崣澶樻疁婵﹤顭峰畷鎺戔枎閹邦喓鍋樻俊鐐€栧ú姗€鎮ч悩鑼殾閻熸瑥瀚閬嶆煛婢跺鐏╅柡灞界墦濮婃椽宕崟鍨ч梺鎼炲妼缂嶅﹤顕ｉ幎鑺ユ櫇闁稿本绋撻崢浠嬫⒑閸濆嫬鏆欓柛濠勬暬楠炲繘鏁撻悩宕囧幐婵炶揪缍佸濠氱叕椤掑嫭鐓涚€光偓鐎ｎ剛袦闂佽桨鐒﹂崝娆忕暦閵娾晜鐒介柨鏂库偓鐔告珣闂傚倸鍊风欢姘跺焵椤掑倸浠滈柤娲诲灡閺呭爼顢氶埀顒勫蓟閵娾晛鍗虫俊顖濄€€閸嬫挸鈹戠€ｎ亝妲梺缁樺姇閹碱偆绮婚敐澶嬬叆闁哄洦顨呮禍楣冩⒑缂佹ɑ鎯堢紒缁樼箞瀵鈽夐姀鐘靛姶闂佸憡鍔楅崑鎾绘偩閸忚偐绠鹃悗鐢殿焾鏍￠悗鍏夊亾缂佸顑欏鏍ㄧ箾瀹割喕绨荤€瑰憡绻傞埞鎴︽偐閹绘帗娈梻鍌氼槸缁夊墎妲愰幘瀛樺缂佹稑顑呭▓顓㈡⒑缂佹﹩娈曢柟鍛婃倐閹儳鐣￠幊濠冩そ椤㈡棃宕熼鐐残曞┑锛勫亼閸婃牜鏁繝鍥ㄢ挃鐎广儱妫涢々鍙夌節婵犲倻澧涢柣鎾寸懇閹鎮藉▓璺ㄥ姼婵炲濮电划宥夊Φ閸曨垰鍗抽柣鏃囨閻撲線姊洪棃娑欐悙閻庢矮鍗冲顐﹀礃椤旇偐锛滈梺闈涚墕濡厼螞閺嶎偆纾介柛灞剧懄缁佹澘顪冮弶鎴炴喐闁轰緡鍣ｉ獮鎺楀即閻樿京鑳洪梻鍌氬€风欢姘跺焵椤掑倸浠滈柤娲诲灡閺呭墎鈧數纭堕崑鎾舵喆閸曨剛顦ㄩ梺鍛婃⒐閻熴儵鎮鹃悜鑺ュ€荤紒娑橆儐閺咃綁姊虹紒姗嗙劷闁轰焦鎮傚畷銏ゎ敍濮橈絾鏂€闂佺粯锕╅崰鏍倶鏉堛劎绠惧璺侯儑閳洜鈧灚婢樼€氭澘鐣烽崼鏇ㄦ晢闁逞屽墰婢规洘绻濆顓犲幍闂佸憡鎸嗛崨顓狀偧闂備礁鎼幊蹇涙偂閿熺姷宓侀柡宥庡亜閸ㄦ繈鏌涜箛鎿冩Ц濞存粓绠栭弻銈囧枈閸楃偛顫╅梺鎼炲€ら崜鐔奉潖婵犳艾纾兼繛鍡樺姉閵堟澘顪冮妶鍡樿偁闁告侗浜滄禍楣冩煕椤垵浜滄繛鎼枤閳ь剝顫夊ú鎴﹀础閸愬樊鍤曞ù鐘差儛閺佸洭鏌ｉ幇鐗堟锭鐎殿喓鍔岄埞鎴︽偐椤旇偐浼囬梺绯曟櫆閻楃姴鐣峰┑瀣嵆闁绘ê鍚€缁楀绻濋悽闈浶ｇ痪鏉跨Ч閹繝鏁愭径灞藉絼闂佹悶鍎甸ˉ鎾诲礆閺夋５鐟邦煥閸曨厾鐓夐梺鍝勭焿缁绘繈宕洪埀顒併亜閹烘垵顏撮柡浣告喘閺岋綁骞囬妸锔界彆闂佸搫鍊甸崑鎾绘⒒閸屾瑨鍏岀紒顕呭灦瀹曟繈寮撮悙宥囧枑缁绘繈宕戦弬銈囨偧闁瑰弶鎸冲畷鐔碱敇閻欌偓閸熷酣姊绘担钘変汗闁冲嘲鐗撳畷婊冣枎閹惧啿鍤戦柟鍏兼儗濞兼寧绂嶅鍫熺厸闁搞儲婀圭花鐣岀磼婢舵ê鏋ら柍褜鍓濋～澶娒洪埡鍐ㄧ筏闁诡垎灞芥闂佸憡娲﹂崹浼存煁閸ャ劎绡€闂傚牊绋掗崳褰掓煛鐎ｎ亞澧︽慨濠傤煼瀹曟帒鈻庨幋顓熜滃┑鐘灮閹虫捇鏁冮鍫濈畺闁跨喓濮撮柋鍥煏婢跺牆鍔ら柣娑栧劦濮婃椽宕崟顓涙瀱闂佸憡蓱閸庢娊鎮鹃崹顐ょ懝闁逞屽墴楠炲啫螖閸涱喗娅滈柟鑲╄ˉ閳ь剝灏欓弫鏍磽閸屾瑨鍏屽┑鐐╁亾缂備胶濮甸悧鏇㈩敋閿濆閱囬柡鍥╁仱閸炶泛鈹戦悩缁樻锭婵☆偄鐭傞獮鎰板礃椤忓棛锛濇繛杈剧到婢瑰﹪宕曡箛鏂讳簻妞ゆ挾濮撮崢瀵糕偓娈垮枛椤兘骞冮姀銈呯闁兼祴鏅涙慨閿嬬節閻㈤潧浠﹂柛銊ㄦ硾椤繈濡搁埡鍌氫粧闂侀潧绻掓刊顓炪€掓繝姘厪闁割偅绻傞弳娆撴煟韫囷絼閭柡宀嬬秮閹垽宕ｆ径瀣絽闂備椒绱徊鍧楀礂濡櫣鏆﹀┑鍌滎焾閸楁娊鏌曟繝蹇涙婵炲懎妫濆缁樻媴閾忓箍鈧﹪鏌涢幘瀵哥疄闁诡喚鍏橀弫鍐焵椤掑嫧鈧?    vision_context: Optional[str] = None
    med_precheck: Optional[dict] = None


class ChatResponse(BaseModel):
    answer: str
    images: List[str] = []
    is_finished: bool = True
    options: List[str] = []
    turn_count: int = 1
    current_slots: Dict[str, str] = {}
    route: str = ""
    trace_data: Dict[str, Any] = {}


# ==========================================
# 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛濠傛健閺屻劑寮撮悙娴嬪亾閸濄儳涓嶉柡宥庡幗閻撴洘銇勯幇鍓佺ɑ缂佲偓閳ь剛绱掗悙顒€鍔ゆ繛灏栤偓鎰佸殨闁割偅娲栭柋鍥ㄧ箾閹寸伝顏堚€栨径濞炬斀闁绘劕寮堕崳钘夆攽閻愨晛浜鹃梻浣告惈鐞氼偊宕愬┑瀣祦濞撴埃鍋撴鐐村浮楠炲鈹戦崱鈺傛笎婵犵數濮烽弫鎼佸磻濞戙垹绠犻柟閭﹀幘缁犻箖鏌嶈閸撶喖寮婚敍鍕勃缂侇垱娲栨禍楣冩煙妫颁胶顦﹂柟顔藉灴濮婃椽宕ㄦ繝鍌氼潎闂佸憡鏌ㄩ惌鍌炲灳閺嶎偀鍋撻敐搴℃灍闁抽攱鍨归埀顒冾潐濞叉牕煤閵娧勬殰婵炴垯鍨洪悡娑㈡倶閻愰鍤欏┑顔煎€块弻娑㈠煛閸愩劋妲愬Δ鐘靛仜椤戝寮崒鐐村癄濠㈣泛顦伴惈蹇涙⒒娴ｅ憡鎯堟い锔诲灦閹冾煥閸繄锛涢梺鍛婃处閸ㄤ即宕掗妸鈺傜叆闁绘柨鎼牎闂佹娊鏀遍崹鍦閹惧瓨濯村┑顔藉焾娴滄繈骞堥妸鈺佺倞妞ゆ帊鑳堕崣鍡涙⒑閸濆嫭绁╁ù婊勭矒閵嗗懘顢楁担椋庣畾闂佸憡鐟ラˇ顖涙叏瀹ュ棭娈介柣鎰綑缁楁帡鎽堕弽顓熺厓鐟滄粓宕滃☉銏″仏闁诡垎灞芥倯婵犮垼娉涢鍥储閻㈠憡鐓熼幖娣灮閳洘銇勯鐐村窛缂侇喛顕ц灒濠靛倽妫勭紞濠囧箖椤忓牆鐒垫い鎺戝閸ㄥ倿鎮规潪鎷岊劅婵炲吋鐗犻弻褑绠涢幘纾嬬闂佹椿鍘介悷锔炬崲濞戙垹骞㈡俊顖氭惈婵垽姊洪崨濠冪厸闁稿鎸剧槐鎾诲磼濮橆兘鍋撻幖浣哥９闁告縿鍎抽惌鎾垛偓瑙勬礀濞诧箓顢曢懞銉﹀弿婵妫楁晶濠氭煕閵堝棙绀冮柕鍥у楠炲洭鍩℃担鍝勫Ф闂備礁鎲￠懝楣冾敄婢舵劕钃熸繛鎴炃氬Σ鍫ユ煕濞嗘劦鐒介柛鎴濈秺濮婅櫣鎷犻弻銉偓妤併亜椤撶偛妲绘い顐㈢箰鐓ゆい蹇撴噹閳ь剙顭烽弻宥夊传閸曨偀鍋撹ぐ鎺撳仧闁哄稁鍘介悡娆撴煟閹捐櫕鎹ｉ柟鐣屽Х缁?4 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧湱鈧懓瀚崳纾嬨亹閹烘垹鍊炲銈呯箰缁夐潧袙鎼淬劍鈷掗柛灞剧懆閸忓本銇勯鐐靛ⅵ妞ゃ垺鐗犲畷鍗炩槈濡⒈鍞归梻浣规偠閸庢椽宕滃▎鎾村珔闁绘柨鍚嬮悡銉︾節闂堟稒锛嶆俊鎻掔秺閺岋繝宕ㄩ鎯у绩闂佸搫鏈粙鎴﹀煡婢舵劕纭€闁绘劕顕禍鍫曟⒒娴ｅ憡璐￠柡灞筋槹缁傚秹顢楅崒娑辨綗闂佸湱鍎ら〃鍛不濮樿鲸鍠愮€广儱顦悡鏇㈡煛閸愶絽浜鹃梺闈涙搐鐎氫即銆佸鈧幃娆撴濞戞帒寰嶉梻鍌欒兌绾爼寮崒鐐茬？闁圭粯甯╅崵妤呮煕閺囥劌鐏犵紒鐘崇⊕閵囧嫰骞掗幋婵呯敖闂佸摜鍠庨澶婎潖濞差亝鍤掗柕鍫濇啗閵忋倖鐓欑痪鏉垮船娴滅増顨ラ悙瀵稿⒌妞ゃ垺娲熼弫鎰板礋椤撶姷鏆伴梻鍌欑閹诧紕鎹㈤崒婧惧亾濮樷偓閸パ呭摋婵炲濮撮鍡涙偂閻斿憡鍙忔俊銈傚亾婵☆偅顨嗙粋宥咁煥閸忕姷鎳撻…銊╁川椤撴繂顥氱紓鍌欒兌缁垶鎯勯姘煎殨闁割偅娲栫粻锝嗙節闂堟稒鎼愰柍褜鍓﹂崹浼村煘閹达附鍊烽柟缁樺笚閸婎垶姊洪懡銈呮毐闁哄懐濞€閹即顢氶埀顒勭嵁鐎ｎ喗鏅濋柍褜鍓熼弻瀣炊椤掍胶鍘搁梺鎼炲劗閺呮盯寮搁弮鍫熺厱婵☆垱瀵чˉ澶愭煃鐟欏嫬鐏︽鐐诧躬閺屾稒绻濋崘銊ヮ潚閻庤娲橀崹鍧楃嵁濡偐纾兼俊顖滃帶楠炴绻濆閿嬫緲閳ь剚鍔欏畷鎴﹀箻缂佹鍙嗛梺鍝勬储閸斿鏌囬娑辨闁绘劖褰冮弳娆撴懚閺嶎厽鐓曟繝濞惧亾闁绘帪绠撳鏌ュ煛娴ｅ弶鏂€闂佺粯鍔曢悺銊х礊閹存惊鏃堟偐閸欏鍠愮紓浣戒含閸嬬偟鎹㈠┑瀣倞鐟滃繘鎮惧ú顏呪拺闁告劕寮堕幆鍫ユ煕婵犲偆鐓奸柟顕€顥撻幉鎾礋閳衡偓缁ㄥ姊洪崫鍕犻柛鏂块叄閵嗗倿宕ｆ径宀€顔曢梺鍛婁緱閸樻崘鍊寸紓鍌欐祰妞存悂骞戦崶褏鏆﹂柟鐑樺灍閺嬪酣鏌熼柇锕€鏋旈柛瀣埞鎴︽晬閸曨偂娌┑鐐插悑閻熲晠鐛Δ鈧…銊╁醇濠靛牜鍞归梻渚€娼х换鍫ュ磹閺囩姷鐭嗛柛鎰ㄦ杺娴滄粍銇勯幘璺烘瀻闁诲繆鏅犻弻娑㈠籍閸喐鏆犵紓浣虹帛缁诲牓骞冩禒瀣棃婵炵顔愮紞渚€寮婚妶鍥ㄥ枂闁告洦鍋勬慨銏ゆ煣閼姐倕浠﹂柟渚垮妼椤啰鎷犻煫顓烆棜闂傚倷绀侀幖顐︽儔婵傜绐楅柟鎹愭硾閸ㄦ繃绻涢崱妯诲碍缂佺姳鍗抽獮鏍庨鈧悘鈺呮⒒閸屻倕鐏″ǎ鍥э躬閹瑩顢旈崟銊ヤ壕鐟滃繘骞忚ぐ鎺撳亜闁绘挸娴烽敍鐔兼⒒閸屾氨澧涘〒姘殔椤洭寮介妸褏顔曢悗鐟板閸犳洜鑺遍懡銈傚亾閻熺増鍟炵紒璇插€块崺鐐哄箣閿旇棄浜归梺鍦帛鐢晜绂掓ィ鍐┾拺闂傚牃鏅濈€佃偐绱掗鐣屾噧妞ゎ偄绻愮叅妞ゅ繐瀚鎰版⒑缂佹ê濮堢憸鏉垮暣閳ユ牜鈧綆鍠楅埛鎴﹀级閻愭潙顥嬫い锔奸檮閵囧嫰顢橀悩鎻掑箣婵犵鈧磭鎽犻柟宄版噽閸栨牠寮撮悢琛″亾婵犳碍鈷戦悷娆忓閸斻倝鏌涢悢閿嬪仴鐎规洦鍓熸俊鎼佸Ψ椤旇棄鐦滈梻渚€娼ч悧鍡椢涘▎鎴滅剨闁汇垹鎲￠悡鏇㈡煟閺冨牊鏁遍柛瀣ㄥ劜椤ㄣ儵鎮欓懠顒傤唺闂佸綊顥撴慨鎾敊韫囨侗鏁婇柦妯侯槺濡诧綁姊婚崒娆戠獢婵炰匠鍥ㄥ亱闁糕剝銇傚☉妯锋瀻闁规儳纾崢閬嶆⒑閸︻厼鍔嬫い銊ユ閹繝寮撮姀鈥斥偓鐢告煥濠靛棝顎楀ù婊勭箘閳ь剝顫夊ú鏍儗閸岀偛钃?URL 闂傚倸鍊搁崐鎼佸磹閹间礁纾圭€瑰嫭鍣磋ぐ鎺戠倞鐟滃繘寮抽敃鍌涚厱妞ゎ厽鍨垫禍婵嬫煕濞嗗繒绠抽柍褜鍓濋～澶娒洪弽顬℃椽鏁傞挊澶婄亖婵炲鍘ч悺銊╂偂閵夆晜鐓涢柛銉㈡櫅娴犳粓鏌嶈閸撴岸骞冮崒姘辨殾鐟滅増甯掔壕濂告煟閹邦剦鍤熼柛姗€娼ч埞鎴︽倷閼碱剙顣洪梺璇茬箲缁诲牆顕ｉ幖浣瑰亜闁硅偐鍋樼花?
# ==========================================
COVERS_DIR = STATIC_COVERS_DIR
UPLOADS_DIR = STATIC_UPLOADS_DIR
os.makedirs(UPLOADS_DIR, exist_ok=True)
MAX_IMAGE_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_IMAGE_BASE64_CHARS = int(MAX_IMAGE_UPLOAD_BYTES * 1.4) + 1024
ALLOWED_IMAGE_FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


def _validate_and_decode_image(raw: str) -> tuple[bytes, str]:
    if not raw:
        raise HTTPException(status_code=400, detail="image_base64 is required")
    if len(raw) > MAX_IMAGE_BASE64_CHARS:
        raise HTTPException(status_code=413, detail="image is too large")

    header = ""
    encoded = raw
    if "," in raw:
        header, encoded = raw.split(",", 1)
    if header and not header.lower().startswith("data:image/"):
        raise HTTPException(status_code=400, detail="only image data URLs are allowed")

    try:
        img_bytes = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="invalid base64 image")

    if len(img_bytes) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="image is too large")

    try:
        with Image.open(io.BytesIO(img_bytes)) as img:
            img.verify()
            image_format = (img.format or "").upper()
    except Exception:
        raise HTTPException(status_code=400, detail="invalid image file")

    ext = ALLOWED_IMAGE_FORMATS.get(image_format)
    if not ext:
        raise HTTPException(status_code=400, detail="only png, jpg, jpeg and webp images are allowed")
    return img_bytes, ext


def _image_mime_type(ext: str) -> str:
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }.get((ext or "").lower(), "application/octet-stream")


def _chat_image_storage_key(user_id: int, session_id: Optional[int], ext: str) -> str:
    session_part = str(session_id) if session_id else "unassigned"
    return f"users/{user_id}/sessions/{session_part}/{uuid.uuid4().hex}.{ext}"


def _presigned_file_url(uploaded_file: UploadedFile, expires_seconds: int = 3600) -> Optional[str]:
    if not uploaded_file or uploaded_file.deleted_at is not None:
        return None
    try:
        return get_storage_service().get_presigned_url(
            uploaded_file.storage_key,
            bucket=uploaded_file.storage_bucket,
            expires_seconds=expires_seconds,
        )
    except Exception as e:
        logger.warning(f"[Storage] failed to sign uploaded file {getattr(uploaded_file, 'id', None)}: {e}")
        return None


def _store_validated_chat_image(
    db: Session,
    *,
    owner_user_id: int,
    raw_image: str,
    session_id: Optional[int] = None,
    run_id: Optional[str] = None,
    commit: bool = False,
) -> UploadedFile:
    img_bytes, ext = _validate_and_decode_image(raw_image)
    mime_type = _image_mime_type(ext)
    key = _chat_image_storage_key(owner_user_id, session_id, ext)
    stored = get_storage_service().put_object(key, img_bytes, mime_type, bucket=os.getenv("CHAT_UPLOAD_BUCKET", "chat-uploads"))
    uploaded = UploadedFile(
        owner_user_id=owner_user_id,
        session_id=session_id,
        run_id=run_id,
        storage_bucket=stored.bucket,
        storage_key=stored.key,
        mime_type=mime_type,
        file_size=stored.size,
        purpose="chat_image",
        meta_data={"original_ext": ext},
    )
    db.add(uploaded)
    db.flush()
    if commit:
        db.commit()
        db.refresh(uploaded)
    return uploaded


def _save_validated_image(raw: str) -> str:
    """Legacy local-file writer kept only for old /static/uploads compatibility paths."""
    img_bytes, ext = _validate_and_decode_image(raw)
    filename = f"{uuid.uuid4().hex}.{ext}"
    filepath = os.path.join(UPLOADS_DIR, filename)
    with open(filepath, "wb") as f:
        f.write(img_bytes)
    return f"/static/uploads/{filename}"


def _local_live_cover_url(i: int) -> str:
    try:
        files = sorted([
            f for f in os.listdir(COVERS_DIR)
            if _re.fullmatch(r"article_\d+\.png", f)
        ])
        if files:
            return f"/static/covers/{files[i % len(files)]}"
    except Exception:
        pass
    return ""


class ImageUploadRequest(BaseModel):
    image_base64: str  # 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾剧懓顪冪€ｎ亝鎹ｉ柣顓炴閵嗘帒顫濋敐鍛婵°倗濮烽崑娑⑺囬悽绋挎瀬闁瑰墽绮崑鎰版煠绾板崬澧婚柛鐐茬埣濮婄粯鎷呴崨濠傛殘缂備礁鎼鈥崇暦閺囥垹绠柤鎭掑劚閸撳綊姊洪悷鏉库挃缂侇噮鍨堕幃锟犲即閻旂繝绨婚梺瑙勬緲婢у海绮欑拠宸唵閻犲搫鎼顓㈡煛鐏炵澧查柟宄版噽缁瑦鎯旈幘鍏呭閻熸粎澧楃敮鎺擃攰闂備礁鎲″ú锕傚垂閹殿喚涓嶇€规洖娲犻崑鎾荤嵁閸喖濮庡┑鐐存綑閸婄宓勫銈嗘⒒閳峰牆銆掓繝姘厪闁割偅绻冮ˉ鐐电磼閳ь剛鈧綆鈧垻鎳撻…銊╁礃椤忓嫮鍘介柣搴㈩問閸犳牠鈥﹂悜钘夋瀬闁圭増婢樺婵嬫煕鐏炲墽鐭婇柡瀣⊕椤ㄣ儵鎮欓弶鎴濐潔闂佽鍨卞Λ鍐极閹版澘宸濇い鏇楀亾闁轰緡浜濠氬磼濮橆兘鍋撻幖浣哥９闁归棿绀佺壕褰掓煕濠靛嫬鍔楅柛瀣尭椤繈鎮℃惔鈽嗘骄缂傚倷绶￠崰妤€螞娓氣偓婵＄敻骞囬弶璺唺闂佺鎻徊楣冨箟?data:image/xxx;base64,... 闂傚倸鍊搁崐鎼佸磹閹间礁纾圭€瑰嫭鍣磋ぐ鎺戠倞妞ゆ帒顦伴弲顏堟偡濠婂啰绠婚柛鈹惧亾濡炪倖甯婇懗鍫曞煝閹剧粯鐓涢柛娑卞枤缁犳﹢鏌涢幒鎾崇瑨闁宠閰ｉ獮妯虹暦閸ヨ泛鏁藉┑鐘殿暜缁辨洟宕戝☉銏″剭闁绘垼妫勭壕濠氭煙閹规劦鍤欑紒鈧崘鈹夸簻闁哄倽锟ラ崑銏ゆ煕閻愯尙鍩ｆ慨濠冩そ濡啫鈽夋潏銊㈡灁闂備礁鎽滄慨鐢搞€冩繝鍌ゅ殨闁哄被鍎查崐鐑芥倵闂堟稒鎲告い鏃€娲熼弻鈩冨緞婵犲嫬顣烘繝鈷€鍌滅煓鐎规洘宀搁幃褔宕奸姀銏㈡闂備線鈧偛鑻晶鎾煛鐏炶姤顥滄い鎾炽偢瀹曘劑顢涘顒傤唺?
    session_id: Optional[int] = None

CHECKIN_ITEM_SEED = [
    {"code": "exercise", "name": "运动", "icon": "run", "icon_bg": "#eaf4cc", "category": "activity", "points": 20, "sort_order": 1},
    {"code": "water", "name": "喝水", "icon": "water", "icon_bg": "#dbeafe", "category": "nutrition", "points": 15, "sort_order": 2},
    {"code": "medicine", "name": "按时用药", "icon": "pill", "icon_bg": "#fde2e4", "category": "medication", "points": 25, "sort_order": 3},
    {"code": "sleep", "name": "睡眠", "icon": "sleep", "icon_bg": "#ede9fe", "category": "recovery", "points": 20, "sort_order": 4},
    {"code": "mood", "name": "心情记录", "icon": "mood", "icon_bg": "#fef3c7", "category": "mental", "points": 10, "sort_order": 5},
]
CHECKIN_ITEM_SEED_BY_CODE = {item["code"]: item for item in CHECKIN_ITEM_SEED}


def _is_broken_label(value: Optional[str]) -> bool:
    text_value = (value or "").strip()
    return not text_value or set(text_value) <= {"?"}


def _checkin_display_name(item: HealthCheckinItem) -> str:
    if not _is_broken_label(item.name):
        return item.name
    seed = CHECKIN_ITEM_SEED_BY_CODE.get(item.code)
    if seed:
        return seed["name"]
    return "健康打卡"

_CHECKIN_MIGRATED = False
_SYSTEM_CHECKIN_ITEMS_PURGED = False
_DB_TABLES_READY = False
_ADMIN_SCHEMA_MIGRATED = False
_CHAT_SCHEMA_MIGRATED = False


def _ensure_database_tables():
    """Create missing SQLAlchemy-managed tables when api_server.py is started directly."""
    global _DB_TABLES_READY
    if _DB_TABLES_READY:
        return
    try:
        Base.metadata.create_all(bind=engine)
        logger.info("[DB] SQLAlchemy table check completed")
    except Exception as e:
        logger.error(f"[DB] Failed to initialize database tables: {e}")
        raise
    _DB_TABLES_READY = True


def _ensure_admin_schema_migrated(db: Session):
    global _ADMIN_SCHEMA_MIGRATED
    if _ADMIN_SCHEMA_MIGRATED:
        return
    _ensure_database_tables()
    try:
        inspector = inspect(db.get_bind())
        user_cols = {c["name"] for c in inspector.get_columns("users")}
        alters = []
        if "role" not in user_cols:
            alters.append("ADD COLUMN role VARCHAR(20) NOT NULL DEFAULT 'user'")
        if "is_active" not in user_cols:
            alters.append("ADD COLUMN is_active BOOL NOT NULL DEFAULT 1")
        if "last_login_at" not in user_cols:
            alters.append("ADD COLUMN last_login_at DATETIME NULL")
        for alter in alters:
            db.execute(text(f"ALTER TABLE users {alter}"))
        if alters:
            db.commit()
            try:
                db.execute(text("CREATE INDEX ix_users_role ON users (role)"))
                db.execute(text("CREATE INDEX ix_users_is_active ON users (is_active)"))
                db.commit()
            except Exception:
                db.rollback()
    except Exception as e:
        db.rollback()
        logger.error(f"[Admin Migration] failed: {e}")
        raise HTTPException(status_code=503, detail="??????")
    _ADMIN_SCHEMA_MIGRATED = True


def _ensure_chat_schema_migrated(db: Session):
    global _CHAT_SCHEMA_MIGRATED
    if _CHAT_SCHEMA_MIGRATED:
        return
    _ensure_database_tables()
    try:
        inspector = inspect(db.get_bind())
        session_cols = {c["name"] for c in inspector.get_columns("chat_sessions")}
        message_cols = {c["name"] for c in inspector.get_columns("chat_messages")}
        session_alters = {
            "current_slots": "ADD COLUMN current_slots JSON NULL",
            "current_route": "ADD COLUMN current_route VARCHAR(80) NOT NULL DEFAULT ''",
            "turn_count": "ADD COLUMN turn_count INT NOT NULL DEFAULT 1",
            "active_run_id": "ADD COLUMN active_run_id VARCHAR(64) NULL",
            "state_version": "ADD COLUMN state_version INT NOT NULL DEFAULT 0",
        }
        for col, alter in session_alters.items():
            if col not in session_cols:
                db.execute(text(f"ALTER TABLE chat_sessions {alter}"))
        message_alters = {
            "run_id": "ADD COLUMN run_id VARCHAR(64) NULL",
            "uploaded_file_id": "ADD COLUMN uploaded_file_id INT NULL",
        }
        for col, alter in message_alters.items():
            if col not in message_cols:
                db.execute(text(f"ALTER TABLE chat_messages {alter}"))
        db.commit()
        for stmt in [
            "CREATE INDEX idx_chat_sessions_active_run_id ON chat_sessions (active_run_id)",
            "CREATE INDEX idx_chat_messages_run_id ON chat_messages (run_id)",
            "CREATE INDEX idx_chat_messages_uploaded_file_id ON chat_messages (uploaded_file_id)",
        ]:
            try:
                db.execute(text(stmt))
                db.commit()
            except Exception:
                db.rollback()
    except Exception as e:
        db.rollback()
        logger.error(f"[Chat Migration] failed: {e}")
        raise HTTPException(status_code=503, detail="chat schema is unavailable")
    _CHAT_SCHEMA_MIGRATED = True


def _admin_count(db: Session) -> int:
    _ensure_admin_schema_migrated(db)
    return db.query(User).filter(User.role == "admin").count()


def _log_admin_action(
    db: Session,
    admin: Optional[User],
    action: str,
    *,
    target_type: Optional[str] = None,
    target_id: Optional[str] = None,
    detail: Optional[dict] = None,
) -> None:
    try:
        db.add(AdminActionLog(
            admin_user_id=admin.id if admin else None,
            action=action[:80],
            target_type=target_type,
            target_id=str(target_id)[:80] if target_id is not None else None,
            detail=detail or {},
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[AdminActionLog] write failed: {e}")


def _ensure_checkin_schema_migrated(db: Session):
    """
    Graceful migration: old tables without owner_user_id get an ALTER TABLE once.
    Failures are logged and skipped so SQLAlchemy can report later errors normally.
    """
    global _CHECKIN_MIGRATED
    if _CHECKIN_MIGRATED:
        return
    try:
        from sqlalchemy import inspect, text
        inspector = inspect(db.get_bind())
        cols = {c["name"] for c in inspector.get_columns("health_checkin_items")}
        if "owner_user_id" not in cols:
            logger.info("[Checkin Migration] adding owner_user_id column")
            db.execute(text(
                "ALTER TABLE health_checkin_items ADD COLUMN owner_user_id INT NULL,"
                " ADD INDEX idx_owner_user_id (owner_user_id)"
            ))
            db.commit()
            logger.info("[Checkin Migration] owner_user_id column added")
        if "icon_bg" not in cols:
            logger.info("[Checkin Migration] adding icon_bg column")
            db.execute(text(
                "ALTER TABLE health_checkin_items "
                "ADD COLUMN icon_bg VARCHAR(20) NOT NULL DEFAULT '#eaf4cc'"
            ))
            db.commit()
    except Exception as e:
        logger.warning(f"[Checkin Migration] skipped: {e}")
    _CHECKIN_MIGRATED = True


def _ensure_checkin_items_seeded(db: Session):
    _ensure_checkin_schema_migrated(db)
    return
    existing_items = {
        row.code: row for row in db.query(HealthCheckinItem)
        .filter(HealthCheckinItem.owner_user_id.is_(None))
        .all()
    }
    existing_codes = {
        code for code in existing_items.keys()
    }
    missing = [item for item in CHECKIN_ITEM_SEED if item["code"] not in existing_codes]
    changed = False
    for seed in CHECKIN_ITEM_SEED:
        existing = existing_items.get(seed["code"])
        if existing and _is_broken_label(existing.name):
            existing.name = seed["name"]
            existing.icon = existing.icon or seed["icon"]
            existing.icon_bg = existing.icon_bg or seed["icon_bg"]
            existing.category = existing.category or seed["category"]
            existing.points = existing.points or seed["points"]
            existing.sort_order = existing.sort_order or seed["sort_order"]
            existing.is_active = True
            changed = True
    if not missing and not changed:
        return
    for item in missing:
        # 缂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁惧墽鎳撻—鍐偓锝庝簻椤掋垺銇勯幇顏嗙煓闁哄被鍔戦幃銏ゅ传閸曟垯鍨介弻娑㈠Ω閵夈儮鍋撻崹顕呮綎濠电姵鑹鹃柋鍥煟閺冣偓娴滀粙鍩€椤掍礁娴柡宀嬬節瀹曢亶顢橀悩鍨闂備礁鎼惌澶岀礊娴ｈ鍙忛柍褜鍓熼弻鏇㈠醇濠靛浂妫￠梻浣诡儥閸欏啫顫忓ú顏勭闁绘劖褰冮‖澶岀磽娴ｇ瓔鍤欓柣妤佹尭椤曪絾绻濆顓熸珳婵犮垼娉涢敃锕傤敇濞差亝鈷戠紓浣姑悘銉︾箾鐠囇呮偧婵炲棎鍨藉Λ鍐ㄢ槈閹烘挻鏉搁梻浣虹帛閸旀洟鎮洪妸銉ф殾闁告洦鍨遍悡鏇㈡煃閸濆嫬鈧粯鏅堕鍌滅＜鐎光偓閸愵喖鎽电紓浣虹帛缁诲牆鐣烽崼鏇熷殝闁割煈鍋呴悵顐⑩攽閻樺灚鏆╅柛瀣仱瀹曞綊妫冨☉姘濡炪倖甯掔€氼剛绮堟径灞稿亾閸忓浜鹃梺鍛婃处閸忔﹢骞忓ú顏呪拺闁告稑锕﹂埥澶愭煥閺囨ê濡界紒鏃傚枎铻ｉ柤濮愬€楅鏇㈡煟韫囨洖浠滈柛濠冩倐閸┾偓妞ゆ帊鑳剁粻鐐烘煕?owner_user_id 濠电姷鏁告慨鐑藉极閸涘﹥鍙忛柣鎴ｆ閺嬩線鏌熼梻瀵割槮缁炬儳娼￠弻鐔衡偓鐢殿焾瀛濈紓浣界堪閸婃繈寮婚敃鈧灒濞撴凹鍨遍敍鍡椻攽閻愬弶鈻曞ù婊勭箞瀵彃顭ㄩ崼鐔哄幗闂侀€涘嵆濞佳勬櫠椤曗偓楠炴牠寮堕幋顖濆惈濠殿喖锕ュ钘夌暦閵婏妇绡€闁稿本顕撮弴鐔虹閻庢稒顭囬惌瀣煟濡ゅ啫孝闁伙絿鍏橀獮瀣晝閳ь剛绮绘繝姘仯闁搞儯鍔岀徊濠氭煟鎼搭喖骞栨い顏勫暣婵″爼宕卞Δ鈧〖缂傚倷绀佸璺侯渻娴犲宓侀煫鍥ㄧ☉閹硅埖銇勯幘璺盒ラ柨?NULL
        db.add(HealthCheckinItem(**item, owner_user_id=None))
    db.commit()


def _purge_system_checkin_items(db: Session):
    global _SYSTEM_CHECKIN_ITEMS_PURGED
    if _SYSTEM_CHECKIN_ITEMS_PURGED:
        return
    _ensure_checkin_schema_migrated(db)
    system_codes = [
        row[0] for row in db.query(HealthCheckinItem.code)
        .filter(HealthCheckinItem.owner_user_id.is_(None))
        .all()
    ]
    if system_codes:
        db.query(UserHealthCheckin).filter(
            UserHealthCheckin.item_code.in_(system_codes)
        ).delete(synchronize_session=False)
        db.query(HealthCheckinItem).filter(
            HealthCheckinItem.owner_user_id.is_(None)
        ).delete(synchronize_session=False)
        db.commit()
        logger.info("[Checkin] purged %s system checkin items", len(system_codes))
    _SYSTEM_CHECKIN_ITEMS_PURGED = True


def _parse_date_or_today(raw_date: Optional[str]) -> date:
    if not raw_date:
        return date.today()
    try:
        return date.fromisoformat(raw_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式无效")


def _calculate_checkin_streak(db: Session, user_id: int, anchor_date: date) -> int:
    all_dates = {
        row[0] for row in db.query(UserHealthCheckin.checkin_date)
        .filter(UserHealthCheckin.user_id == user_id)
        .all()
    }
    streak = 0
    cursor = anchor_date
    while cursor in all_dates:
        streak += 1
        cursor -= timedelta(days=1)
    return streak


def _checkin_date_window(target_date: date):
    start_31 = target_date - timedelta(days=30)
    return [start_31 + timedelta(days=i) for i in range(31)]


def _build_checkin_item_view(item: HealthCheckinItem, item_records: dict, target_date: date):
    week_start_date = target_date - timedelta(days=target_date.weekday())
    month_start_date = target_date.replace(day=1)
    record = item_records.get(target_date)
    done_today = bool(record and record.status == "done")
    week_count = sum(
        1 for checkin_date, row in item_records.items()
        if checkin_date >= week_start_date and row.status == "done"
    )
    month_count = sum(
        1 for checkin_date, row in item_records.items()
        if checkin_date >= month_start_date and row.status == "done"
    )
    return {
        "code": item.code,
        "name": _checkin_display_name(item),
        "icon": item.icon,
        "icon_bg": item.icon_bg,
        "category": item.category,
        "points": item.points,
        "is_active": item.is_active,
        "is_custom": item.owner_user_id is not None,
        "today_status": record.status if record else "pending",
        "today_points": record.points_earned if record else 0,
        "value_json": record.value_json if record else None,
        "done_today": done_today,
        "done": done_today,
        "status": record.status if record else "pending",
        "week_count": week_count,
        "month_count": month_count,
        "last31": [
            {
                "date": checkin_date.isoformat(),
                "done": bool(item_records.get(checkin_date) and item_records[checkin_date].status == "done"),
            }
            for checkin_date in _checkin_date_window(target_date)
        ],
    }


def _load_checkin_item_view(db: Session, user_id: int, item: HealthCheckinItem, target_date: date):
    start_31 = target_date - timedelta(days=30)
    records = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == user_id,
        UserHealthCheckin.item_code == item.code,
        UserHealthCheckin.checkin_date >= start_31,
        UserHealthCheckin.checkin_date <= target_date,
    ).all()
    return _build_checkin_item_view(
        item,
        {record.checkin_date: record for record in records},
        target_date,
    )


def _build_today_checkin_payload(db: Session, user_id: int, target_date: date):
    _ensure_database_tables()
    _ensure_checkin_schema_migrated(db)
    _ensure_checkin_items_seeded(db)
    # 濠电姷鏁告慨鐑藉极閸涘﹥鍙忛柣鎴ｆ閺嬩線鏌熼梻瀵割槮缁炬儳顭烽弻锝夊箛椤掍焦鍎撻梺鎼炲妼閸婂潡寮诲☉銏╂晝闁挎繂妫涢ˇ銉х磽娴ｅ搫小闁告濞婂璇测槈濡攱鏂€闂佸憡娲﹂崑鍕叏閵忋倖鈷戞繛鑼额嚙楠炴鏌熼悷鐗堝枠鐎殿喖顭烽幃銏ゆ惞閸︻叏绱查梻渚€娼х换鎺撴叏閻㈠憡鍊堕柛顐犲劜閳锋垶鎱ㄩ悷鐗堟悙闁逞屽厵閸婃繂鐣烽幎鑺ユ櫜濠㈣泛锕ㄩ幗鏇㈡倵楠炲灝鍔氶柣妤佺矊椤﹪濡搁埡鍌楁嫼闂佸憡绋戦敃銉т焊閻㈠憡鐓曢柣妯虹－婢ь亝銇勯弴顏嗙ɑ缂佺粯绻傞～婵嬵敄閳诡厼娲﹂悡鏇㈡煃閳轰礁鏆熼柟鍐叉处娣囧﹦绱掗姀鐘崇亶缂備胶绮换鍫熸叏閳ь剟鏌ㄥ┑鍡橆棤闁靛棙鍔曢—鍐Χ韫囨洜鏆ゆ繛瀛樼矊閻栧ジ鐛崱娑樼妞ゆ帊绀侀崝鍛存⒑闂堟稓绠為柛銊ヮ煼閹?seed 缂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁惧墽鎳撻—鍐偓锝庝簻椤掋垺銇勯幇顏嗙煓闁哄被鍔戦幃銏ゅ传閸曟垯鍨介弻娑㈠Ω閵夈儮鍋撻崹顕呮綎濠电姵鑹鹃柋鍥煟閺冣偓娴滀粙鍩€椤掍礁娴柡宀嬬節瀹曢亶顢橀悩鍨闂備礁鎼惌澶岀礊娴ｈ鍙忛柍褜鍓熼弻鏇㈠醇濠靛浂妫￠梻浣诡儥閸欏啫顫忓ú顏勭闁绘劖褰冮‖澶岀磽娴ｇ瓔鍤欓柣妤佹尭椤曪絾绻濆顓熸珳婵犮垼娉涢敃锕傤敇濞差亝鈷戠紓浣姑悘銉︾箾鐠囇呮偧婵炲棎鍨藉Λ鍐ㄢ槈閹烘挻鏉搁梻浣虹帛閸旀洟鎮洪妸銉ф殾闁告洦鍨遍悡鏇㈡煃閸濆嫬鈧粯鏅堕鍌滅＜鐎光偓閸愵喖鎽电紓浣虹帛缁诲牆鐣烽崼鏇熷殝闁割煈鍋呴悵顐⑩攽閻樺灚鏆╅柛瀣仱瀹曞綊妫冨☉姘濡炪倖甯掔€氼剛绮堟径灞稿亾閸忓浜鹃梺鍛婃处閸忔﹢骞忓ú顏呪拺闁告稑锕﹂埥澶愭煥閺囨ê濡界紒鏃傚枎铻ｉ柤濮愬€楅鏇㈡煟韫囨洖浠滈柛濠冩倐閸┾偓妞ゆ帊鑳剁粻鐐烘煕閵娾晝鐣虹€殿噮鍣ｅ畷鐓庘攽閸偅袨濠碉紕鍋戦崐鏍蓟閵娿儙锝夊醇閿濆孩鈻岄梻浣告惈閺堫剟鎯勯娑楃箚闁绘垹鐡旈弫濠囨煙椤栧棗鍟槐顒佺節閻㈤潧浠滄い鏇ㄥ幗閹便劌顓兼径濠傜獩濡炪倖鐗楃划锝囨閸愭祴鏀介柣鎰煐瑜把呯磼閼艰泛浜归柛鎺撳笒閳诲酣骞橀崗鍛倞闂備焦鍎崇换鎰耿鏉堚晛顥氬┑鍌氭啞閸嬶綁鏌涢妷顔荤盎闁汇劌鎼…鑳檪闂傚嫬瀚伴妴鍐Ψ閳哄倸鈧兘鏌熺紒妯虹瑲婵炲牐灏欑槐鎾存媴缁嬪簱鍋撻崷顓熸殰闁绘劕鎼悞鍨亜閹烘埊鍔熺紒澶屾暬閺屽秷顧侀柛鎿勭畵瀹曘垽骞栨担鍝ユ煣闂佺粯顭堥褔鎷戦悢鍏肩厪濠电偟鍋撳▍鍛存煕濡ゅ嫭鐝ǎ鍥э躬婵″爼宕掑顐㈩棜婵犵數鍋涢悺銊у垝閹惧墎涓嶉柡宓本缍庨梺鎯х箰濠€杈╁閸忛棿绻嗘い鏍ㄧ箥閸ゆ瑩姊绘径濠勑㈤柍瑙勫灴閹瑩骞撻幒鏃堢崜闂備胶绮〃鍫熸叏閹绢喗鏅濋柕蹇ョ磿閻熷綊鏌嶈閸撴瑩锝炶箛娑欐優闁革富鍘鹃敍婊冣攽閳藉棗鐏℃繛鍙夛耿瀹曞綊宕稿Δ鈧粻鏍煥閻斿搫校闁哄懏鎮傞弻锝呂熼崹顔炬闂佺粯鎸婚崹鍨潖濞差亜宸濆┑鐘插暙椤︹晠姊洪崨濠冨鞍缂佸鎸鹃崚鎺旂磼濡浜濋梺鍛婂姀閺呮繈宕㈤崨濠勭瘈闁靛骏绲剧涵鐐亜閹存繃鍤囬柟顔斤耿婵＄兘鍩￠崒婊冨笚闁荤喐绮嶇划鎾崇暦濠婂喚娼╅悹楦挎閻ｆ椽姊虹粙璺ㄧ伇闁稿鍨块崺鈧い鎴ｆ硶椤︼附銇勯锝囩疄闁硅櫕绮撳畷褰掝敃閿濆洤绀佸┑鐘垫暩婵即宕归悡搴樻灃婵炴垯鍨洪弲婵嬫煥閺囩偛鈧摜绮堟径鎰叆闁哄洦顨呮禍鎯ь渻閵堝簼绨婚柛鐔风摠娣囧﹪宕奸弴鐐茶€垮┑掳鍊曢崯浼村箟椤忓牊鈷掑ù锝堟鐢盯鎮介锝勭敖缂侇喖顭烽獮妯虹暦閸ャ劍顔曢梻浣瑰缁诲倿鎮ц箛娑欏仾闁逞屽墮閳规垿鎮欓弶鎴犱桓闂佽崵鍠嗛崕鐢稿春閳ь剚銇勯幒鎴濃偓褰掑汲椤掑嫭鐓涢悘鐐额嚙婵″ジ鏌嶇憴鍕伌鐎规洖宕埥澶愬箥娴ｉ晲澹曞┑掳鍊撶欢鈥斥枔娴犲鐓熼柟閭﹀墻閸ょ喎顭胯閸撴稓妲愰幒鏃傜＜婵☆垰鍚嬮崚娑㈡倵鐟欏嫭绀堥柛妯犲洠鈧箓濡搁埡浣侯槰闂侀潧臎娴ｉ晲绮￠梻鍌氬€搁崐宄懊归崶顒夋晪闁哄稁鍘肩粣妤佺箾閹寸們姘跺几閺嶃劎绠鹃柟瀛樼懃閻忊晠鏌ｉ幘顖楀亾閹颁胶鍞甸柣鐘叉惈閵堜粙鏁撻悩鍐蹭簻闂佹儳绻愬﹢閬嶆晬濠婂啠鏀介柍钘夋閻忋儲銇勯弴鍡楁祩閺佸倸霉閻撳海鎽犻柍閿嬪浮閺屾稓浠﹂崜褎鍣梺绋跨箰閺堫剟濡甸崟顖ｆ晝闁靛繆鏅涢崜鍫曟倵鐟欏嫭绀冮柨鏇樺灲楠炲﹤螖閸滀焦鏅┑顔斤供閸樺ジ宕滈悽鍛娾拻濞达絿鐡旈崵娆愭叏濮楀牏鐣甸柨婵堝仦瀵板嫭绻涢幒鎴炴儓閾伙綁鏌涜箛鏇炲付閹兼潙锕娲礈閹绘帊绨撮梺绋挎唉娴滎剛鍒掓繝姘唨妞ゆ挾鍠撻崢鎾绘⒑閸涘﹦绠撻悗姘煎幗閸掑﹥绺介崨濠勫幐婵炶揪缍佸濠氱叕椤掑嫭鐓涚€光偓鐎ｎ剛袦闂佽鍠撻崹濠氬窗婵犲啯缍囬柕濠忓瘜閸氬绻?    # _ensure_checkin_items_seeded(db)
    items = db.query(HealthCheckinItem).filter(
        HealthCheckinItem.is_active == True,
        or_(HealthCheckinItem.owner_user_id == user_id, HealthCheckinItem.owner_user_id.is_(None)),
    ).order_by(HealthCheckinItem.sort_order.asc(), HealthCheckinItem.id.asc()).all()
    records = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == user_id,
        UserHealthCheckin.checkin_date == target_date
    ).all()
    record_map = {record.item_code: record for record in records}

    checkins = []
    total_points = 0
    completed_count = 0
    for item in items:
        record = record_map.get(item.code)
        done = bool(record and record.status == "done")
        points_earned = record.points_earned if record else 0
        total_points += points_earned
        completed_count += 1 if done else 0
        checkins.append({
            "code": item.code,
            "name": _checkin_display_name(item),
            "icon": item.icon,
            "icon_bg": item.icon_bg,
            "category": item.category,
            "points": item.points,
            "done": done,
            "status": record.status if record else "pending",
            "value_json": record.value_json if record else None,
            # 闂傚倸鍊搁崐鎼佸磹閹间礁纾圭€瑰嫭鍣磋ぐ鎺戠倞鐟滃繘寮抽敃鍌涚厱妞ゎ厽鍨垫禍婵嬫煕濞嗗繒绠抽柍褜鍓濋～澶娒洪弽顬℃椽鏁傞挊澶婄亖婵犻潧鍊搁幉锟犳偂濞戙垺鍊堕柣鎰邦杺閸ゆ瑩鏌涢弮鈧畝鎼佸蓟閿濆围闁搞儜鍌氼棜缂傚倷鑳剁划顖炴儎椤栫偟宓侀悗锝庡枟閸婄兘鏌涢…鎴濅簻婵炲懏顨嗙换婵嬫偨闂堟稐绮堕梺纭呮珪閸旀牜鎹㈠☉銏犵劦妞ゆ帒瀚悡娑氣偓鍏夊亾閻庯綆鍓欓崺宀勬煣娴兼瑧绉柡灞剧缁犳盯骞橀搹顐⑩偓顖氣攽椤曞棛鍒伴悗姘緲椤繐煤椤忓嫬绐涙繝鐢靛Т鐎涒晠鎮炬搴ｇ＜闁绘劦鍓氱欢鑼磼婢跺寒娼愬ǎ鍥э躬閹晫绮欑捄顭戞Ч婵＄偑鍊栭悧妤呮嚌妤ｅ啫鑸归柕濞炬櫆閳锋垿姊婚崼鐔剁繁闁绘帡绠栭弻娑㈠棘鐠恒劎鍔柦妯煎枑缁绘繈妫冨☉鍗炲壉闂佹娊鏀辩敮锟犲蓟濞戞矮娌柛鎾楀本娈归梻渚€娼荤徊鎯ь渻娴犲钃熼柕濞炬櫅閸楄櫕淇婇婵囶仩濞寸厧鐗撻幃妤€鈻撻崹顔界仌濡炪倖娉﹂崶褏鍙€婵犮垼鍩栭崝鏇綖閸涘瓨鐓熸俊顖涙た閸熷繘鏌涢悙瀛樸仢婵﹦绮幏鍛存倻濡儤鐣俊鐐€栧ú锕傚矗閸愩劎鏆﹂柡鍥ュ灩缁犳稒銇勯幋鏃€娅嗛柣鈺婂灡娣囧﹪鎳滈棃娑氱獮闁诲函鎬ラ崟顐紲濠电姷鏁搁崑鐐哄箰婵犳碍鍤岄柟顖ｇ亹濞差亜围濠㈣泛锕ら崵鎴濃攽閻愭潙鐏﹂柣鐕傜稻缁傚秴顭ㄩ崼鐔哄幐闂佹悶鍎崕杈ㄤ繆閸忕⒈娈介柣鎰懖閹寸偟鈹嶅┑鐘叉搐閻顭跨捄鐚村伐妞ゎ偅甯掕灃闁绘﹢娼ф禒锕傛偨椤栨稑绗╅柣蹇撳暞缁绘稒娼忛崜褏袦闂佸搫鎳撶亸娆撴嚍闁秵鍤掗柕鍫濇川閿涙繈姊虹粙鎸庢拱闁荤啙鍥х鐎广儱妫庢禍婊堟煏婵炲灝鍔存俊顖楀亾濠电姷顣介崜婵嬪箰閹惰棄鏄ラ柍褜鍓氶妵鍕箳閹存繍浠鹃梺缁樻尰濞茬喖骞冨Δ鍛櫜閹肩补鈧尙鍑归梺璇茬箰濞存岸宕㈡禒瀣﹂柛鏇ㄥ灠缁犲鏌涢幇顒€绾ч柛濠庡灦閹宕归锝囧嚒闁诲孩鍑归崢楣冨箲閵忕姭鏀介悗锝庝簽椤︺劑姊洪幐搴㈩梿闁稿氦顕ч‖濠囧箹娴ｅ厜鎷绘繛杈剧悼閻℃棃宕靛▎寰棃鎮╅搹顐⑩偓鎰偓瑙勬磸閸ㄥ綊鈥﹂妸鈺侀唶婵犻潧鐗炵槐閬嶆⒒娴ｇ儤鍤€濠⒀呮櫕閸掓帡顢涢悙鏉戜簵闂佸憡鍔︽禍婵嬪窗閹邦厾绡€濠电姴鍊绘晶鏇犵磼閳ь剟宕奸妷锔惧幗闂佸搫鍊归娆忊枔閻樼粯鐓曢柕鍫濇缁€鍐煏閸パ冾伃妤犵偛娲畷婊勬媴閻戞ɑ顔忛梻浣规偠閸庨亶鎮洪妸褎宕叉繝闈涱儏椤懘鏌ㄥ┑鍡橆棤闁靛棙鍔欏娲箰鎼淬垻鈹涚紓浣哄У閹瑰洭濡存担鍓叉建闁逞屽墴楠炲啫顭ㄩ崗鍓у枛瀹曠兘顢橀悩鍨瘞闂傚倸鍊烽懗鍓佸垝椤栫偛绀夐柡鍥╁€ｉ悢鍝ョ瘈闁搞儜鍜冪吹闂傚鍋勫ú锔剧矙閹烘纾归柣銏犳啞閻撴洘銇勯鐔风仴濞存粍绮庣槐鎺懳旀担鍝ョ懖闂侀潧娲ょ€氫即銆佸鈧幃娆戔偓娑櫳戦崐鐑芥⒒娴ｉ涓茬紒鎻掑⒔閹广垽宕煎┑鍫熸闂佺粯姊婚埛鍫ュ极閸℃稒鐓曢悘鐐村礃婢规绱掗悩鍗炲祮婵﹤顭峰畷鎺戔枎閹搭厽袦濠电姰鍨婚幊鎾绘晝椤忓嫮鏆﹂柟杈剧畱瀹告繃銇勯弽銊р槈閹兼潙锕幃妤呯嵁閸喖濮庨梺鐟板级閿曘垽骞冮姀銈嗗亗閹艰揪绲芥慨锔戒繆閻愵亜鈧牜鏁幒鏂哄亾濮樼厧寮柛鈺傜洴楠炲鏁傜憴锝嗗闂備礁澹婇崑鍡涘窗閹捐鐓€闁哄洨鍠嗘禍婊堟煏婢舵稑顩柣顓炵灱缁辨帗娼忛妸銉х懖閻庡灚婢樼€氼參骞嗛弮鍫晩闁伙絽鐬煎暩濠?            "is_custom": item.owner_user_id is not None,
        })

    total_count = len(items)
    completion_rate = round((completed_count / total_count) * 100) if total_count else 0
    streak_days = _calculate_checkin_streak(db, user_id, target_date)
    return {
        "date": target_date.isoformat(),
        "checkins": checkins,
        "summary": {
            "completed_count": completed_count,
            "total_count": total_count,
            "completion_rate": completion_rate,
            "total_points": total_points,
            "streak_days": streak_days,
        }
    }

def _build_today_checkin_payload(db: Session, user_id: int, target_date: date):
    _ensure_database_tables()
    _ensure_checkin_schema_migrated(db)
    _purge_system_checkin_items(db)
    items = db.query(HealthCheckinItem).filter(
        HealthCheckinItem.is_active == True,
        HealthCheckinItem.owner_user_id == user_id,
    ).order_by(HealthCheckinItem.sort_order.asc(), HealthCheckinItem.id.asc()).all()

    start_31 = target_date - timedelta(days=30)
    records = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == user_id,
        UserHealthCheckin.checkin_date >= start_31,
        UserHealthCheckin.checkin_date <= target_date,
    ).all()
    records_by_code = {}
    for record in records:
        records_by_code.setdefault(record.item_code, {})[record.checkin_date] = record

    checkins = [
        _build_checkin_item_view(item, records_by_code.get(item.code, {}), target_date)
        for item in items
    ]
    completed_count = sum(1 for item in checkins if item["done"])
    total_points = sum(item.get("today_points") or 0 for item in checkins)
    total_count = len(checkins)
    completion_rate = round((completed_count / total_count) * 100) if total_count else 0
    return {
        "date": target_date.isoformat(),
        "checkins": checkins,
        "summary": {
            "completed_count": completed_count,
            "total_count": total_count,
            "completion_rate": completion_rate,
            "total_points": total_points,
            "streak_days": _calculate_checkin_streak(db, user_id, target_date),
        }
    }


def _build_home_dashboard(db: Session, user: User):
    target_date = date.today()
    today_payload = _build_today_checkin_payload(db, user.id, target_date)
    summary = today_payload["summary"]
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == user.id).first()
    profile_data = profile.profile_data if profile and profile.profile_data else {}
    session_count = db.query(ChatSession).filter(ChatSession.user_id == user.id).count()

    age = profile_data.get("age") or 0
    weight = profile_data.get("weight") or 0
    height = profile_data.get("height") or 0
    chronic_count = len(profile_data.get("diseases") or []) + len(profile_data.get("past_diseases_common") or []) + len(profile_data.get("past_diseases_custom") or [])

    health_score = 70
    if summary["completion_rate"] >= 80:
        health_score += 12
    elif summary["completion_rate"] >= 50:
        health_score += 6
    if summary["streak_days"] >= 7:
        health_score += 8
    elif summary["streak_days"] >= 3:
        health_score += 4
    if profile_data.get("exercise") == "??3???":
        health_score += 6
    if profile_data.get("sleep") == "?????":
        health_score += 4
    if chronic_count > 0:
        health_score -= 8
    if isinstance(height, (int, float)) and isinstance(weight, (int, float)) and height > 0:
        bmi = weight / ((height / 100) ** 2)
        if 18.5 <= bmi < 24:
            health_score += 4
        elif bmi >= 28:
            health_score -= 6
    health_score = max(35, min(98, int(health_score)))

    score_tags = []
    if summary["streak_days"] > 0:
        score_tags.append(f"???? {summary['streak_days']} ?")
    if summary["completion_rate"] >= 60:
        score_tags.append("??????")
    if chronic_count == 0:
        score_tags.append("?????")
    if not score_tags:
        score_tags = ["?????????", "?????????"]

    if summary["completed_count"] < summary["total_count"]:
        missing_names = [item["name"] for item in today_payload["checkins"] if not item["done"]][:2]
        missing_text = "?".join(missing_names)
        tip = f"???? {summary['total_count'] - summary['completed_count']} ????????? {missing_text}?"
    elif chronic_count > 0:
        tip = "????????????????????????????"
    else:
        tip = "????????????????????????"

    recent_sessions = db.query(ChatSession).filter(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).limit(3).all()
    recent_chat_cards = []
    for session in recent_sessions:
        last_message = db.query(ChatMessage).filter(ChatMessage.session_id == session.id).order_by(ChatMessage.created_at.desc()).first()
        preview = (last_message.content[:32] + "...") if last_message and len(last_message.content) > 32 else (last_message.content if last_message else "????????")
        recent_chat_cards.append({
            "id": session.id,
            "title": session.title,
            "preview": preview,
            "time": session.updated_at.strftime("%m-%d %H:%M") if session.updated_at else "",
        })

    metrics = [
        {"key": "checkins", "label": "????", "value": str(summary["completed_count"]), "unit": "?"},
        {"key": "streak", "label": "????", "value": str(summary["streak_days"]), "unit": "?"},
        {"key": "sessions", "label": "????", "value": str(session_count), "unit": "?"},
        {"key": "points", "label": "????", "value": str(summary["total_points"]), "unit": "?"},
    ]
    return {
        "username": user.username,
        "profile_exists": bool(profile and profile.profile_data),
        "health_score": health_score,
        "score_tags": score_tags[:3],
        "tip": tip,
        "metrics": metrics,
        "today_checkins": today_payload,
        "recent_sessions": recent_chat_cards,
        "profile_snapshot": {
            "age": age,
            "height": height,
            "weight": weight,
        }
    }


@app.post("/api/upload_image")
async def upload_image(req: ImageUploadRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if req.session_id:
        session = db.query(ChatSession).filter(
            ChatSession.id == req.session_id,
            ChatSession.user_id == current_user.id,
        ).first()
        if not session:
            raise HTTPException(status_code=404, detail="session not found")
    uploaded = _store_validated_chat_image(
        db,
        owner_user_id=current_user.id,
        raw_image=req.image_base64,
        session_id=req.session_id,
        commit=True,
    )
    signed_url = _presigned_file_url(uploaded)
    return {
        "file_id": uploaded.id,
        "url": signed_url,
        "storage_key": f"{uploaded.storage_bucket}/{uploaded.storage_key}",
        "mime_type": uploaded.mime_type,
        "size": uploaded.file_size,
    }


@app.get("/api/files/object/{bucket}/{object_path:path}")
def get_local_object_file(bucket: str, object_path: str, exp: int = Query(...), sig: str = Query(...)):
    if not verify_object_url_signature(bucket, object_path, exp, sig):
        raise HTTPException(status_code=403, detail="file URL expired or invalid")
    storage = get_storage_service()
    if not isinstance(storage, LocalStorageService):
        raise HTTPException(status_code=404, detail="local object proxy is not enabled")
    path = storage.local_path(bucket, object_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="file not found")
    return FileResponse(path)


@app.get("/api/recommend_queries")
def get_recommend_queries():
    try:
        selected = random.sample(RECOMMEND_QUESTIONS, 3)
        return {"status": "success", "queries": selected}
    except Exception as e:
        return {"status": "error", "queries": ["?????????", "????????", "?????????"]}


_ARTICLE_SCHEMA_MIGRATED = False


def _ensure_article_schema_migrated(db: Session):
    global _ARTICLE_SCHEMA_MIGRATED
    if _ARTICLE_SCHEMA_MIGRATED:
        return
    _ensure_database_tables()
    required = {
        "tags",
        "related_entities",
        "sources",
        "reading_time",
        "risk_level",
        "audience",
        "status",
        "is_hot",
        "updated_at",
    }
    try:
        def _columns() -> set[str]:
            inspector = inspect(db.get_bind())
            return {c["name"] for c in inspector.get_columns("articles")}

        cols = _columns()
        alters = []
        if "tags" not in cols:
            alters.append("ADD COLUMN tags JSON NULL")
        if "related_entities" not in cols:
            alters.append("ADD COLUMN related_entities JSON NULL")
        if "sources" not in cols:
            alters.append("ADD COLUMN sources JSON NULL")
        if "reading_time" not in cols:
            alters.append("ADD COLUMN reading_time INT NOT NULL DEFAULT 3")
        if "risk_level" not in cols:
            alters.append("ADD COLUMN risk_level VARCHAR(20) NOT NULL DEFAULT 'low'")
        if "audience" not in cols:
            alters.append("ADD COLUMN audience VARCHAR(100) NULL")
        if "status" not in cols:
            alters.append("ADD COLUMN status VARCHAR(20) NOT NULL DEFAULT 'published'")
        if "is_hot" not in cols:
            alters.append("ADD COLUMN is_hot BOOL NOT NULL DEFAULT 0")
        if "updated_at" not in cols:
            alters.append("ADD COLUMN updated_at DATETIME NULL DEFAULT CURRENT_TIMESTAMP")
        if alters:
            for alter in alters:
                db.execute(text(f"ALTER TABLE articles {alter}"))
            db.commit()

        missing = required - _columns()
        if missing:
            raise RuntimeError(f"articles table missing columns after migration: {sorted(missing)}")

        db.execute(text("UPDATE articles SET status='published' WHERE status IS NULL OR status=''"))
        db.execute(text("UPDATE articles SET reading_time=3 WHERE reading_time IS NULL OR reading_time < 1"))
        db.execute(text("UPDATE articles SET risk_level='low' WHERE risk_level IS NULL OR risk_level=''"))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[Article Migration] failed: {e}")
        raise HTTPException(status_code=503, detail="??????")
    _ARTICLE_SCHEMA_MIGRATED = True


def _json_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else [value]
        except Exception:
            return [part.strip() for part in value.split(",") if part.strip()]
    return []


NEW_COVER_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def _normalize_article_title(value: str) -> str:
    text_value = unicodedata.normalize("NFKC", str(value or "")).strip()
    return re.sub(r"\s+", "", text_value)


def _new_cover_map() -> dict[str, str]:
    if not os.path.isdir(STATIC_NEW_COVERS_DIR):
        return {}
    covers: dict[str, str] = {}
    for filename in os.listdir(STATIC_NEW_COVERS_DIR):
        stem, ext = os.path.splitext(filename)
        if ext.lower() not in NEW_COVER_EXTENSIONS:
            continue
        key = _normalize_article_title(stem)
        if key and key not in covers:
            covers[key] = filename
    return covers


def _article_new_cover_filename(article: Article, cover_map: Optional[dict[str, str]] = None) -> str:
    mapping = cover_map if cover_map is not None else _new_cover_map()
    return mapping.get(_normalize_article_title(getattr(article, "title", "")), "")


def _article_new_cover_url(article: Article, cover_map: Optional[dict[str, str]] = None) -> str:
    filename = _article_new_cover_filename(article, cover_map)
    return f"/static/new_covers/{quote(filename)}" if filename else ""


def _article_has_new_cover(article: Article, cover_map: Optional[dict[str, str]] = None) -> bool:
    return bool(_article_new_cover_filename(article, cover_map))


def _filter_articles_with_new_covers(articles: list[Article]) -> list[Article]:
    cover_map = _new_cover_map()
    return [article for article in articles if _article_has_new_cover(article, cover_map)]


def _article_tags(article: Article) -> list:
    tags = _json_list(getattr(article, "tags", None))
    if not tags:
        tags = [article.category]
        haystack = f"{article.title or ''} {article.summary or ''}"
        for token in ["睡眠", "运动", "饮食", "用药", "过敏", "慢病", "体检", "儿童", "老人"]:
            if token in haystack:
                tags.append(token)
    return list(dict.fromkeys([t for t in tags if t]))[:8]


def _article_reading_time(article: Article) -> int:
    explicit = getattr(article, "reading_time", None)
    if explicit:
        return max(1, int(explicit))
    return max(1, round(len(article.content or "") / 500))


def _optional_user_from_request(request: Request, db: Session) -> Optional[User]:
    auth = request.headers.get("Authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    try:
        payload = jwt.decode(auth.split(" ", 1)[1].strip(), SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        return db.query(User).filter(User.username == username).first() if username else None
    except JWTError:
        return None


def _favorite_ids(db: Session, user: Optional[User], article_ids: list[int]) -> set[int]:
    if not user or not article_ids:
        return set()
    rows = db.query(ArticleFavorite.article_id).filter(
        ArticleFavorite.user_id == user.id,
        ArticleFavorite.article_id.in_(article_ids),
    ).all()
    return {row[0] for row in rows}


def _serialize_article(article: Article, is_favorited: bool = False, include_content: bool = False) -> dict:
    new_cover_url = _article_new_cover_url(article)
    data = {
        "id": article.id,
        "title": article.title,
        "category": article.category,
        "summary": article.summary,
        "cover_image": new_cover_url or article.cover_image,
        "view_count": article.view_count or 0,
        "likes": article.likes or 0,
        "tags": _article_tags(article),
        "related_entities": _json_list(getattr(article, "related_entities", None)),
        "sources": _json_list(getattr(article, "sources", None)),
        "reading_time": _article_reading_time(article),
        "risk_level": getattr(article, "risk_level", None) or "low",
        "audience": getattr(article, "audience", None) or "健康科普读者",
        "status": getattr(article, "status", None) or "published",
        "is_hot": bool(getattr(article, "is_hot", False)),
        "is_favorited": is_favorited,
        "date": article.created_at.strftime("%Y-%m-%d") if article.created_at else "",
        "updated_at": article.updated_at.strftime("%Y-%m-%d") if getattr(article, "updated_at", None) else "",
    }
    if include_content:
        data["content"] = article.content
    return data


def _record_article_event(
    db: Session,
    event_type: str,
    user: Optional[User] = None,
    article_id: Optional[int] = None,
    duration_ms: Optional[int] = None,
    query: Optional[str] = None,
    meta_data: Optional[dict] = None,
):
    try:
        db.add(ArticleEvent(
            user_id=user.id if user else None,
            article_id=article_id,
            event_type=event_type[:30],
            duration_ms=duration_ms,
            query=(query or "")[:255] or None,
            meta_data=meta_data or None,
        ))
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[ArticleEvent] write failed: {e}")

@app.get("/api/articles")
def get_articles(request: Request, category: Optional[str] = None, db: Session = Depends(get_db)):
    _ensure_article_schema_migrated(db)
    user = _optional_user_from_request(request, db)
    query = db.query(Article).filter(or_(Article.status == "published", Article.status.is_(None)))
    if category and category != "all":
        query = query.filter(Article.category == category)
    articles = _filter_articles_with_new_covers(query.order_by(Article.created_at.desc()).all())
    favs = _favorite_ids(db, user, [a.id for a in articles])
    return [_serialize_article(a, a.id in favs) for a in articles]


def _admin_content_base_url() -> str:
    return os.getenv("MEDICAL_GRAPHRAG_API_BASE", "http://localhost:8026/api/v1").rstrip("/")


def _admin_health_article_asset_url(url: Optional[str]) -> str:
    if not url:
        return ""
    prefix = "/api/v1/content/assets/"
    if url.startswith(prefix):
        return f"/api/health-articles/assets/{url[len(prefix):]}"
    admin_asset_prefix = f"{_admin_content_base_url()}/content/assets/"
    if url.startswith(admin_asset_prefix):
        return f"/api/health-articles/assets/{url[len(admin_asset_prefix):]}"
    return url


def _normalize_admin_health_article(article: dict, include_content: bool = False) -> dict:
    if article and "item" in article and isinstance(article.get("item"), dict):
        article = article["item"]
    content = article.get("content") or ""
    payload = {
        "id": article.get("id"),
        "title": article.get("title") or "",
        "category": "专家科普",
        "summary": article.get("summary") or "",
        "cover_image": _admin_health_article_asset_url(article.get("cover_url")),
        "view_count": 0,
        "likes": 0,
        "tags": article.get("tags") or [],
        "related_entities": [],
        "sources": ["TrustMed Rag 管理端"],
        "reading_time": max(1, round(len(content) / 500)) if content else 3,
        "risk_level": "low",
        "audience": "健康科普读者",
        "status": article.get("status") or "published",
        "is_hot": False,
        "is_expert": True,
        "date": (article.get("published_at") or article.get("created_at") or "")[:10],
        "updated_at": (article.get("updated_at") or "")[:10],
    }
    if include_content:
        payload["content"] = content
    return payload


@app.get("/api/health-articles")
def get_admin_published_health_articles(limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0)):
    try:
        response = _requests.get(
            f"{_admin_content_base_url()}/content/public/health-articles",
            params={"limit": limit, "offset": offset},
            timeout=5,
        )
        response.raise_for_status()
        data = response.json()
        items = [_normalize_admin_health_article(item) for item in data.get("items", [])]
        return {"items": items, "total": data.get("total", len(items)), "limit": data.get("limit", limit), "offset": data.get("offset", offset)}
    except Exception as e:
        logger.warning(f"[HealthArticlesProxy] list failed: {e}")
        raise HTTPException(status_code=502, detail="???????")


@app.get("/api/health-articles/assets/{object_path:path}")
def get_admin_health_article_asset(object_path: str):
    try:
        response = _requests.get(
            f"{_admin_content_base_url()}/content/assets/{object_path}",
            timeout=5,
        )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="asset_not_found")
        response.raise_for_status()
        return Response(
            content=response.content,
            media_type=response.headers.get("content-type", "application/octet-stream"),
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[HealthArticlesProxy] asset failed: {e}")
        raise HTTPException(status_code=502, detail="???????")


@app.get("/api/health-articles/{article_id}")
def get_admin_published_health_article(article_id: int):
    try:
        response = _requests.get(
            f"{_admin_content_base_url()}/content/public/health-articles/{article_id}",
            timeout=5,
        )
        if response.status_code == 404:
            raise HTTPException(status_code=404, detail="article_not_found_or_archived")
        response.raise_for_status()
        data = response.json()
        return _normalize_admin_health_article(data, include_content=True)
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"[HealthArticlesProxy] detail failed: {e}")
        raise HTTPException(status_code=502, detail="???????")


def _search_text(value: Any) -> str:
    text_value = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"\s+", "", text_value)


def _search_terms(query: Optional[str]) -> list[str]:
    normalized = unicodedata.normalize("NFKC", (query or "").strip()).lower()
    if not normalized:
        return []
    terms: list[str] = []
    compact = re.sub(r"\s+", "", normalized)
    if len(compact) > 1:
        terms.append(compact)
    chunks = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", normalized)
    for chunk in chunks:
        if len(chunk) > 1:
            terms.append(chunk)
        if re.fullmatch(r"[\u4e00-\u9fff]+", chunk) and len(chunk) > 4:
            terms.extend(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return list(dict.fromkeys([term for term in terms if len(term) > 1]))


def _article_relevance_score(article: Article, terms: list[str]) -> int:
    if not terms:
        return 0
    title = _search_text(article.title)
    summary = _search_text(article.summary)
    content = _search_text(article.content)
    tag_text = _search_text(" ".join(_article_tags(article)))
    entity_text = _search_text(" ".join(_json_list(getattr(article, "related_entities", None))))
    score = 0
    for term in terms:
        if term in title:
            score += 120 + min(len(term) * 8, 80)
        if term in tag_text:
            score += 90
        if term in entity_text:
            score += 75
        if term in summary:
            score += 55
        if term in content:
            score += 20
    compact_query = terms[0]
    if compact_query == title:
        score += 300
    elif title.startswith(compact_query):
        score += 160
    return score


@app.get("/api/articles/search")
def search_articles(
    request: Request,
    q: Optional[str] = Query(default=None),
    category: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    entity: Optional[str] = Query(default=None),
    sort: str = Query(default="latest"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=50),
    db: Session = Depends(get_db),
):
    _ensure_article_schema_migrated(db)
    user = _optional_user_from_request(request, db)
    query = db.query(Article).filter(or_(Article.status == "published", Article.status.is_(None)))
    if category and category != "all":
        query = query.filter(Article.category == category)
    terms = _search_terms(q)
    candidates = _filter_articles_with_new_covers(query.all())
    if tag:
        candidates = [a for a in candidates if tag in _article_tags(a)]
    if entity:
        candidates = [
            a for a in candidates
            if entity in _json_list(getattr(a, "related_entities", None))
            or entity in (a.title or "")
            or entity in (a.summary or "")
        ]
    relevance_scores: dict[int, int] = {}
    if terms:
        relevance_scores = {a.id: _article_relevance_score(a, terms) for a in candidates}
        candidates = [a for a in candidates if relevance_scores.get(a.id, 0) > 0]
    if sort == "relevance" and terms:
        candidates.sort(
            key=lambda a: (
                relevance_scores.get(a.id, 0),
                (a.view_count or 0) * 2 + (a.likes or 0) * 5,
                a.created_at or datetime.min,
            ),
            reverse=True,
        )
    elif sort == "hot":
        candidates.sort(key=lambda a: ((a.view_count or 0) * 2 + (a.likes or 0) * 5), reverse=True)
    elif sort == "likes":
        candidates.sort(key=lambda a: a.likes or 0, reverse=True)
    else:
        candidates.sort(key=lambda a: a.created_at or datetime.min, reverse=True)
    total = len(candidates)
    start = (page - 1) * page_size
    page_items = candidates[start:start + page_size]
    favs = _favorite_ids(db, user, [a.id for a in page_items])
    if q:
        _record_article_event(db, "search", user=user, query=q, meta_data={"category": category, "tag": tag, "entity": entity})
    return {
        "items": [_serialize_article(a, a.id in favs) for a in page_items],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


# ==========================================
# 濠电姷鏁告慨鐑藉极閸涘﹥鍙忛柣鎴ｆ閺嬩線鏌熼梻瀵割槮缁炬儳顭烽弻锝夊箛椤掍焦鍎撻梺鎼炲妼閸婂潡寮诲☉銏╂晝闁挎繂妫涢ˇ銉х磽娴ｅ搫孝缂傚秴锕璇差吋婢跺﹣绱堕梺鍛婃处閸嬧偓闁稿鎸荤换婵嗩潩閵夈垹浜鹃柛娑欐綑閻顭跨捄铏圭伇闁伙箑鐗撳濠氬磼濮樺崬顤€婵炴挻纰嶉〃濠傜暦閺囷紕鐤€婵炴垶鐟ч崢浠嬫⒑閸︻厼鍔嬮柛銊潐缁傛帒鈽夐姀锛勫帾闂佹悶鍎滈崘鍙ョ磾婵°倗濮烽崑娑㈩敄婢舵劑鈧礁顫滈埀顒勫箖濞嗗浚鍟呮い鏃€鍨濆锕傛⒒閸屾艾鈧绮堟笟鈧獮妤€顭ㄩ崼婵嬫７闂侀潧顦弲娑氬鐠囨祴鏀介柣妯哄级閸屻劑鏌嶈閸忔稓绮堟笟鈧敐鐐测攽鐎ｎ亞鐤€婵炶揪绲介崢婊堝几瀹€鍕拻闁稿本鐟чˇ锕傛煙绾板崬浜伴柟顖氼槹缁虹晫绮欑捄銊у炊闂備礁鎼粙渚€宕㈤幆褏鏆ゅù锝嗘偠閳ь剚甯掗～婵嬫晲閸涱剙顥氬┑掳鍊楁慨鐑藉磻閻愬灚鏆滈柨鐔哄Х瀹撲線鎮楅敐搴濈按闁衡偓娴犲鐓欓梺顓ㄧ畱楠炴绱掓径鎰喚婵﹦绮幏鍛驳鐎ｎ亝顔勬繝娈垮枟閿曨偆绮婚幘缁樻櫜闁绘劖娼欑欢鐐测攽閻愭潙绗掗柟纰卞亰閿濈偛顭ㄩ崼婵堝姦濡炪倖甯掔€氼剟寮告笟鈧弻娑㈠箛闂堟稒鐏堝銈庡亜閹虫﹢寮婚弴銏犻唶婵犻潧娲ゅ▍銈夋⒑鐠囨彃鐦ㄩ柛鎾跺枛楠炲啫螖閸涱垰绁﹂梺鍓茬厛閸犳牗鎱ㄦ惔銊︹拺闁荤喐婢樺Σ缁樸亜閹存繃鍤囬柍銉畵瀹曞爼顢楅埀顒勬偂濞戙垺鐓曢悘鐐扮畽閼测晞濮冲┑鐘崇閳锋垿鏌涢敂璇插箹闁告柨顑夐弻娑㈠Ω瑜庨弳顒勬煕閳规儳浜炬俊鐐€栫敮濠囨倿閿曞倸纾归柟閭﹀枓閸嬫挸鈻撻崹顔界彯闂佺顑呴敃銉︾┍婵犲洤閱囬柡鍥╁仜閼板灝鈹戦悙鍙夘棡闁糕晛鐗撳畷顖涙償閵婏腹鎷洪柣鐘叉处瑜板啴顢楅姀銏㈢＜閻庯綆鍋勫ù顕€鏌嶉妷顖滅暤鐎规洖鐖奸、妤佹媴缁嬪灝顥楁繝鐢靛Х閺佸憡鎱ㄩ幘顔肩柈闁规鍠氭稉宥呂旈敐鍛殲闁稿﹦鏁婚弻锝夊閳藉棗鏅遍梺缁樺浮缁犳牠寮诲☉妯滅喐绗熼姘啀闂備線鈧偛鑻晶顖涖亜閺冣偓閻楃姴鐣烽幎绛嬫晪闁逞屽墮閻ｇ兘骞嬮悙鐢电槇闂佸憡鍓崨顖滄毎闂傚倷绀侀崥瀣箖閸屾凹鐒界憸蹇撯枎閵忋倖鍊锋繛鏉戭儐閺傗偓闂備胶绮摫鐟滄澘鍟撮、鏃堝箻椤旂晫鍘卞┑顔斤供閸撴岸宕甸崶顒佺厸鐎光偓鐎ｎ剛蓱闂佽鍨卞Λ鍐极閹版澘骞㈡俊銈勮兌閺嗘岸姊绘担绛嬪殭閻庢稈鏅犻幆宀勵敋閳ь剙鐣烽敓鐘茬闁芥ê锛夐妷鈺傜厱鐎光偓閳ь剟宕戦悙鍝勭柧婵犻潧顑嗛悡鍐喐濠婂牆绀堟繝闈涱焾娴滅懓霉閿濆懎顥忔繛鎾愁煼閺屾洟宕煎┑鍥舵！缂佹儳澧介弫濠氬蓟濞戞埃鍋撻敐搴′簼閻忓繒澧楅妵鍕Ω閿濆懎濮﹂梺璇″枟閻熝囧焵椤掑倹鏆╂い顓炵墦閸╂稓浠︽潪鎸庢閹晠妫冨☉妤佸媰闂備焦瀵уú蹇涘垂娴犲违濞达絿纭堕弸搴ㄦ煙閻愵剚缍戞繛鍫㈠枛濮婃椽妫冨☉杈€嗛梻鍌氬鐎氫即銆佸▎鎺旂杸婵炴垶鐟㈤幏濠氭⒑缁嬫寧婀伴柣鐔濆懐鐜婚柡鍐ｅ亾缂佺粯绋掗幏鍛村礃閹绘帗鍋愬┑鐐村灦椤倿寮崼婵嗙獩濡炪倖鎸炬刊瀵告椤曗偓濮婄粯鎷呴崫銉ㄥ┑鈽嗗亜濞尖€愁潖閽樺鍚嬪璺猴功閸旓箑顪冮妶鍡楀潑闁稿鎹囬弻鐔煎礃閼碱剛顔婄紓鍌氬€归幑鍥箖閹呮殕闁逞屽墴閹苯鈻庨幘瀵稿幈閻熸粌閰ｉ妴鍐川閺夋垶鐎柣鐔哥懃鐎氼亞鎹㈤崱娑欑厽闁规澘鍚€缁ㄥ鏌嶈閸撴岸鎮у鍫濇瀬妞ゆ洍鍋撴鐐村笒铻栭柍褜鍓涙竟鏇㈡偡閹佃櫕鏂€闂佺粯蓱閸撴岸宕甸悢鍏肩厱闁靛鍠栨晶濠氭煛娴ｅ壊鍎旈柡灞界Х椤т線鏌涢幘瀵告噰闁?LLM 濠电姷鏁告慨鐑藉极閸涘﹥鍙忛柣鎴ｆ閺嬩線鏌熼梻瀵割槮缁惧墽绮换娑㈠箣濞嗗繒鍔撮梺杞扮椤戝棝濡甸崟顖氱閻犺櫣鍎ら悗楣冩⒑閸涘﹦鎳冪紒缁橈耿楠炲啫螖閳ь剟鍩ユ径濞炬瀻婵炲棗娴锋潻鏃€绻濋悽闈涗粶闁绘鎳愰崚鎺戔枎閹惧疇鎽曢梺缁樻煥閸氬鍩涢幒妤佸€甸梻鍫熺☉椤ュ繑淇婂顔兼珝婵﹤鎼叅閻犲洦褰冪粻鍝勵渻閵堝啫濡奸柨鏇ㄤ邯楠炲啴鏁撻悪鈧弫鍐煥閺囨浜鹃梺缁樺姇閿曨亪寮婚弴鐔虹鐟滃宕戦幘鏂ユ斀妞ゆ柨鍚嬮崰妯绘叏婵犲懏顏犵紒杈ㄥ笒铻ｉ悹鍥皺椤ｆ煡姊绘担渚劸闁挎洏鍊曢…鍥晸閻樿尙鐣洪梺璺ㄥ枔婵參寮崘顔界叆闁哄洦顨呮禍楣冩⒑缂佹ê濮囬柨鏇ㄤ邯瀵寮撮悢铏圭槇闂婎偄娲﹂懝楣冨箖閸涘瓨鈷戦柣鐔告緲濡茶崵绱撳鍜冨伐妞ゎ偄绻橀幖鍦喆閸曨偆褰撮梻浣告贡缁垳鏁悙鍙傦絾瀵肩€涙ǚ鎷洪梺鍛婄缚閸庤鲸鐗庢繝娈垮枟鑿ч柛鏂胯嫰瀹撳嫰姊洪崷顓烆暭婵犮垺顭囩划濠氭惞椤愶紕绠氶梺闈涚墕閸婂憡绂嶉崜褏纾藉〒姘搐娴滄粎绱掓径濠勭Ш鐎殿喛顕ч埥澶婎潩椤愶絽濯扮紓鍌氬€烽梽宥夊垂閻熼澏鎺楀焵椤掑嫭鈷掑ù锝堝Г绾爼鏌涢悩铏鞍闁逛究鍔戦幃浠嬪川婵犲倷绨甸梻浣虹帛椤洨鍒掗鐐村亗闁哄洨鍠嗘禍婊勩亜閹捐泛鏋庨柣蹇嬪劚闇夋繝濠傚閻帡鏌″畝瀣К缂佺姵鐩獮姗€鎳滈崹顐㈡灈濠碉紕鍋戦崐鎴﹀礉婵犲洤纾块柣銏㈩焾缁犳牗绻涢崱妯诲碍缂佺嫏鍥ㄧ厵閻庢稒顭囩粻銉︾箾閸忚偐顣插ǎ鍥э躬婵″爼宕堕‖顔哄劦閺屾稓鈧綆鍋嗗ú瀛樸亜閵忥紕澧€垫澘瀚伴獮鍥敆娴ｈ　鍋撻鍕拺缂佸娉曢悘閬嶆煕鐎ｎ剙浠辩€规洘鍨块崹鎯ь熆閸曨剛鈯曢柨娑欏姈閹峰懏绗熼娴舵粍淇婇悙顏勨偓鎴﹀磿閺屻儲鍤屽Δ锝呭暙閻撴﹢鏌熸潏楣冩闁稿鍔栭妵鍕籍閸パ傛睏婵炲鍘уú銈夊煘閹达附鍊风€瑰壊鍠楁晥闂備胶鍎甸弲鈺呭垂閸洖违濞撴埃鍋撶€殿喕绮欓、姗€鎮欓懠鍨涘亾閸喒鏀介柍钘夋閻忥綁鏌嶅畡鎵闂囧鏌涢弴銊ヤ簮闁衡偓閼恒儯浜滈柡鍌涱儥濞肩喎霉閻樺磭鐭掗柡灞剧〒閳ь剨缍嗛崑鍛暦瀹€鍕厸閻忕偟纭堕崑鎾崇暦閸ャ劍鐣烽梺璇插嚱缂嶅棝宕滃☉婧惧徍闂傚倸鍊烽懗鍓佹兜閸洖鐤炬い蹇撴瀹曞弶绻濋棃娑欘棤闁哄棴闄勯幈銊ヮ渻鐠囪弓澹曢柣搴ゎ潐濞叉鍒掕箛娴板洭骞撻幒鍡樻杸濡炪倖姊婚鏇㈠磻閵忊剝鍙忓┑鐘插鐢盯鏌熷畡鐗堝殗鐎规洏鍔嶇换婵嬪磼濞戞瑧鏆繝纰夌磿閸嬫垿宕愰弴鐘冲床闁硅揪绠戠粻鏍煕鐏炵偓鐨戦柡鍡畵閺屾盯顢曢敐鍡欘槬闁诡垳鍠栧娲濞戣鲸肖闂佺閰ｆ禍鎯版＂?5 缂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁惧墽鎳撻—鍐偓锝庝簻椤掋垹鈹戦姘ュ仮闁哄矉绱曟禒锔炬嫚閹绘帒顫撻梻浣虹帛鐢帡鎮樺璺何﹂柛鏇ㄥ灠缁犳娊鏌熺€涙绠栨繛鍫熺叀濮婅櫣绱掑Ο鑲╊吋婵炲瓨绮犳禍顏勵嚕鐠囨祴妲堟慨姗嗗亝閸曞啴鏌ｉ悩鍙夊鐟滄澘鍟撮敐鐐哄炊椤掍讲鎷?# ==========================================
# 闂傚倸鍊搁崐鎼佸磹閹间礁纾圭€瑰嫭鍣磋ぐ鎺戠倞妞ゆ帒顦伴弲顏堟偡濠婂啴鍙勯柕鍡楀暣婵＄柉顦撮柣顓熺懇閺屾盯寮婚婊冣偓鎰板磻閹捐閿ゆ俊銈勮閹峰搫顪冮妶鍡楀潑闁稿鎸剧槐鎺撳緞婵犲骸娈舵繝銏ｎ潐濞叉鎹㈠┑鍡╂僵妞ゆ挾濮撮獮妤呮⒒娴ｅ摜绉洪柛瀣躬瀹曟粌顫濈捄铏瑰幒闂佸搫娲ㄩ崰鎾剁不妤ｅ啯鐓欓柣鎰靛墮婢ь垶鏌ｉ幘鐐藉仮闁哄矉绱曟禒锕傚礈瑜夊Σ鍫ユ⒑娴兼瑧绋荤紒璇茬墦瀹曟椽鍩€椤掍降浜滈柟鐑樺灥椤忊晝鐥幆褜鐓奸柡宀嬬秮楠炲洭顢楁担鐟板壍缂備胶鍋撻崕鍐诧耿鏉堚晜顫曢柟鎯х摠婵潙螖閻橆喖濡界紒顔肩Ф缁顓奸崪浣哄弳闂佸壊鍋嗛崰鎾诲储閻㈠憡鈷戠痪顓炴媼濞兼劙鏌涢弮鈧悧婊冣槈閻㈢绀堢憸澶愬磻閹炬枼妲堟繛鍡樺灩閻ｈ櫣绱撴担铏瑰笡缂佸鎹囬崺鈧い鎺戝€归弳鈺傘亜椤撶偟澧︽い銏℃尭閻ｆ繈宕熼鍌氬箞闂備線娼ч…鍫ュ礉瀹ュ洦鍏滈柣鎰靛厵娴滄粓鏌￠崶鏈电敖缂佸鍠氶埀顒冾潐濞叉牠濡剁粙娆惧殨闁圭虎鍠楅崐鐑芥⒒閸喓鈽夌紒鐘靛缁绘繈鎮介棃娑楀摋闂佽妞挎禍鐐差嚗婵犲洤绠查柟鎵虫櫃濮规姊洪崨濠傚闁哄懏绻堥妴鍛村箵閹广劍妫冮弫鎰板川椤撶喐顔夐梻浣虹帛閹搁箖宕伴弽顓炶摕闁跨喓濮寸粈鍐煏婵炲灝鍔楅柛瀣崌瀹曨偊濡烽敂鑺ヮ唶闂備胶鍘ч幗婊堝极閸濄儳涓嶅Δ锝呭暞閻撴洘銇勯幇鈺佲偓鏇㈠几閺冨倻纾奸柣姗€娼ф禒锕傛煙娓氬灝濡界紒缁樼箞瀹曟﹢鍩炴径姝屾濠德板€楁慨鐑藉磻閻愬搫绀夐柡宥庡幖缁犳岸鏌￠崘銊у闁哄懏鐓￠弻娑氫沪閹冩瘓濠电偛鍚嬮幑鍥ь潖婵犳艾纾兼繛鍡樺笒閸橈繝姊虹粙鎸庢崳闁轰浇顕ч悾宄邦煥閸愶絾鏂€闂佺硶鈧磭绠查柡浣哄█濮婅櫣绮欓崠鈩冾暭闂佸壊鍋嗙亸鐒?闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛銈呭閺屾盯顢曢敐鍡欙紭闂侀€炲苯鍘搁柣鎺炲閹广垹鈹戠€ｎ亞鍊為悷婊冮鍗辨い鎺戝閳锋垿鏌涘┑鍡楊伀闁宠顦甸弻娑樜熼崹顔绘睏缂備緡鍠栭…鐑藉箖瑜斿畷濂告偄瀹勯绱熷┑鐘垫暩婵炩偓婵炰匠鍏炬稑顭ㄩ崼顒傜◤闂佹寧娲嶉崑鎾绘?-> LLM 缂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁惧墽鎳撻—鍐偓锝庝簼閹癸綁鏌ｉ鐐搭棞闁靛棙甯掗～婵嬫晲閸涱剙顥氶梻浣藉Г钃辩紒璇插€垮﹢渚€姊虹粙璺ㄧ闁告艾顑囩槐鐐哄箣閿旂晫鍘遍梺闈涱焾閸庨亶鍩€椤掆偓濞硷繝鐛崘顏呭枂闁告洍鏅涙禍浼存煟閻斿爼妾烽柛濠冨姍瀹曟垿骞樼拠鑼姦濡炪倖甯掔€氼參鎮″☉銏″€甸柨婵嗙凹缁ㄤ粙寮介敓鐘斥拺闂侇偅绋撻埞鎺楁煕閺傝法鐒搁柍銉︽瀹曟﹢顢旈崨顓犲酱闂傚倸顭崑鎺楀储婵傛潌澶婎潩閼哥鎷洪柣鐘叉处瑜板啴顢楅姀掳浜滈柡鍐ｅ亾闁绘濮撮悾閿嬪閺夋垹顔掗柣鐘叉穿鐏忔瑩藝椤曗偓濮婅櫣娑甸崨顔兼锭缂備胶濮甸崹鍦垝婵犳艾鍐€妞ゆ挾鍠撻崢浠嬫⒑绾懏褰х紒鏌ョ畺钘熷璺虹灱绾惧ジ鏌ｅ▎鎰噧婵炶绠撻幃鈥斥枎閹寸姷锛滈柣搴秵娴滅偞绂掗姀掳浜滈柟鍝勵儏閻忣亪鏌?-> 濠电姷鏁告慨鐑藉极閸涘﹥鍙忛柣鎴ｆ閺嬩線鏌涘☉姗堟敾闁告瑥绻愰湁闁稿繐鍚嬬紞鎴︽煕閵娿儱鈧潡寮婚敐澶婄鐎规洖娲ら崫娲⒑閸濆嫷鍎愰柣妤侇殘閹广垹鈽夐姀鐘殿吅闂佺粯鍔曢顓炩枔閵堝鈷戞繛鑼额嚙楠炴鏌熼悷鐗堝枠鐎殿喛顕ч埥澶愬閳哄倹娅囬梻浣瑰缁诲倸螞濞戔懞鍥Ψ閳哄倵鎷洪梺鍛婄箓鐎氱兘宕曟惔锝囩＜闁兼悂娼ч崫铏光偓娈垮枛椤嘲顕ｉ幘顔碱潊闁绘顕ч弫瑙勭節閻㈤潧孝闁诲繑宀稿畷婵嬪冀椤愩倖鍣梻鍌氬€峰ù鍥敋瑜忛埀顒佺▓閺呯娀骞婂┑鍥ュ亝闁稿浚鍨板ú锔锯偓闈涖偢瀵爼骞婄粵鍦暤闁哄本鐩鎾Ω閵壯傚摋缂傚倷鑳舵慨鍨箾婵犲洤钃熸繛鎴欏灩閻掓椽鏌涢幇鍓佺窗婵炲矈浜濈换婵嬪閿濆孩缍堝┑鐐插级椤洭骞戦姀鐘斀閻庯綆鍋勬禒娲⒒閸屾氨澧涢柛鎺嗗亾濠电偞鍨崹娲偂閺囥垺鍊甸柨婵嗛娴滄繄鈧娲栭惌鍌炲蓟閳ュ磭鏆嗛柍褜鍓熷畷浼村箻閼告娼熼梺鍦劋椤ㄥ懘锝為崨瀛樼厽婵☆垵娅ｉ敍宥吤瑰搴濈凹缂佺粯绻勯崰濠偽熷ú缁樼秹闂備胶顭堥鍛村箠濮椻偓瀵偊宕橀纰辨綂闂侀潧鐗嗛幊鎾诲箺閺囩偐鏀介柣鎰綑閻忕喖鏌涢妸褎鍤€闁轰緡鍣ｅ缁樼瑹閳ь剙顭囪閹广垽宕卞☉妯碱槶濠电娀娼ч鍡涘磻閻斿吋鐓涚€广儱楠搁獮妤呮煕鐎Ｑ冨⒉缂佺粯绻冪换婵嬪磼濮橀棿鐥紓鍌欐祰濞夋稑顕ｉ崜浣瑰床婵炴垶纰嶉崗婊冾渻鐎ｎ亝鎹ｉ柣锕€鐗撻幃妤€鈻撻崹顔界仌濡炪倖娉﹂崶褏鍙€婵犮垼鍩栭崝鏇綖閸涘瓨鐓熸俊顖濆吹婢э箓鏌涢弬鍧楀弰妤犵偛顦辩划娆忊枎閹勫€梻浣虹《閸撴繈鎮烽姣椽宕ㄦ繝浣虹畾闂侀潧鐗嗙换鎺楀礆娴煎瓨鐓曢柣鏇氱閻忥箓鏌熼鍡欑瘈闁诡喓鍨介獮鏍倷閹殿喚娉块梻鍌欐祰椤顭垮Ο缁樻珷閹兼番鍔岀粻鏍р攽閸屾碍鍟為柣鎾跺枑閵囧嫰骞樼捄杞板摋闁诲孩鐭崡鍐参涙担鐟扮窞閻庯綆鍓涢惁鍫熺節閻㈤潧孝闁稿﹥鎮傚鎶芥倷閻戞鍘遍梺缁樻煥閹碱偅绂掕缁辨帗娼忛妸銉х懆闁剧粯鐗犻弻娑㈠箛閳轰礁顬夐梺璇茬箰閻楁挸顫忛搹鐟板闁哄洨鍠愬鎺楁⒑缁嬫鍎愰柟鍛婃倐閳ユ棃宕橀鍢壯囨煕閳╁喚娈旀繛鍏煎灴濮婅櫣绮欏▎鎯у壉闂佸湱顭堥…閿嬩繆閻㈢绀嬫い鏍电稻閺咃綁姊虹紒妯哄婵炲吋鐟╄棟闁冲搫鎳忛悡鐔煎箹濞ｎ剙濡芥繛鎳峰洦鐓忛柛顐犲焺閻掔偓銇勯弴顏嗙М鐎规洖銈稿鎾倷閻㈠灚鐎?# 婵犵數濮烽弫鍛婃叏閻戣棄鏋侀柛娑橈攻閸欏繘鏌ｉ幋锝嗩棄闁哄绶氶弻鐔兼⒒鐎靛壊妲紒鎯у⒔缁垳鎹㈠☉銏犵闁哄啠鍋撻柛銈呯Ч閺屾盯濡烽鐓庘拻闂佽桨绀佸ú顓㈠蓟閺囷紕鐤€闁哄洨鍊妷锔轰簻闁挎棁顕у▍宥夋煙椤旂瓔娈滈柣娑卞櫍瀹曞綊顢欓悡搴經濠碉紕鍋戦崐褏鈧潧鐭傚畷鐟扮暦閸パ冪亰闂佸壊鍋侀崕閬嶆煁閸ャ劎绡€闁靛骏绱曠粻鎾剁磽瀹ュ拑韬鐐插暣閸╁嫰宕橀埡浣稿Τ闂備焦瀵х换鍌毼涘▎鎾崇獥闁哄稁鍘介埛鎺懨归敐鍛暈闁诡垰鐗撻弻鐔风暋闁箑鍓堕悗瑙勬礃缁矂锝炲┑鍫熷磯闁惧繐婀遍弶浠嬫⒒娓氣偓濞佳団€﹂崼銉ョ？婵炲棙鎸婚崑鍕煟閹寸伝顏堟偟娴煎瓨鈷戦柛锔诲幘鐢盯鎮介妤佹珔妞ゎ厼娲︾粋鎺斺偓锝庡亐閹风粯绻涙潏鍓хК妞ゎ偄顦靛畷鎴︽偐缂佹鍘辨繝鐢靛Т閸燁垳绮堢€ｎ喗瀵犳繝闈涱儐閻撶喖鏌曡箛瀣労闁绘帒澧界槐鎺楁偐瀹曞洤鈷岄梺璇″枛閸㈡煡鍩㈡惔銈囩杸闁瑰灝鍟╃槐鎴︽⒒娴ｈ棄鍚归柛鐘虫礋閿濈偞寰勯幇顑╋箓鏌涢弴銊ョ仩闁告劏鍋撻梻渚€娼ч…顓熶繆閸パ€鏋旈柕鍫濇缁犻箖鎮楅悽娈跨劸妞ゅ骸鐭傞弻娑㈠Ω閵婏妇銆愬銈庡亜缁绘﹢骞冨鍏剧喓鎷犲顔借拫闂傚倷鑳剁划顖炪€冮崨瀛樺亱闊洦绋戝洿闂佽顔栭崰姘卞閽樺鈧帒顫濋浣规倷闂佸搫顑嗙粙鎴︹€﹂懗顖ｆЬ闂佸搫鎷嬮崑濠傜暦濮樿泛绠抽柡鍐ｅ亾閻庢碍纰嶇换娑㈠级閹搭厼鍓卞銈庡亜缁夌懓顫忓ú顏咁棃婵炴垶姘ㄩ濠冪節濞堝灝鏋涙い鏇ㄥ幘閸掓帡鏁愰崨顏咁潔濠碘槅鍨堕弨閬嶅棘閳ь剟姊绘担铏瑰笡闁搞劑娼х叅闁靛ě鍛厠闂佹眹鍨归幉锟犳偂閺囥垻鍙撻柛銉ｅ妽椤ユ牠鏌ら幏灞剧《闁逞屽墲椤煤濮椻偓瀹曞綊宕稿Δ鍐ㄧウ濠殿喗銇涢崑鎾垛偓瑙勬礈閸忔﹢銆佸鈧幃鈺呮惞椤愩倕绔煎┑鐘垫暩婵兘寮幖浣哥；闁绘劕鎼崹鍌炴煙椤栨粌顣奸柟鍐茬焸閺?/api/articles/{article_id} 濠电姷鏁告慨鐑藉极閸涘﹥鍙忛柣鎴ｆ閺嬩線鏌熼梻瀵割槮缁炬儳顭烽弻锝夊箛椤掍焦鍎撻梺鎼炲妼閸婂潡寮诲☉銏╂晝闁挎繂妫涢ˇ銉╂⒑閽樺鏆熼柛鐘崇墵瀵寮撮悢铏诡啎闂佺粯鍔﹂崜姘舵偟閿曞倹鈷戦弶鐐村椤︼箓鎮楀顐㈠祮鐎殿喛顕ч埥澶娢熼柨瀣垫綌闂備礁鎲￠〃鍫ュ磻閻愮儤鍊剁€广儱鎳夐弨浠嬫煟閹邦剙绾ч柛锝堟閳规垿鎮欓埡浣峰闂傚倷绀侀幖顐︽儗婢跺苯绶ら柛濠勫枔娴滀粙姊绘担鍝勫付妞ゎ偅娲熷畷鎰板箣閿曗偓绾惧鏌ｉ幇顔煎妺闁抽攱鍨块弻娑樷攽閸℃浼岄梺绋垮閸旀瑩寮婚敐鍛傛棃宕橀妸銏＄€伴柣搴㈩問閸犳牠鈥﹀畡閭﹀殨闁圭虎鍠楅崑鍕煣韫囨凹鍤冮柛鐔烽叄濮婄粯鎷呯粙娆炬闂佺粯鎸搁悧鎾崇暦娴兼潙鍐€闁靛绠戝鍧楁煙閸忚偐鏆橀柛鏂跨Ч閸╂盯骞嬮敂鐣屽幈濠电娀娼уΛ妤咁敂閳哄懏鐓冪憸婊堝礈濞戙垹鏋侀悹鍥皺閺嗭箓鏌熸潏鍓х暠缂佺姾宕电槐鎾存媴妤犮劍宀搁獮?# ==========================================
import time as _time
import random as _random
import requests as _requests
from datetime import datetime as _dt
from datetime import date, timedelta
# 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾剧懓顪冪€ｎ亝鎹ｉ柣顓炴閵嗘帒顫濋敐鍛婵°倗濮烽崑娑⑺囬悽绋挎瀬闁瑰墽绮崑鎰版煠绾板崬澧绘俊鑼厴濮婄粯鎷呴崨濠冨創濠电偛鐪伴崹钘夌暦瑜版帒鎹舵い鎾跺С缁楀姊虹紒姗嗙劸妞ゆ泦鍛笉閻熸瑥瀚粻楣冩煥濠靛棝顎楀褜鍠栭埞鎴﹀灳閼碱剛鐓撻梺鍝勬湰缁嬫捇鍩€椤掑﹦绁烽柛鏂跨焸閸┾偓妞ゆ帊鑳剁粻鐐碘偓娈垮枛椤嘲顕ｉ幘顔藉亜闁惧繗顕栭崯搴ㄦ煟閻斿摜鐭婄紒澶婄秺閻涱喗绻濋崶褏鐤€闂佺粯顨呴悧濠傗枍閺嵮€鏀介柣妯肩帛濞懷勩亜閹存繃顥㈤柛鈺傜洴楠炲鏁傞挊澶夌敾婵犵數鍋涘Λ妤冩崲閹伴偊鏁傞柍鍝勬噺閻撴洟鏌￠崒婵愬殭闁逞屽墯椤ㄥ﹪骞嗛埀顒併亜韫囨挻顥犵痪鎯с偢閺屾洝绠涢弴鐐愩垻绱掗銏⑿㈤摶鏍煟濮椻偓濞佳勭閿斿浜滈柕濞垮劵闊剚顨ラ悙鎻掓殻闁糕晪绻濆畷姗€顢旈崱顓犲簥濠电姷顣藉Σ鍛村垂閹惰棄鍌ㄧ憸宥夘敋?0 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾剧懓顪冪€ｎ亝鎹ｉ柣顓炴閵嗘帒顫濋敐鍛婵°倗濮烽崑娑⑺囬悽绋挎瀬鐎广儱顦粈瀣亜閹哄秶鍔嶆い鏂挎喘濮婄粯鎷呴崨濠呯闂佺绨洪崐婵嗙暦閻㈠憡鏅濋柍褜鍓熼崺銉﹀緞婵炪垻鍠栭弻銊р偓锝庡亝濠㈡垿姊绘担绛嬪殭闁告垹鏅槐鐐哄幢濡⒈娲搁梺缁樺姃鐠€锕傚极?TTL闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛濠傛健閺屻劑寮崼鐔告闂佺顑嗛幐鍓у垝椤撶偐妲堟俊顖濐嚙濞呇囨⒑濞茶骞楅柣鐔叉櫊瀵鎮㈤崨濠勭Ф婵°倧绲介崯顖烆敁瀹ュ鈷戠紒瀣仢椤掋垺銇勯妸銉含鐎殿喛顕ч埥澶婎潩椤愶絽濯伴梻浣告啞閹稿棝鍩€椤掆偓鍗遍柛顐ｇ箥濞撳鏌曢崼婵囶棡缂佲偓鐎ｎ€㈢懓顭ㄩ崘顏勭厽閻庢鍠涢褔顢橀崗鐓庣窞濠电姳鑳堕悙濠囨⒒娴ｅ憡鍟炴繛璇х畵瀹曟粌鈻庤箛濠冪€洪梺鎸庣箓濞层劎澹曢挊澹濆綊鏁愰崱妤冪シ婵炲瓨绮撶粻鏍ь潖閾忓湱鐭欓柟绋垮閹疯鲸绻濋姀锝庢綈婵炶尙鍠庨悾鐑藉箣閿曗偓缁犲鏌ゆ慨鎰偓鏇犵矈閿曞倹鈷戦柛鎾瑰皺閸樻盯鏌涢悩宕囧闁逛究鍔嶉幆鏃堝煢閳ь剟寮ㄦ禒瀣厽闁逛即鍋婇弶娲煕閵堝拋鍎戠紒杈ㄥ浮閹晠鎼归銏㈩暡婵＄偑鍊栧ú鈺冨緤閸ф鐒垫い鎺嗗亾婵犫偓鏉堛劍鍙忛柣鎴ｅГ閸嬵亪鏌嶈閸撴瑩鍩為幋锔藉€烽柡澶嬪灩娴犳悂姊洪懡銈呮殌闁搞儜鍐ㄤ憾闂備焦鐪归崹褰掑箟閿熺姴鐓曢柟杈鹃檮閻撴洘绻濋棃娑欘棞妞ゅ繐鐡ㄦ穱濠囧箵閹烘梻楔闂佸搫鏈惄顖涗繆閹间礁鐓涢柛灞剧洴閺侇亪姊洪崨濠庢疁濞存粌鐖煎濠氬Χ閸℃ê寮块梺褰掑亰閸忔﹢宕戦幘璇茬疀闁哄娉曢敍娑㈡⒑閸︻厼浜鹃柡瀣偢瀵悂寮崼鐔哄幈濡炪値鍘介崹鐢稿几閻斿吋鐓熼煫鍥ㄦ⒐缁€鍐磼缂佹绠撻柍缁樻崌瀹曞綊顢欓悾灞肩按闂傚倷鑳堕…鍫ユ晝閵夈儍娲偄缁楄　鍋撴担鍓叉僵閻犺桨缍嶉妸鈺傜厓閺夌偞濯介崗灞俱亜閿旇姤绶叉い顏勫暣婵″爼宕卞Δ鈧闂備胶顭堥鍛偓姘嵆閻涱噣宕橀鑲╊吅闂佹寧妫佽闁归攱妞藉娲川婵犲嫧妲堥梺鎸庢穿缁插灝鈻庨姀銈嗙劶鐎广儱妫涢崢鍗炩攽椤斿浠滈柛瀣尵缁辨帡鎮╁畷鍥р吂闂佷紮绲块崗妯绘叏閳ь剟鏌曢崼婵囶棤闁告柨鐖奸幃妤呯嵁閸喖濮庡┑鈽嗗亜閻倿鐛箛鎾佹椽顢旈崨顏呭闂備礁鎲＄粙鎴︽晝閿斿墽涓嶉柟鍓х帛閸婂灚鎱ㄥΟ鐓庡付濠⒀勬尦閺屾盯鍩為幆褌澹曞┑锛勫亼閸婃牜鏁幒妤€绐楁慨姗嗗墻閻掍粙鏌熼幍顔碱暭闁绘挻娲熼弻鐔兼倻濡櫣鍘愰梻濠庡墻閸撴盯鍩€椤掑喚娼愭繛鍙夛耿瀹曟繂鈻庨幘宕囩暫闂侀潧鐗嗗Λ娆愬垔閹绢喗鍋℃繛鍡楃箰椤忣偆绱撻崼婵愮吋婵﹤顭峰畷鎺戭潩椤戣棄浜鹃柟闂寸绾剧懓顪冪€ｎ亝鎹ｉ柣顓炴閵嗘帒顫濋敐鍛婵°倗濮烽崑娑⑺囬悽绋挎瀬闁瑰墽绮崑鎰版煕閹邦垰绱﹂柣銏狀煼濮婄粯鎷呴悷閭﹀殝濠电偛寮堕悧鐘茬暦閹版澘绠瑰ù锝囶焾閻庮參姊虹粙璺ㄧ伇闁稿鍋ゅ畷鎴︽晲婢跺﹦楠囬梺缁樺姈濞兼瑩藟鐎ｎ剛纾煎璺猴攻閸嬨儵鏌＄仦璇插闁宠鍨垮畷閬嶅煛閸屽偊绠撳铏圭磼濡櫣鐟ㄩ梺璇茬箲瀹€鍛婁繆閻㈢绀嬫い鏍ㄦ皑椤斿﹪姊洪悷鎵憼缂佽绉电粋鎺撱偅閸愨斁鎷虹紓浣割儐椤戞瑩宕曞鍛＝鐎广儱瀚粣鏃傗偓娈垮枛椤兘骞冮姀銈呯闁兼祴鏅涙慨娲⒒娴ｇ懓顕滄繛鎻掔Ч瀹曟垿骞樼紒妯煎幐闁诲函缍嗛崜娆撳几濞戙垺鐓涚€光偓鐎ｎ剛鐦堥悗瑙勬礉缁墽绮诲☉銏犵睄闁逞屽墴閹敻寮撮悩鐢碉紳?LLM 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾剧懓顪冪€ｎ亝鎹ｉ柣顓炴閵嗘帒顫濋敐鍛婵°倗濮烽崑娑⑺囬悽绋挎瀬鐎广儱顦粈瀣亜閹扳晛鐏ù婊€鍗抽弻锝嗘償閳ュ啿杈呴梺绋款儐閹瑰洭寮诲☉銏犲嵆闁靛鍎遍～鈺佲攽閻愯尙澧崇紒韫矙閳ユ棃宕橀鍢壯囨煕閳╁啰鎳冩い顐庡喚娓婚柕鍫濇绾炬悂鏌涢妸銈囩煓妤犵偛鍟撮幃婊堟嚍閵壯冨Ш闂備焦瀵уΛ浣肝涢崟顓犵＝闁瑰墽绮埛鎺戙€掑锝呬壕闂侀€炲苯澧伴柛瀣洴閹崇喖顢涘☉娆愮彿濡炪倖娲嶉崑鎾绘煛瀹€瀣М闁轰焦鍔欏畷鎯邦槻妤犵偛顑呰灃闁绘﹢娼ф禒锕傛煥閺囨ê鐏╅柣锝囧厴閹粎绮电€ｎ偅娅嶉梻浣虹帛椤牆鈻嶉弴鐘愁偨妞ゆ劧闄勯埛鎺楁煕鐏炲墽鎳呮い锔肩畵閺岀喓鍠婇崡鐐扮盎闁绘挶鍊濋弻鏇熺箾閻愵剚鐝旂紓浣瑰姈椤ㄥ﹪寮婚悢鍏肩劷闁挎洍鍋撻柛妯绘尦閺岋紕鈧綆浜炴晥闂佸搫鏈ú婵堢不濞戞埃鍋撻敐搴濈按闁稿鎹囧浠嬵敇閻愰潧骞堥梻浣侯攰閹活亪姊介崟顖涘亗闁绘柨鍚嬮悡鐔兼煛閸愩劌浜為柣鎺斿亾閵囧嫰濡烽妷顔煎壎闂佸搫鐬奸崰鏍箖閸撗傛勃閻熸瑱绲鹃悗浼存⒒娴ｅ憡鎯堥柡鍫墴閹嫰顢涘鐓庢濡炪倖娲嶉崑鎾绘煕閳规儳浜炬俊鐐€栫敮鎺斺偓姘煎墴瀹曞綊宕掗悙瀵稿幈閻熸粌閰ｉ妴鍐川閺夋垵寮烽梺闈涱槴閺呮粓鎮?_LIVE_CACHE = {"articles": None, "cached_at": None}
_LIVE_CACHE = {"articles": None, "cached_at": None, "covers_pending": False}
_LIVE_CACHE_TTL_SEC = 1800
async def _build_live_article(raw: dict, idx: int) -> dict:
    raw_title = (raw.get("title") or "????").strip()
    raw_content = (raw.get("content") or raw.get("summary") or "").strip()
    raw_url = (raw.get("url") or "").strip()
    local_cover = _local_live_cover_url(idx)

    summary = raw_content[:180] if raw_content else raw_title
    content = raw_content or summary
    tags = raw.get("tags") if isinstance(raw.get("tags"), list) else []
    if not tags:
        tags = ["????"]

    return {
        "id": f"live-{idx}-{abs(hash(raw_url or raw_title)) % 100000}",
        "title": raw_title[:80],
        "summary": summary[:220],
        "content": content,
        "category": raw.get("category") or "????",
        "tags": list(dict.fromkeys([str(t) for t in tags if t]))[:6],
        "cover": local_cover,
        "cover_image": local_cover,
        "source_url": raw_url,
        "source": raw.get("source") or "??????",
        "published_at": raw.get("published_at") or _dt.utcnow().isoformat(),
        "is_live": True,
    }


async def _background_generate_covers(articles: list):
    _LIVE_CACHE["covers_pending"] = False
    for article in articles:
        article["_cover_generating"] = False


def _fallback_live_articles(limit: int = 12) -> list[dict]:
    db = SessionLocal()
    try:
        _ensure_article_schema_migrated(db)
        rows = (
            db.query(Article)
            .filter(or_(Article.status == "published", Article.status.is_(None)))
            .order_by(Article.is_hot.desc(), Article.view_count.desc(), Article.created_at.desc())
            .limit(limit)
            .all()
        )
        articles = []
        for article in rows:
            payload = _serialize_article(article, is_favorited=False, include_content=True)
            payload.update({
                "id": f"live-local-{article.id}",
                "source": "local",
                "source_url": "",
                "published_at": payload.get("updated_at") or payload.get("date"),
                "cover": payload.get("cover_image"),
                "is_live": True,
            })
            articles.append(payload)
        return articles
    except Exception as e:
        logger.warning(f"Realtime article local fallback failed: {e}")
        return []
    finally:
        db.close()


def _live_fallback_response(reason: str) -> dict:
    articles = _fallback_live_articles()
    _LIVE_CACHE["articles"] = articles
    _LIVE_CACHE["cached_at"] = _dt.now()
    _LIVE_CACHE["covers_pending"] = False
    return {
        "articles": articles,
        "cached": False,
        "fallback": True,
        "covers_pending": False,
        "message": reason,
    }

@app.get("/api/articles/hot-realtime")
async def get_hot_realtime_articles(refresh: bool = False):
    if not refresh and _LIVE_CACHE.get("articles") and _LIVE_CACHE.get("cached_at"):
        age = (_dt.now() - _LIVE_CACHE["cached_at"]).total_seconds()
        if age < _LIVE_CACHE_TTL_SEC:
            return {
                "articles": _LIVE_CACHE["articles"],
                "cached": True,
                "cache_age_min": int(age / 60),
                "covers_pending": _LIVE_CACHE.get("covers_pending", False),
            }

    api_key = os.getenv("TAVILY_API_KEY", "")
    if not api_key:
        return await asyncio.to_thread(_live_fallback_response, "TAVILY_API_KEY is not configured")

    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=api_key)
        results = await asyncio.to_thread(
            client.search,
            query="latest public health medical wellness news today",
            search_depth="basic",
            max_results=12,
            exclude_domains=[
                "nature.com", "sciencedirect.com", "pubmed.ncbi.nlm.nih.gov",
                "thelancet.com", "nejm.org", "biorxiv.org", "medrxiv.org",
                "bmj.com", "springer.com", "wiley.com", "cell.com",
                "jamanetwork.com", "arxiv.org", "researchgate.net",
            ],
        )
        raw_list = results.get("results", [])
        if not raw_list:
            return await asyncio.to_thread(_live_fallback_response, "Realtime source returned no results")
        articles = await asyncio.gather(*[_build_live_article(r, i) for i, r in enumerate(raw_list)])
        _LIVE_CACHE["articles"] = articles
        _LIVE_CACHE["cached_at"] = _dt.now()
        _LIVE_CACHE["covers_pending"] = False
        return {"articles": articles, "cached": False, "covers_pending": False, "message": "?????????"}
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Realtime article source failed, using local fallback: {e}")
        return await asyncio.to_thread(_live_fallback_response, "Realtime source is unavailable")


PROFILE_RECOMMENDATION_RULES = {
    "时令与养生": ("睡眠", "熬夜", "失眠", "运动", "饮食", "体重", "BMI", "咖啡", "久坐", "经期"),
    "用药红绿灯": ("药", "用药", "阿司匹林", "抗生素", "降压", "降糖", "胰岛素", "褪黑素", "过敏"),
    "硬核诊疗局": ("体检", "检查", "报告", "结节", "血压", "血糖", "尿蛋白", "胃镜", "心率", "慢病"),
    "辟谣粉碎机": ("误区", "谣言", "真的", "一定", "能不能", "安全吗", "排毒", "偏方"),
    "睡眠": ("睡眠", "熬夜", "失眠", "夜班"),
    "运动": ("运动", "久坐", "健身", "出汗"),
    "饮食": ("饮食", "素食", "肉食", "油盐", "咖啡", "水果"),
    "过敏": ("过敏", "阿司匹林", "花粉", "海鲜", "青霉素"),
    "慢病": ("高血压", "糖尿病", "高血脂", "冠心病", "风湿", "哮喘"),
}


def _profile_preferred_article_terms(profile_text: str) -> set[str]:
    preferred: set[str] = set()
    for tag, tokens in PROFILE_RECOMMENDATION_RULES.items():
        if any(token in profile_text for token in tokens):
            preferred.add(tag)
    return preferred


@app.get("/api/articles/recommended")
async def get_recommended_articles(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _ensure_article_schema_migrated(db)
    articles = _filter_articles_with_new_covers(
        db.query(Article).filter(or_(Article.status == "published", Article.status.is_(None))).all()
    )
    if not articles:
        return {"articles": [], "fallback": True, "message": "暂无可推荐文章"}

    profile = db.query(HealthProfile).filter(HealthProfile.user_id == current_user.id).first()
    profile_text = json.dumps(profile.profile_data, ensure_ascii=False) if profile and profile.profile_data else ""
    favorite_rows = db.query(ArticleFavorite.article_id).filter(ArticleFavorite.user_id == current_user.id).all()
    favorite_ids = {row[0] for row in favorite_rows}
    favorite_articles = [a for a in articles if a.id in favorite_ids]
    preferred_tags = set()
    for article in favorite_articles:
        preferred_tags.update(_article_tags(article))
        if article.category:
            preferred_tags.add(article.category)
    preferred_tags.update(_profile_preferred_article_terms(profile_text))

    def score(article: Article) -> int:
        tags = set(_article_tags(article))
        if article.category:
            tags.add(article.category)
        haystack = _search_text(
            f"{article.title or ''} {article.summary or ''} "
            f"{' '.join(tags)} {' '.join(_json_list(getattr(article, 'related_entities', None)))}"
        )
        value = (article.view_count or 0) + (article.likes or 0) * 5
        value += len(tags & preferred_tags) * 500
        for tag in preferred_tags:
            if _search_text(tag) in haystack:
                value += 120
        if article.id in favorite_ids:
            value -= 1000
        return value

    ranked = sorted(articles, key=score, reverse=True)[:6]
    favs = _favorite_ids(db, current_user, [a.id for a in ranked])
    result = []
    for article in ranked:
        tags = set(_article_tags(article))
        if article.category:
            tags.add(article.category)
        haystack = _search_text(f"{article.title or ''} {article.summary or ''} {' '.join(tags)}")
        reason_tags = [
            tag for tag in preferred_tags
            if tag in tags or _search_text(tag) in haystack
        ][:3]
        reason = f"匹配你的健康档案关注点：{'、'.join(reason_tags)}" if reason_tags else "近期热度较高的健康科普"
        result.append({**_serialize_article(article, article.id in favs), "reason": reason})
    return {
        "articles": result,
        "fallback": not bool(preferred_tags),
        "message": "ok",
    }


@app.get("/api/articles/favorites")
def list_favorite_articles(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_article_schema_migrated(db)
    rows = db.query(Article).join(ArticleFavorite, ArticleFavorite.article_id == Article.id).filter(
        ArticleFavorite.user_id == current_user.id,
        or_(Article.status == "published", Article.status.is_(None)),
    ).order_by(ArticleFavorite.created_at.desc()).all()
    rows = _filter_articles_with_new_covers(rows)
    return {"articles": [_serialize_article(article, True) for article in rows]}


@app.post("/api/articles/track")
def track_article_event(
    payload: ArticleTrackPayload,
    request: Request,
    db: Session = Depends(get_db),
):
    _ensure_article_schema_migrated(db)
    user = _optional_user_from_request(request, db)
    if payload.article_id:
        article = db.query(Article.id).filter(Article.id == payload.article_id).first()
        if not article:
            raise HTTPException(status_code=404, detail="?????")
    _record_article_event(
        db,
        payload.event_type,
        user=user,
        article_id=payload.article_id,
        duration_ms=payload.duration_ms,
        query=payload.query,
        meta_data=payload.meta_data,
    )
    return {"status": "ok"}


@app.post("/api/articles/{article_id}/favorite")
def favorite_article(article_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_article_schema_migrated(db)
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="?????")
    exists = db.query(ArticleFavorite).filter(
        ArticleFavorite.user_id == current_user.id,
        ArticleFavorite.article_id == article_id,
    ).first()
    if not exists:
        db.add(ArticleFavorite(user_id=current_user.id, article_id=article_id))
        _record_article_event(db, "favorite", user=current_user, article_id=article_id)
    return {"is_favorited": True}


@app.delete("/api/articles/{article_id}/favorite")
def unfavorite_article(article_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_article_schema_migrated(db)
    deleted = db.query(ArticleFavorite).filter(
        ArticleFavorite.user_id == current_user.id,
        ArticleFavorite.article_id == article_id,
    ).delete()
    db.commit()
    if deleted:
        _record_article_event(db, "unfavorite", user=current_user, article_id=article_id)
    return {"is_favorited": False}


@app.get("/api/articles/{article_id}")
async def get_article_detail(article_id: int, request: Request, db: Session = Depends(get_db)):
    _ensure_article_schema_migrated(db)
    user = _optional_user_from_request(request, db)
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article or not _article_has_new_cover(article):
        raise HTTPException(status_code=404, detail="?????")
    article.view_count += 1
    db.commit()
    _record_article_event(db, "view", user=user, article_id=article.id)
    favs = _favorite_ids(db, user, [article.id])
    return _serialize_article(article, article.id in favs, include_content=True)


# ==========================================
# 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛銈呭閺屾盯骞橀懠顒夋М闂佹悶鍔嶇换鍐Φ閸曨垰鍐€妞ゆ劦婢€缁墎绱撴担鎻掍壕婵犮垼娉涢鍕崲閸℃稒鐓忛柛顐ｇ箖閸ｆ椽鏌涢敐鍥у幋闁哄苯绉烽¨渚€鏌涜箛鏃傛创闁诡喚鍋ら弫鍌炴倷椤掆偓鎼村﹤鈹戦悩缁樻锭妞ゆ垵鎳愮划鍫⑩偓锝庡亖娴滄粓鏌熼幑鎰【閸熸悂姊洪崨濠冪叆缂傚秴锕ら～蹇曠磼濡顎撻梺鍛婄☉閿曘劎娑甸埀顒€鈹戦悩顐ｅ闁告洖鐏氶悾璺侯渻閵堝骸骞栭柣蹇旂箚閻忔帡姊洪崗鑲┿偞闁哄懏鐟х划顓熷緞閹邦厸鎷洪梺鍛婄箓鐎氬嘲危瑜版帗鐓ユ繛鎴炵懄缁€澶愭煏閸ャ劌濮嶆鐐村浮楠炴鈧稒顭囪ぐ鍛婄節閻㈤潧袨闁搞劌銈稿畷娲冀椤掍焦鍣峰┑锛勫亼閸婃垿宕曢弻銉﹀亱闁绘ê妯婂鏍煟閹寸伝顏堫敋鏉堛劎绠鹃柛鈩兠悘銉︺亜閺団€插惈缂佽鲸鎸荤粭鐔煎炊瑜庨悘宥夋⒑閼姐倕鏆遍柡鍛洴椤㈡岸鏁愰崶銊ョ彴濠电偞娼欓鍡涳綖瀹ュ鈷戦梻鍫熺〒缁犲啿鈹戦鑺ュ唉鐎规洦鍓熸俊鎼佸煛閸屾瀚奸梻鍌氬€搁悧濠冪▔閻熸壋妲堥柕蹇曞Х椤︺劑姊虹紒妯哄Е闁告搫绠戣灋闁告洦鍘剧壕浠嬫煕鐏炴崘澹橀柍褜鍓氶幃鍌氱暦閹邦収妲归幖杈剧悼閻掑吋绻涙潏鍓у埌妞ゎ偅娲樼粋宥夋偡闁妇鍞甸梺鍏兼倐濞佳勬叏閸モ晝妫い鎾寸☉娴滈箖姊婚崒娆戭槮闁硅绻濆畷婵單熼梻瀵稿墾濠电偛妫欓幐绋啃ч弻銉︾叆闁哄啠鍋撻柛銊︾箘閹广垽宕卞Ο闀愮盎闂佸搫绉查崝搴ｇ磽濮樿埖鐓冪憸婊堝礂濮椻偓瀹曟垿骞樼紒妯锋嫼闂佸憡绻傜€氱兘宕曡箛娑欌拺閻㈩垼鍠氱粔顕€鏌熼鍡欑瘈妞ゃ垺娲熼弫鍐焵椤掑嫭鍊峰┑鐘叉处閻撳繐鈹戦悩鑼闁伙綀浜惀顏堝级鐠恒剱銏ゆ煃鐟欏嫬鐏存い銏＄懃閳诲酣鎮欓鍌溞ラ梻鍌欒兌缁垶骞愰幘顔肩劦妞ゆ帊绀佹晶顖涚箾閸忚偐澧甸柡宀嬬秮楠炲鎮欓崱妯虹仸闁逛究鍔岄…銊╁醇閻斿搫骞嶉柣搴ｆ嚀鐎氼厽鍒婇悾灞稿亾濮橆偄宓嗛柡灞剧☉铻ｇ€瑰嫭婢橀埛澶岀磽娴ｈ櫣甯涚紒璇茬墦瀹曞搫鈽夐姀鐘靛姦濡炪倖甯掔€氼剛绮堢€ｎ喗鐓曟い顓熷灥娴滅偤鏌￠崱鎰姦婵﹦绮幏鍛槹鎼存繆顩紓鍌欐祰瀵挾鍒掑▎鎾崇畺婵☆垯璀﹀鈺呮偣妤︽寧顏犳い鏂挎濮婃椽宕ㄦ繝浣虹箒闂佹悶鍔戞禍璺虹暦娴兼潙骞㈡繛鎴炵懅閸橀亶姊洪崫鍕偍闁告柨鏈粋宥咁煥閸愶絾鏂€闂佹枼鏅涢崯顐﹀煝閺囩喐鍙忓┑鐘插鐢盯鏌熷畡鐗堝殗闁诡喗绮岃灒闁绘垶顭囬弳姘舵⒒閸屾瑦绁版い鏇嗗懏宕查柟瀵稿У瀹曟煡鏌涢弴銊ョ仩闁绘帒鐏氶妵鍕箳瀹ュ浂妲梺鎼炲€ら崜鐔煎蓟濞戙垺鍋愰柧蹇ｅ亞濞堛倝姊洪崨濠傜瑲閻㈩垪鈧磭鏆﹀┑鍌氭啞閸嬪嫬顪冪€ｎ亝鎹ｉ柣娑卞櫍濮婄粯鎷呴崫銉ㄩ梺绋款儏閿曨亜鐣烽姀掳鍋呴柛鎰╁妼閸嬪秵绻涚€电孝妞ゆ垵妫濆畷鎴﹀焺閸愵亞鐦堟繝鐢靛Т閸婃悂顢旈埡鍛厽闊洦鎸鹃幗鐘绘煏閸パ冾伃濠殿喒鍋撻梺鐐藉劥瀵挾鈧稈鏅涢—鍐Χ韫囨洜鏆犲銈嗘处閸樻儳危閹版澘绠抽柟鎯ь嚟缁夊爼姊虹€圭姵銆冩俊鐐村笧閸掓帞鎹勬笟顖涘瘜闂侀潧鐗嗗Λ娆戜焊椤撱垺鐓曟慨姗嗗墻閸庡繑銇勯弴妯哄姦鐎规洜鍠栭、妯衡槈濡懓顥氶梻浣瑰缁诲倻鑺遍懖鈺勫С濡炲娴风壕浠嬫煕鐏炲墽鈯曠€规洖鏈幈銊︾節閸曨厼绗＄紓浣诡殘閸犳牠宕洪埀顒併亜閹哄棗浜惧銈庡幖濞测晠藝瑜版帗鐓冮柕澶樺灣閻ｅ灚銇勯姀鈽呰€块柟顔规櫊瀹曟宕妷褎鍠掗梻鍌氬€烽懗鍫曘€佹繝鍥х妞ゅ繐鐗婇埛鏃堟煕閺囥劌鐏犵痪鎯ь煼閺屾稑鐣濋埀顒勫磻閻愮儤鍋傛繛鎴欏灪閻撴洟鏌曟径鍫濈仾婵炲懎鎳庨湁婵犲﹤妫鎰庨崶褝韬鐐存崌楠炴帡寮惔鎾冲緧闂傚倷绀侀幖顐︽儔婵傜绐楅柡宥庡幘瀹撲線鏌涢埄鍐ㄥ毈婵℃煡绠栧娲捶椤撶喐鐝繝鐢靛亹閸嬫捇姊虹拠鈥虫灍妞ゎ厼鐗忕划璇测槈閵忊剝娅滈梺鍛婁緱閸橀箖鎯堥崟顖涒拻濞达絽鎲￠幆鍫ユ煟椤撶儐妲虹紒杈╁仦缁楃喖鍩€椤掑嫬违濞撴埃鍋撶€殿喗鎸虫慨鈧柍鈺佸暞閻濇娊姊绘繝搴′簻婵炶绠撳畷娲礃椤旇偐锛涢梺瑙勫礃椤曆囨煥閵堝棔绻嗛柕鍫濆閸忓矂鏌涘Ο鐑樺暈缂佺粯绋掑蹇涘礈瑜嶉崺宀勬⒑绾懎袚缂侇喖鐭傞幃娲敇閵忊檧鎷绘繛杈剧秮椤ユ挻绋夐懠顒傜＜闁圭粯甯掗埛鏃堟煙楠炲灝鐏茬€规洜鍘ч埞鎴﹀醇閻斿壊鍟庨梻鍌欒兌缁垰顫忔繝姘厱闁割偁鍎遍弸渚€鏌涢弴銊ュ幍濞存粍绮撻弻鐔兼焽閿曗偓閺嬫盯鏌＄€ｎ偄鐏︾紒缁樼洴瀹曘劑顢橀悤浣癸紗闂備礁鎼惉濂稿窗閹邦喚绠旈柣鏃傚帶閸ㄥ倹銇勯弮鍌楁嫛闁诲孩濞婂缁樻媴閸濄儲鐎銈庡亜椤﹂潧鐣疯ぐ鎺戝瀭妞ゆ梻鍋撳▓楣冩⒑缂佹ɑ鈷掗柍宄扮墦瀵偊宕橀鐣屽帾闂佸壊鍋呯换鍌烆敂椤忓牊鐓曢悗锝庝簼閸ゅ洦鎱ㄦ繝鍕笡闁瑰嘲鎳愮划鐢碘偓锝庝簼椤斿嫮绱撻崒娆掑厡缂侇噮鍨跺畷婵單旈崨顓狀唹闂侀潧绻堥崐鏇犵不濞戙垺鐓熸俊銈傚亾闁绘妫欑粋宥夘敍閻愮补鎷虹紓鍌欑劍钃遍悘蹇ｅ幗缁绘稓鈧數顭堥崢瀵糕偓娈垮枟婵炲﹪寮崘顔肩＜婵炴垶鑹剧敮鎯р攽閻橆喖鐏辨繛澶嬬洴閹囧幢濞戞顦梺鍦檸閸犳鎮￠弴鐔稿弿婵妫楁晶濠氭煕鎼淬垺顥堥柡宀嬬磿娴狅箓宕滆濡插牓姊虹€圭姵顥夋い锕傛涧閻ｇ兘鏁撻悩鍐测偓鐑芥煠绾板崬澧婚柛鐔奉儔濮婂宕掑▎鎴М闂佺顕滅换婵嬪极閸愵喖顫呴柨娑樺濞叉悂姊洪崜鎻掍簼婵炲弶鐗犻幏鎴︽偄閸忚偐鍙嗗┑鐘绘涧濡厼危瑜版帗鐓曢悗锝庡亜婵牏绱掔紒妯兼创鐎规洖宕灃闁告劦浜堕崬褰掓⒒娓氣偓濞艰崵寰婇崸妤€绠犻柟鍓х帛缁犳帗绻濋悽闈浶㈤柨鏇樺€濆畷顖炲Ω閳轰胶锛涢梺闈涚箞閸婃牠鎮?# ==========================================
@app.post("/api/articles/{article_id}/like")
def like_article(article_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_article_schema_migrated(db)
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article:
        raise HTTPException(status_code=404, detail="?????")

    existing_like = db.query(ArticleLike).filter(
        ArticleLike.user_id == current_user.id,
        ArticleLike.article_id == article_id,
    ).first()
    if existing_like:
        _record_article_event(db, "like_repeat", user=current_user, article_id=article_id)
        return {"likes": article.likes or 0, "liked": True, "already_liked": True}

    db.add(ArticleLike(user_id=current_user.id, article_id=article_id))
    article.likes = (article.likes or 0) + 1
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        current_likes = db.query(Article.likes).filter(Article.id == article_id).scalar() or 0
        _record_article_event(db, "like_repeat", user=current_user, article_id=article_id)
        return {"likes": current_likes, "liked": True, "already_liked": True}

    _record_article_event(db, "like", user=current_user, article_id=article_id)
    return {"likes": article.likes or 0, "liked": True, "already_liked": False}


# ==========================================
# 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾剧懓顪冪€ｎ亝鎹ｉ柣顓炴閵嗘帒顫濋敐鍛婵°倗濮烽崑鐐烘偋閻樻眹鈧線寮撮姀鈩冩珖闂侀€炲苯澧板瑙勬礋瀹曠兘顢橀悩纰夌床闂佽鍑界紞鍡涘磻閹捐埖顫曢柍鍝勫€荤粻鍓р偓鐟板閸犳洜鑺辨繝姘畾闁绘柨鍚嬮埛鎴︽煕閹邦剙绾ч柟顖氱墦閺屾稒绻濋崒娑樻殘缂備礁鍊圭敮鐔妓囩憴鍕弿濠电姴鎳忛鐘电磼鏉堛劌绗掗摶锝夋煣韫囨稈鍋撳☉姘鳖槰婵犵數濮烽。钘壩ｉ崨鏉戠；闁告洦鍘搁崑鎾愁潩閻撳孩鐏撻梺?AI 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁炬儳缍婇弻鐔兼⒒鐎靛壊妲紒鐐劤缂嶅﹪寮婚悢鍏尖拻閻庨潧澹婂Σ顔剧磼閻愵剙鍔ゆい顓犲厴瀵鈽夐姀鐘靛姶闂佸憡鍔︽禍鏍ｉ崼銏㈢＝濞达絿鎳撴慨鍫熴亜閵娿儲顥㈡鐐茬墦婵℃悂濡锋惔锝呮灈鐎规洖缍婇、娆撳箚瑜嶇紓姘舵⒒閸屾瑨鍏屾い顓炵墦椤㈡牠宕橀鑲╋紮濠电娀娼ч鍛存嫅閻斿吋鐓ユ繝闈涙－濡插憡淇婇锝忚€块柡灞剧洴閳ワ箓骞嬪┑鍥╀憾闂備胶顭堥…顒勫垂鐠轰警娼栨繛宸簻娴肩娀鏌涢弴銊ュ妞ゅ孩鐩娲川婵犲啫闉嶉悗鍏夊亾闁归棿鑳跺畵渚€鏌涢鐘插姎閹喖姊洪棃娑辨▓闁哥姵顨呴娆徝洪鍛嫼缂備緡鍨卞ú鏍ㄦ櫠閸欏浜滈柕濞垮劜閸ゅ洭鏌ㄥ┑鍫濅槐妞ゃ垺娲熼弫鍌滄喆閿濆棗顏归梻鍌欑閹诧紕缂撻崸妤€纾块弶鍫涘妿椤╂彃鈹戦崒姘暈闁绘挻娲熼弻锟犲磼濠靛洨銆婇梺缁樺笒閹诧紕鎹㈠┑瀣劦妞ゆ帊鐒︽刊瀵哥磼椤栨稒绀冮柡鍌楀亾闂傚倷鑳剁划顖炩€﹂崼銉晪婵犲﹤鍠氶崯鍛存煏婢跺棙娅嗛柣鎾存礋閺岋綁寮村槌栨М婵炲瓨绮庢晶妤呭Φ閸曨垰顫呴柍钘夋閻や焦绻濆▓鍨灀闁稿鎹囧娲濞戞艾顣哄┑鈽嗗亝閻╊垰鐣烽姀銈呯濞达絽婀遍崢浠嬫煙閸忓吋鍎楅柛銊ョ－缁棃妫冨ù銏㈡嚀椤劑宕熼鐘靛帨闁诲孩顔栭崰妤佺箾婵犲洤绠栭柕蹇嬪€栭幆鐐烘煕閿旇寮跨紒鍗炲级娣囧﹪鎮欓鍕ㄥ亾閺嶎灛娑欑瑹閳ь剟鏁愰悙鏉戠窞濠电偟鍋撻悗鐑樼節閻㈤潧孝婵炶绠撻幃鈥斥枎閹扳晙绨婚梺鍝勫暙濞村倸顭囬幇顓犵闁告侗鍙忛弨缁樸亜閵婏絽鍔﹂柟顔界懅閳ь剚绋掗敋缁炬儳鍟胯灃闁绘﹢娼ф禒锕傛偨椤栥倗绡€闁绘侗鍠氶埀顒婄秵娴滃爼鎮￠妷鈺傜厱婵炴垵宕悘杈ㄤ繆閹绘帞绉烘慨濠傤煼瀹曟帒顫濋钘変壕闁归棿绀佺壕鐟邦渻鐎ｎ亝鎹ｉ柣顓炴閵嗘帒顫濋敐鍛婵°倗濮烽崑鐐烘偋閻樻眹鈧線寮撮姀鈩冩珖闂侀€炲苯澧板瑙勬礋瀹曠兘顢橀悩纰夌床闂佽鍑界紞鍡涘磻閹捐埖顫曢柍鍝勫€荤粻鍓р偓鐟板閸犳洜鑺辨總鍛婄厸闁告侗鍠氶崣鈧梺鍝勬湰閻╊垱淇婇悜钘夘潊闁斥晛鍟拌ぐ鍥ㄤ繆閻愵亜鈧呪偓闈涚焸瀹曞湱鎹勬笟顖涚稁濠电偛妯婃禍婊勫閻樼粯鐓曢柡鍥ュ妼娴滄繃绻涢崼鐔虹煉婵﹨娅ｇ划娆忊枎閹冨闂備礁婀遍…鍫熸櫠閽樺）锝夊箛閺夎法顔婇梺鍝勫€搁幖顐ｇ妤ｅ啯鐓ユ繝闈涙閸戝綊鏌熼懠顒€浜惧ǎ鍥э躬閹崇姵锛愬┑鍥╃Х缂傚倷鑳剁划顖滅矙閹捐绐楀┑鐘叉搐閻鎱ㄥ璇蹭壕闂佺顑嗛〃鍛扮亙闂佺粯锕㈠褎绂掑鍛＜濠㈣泛锕︾粔娲煏閸℃洜顦﹂柍璇查叄楠炴﹢寮堕幋鏂夸壕闂佸灝顑冩禍婊堟煙閸濆嫭顥滃ù婊堢畺濮婃椽宕崟顒夋￥闂佸摜濮甸崝妤呭箲閵忕姭鏀介悗锝庡亜娴犳椽姊婚崒姘卞缂佸鍔曢埢浠嬵敂閸啿鎷洪梺纭呭亹閸嬫稒淇婃總鍛婄厽闁哄诞浣镐划闂佺粯渚楅崳锝夌嵁閸ヮ剙绾ч悹渚厛閸熷洤鈹戦悙瀛樺鞍闁艰鍎崇叅闁挎洖鍊搁悿楣冩煟閹邦喖鍔嬮柍閿嬪灩缁辨帡顢涘☉娆戭槬婵犫拃鍡楃毢缂佽鲸甯為埀顒婄秵閸嬪嫰鎮樼€电硶鍋撶憴鍕闁荤啿鏅犻獮鍐煛閸愵亞锛滃┑鈽嗗灣閸樠囩嵁閸儲鈷掑〒姘ｅ亾闁逞屽墯缁嬫垵鐣甸崱妯肩濞达絽鍟垮ú銈夊礄閻樼粯鐓熼柡鍌氱仢閹垿鏌ｉ幘瀵告创闁哄本绋戦…銊╁焵椤掑倻鐭嗗ù锝堫潐濞呯娀鏌熺紒銏犳灍闁绘挻鐟﹂妵鍕籍閳ь剟寮告繝姘殌闁秆勵殕閻撴瑦銇勯弮鍌涙珪闁瑰啿娲﹂〃銉╂倷鐎涙ê纾冲Δ鐘靛仦鐢€崇暦閸楃倣鐔兼惞鐟欏嫅銊╂⒒閸屾艾鈧悂宕愭搴㈩偨婵﹩鍓﹂悞鐣屾喐閺冨牆绠栫憸鏃堝箖濞嗘垹绀勯柣妯兼暩閻ｉ箖姊绘担瑙勫仩闁稿孩妞藉畷婊冣枎閹炬潙鈧潡鏌涢…鎴濅簴濞存粍绮撻弻鐔煎传閸曨厜銈夋偣閹邦亜宓嗛柡灞剧洴閹垺顦版惔锝庡晪闂備礁鎼張顒勬儎椤栫偛绠栭柍杞拌兌閺嗭箓鏌涢妷鎴斿亾闁瑰嚖绻濆缁樻媴閻戞ê娈岀紓浣哄Т缁夌懓鐣烽崷顓熷厹闁告侗鍨花鐑芥⒒閸屾瑧顦﹂柟璇х節瀹曟繆绠涘☉妯兼煣濠电偞鍨堕悷锕傚磿閻斿皝鏀介柣妯哄级婢跺嫰鏌嶉柨瀣仼闁汇儺浜、姗€鎮欓弶鎴烆仭闁荤喐绮嶆俊鎼佸川椤栨粣绱查梻浣虹帛閿氶柛鐔锋健閹﹢骞橀鐣屽幍?LLM 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛濠傛健閺屻劑寮撮悙娴嬪亾閸濄儳涓嶉柡宥庡幗閻撴洘銇勯幇鍓佺ɑ缂佲偓閳ь剛绱掗悙顒€鍔ゆ繛纭风節瀵鎮㈤悡搴ｇ暰闂佺粯顨呴悧婊兾涢崟顓犵＜闁绘劦鍓欓崝銈嗐亜椤撶姴鍘寸€殿喖顭烽弫鎰緞婵犲喚妫熼梻浣稿閻撳牓宕板Δ鍛９妞ゆ牜鍋為埛鎴犵磼鐎ｎ偒鍎ラ柛搴＄Ч閺屾稒绻濋崘顏嗙杽閻庢鍠栭…鐑藉极閹邦厼绶炲┑鐘插濞煎姊绘担渚劸闁哄牜鍓熼幊婵嬫倷椤掑偆娴勫┑顔姐仜閸嬫挾鈧?# ==========================================
class ArticleAskRequest(BaseModel):
    question: str


async def _article_ask_generator(article_title: str, article_content: str, question: str):
    system_prompt = (
        "你是健康知识文章的 AI 伴读助手，只能基于给定文章内容回答用户问题。"
        "回答要面向普通用户，简洁、准确，不给诊断结论或处方建议。"
        f"\n文章标题：{article_title}\n文章内容：{article_content[:6000]}"
    )
    try:
        stream = await fast_llm.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": question},
            ],
            stream=True,
            temperature=0.5,
        )
        async for chunk in stream:
            delta = chunk.choices[0].delta.content
            if delta:
                yield f"data: {json.dumps({'type': 'chunk', 'content': delta}, ensure_ascii=False)}\n\n"
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
    except Exception as e:
        logger.error(f"文章 AI 伴读生成失败: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': 'AI 伴读暂时不可用，请稍后重试。'}, ensure_ascii=False)}\n\n"


@app.post("/api/articles/{article_id}/ask")
async def ask_article_question(article_id: int, req: ArticleAskRequest, db: Session = Depends(get_db)):
    article = db.query(Article).filter(Article.id == article_id).first()
    if not article or not _article_has_new_cover(article):
        raise HTTPException(status_code=404, detail="?????")
    return StreamingResponse(
        _article_ask_generator(article.title, article.content, req.question),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/register")
@limiter.limit("5/minute")
def register_user(request: Request, user_req: RegisterUserParams, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user_req.username).first()
    if db_user:
        raise HTTPException(
            status_code=409,
            detail={"code": "USERNAME_EXISTS", "message": "??????"},
        )
    hashed_password = get_password_hash(user_req.password)
    new_user = User(username=user_req.username, password_hash=hashed_password)
    db.add(new_user)
    db.commit()
    return {"message": "????"}


@app.post("/api/login")
@limiter.limit("5/minute")
def login_user(request: Request, user_req: LoginUserParams, db: Session = Depends(get_db)):
    _ensure_admin_schema_migrated(db)
    user = db.query(User).filter(User.username == user_req.username).first()
    if not user or not verify_password(user_req.password, user.password_hash):
        raise HTTPException(status_code=401, detail="????")
    if hasattr(user, "is_active") and not user.is_active:
        raise HTTPException(status_code=403, detail="????")
    user.last_login_at = datetime.now(timezone.utc)
    db.commit()
    access_token = create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer", "username": user.username, "role": getattr(user, "role", "user")}


# Legacy /api/admin/* implementation removed. The C-end uses only the read-only
# /api/health-articles proxy; all admin writes live in medical-graphrag.

@app.post("/api/profile")
def save_profile(payload: ProfilePayload, current_user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    profile_data = payload.profile_data.model_dump(mode="json", exclude_none=True)
    profile_data["_schema_version"] = "health_profile_v1"
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == current_user.id).first()
    if profile:
        profile.profile_data = profile_data
    else:
        new_profile = HealthProfile(user_id=current_user.id, profile_data=profile_data)
        db.add(new_profile)
    db.commit()
    return {
        "message": "????",
        "status": "success",
        "schema_version": "health_profile_v1",
    }


@app.get("/api/profile")
def get_profile(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == current_user.id).first()
    if not profile:
        return {"profile_data": None}
    return {"profile_data": profile.profile_data}


PROFILE_INSIGHT_DISCLAIMER = "本内容为健康评估与风险提示，不构成临床诊断。"
PROFILE_INSIGHT_FORBIDDEN_TERMS = (
    "确诊为",
    "诊断为",
    "正式诊断",
    "最终诊断",
    "开具处方",
    "处方用药",
    "必须服用",
    "立即服用",
)


def _profile_as_list(value):
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _profile_join_values(data: dict, *keys: str) -> str:
    values = []
    for key in keys:
        values.extend(_profile_as_list((data or {}).get(key)))
    return "、".join(dict.fromkeys(values))


def _profile_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _profile_data_to_dict(raw) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    return {}


def _profile_rule_insights(data: dict) -> list[dict]:
    data = data or {}
    insights = []
    height_num = _profile_float(data.get("height"))
    weight_num = _profile_float(data.get("weight"))

    if height_num > 0 and weight_num > 0:
        bmi = weight_num / ((height_num / 100) ** 2)
        if bmi < 18.5:
            insights.append({
                "title": "体重偏轻提醒",
                "content": "根据身高和体重估算，您的 BMI 偏低。建议关注蛋白质、优质脂肪和规律进餐，若伴随乏力、月经紊乱或体重持续下降，应考虑线下评估营养和内分泌情况。",
                "tags": ["BMI", "营养"]
            })
        elif bmi >= 28:
            insights.append({
                "title": "体重管理风险提示",
                "content": "根据身高和体重估算，您的 BMI 已进入肥胖范围。建议优先从饮食结构、运动频率和睡眠节律入手管理体重，并同步关注血压、血脂和血糖等代谢指标。",
                "tags": ["BMI", "代谢"]
            })
        elif bmi >= 24:
            insights.append({
                "title": "体重偏高趋势提醒",
                "content": "根据身高和体重估算，您的 BMI 略高于理想范围。建议减少高糖高油饮食，增加规律运动，并持续观察腰围、血压和血脂变化。",
                "tags": ["BMI", "生活方式"]
            })

    sleep = str(data.get("sleep") or "")
    if "熬夜" in sleep or "失眠" in sleep:
        insights.append({
            "title": "睡眠节律需要优先修复",
            "content": "档案显示您存在熬夜或失眠情况。长期睡眠不足会影响免疫、代谢和情绪稳定，建议先固定入睡时间，减少睡前屏幕刺激，并观察白天疲劳和注意力变化。",
            "tags": ["睡眠", "恢复"]
        })

    chronic_text = _profile_join_values(data, "diseases", "past_diseases_common", "past_diseases_custom")
    if chronic_text:
        insights.append({
            "title": "慢病与用药风险需要持续监测",
            "content": f"档案中记录了 {chronic_text}。后续问诊和用药咨询时应主动告知这些病史，尤其涉及降压药、降糖药、止痛退烧药或抗感染药时，需要避免禁忌和相互作用风险。",
            "tags": ["慢病", "用药安全"]
        })

    allergy_text = _profile_join_values(data, "allergies", "allergies_common", "allergies_custom")
    if allergy_text:
        insights.append({
            "title": "过敏史是用药安全红线",
            "content": f"档案中记录了 {allergy_text} 相关过敏信息。购药、接种疫苗或接受检查治疗前，应主动告知医生和药师，避免再次接触可疑过敏原。",
            "tags": ["过敏", "安全"]
        })

    exercise = str(data.get("exercise") or "")
    if "几乎不运动" in exercise or "偶尔运动" in exercise:
        insights.append({
            "title": "运动频率仍有提升空间",
            "content": "档案显示您的运动频率偏低。建议从低强度、可坚持的活动开始，例如快走、骑行或力量训练，每周逐步增加频次，避免突然高强度运动导致损伤。",
            "tags": ["运动", "习惯"]
        })

    if not insights:
        insights.append({
            "title": "综合健康维护建议",
            "content": "当前档案未触发明显风险红线。建议继续保持规律作息、均衡饮食、适量运动，并定期更新体检、用药、过敏和既往病史信息，以便系统给出更贴合个人情况的提示。",
            "tags": ["综合", "预防"]
        })
    return insights[:5]


def _build_profile_health_context(data: dict) -> dict:
    data = data or {}
    height_num = _profile_float(data.get("height"))
    weight_num = _profile_float(data.get("weight"))
    bmi = round(weight_num / ((height_num / 100) ** 2), 1) if height_num > 0 and weight_num > 0 else None
    risk_points = []

    if bmi is not None:
        if bmi < 18.5:
            risk_points.append({"type": "BMI", "level": "medium", "evidence": f"BMI={bmi}，体重偏轻"})
        elif bmi >= 28:
            risk_points.append({"type": "BMI", "level": "high", "evidence": f"BMI={bmi}，达到肥胖范围"})
        elif bmi >= 24:
            risk_points.append({"type": "BMI", "level": "medium", "evidence": f"BMI={bmi}，体重偏高"})

    sleep = str(data.get("sleep") or "")
    if "熬夜" in sleep or "失眠" in sleep:
        risk_points.append({"type": "睡眠", "level": "medium", "evidence": f"睡眠质量：{sleep}"})

    exercise = str(data.get("exercise") or "")
    if "几乎不运动" in exercise or "偶尔运动" in exercise:
        risk_points.append({"type": "运动", "level": "medium", "evidence": f"运动频率：{exercise}"})

    chronic_text = _profile_join_values(data, "diseases", "past_diseases_common", "past_diseases_custom")
    if chronic_text:
        risk_points.append({"type": "既往病史", "level": "high", "evidence": chronic_text})

    allergy_text = _profile_join_values(data, "allergies", "allergies_common", "allergies_custom")
    if allergy_text:
        risk_points.append({"type": "过敏史", "level": "high", "evidence": allergy_text})

    profile_summary = {
        "gender": data.get("gender"),
        "age": data.get("age"),
        "height_cm": data.get("height"),
        "weight_kg": data.get("weight"),
        "bmi": bmi,
        "diet": data.get("diet"),
        "exercise": data.get("exercise"),
        "sleep": data.get("sleep"),
        "smoking": data.get("smoking"),
        "drinking": data.get("drinking"),
        "chronic_diseases": chronic_text,
        "allergies": allergy_text,
        "surgeries": _profile_join_values(data, "surgeries", "surgeries_common", "surgeries_custom"),
        "vaccines": _profile_join_values(data, "vaccines_common", "vaccines_custom"),
    }
    return {
        "profile_summary": {k: v for k, v in profile_summary.items() if v not in (None, "", [], {})},
        "risk_points": risk_points,
    }


def _profile_text_has_forbidden_health_claim(text: str) -> bool:
    clean = str(text or "")
    return any(term in clean for term in PROFILE_INSIGHT_FORBIDDEN_TERMS)


def _normalize_profile_llm_insights(payload) -> list[dict]:
    raw_items = payload.get("insights") if isinstance(payload, dict) else payload
    if not isinstance(raw_items, list):
        raise ValueError("LLM profile insight payload is not a list")

    insights = []
    combined_text = json.dumps(raw_items, ensure_ascii=False)
    if _profile_text_has_forbidden_health_claim(combined_text):
        raise ValueError("LLM profile insight contains forbidden diagnosis or prescription wording")

    for item in raw_items[:5]:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        content = str(item.get("content") or "").strip()
        if not title or not content:
            continue
        tags = item.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        insights.append({
            "title": title[:40],
            "content": content[:260],
            "tags": [str(tag).strip()[:12] for tag in tags if str(tag).strip()][:3],
        })

    if len(insights) < 3:
        raise ValueError("LLM profile insight payload has fewer than 3 valid cards")
    return insights[:5]


async def _generate_llm_profile_insights(data: dict, risk_context: dict) -> list[dict]:
    system_prompt = (
        "你是面向普通用户的数字健康档案评估助手。"
        "你的任务是基于用户已填写的结构化档案和系统规则抽取出的风险点，生成健康评估与风险提示卡片。"
        "禁止编造未提供的病史、检查结果、诊断结论或药物处方。"
        "禁止使用“确诊为”“诊断为”“正式诊断”“最终诊断”“开具处方”“必须服用”等表达。"
        "输出必须是严格 JSON 对象，格式为："
        "{\"insights\":[{\"title\":\"...\",\"content\":\"...\",\"tags\":[\"...\"]}]}。"
        "生成 3 到 5 张卡片；每张 content 80 到 180 字；语言面向普通用户。"
        "如果存在过敏史、既往病史、明显 BMI 异常、睡眠问题或运动不足，必须优先覆盖。"
        f"整体免责声明：{PROFILE_INSIGHT_DISCLAIMER}"
    )
    user_payload = {
        "profile_summary": risk_context.get("profile_summary", {}),
        "risk_points": risk_context.get("risk_points", []),
        "task": "基于上述证据生成个性化健康评估卡片，只给健康管理建议和就医提醒，不给诊断或处方。",
    }
    response = await asyncio.wait_for(
        shared_client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=900,
        ),
        timeout=12,
    )
    content = response.choices[0].message.content or "{}"
    return _normalize_profile_llm_insights(json.loads(content))


# ==========================================
# 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛濠傛健閺屻劑寮撮悙娴嬪亾瑜版帒纾块柟瀵稿У閸犳劙鏌ｅΔ鈧悧鍡欑箔閹烘鐓曢柕濞垮劚閳ь剙娼″濠氭晲婢跺浜滅紓浣割儐椤戞瑥螞閸℃稒鈷戦柛婵嗗閿涙棃姊婚崟顐㈩伃濠碉紕鏁诲畷鐔碱敍濮橀硸鍞洪梻浣烘嚀閻°劎鎹㈠鍡欘浄闁稿繘妫跨换鍡樹繆閵堝倸浜鹃梺纭呮珪閿氭い顐㈢箰鐓ゆい蹇撳椤旀劙姊虹紒妯哄鐟滄澘鍟幈銊╁醇閺囩啿鎷洪柣鐘叉穿鐏忔瑧绮婚弻銉︾厽闁冲搫锕ら悘锔筋殽閻愭彃鏆ｅ┑顔瑰亾闂侀潧鐗嗗Λ妤佺濡ゅ懏鐓欓柤鍦瑜把呯磼閼艰泛浜规俊鍙夊姇椤劑宕奸悢鍝勫箺闂佺懓鍚嬮悾顏堝磹閵堝拋鐒介柟閭﹀幘缁犻箖鏌涘▎蹇ｆШ濠⒀屼邯閹繝濡舵径瀣幗濠碘槅鍨遍娆撳吹濞嗘垹妫柟顖嗗瞼鍚嬮梺鍝勭灱閸犳牕鐣峰鍡╂Ь闁汇埄鍨遍惄顖炲蓟閿濆绠婚柛妤冨仜婵箓姊虹拠鈥虫灍妞ゃ劌锕顐﹀箛閺夎法顦ㄩ梺闈涱焾閸庢椽鎮楁潏鈺冪＝闁稿本鐟х拹浼存煕閻樻剚娈滄鐐村姍瀹曟ê顔忛鐣岀▉婵犵數鍋涘Ο濠冪濠婂喚鍟呮繝闈涙閺€浠嬫煟濡绲绘い鎺嬪灲閺岋綁鏁愰崨顐划濠殿喖锕ㄥ▍锝囧垝濞嗗繆鏋庨柟顖嗗啫顥庣紓鍌氬€风粈渚€顢栭崱娑欏亱闁绘娅ｉ悢鍛存⒒娴ｅ憡鍟炴繛璇х畵瀹曞綊鏌嗗鍛幈?AI 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛銈呭閺屾盯顢曢敐鍡欙紩闂侀€炲苯澧剧紒鐘虫尭閻ｉ攱绺界粙璇俱劍銇勯弮鍥撴繛鍛墦濮婄粯鎷呴崨濠傛殘闂佸憡妫戠粻鎾崇暦娴兼潙鍐€妞ゆ劑鍩勫Λ婊堟⒑缁夊棗瀚峰▓鏇㈡煃闁垮鐏﹂柕鍥у楠炴帡宕卞鎯ь棜缂傚倸鍊风粈渚€藝椤栫儐鏁嬫い鎾跺Т閸ㄦ繂鈹戦悩瀹犲闁告劏鍋撴俊鐐€栭崝妤呭窗鎼淬垻顩查柣鎰靛墻濞堜粙鏌ｉ幇顖氱毢濞寸姰鍨介弻娑㈠籍閳ь剙鐣濋幖浣歌摕闁绘梻鈷堥弫濠囨煠濞村娅冮柧蹇撻叄閺岋綁骞橀崡鐐╁亾閺嶎厼桅闁告洦鍠氶悿鈧梺鍦亾濞兼瑥鈻嶉敐澶嬧拺闂侇偆鍋涢懟顖涙櫠娴煎瓨鐓曢煫鍥ㄦ閼拌法鈧鍠栭…閿嬩繆閸洖鐐婄憸搴ㄦ倵閹惰姤鈷戦柛娑橆煬濞堟﹢鏌涚€ｎ偆娲撮柨婵堝仱椤㈡棃宕熼崹顐㈢槣闂備線娼ч悧鍡涘箠濮椻偓椤㈡挸螖娴ｅ吀绨婚梺鎸庢礀閸婄懓鈽夎閵囧嫰顢曢姀銏㈩啋闂佸湱鍘у﹢閬嶅箟閹绢喖绀嬫い鎰剁悼閳ь剦鍙冨缁樻媴閸涘﹤鏆堝┑鐐额嚋闂勫嫮绮嬪澶嬫櫜濠㈣泛锕ュΣ顒勬⒑閸涘﹦鈽夐柣掳鍔戦崺娑㈠箣閻樼數锛滈柣搴秵閸嬪嫰顢氬鍫熺厱闁绘劕妯婂Σ铏圭磼鏉堛劌娴い銏″哺閸┾偓妞ゆ帒鍊绘稉宥夋煛瀹ュ啫濡虹紒?
# ==========================================
@app.get("/api/profile/ai-insights")
async def get_ai_insights(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == current_user.id).first()

    if not profile or not profile.profile_data:
        return {
            "status": "error",
            "insights": [],
            "generation_mode": "none",
            "model": None,
            "disclaimer": PROFILE_INSIGHT_DISCLAIMER,
        }

    data = _profile_data_to_dict(profile.profile_data)
    try:
        insights = await _generate_llm_profile_insights(data, _build_profile_health_context(data))
        return {
            "status": "success",
            "insights": insights,
            "generation_mode": "llm_guarded",
            "model": FAST_MODEL,
            "disclaimer": PROFILE_INSIGHT_DISCLAIMER,
        }
    except Exception as exc:
        logger.warning(f"[Profile/AIInsights] LLM generation failed, fallback to rules: {exc}")
        return {
            "status": "success",
            "insights": _profile_rule_insights(data),
            "generation_mode": "rule_fallback",
            "model": None,
            "disclaimer": PROFILE_INSIGHT_DISCLAIMER,
        }


@app.get("/api/checkins/today")
def get_today_checkins(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _build_today_checkin_payload(db, current_user.id, date.today())


@app.post("/api/checkins")
def save_checkin(payload: CheckinPayload, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_database_tables()
    _ensure_checkin_schema_migrated(db)
    _purge_system_checkin_items(db)
    target_date = _parse_date_or_today(payload.checkin_date)
    item = db.query(HealthCheckinItem).filter(
        HealthCheckinItem.code == payload.item_code,
        HealthCheckinItem.is_active == True,
        HealthCheckinItem.owner_user_id == current_user.id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="打卡项不存在")

    record = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == current_user.id,
        UserHealthCheckin.item_code == payload.item_code,
        UserHealthCheckin.checkin_date == target_date
    ).first()

    if record:
        record.status = payload.status
        record.value_json = payload.value_json
        record.points_earned = item.points if payload.status == "done" else 0
    else:
        db.add(UserHealthCheckin(
            user_id=current_user.id,
            item_code=payload.item_code,
            checkin_date=target_date,
            status=payload.status,
            value_json=payload.value_json,
            points_earned=item.points if payload.status == "done" else 0,
        ))
    db.commit()
    item_view = _load_checkin_item_view(db, current_user.id, item, target_date)
    return {
        "message": "打卡已保存",
        "item_code": payload.item_code,
        "date": target_date.isoformat(),
        "item": item_view,
        "today": _build_today_checkin_payload(db, current_user.id, target_date)
    }


@app.delete("/api/checkins/{item_code}")
def delete_checkin(item_code: str, checkin_date: Optional[str] = None, current_user: User = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    target_date = _parse_date_or_today(checkin_date)
    record = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == current_user.id,
        UserHealthCheckin.item_code == item_code,
        UserHealthCheckin.checkin_date == target_date
    ).first()
    if record:
        db.delete(record)
        db.commit()
    return {
        "message": "打卡记录已删除",
        "item_code": item_code,
        "date": target_date.isoformat(),
        "today": _build_today_checkin_payload(db, current_user.id, target_date)
    }


# ==========================================
# 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛銈呭閺屾盯顢曢敐鍡欘槬闂佺琚崝搴ㄥ箟閹间礁绫嶉柛顐ｆ儕閵夆晜鐓曢柟鑸妽濞呭棝鏌涙惔锝呮灈闁哄本娲濈粻娑氣偓锝庝簽閸旀潙鈹戦悙璺虹毢妞ゎ厼鐗撻崺鐐哄箣閿旇棄浜归柣鐘叉厂閸愌呯煑闂傚倷鑳剁划顖炪€冮崨瀛樻櫇妞ゅ繐瀚弳锕傛煙鏉堝墽鐣遍柣鎾寸洴閺屾稑鈽夐崡鐐寸亾闂佸憡眉缁瑥顫忓ú顏呯劵婵炴垶锚缁侇喖鈹戦悙鏉垮皟闁告洦鍓氶悵閿嬬箾鐎电甯堕柣掳鍔戦幃锟犲礃椤忓棛锛濇繛杈剧秬閸嬪倿骞嬮悩鐢电劶闂侀€炲苯澧い顏勫暣婵¤埖鎯旈垾宕囧摋婵犵數鍋涢ˇ鏉棵洪悢鑲╁祦闁硅揪绠戠粈瀣亜閹烘垵鈧骞婂┑鍡╂富闁靛牆妫涙晶顒勬煠閸︻厼浜剧紒鏃傚枛瀹曞ジ濡烽敂瑙勫濠电偠鎻徊鍧楀箠閹惧瓨娅犳い鏍嚔閻熼偊鐓ラ柛娑卞幒濡叉劙鎮楀▓鍨珮闁稿鎳愰幑銏犫攽閸♀晜鍍靛銈嗗笒閸燁垶骞夐悧鍫㈢瘈闁汇垽娼ф禒锕傛煕閵娿儳鍩ｉ柍銉畵瀹曟帡鎮欓懠顒傛綁闂備胶顭堥張顒勩€冮崨顔绢洸濡わ絽鍟悡銉︾節闂堟稒顥㈡い搴㈩殔椤儻顦遍柛妤佸▕瀵鏁愭径瀣珖闂侀€炲苯澧撮柟顔ㄥ洤绠婚柟棰佺劍缂?CRUD闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛濠傛健閺屻劑寮崼鐔告闂佺顑嗛幐鍓у垝椤撶偐妲堟俊顖濐嚙濞呇囨⒑濞茶骞楅柣鐔叉櫊瀵鎮㈤崗鐓庘偓缁樹繆椤栨繃顏犻柛鏃傤焾閳规垿鎮欓懠顒佺檨闂佸搫鎳愭繛鈧€殿喖顭峰鎾偄閾忚鍟庨梻浣虹帛閸旓箓宕滃鑸靛仧闁哄啫鐗婇埛鎴︽煕濠靛棗顏撮柛搴℃捣缁辨帡鎳滈棃娑樻懙閻庢鍠栭…宄邦嚕閹绢喖顫呴柣妯款嚙閺佽绻濋悽闈浶㈤柣蹇斿哺瀹曟繈寮介銈嗗櫘缂傚倸鍊搁崐鎼佸磹閻戣姤鍤勯柤鎼佹涧閸ㄦ棃鎮楅棃娑欐喐缂佲偓婵犲倶鈧帒顫濋敐鍛闁诲孩顔栭崰鏍€﹂悜钘夋瀬闁归偊鍘肩欢鐐测攽閻愨晜濯伴柛銉戝拋鍟囬梺鍝勵槸閻楀棙鏅堕悾灞筋棜闁割煈鍟旇ぐ鎺撳亹妞ゆ劧绲鹃悵鏃堟⒑鐎圭媭娼愰柛銊ユ健楠炲啫鈻庨幘鏉戞濡炪倖宸婚崑鎾淬亜閿濆懐绉烘慨濠冩そ瀹曘劍绻濇惔銏㈡殾濠电姷鏁搁崑娑㈡晝椤忓牊鍋樻い鏇楀亾鐎规洘锕㈡俊鍛婃償閵忊槅妫冮悗瑙勬礃閿曘垽宕洪埄鍐╁闁革富鍘搁崑鎾活敇閵忊檧鎷绘繛杈剧到閹诧繝宕悙瀵哥閻犲泧鍛殼閻庤娲忛崹娲Χ閿濆绀冮柕濞у懎骞€濠电姷鏁告繛鈧繛浣冲洦鍋嬮煫鍥ㄦ礀椤ユ岸鏌﹀Ο渚＆鐟滅増甯楅弲鏌ユ煕濞戝崬鏋熼柛搴㈡崌閺屾盯鎮㈤崨濠傗偓鎰叏婵犲啯銇濈€规洦鍋婃俊鐑藉Ψ閹板墎绉柡宀嬬到铻栭柛鎰╁妷閺嬪懘姊虹拠鈥虫灆闁告濞婇妴浣糕枎閹存繃鐎抽梺鍛婎殘閸嬬偤宕愰姘ｆ斀闁绘﹩鍠栭悘杈ㄧ箾婢跺娲撮柡浣稿暣閺佸啴宕掗妶鍡樻珦闂備礁婀遍崕銈夈€冮崱娑樼厱闁硅揪闄勯悡鏇熺箾閹寸儑鍏€规悶鍎遍埞鎴︻敊閼测晛鈪遍梺鐟板级閹倸顕ｉ崼鏇炲瀭妞ゆ棁濮ら鎺楁⒒娴ｄ警鏀版繛鍛礋楠炴垿宕堕鍌氱ウ闂佸綊鍋婇崢浠嬪磿閻斿摜绡€闂傚牊绋撴晶銏ゆ煟閿濆鐣烘慨濠勭帛閹峰懘鎼归悷鎵偧闂備礁鎲″Λ鎴︽⒔閸曨厾鐭夌€广儱鎷嬮悡銉╂煕椤愶絿绠橀柛鏃撶畱椤啴濡堕崱妤冪憪闂佺粯甯俊鍥╁垝閸儱鐒垫い鎺戝閳锋垿鏌熺粙鍨劉缁剧偓鎮傞弻娑㈠Ω閵婏富妫勯梺浼欑悼閸忔﹢寮幘缁樺亹闁告劖绁撮崑鎾绘倻閼恒儳鍘撻梺鍛婄箓鐎氼剟鍩€椤掑倻甯涚紒鍌氱Ч瀵粙顢橀悢鍙夊濠电偠鎻徊鍧楀磿閵堝鍚归柍褜鍓欓—鍐Χ閸℃ê顦╅梺鍛婄懃閸燁偊鎮鹃悜钘夌闁挎棁妫勯埀顒勬敱缁绘盯寮堕幋顓炲壉闂佽瀛╁钘夘潖閾忓湱纾兼慨妤€妫涢崝椋庣磽娓氬洤娅橀柛銊ョ埣楠炲啴鏁撻悩鍐蹭簻闂佺绻楅崑鎰板储閻㈠憡鈷掑〒姘搐瀵法绱掗悩鍐茬伌闁绘侗鍣ｅ浠嬵敃閵堝浄绱查梻浣告贡缁垳鏁埡鍛亗濠靛倸鎲￠悡娑氣偓鍏夊亾閻庯綆鍓涜ⅲ缂傚倷鑳舵慨鐢电矙閹烘梹宕叉繝闈涱儏缁€鍐煃閸濆嫬鏆欑紓宥呭暣濮婂宕掑▎鎴М闂佸湱鈷堥崑濠囧箚鐏炴儳绶為悘鐐村劤鎼村﹤鈹戦悩缁樻锭妞ゆ垵鎳樺浼村Ψ閳哄倻鍘撻悷婊勭矒瀹曟粌顫濈捄浣曪箓鏌涢弴銊ョ仩缂佺姵濞婇弻鐔衡偓娑欘焽缁犮儱霉鐏忔牕浜鹃梻浣筋嚙濮橈箓锝炴径鎰濡炲瀛╅鑺ユ叏濡搫顣崇€规挷绶氶弻鈥愁吋鎼粹€茬爱闂佺顑嗛幐鎼佸煡婢跺ň鏋嶉柧蹇ｅ亜閻忊晛霉濠婂嫭鍊愭い銏★耿閹?# ==========================================
import re as _re
import uuid as _uuid


def _generate_user_item_code(db: Session, user_id: int, name: str) -> str:
    slug = _re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")[:24] or "item"
    for _ in range(5):
        code = f"u{user_id}_{slug}_{_uuid.uuid4().hex[:4]}"
        exists = db.query(HealthCheckinItem.id).filter(HealthCheckinItem.code == code).first()
        if not exists:
            return code
    # 闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛銈呭閺屾盯顢曢敐鍡欙紩闂侀€炲苯澧剧紒鐘虫尭閻ｉ攱绺界粙娆炬綂闂佺偨鍎遍崯璺ㄨ姳閵夆晜鈷掑ù锝囧劋閸も偓濡炪倖娲﹂崢浠嬪箞閵娾晛绠绘い鏃傚帶閻庮厼鈹戦悩缁樻锭闁绘鍟村畷鎴﹀箻鐠囪尙顦ф繝銏ｆ硾閿曪絾绔熼弴銏♀拻濞撴埃鍋撻柍褜鍓涢崑娑㈡嚐椤栨稒娅犻柟缁㈠枟閻撴瑧鈧娲栧ú銈嗙闁秵鐓涢悘鐐跺Г椤ユ粍銇勯幘鐐藉仮鐎规洏鍔戦、娑㈡晲閸涱喗娈介梻鍌氬€搁崐椋庣矆娓氣偓瀹曘儳鈧綆鍋嗛々鎻捗归悩宸剰缁炬儳娼￠弻鐔虹磼濡搫娼戦梺鍝勵儏缁夊綊寮婚垾宕囨殼妞ゆ梻鎳撴禍楣冩⒑鐞涒€充壕闂佸吋浜介崕顖涚濠婂牊鐓涢柛鎰剁到娴滈箖姊洪崫鍕靛剳缂侇噮鍨崇划顓㈡偄鐏忎焦顫嶉梺闈涢獜缁辨洟宕㈤柆宥嗙厽閹兼惌鍨崇粔闈浢瑰鍕煉妞ゃ垺妫冮、姗€濮€閿涘嫬骞堥梻浣哥－閹虫捇鎮樺┑瀣€堕弶鍫氭櫇绾惧ジ鏌ｅΟ铏癸紞闁宠棄顦甸弻宥夋寠婢舵ɑ笑闂佸疇顕ч柊锝夌嵁鐎ｎ亖鏀介柛鈩冪懐閸熷牆鈹戦悩娈挎殰缂佽鲸娲熷畷鎴﹀箣閿曗偓绾惧綊鏌曢崼婵愭Ц缁炬儳顭烽弻鐔兼倻濮楀棙鐣剁紓浣插亾闁割偆鍠撶粻楣冩煕閳╁厾顏呮叏閸愵亞纾奸悹鍥у级椤ユ粓鏌嶇憴鍕伌鐎规洘甯掗～婵嬵敇閻愬瓨鐣奸梻鍌欒兌缁垶骞愭ィ鍐ㄧ獥闁哄稁鍘奸拑鐔兼煏婵炲灝鍔楅柡鈧禒瀣厱闁斥晛鍟╃欢閬嶆煃瑜滈崜姘躲€冮崨绮光偓锕傛嚄椤栵絾些婵＄偑鍊栧ú锕傚矗閸愵喖鏄ラ柍?uuid闂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌ｉ幋锝呅撻柛濠傛健閺屻劑寮崼鐔告闂佺顑嗛幐鍓у垝椤撶偐妲堟俊顖濐嚙濞呇囨⒑濞茶骞楅柣鐔叉櫊瀵鎮㈤崨濠勭Ф婵°倧绲介崯顖烆敁瀹ュ鈷戠紒瀣仢椤掋垽鏌＄仦璇插闁糕晝鍋ら獮瀣晝閳ь剛澹曢崗鑲╃瘈濠电姴鍊归幑锝夋煕閺冣偓閹倸顫忛搹瑙勫珰闁肩⒈鍓涢鎴濃攽閻愬弶鍣烘繛鍙夘焽缁碍娼忛妸褏鐦堥梺鎼炲劥閸╂牠寮查鈧埞鎴︽偐缂佹ɑ閿┑鈽嗗亝椤ㄥ﹪鐛崱娑欏€烽柣鎴炃氶幏娲倵鐟欏嫭绀€婵炶绠撳畷鐢稿焵椤掑倻纾藉ù锝呭濡叉椽鏌℃担瑙勫€愭鐐村灴婵偓闁绘﹩鍋呴弬鈧梺璇插嚱缂嶅棝宕戦幒鎳虫盯宕橀妸銏℃杸闂佺粯鍔栧娆撴倶閿曞倹鐓熼柣鏇氱閻忕娀鎽堕悙鐑樼叆闁绘柨鎼牎闂佹娊鏀遍崹鍧楀蓟濞戞ǚ鏀介柛鈩冾殢娴犳儳顪冮妶鍐ㄥ姢闁稿鍠撳Σ鎰板箳濡ゅ﹥鏅╅梺鍏肩ゴ閺呮繈宕濋鐐粹拺缂侇垱娲橀弶娲煕鎼淬垹绲绘い鏇樺劦瀹曠喖顢涘杈╂澑婵＄偑鍊栫敮鎺楀磻閸℃稑鐤悗锝庡亗缁诲棝鏌ｉ幇鍏哥盎闁逞屽墯閻楃娀骞冭铻栭柛娑卞枛閸撶懓顪冮妶鍡樷拻闁哄拋鍋婇幃鐢稿醇閺囩喓鍘搁梺鎼炲劘閸庨亶鎮橀鍫熺厓?    return f"u{user_id}_{_uuid.uuid4().hex[:12]}"


@app.post("/api/checkins/items")
def create_custom_checkin_item(payload: CheckinItemCreatePayload,
                                current_user: User = Depends(get_current_user),
                                db: Session = Depends(get_db)):
    _ensure_database_tables()
    _ensure_checkin_schema_migrated(db)
    name = (payload.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="打卡项名称不能为空")
    if len(name) > 30:
        raise HTTPException(status_code=400, detail="打卡项名称不能超过 30 个字符")
    points = max(0, min(int(payload.points or 0), 200))
    icon = (payload.icon or "activity").strip()[:32] or "activity"
    icon_bg = (payload.icon_bg or "#eaf4cc").strip()[:20] or "#eaf4cc"
    category = (payload.category or "custom").strip()[:50] or "custom"

    code = _generate_user_item_code(db, current_user.id, name)

    # sort_order 缂傚倸鍊搁崐鎼佸磹閹间礁纾归柟闂寸绾惧綊鏌熼梻瀵割槮缁炬儳缍婇弻锝夊箣閿濆憛鎾绘煕婵犲倹鍋ラ柡灞诲姂瀵挳鎮欏ù瀣壕鐟滅増甯掔壕鍨攽閻樺弶澶勯柍閿嬪灦閵囧嫰骞掗崱妞惧缂傚倷绀侀ˇ浼村箰閹惰姤鍋樻い鏇楀亾濠殿喒鍋撻梺鎸庣☉鐎氼噣顢欓弴銏♀拺缂侇垱娲栨晶鏌ユ煏閸℃ê绗掓い顐ｇ箘閹瑰嫰鎼归惂鍝ュ耿闂傚倷娴囬～澶婄暦濮椻偓椤㈡俺顦归柟顔惧仱瀹曞綊顢曢悩杈╃泿闂備礁婀遍崕銈夊垂閻㈠壊鏁傛い蹇撶墛閻撳啴姊洪崹顕呭剰闁诲繐鐡ㄩ幈銊︾節閸屻倗鍚嬮悗瑙勬礈閸樠囷綖濠靛鏁囬柣鏂挎惈缂嶁偓缂傚倸鍊搁崐椋庢媼閺屻儱纾婚柟鍓х帛閸婄敻鏌ㄥ┑鍡涱€楅柡瀣枛閺岋綁骞樼€涙顦伴梺鍝勭焿缁绘繂鐣烽幒鎴旀斀闁搞儮鏂傞埀顒€锕幃妤€鈽夊▎鎴犵杽濠殿喖锕ュ钘壩涢崘銊㈡婵炲棙蓱閿涗線姊哄Ч鍥х労闁割煈浜崺鈧い鎺嗗亾缁剧虎鍙冨鎶芥晝閳ь剟鍩為幋锕€纾兼繝濠傛捣閸旀悂姊洪崨濠庢畷濠电偛锕濠氭晲婢跺﹦鐤€濡炪倖鏌ㄦ晶浠嬎囨导瀛樷拺闁告繂瀚崒銊╂煕閵婏附銇濋柟顔光偓鏂ユ瀻闁规儳顕崢浠嬫⒑鐟欏嫬绀冩繛鍛礋楠炴垿鏁愭径瀣幍闂佷紮绲介懟顖氭毄缂傚倷娴囨ご鍝ユ暜閻愬搫绠柣妯款嚙缁犵敻鏌熼悜妯肩畱缂佽鲸姊规穱濠囨倷椤忓嫧鍋撻幋锕€鍨傞柛婵嗗珋濞戙垹绀冮柕濞у嫭顔曢梻浣芥硶閸犳挻鎱ㄩ悽绋跨９濠电姵纰嶉悡鏇㈡煃閳轰礁骞樻い蹇撶墛閸庡﹥绻濇繝鍌滃闁抽攱甯掗妴鎺戭潩閿濆懍澹曟繝鐢靛仒閸栫娀宕堕妸銉ょ綍闂備胶纭堕崜婵嬫憘鐎ｎ喖鐐婃い鎺戭槹閺呮繈姊洪幐搴㈢５闁稿鎸鹃惀顏堝箚瑜滈悡濂告煛瀹€鈧崰鏍箖濞嗘搩鏁嗗ù锝堟〃缁辨ɑ绻濈喊妯哄⒉鐟滄澘鍟撮幃褎绻濋崶褏鐤勯梺闈涱焾閸庢瑩鎮㈤崗鐓庝簵闁硅偐琛ラ埀顒冨皺鐢盯姊婚崒娆愮グ妞ゆ洘鐗犲畷浼村冀椤撶喎浜遍梺鍦亾閸撴艾顭囬弽顐ょ＝濞达綀鍋傞幋鐐插灁闁圭虎鍠楅悡鏇熺箾閹寸儑鍏€规悶鍎甸弻锟犲幢濡ゅ啫鈪靛┑顔硷攻濡炰粙骞婇弽顓炵厸闁稿本纰嶉悾顒€鈹戦悩顐ｅ闁稿本绮庨悡鎾绘⒑閻熸壆锛嶉柛瀣ㄥ€曢悾鐑芥晸閻樺啿鈧墎绱撴担鑲℃垿濡靛┑鍥ヤ簻闁靛繆鍓濋ˉ鍫⑩偓瑙勬礈閸犳牠銆佸鈧幃娆忊枔?    max_sort = db.query(HealthCheckinItem).order_by(HealthCheckinItem.sort_order.desc()).first()
    max_sort = db.query(HealthCheckinItem).order_by(HealthCheckinItem.sort_order.desc()).first()
    next_sort = (max_sort.sort_order if max_sort else 0) + 1

    item = HealthCheckinItem(
        code=code, name=name, icon=icon, icon_bg=icon_bg, category=category,
        points=points, sort_order=next_sort, is_active=True,
        owner_user_id=current_user.id,
    )
    db.add(item)
    db.commit()
    return {
        "message": "打卡项已创建",
        "item": {
            "code": item.code, "name": item.name, "icon": item.icon, "icon_bg": item.icon_bg,
            "category": item.category, "points": item.points,
            "is_custom": True,
        },
        "today": _build_today_checkin_payload(db, current_user.id, date.today()),
    }


@app.put("/api/checkins/items/{item_code}")
def update_custom_checkin_item(item_code: str, payload: CheckinItemUpdatePayload,
                                current_user: User = Depends(get_current_user),
                                db: Session = Depends(get_db)):
    item = db.query(HealthCheckinItem).filter(HealthCheckinItem.code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="打卡项不存在")
    if item.owner_user_id is None:
        raise HTTPException(status_code=403, detail="系统默认打卡项不能编辑")
    if item.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="不能编辑其他用户的打卡项")

    if payload.name is not None:
        n = payload.name.strip()
        if not n:
            raise HTTPException(status_code=400, detail="打卡项名称不能为空")
        if len(n) > 30:
            raise HTTPException(status_code=400, detail="打卡项名称不能超过 30 个字符")
        item.name = n
    if payload.icon is not None:
        item.icon = payload.icon.strip()[:32] or "activity"
    if payload.icon_bg is not None:
        item.icon_bg = payload.icon_bg.strip()[:20] or "#eaf4cc"
    if payload.category is not None:
        item.category = payload.category.strip()[:50] or "custom"
    if payload.points is not None:
        item.points = max(0, min(int(payload.points), 200))
    if payload.is_active is not None:
        item.is_active = bool(payload.is_active)
    db.commit()
    return {
        "message": "打卡项已更新",
        "item": {
            "code": item.code, "name": item.name, "icon": item.icon, "icon_bg": item.icon_bg,
            "category": item.category, "points": item.points,
            "is_active": item.is_active, "is_custom": True,
        },
        "today": _build_today_checkin_payload(db, current_user.id, date.today()),
    }


@app.delete("/api/checkins/items/{item_code}")
def delete_custom_checkin_item(item_code: str,
                                current_user: User = Depends(get_current_user),
                                db: Session = Depends(get_db)):
    item = db.query(HealthCheckinItem).filter(HealthCheckinItem.code == item_code).first()
    if not item:
        raise HTTPException(status_code=404, detail="打卡项不存在")
    if item.owner_user_id is None:
        raise HTTPException(status_code=403, detail="系统默认打卡项不能删除")
    if item.owner_user_id != current_user.id:
        raise HTTPException(status_code=403, detail="不能删除其他用户的打卡项")

    db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == current_user.id,
        UserHealthCheckin.item_code == item_code,
    ).delete()
    db.delete(item)
    db.commit()
    return {
        "message": "打卡项已删除",
        "item_code": item_code,
        "today": _build_today_checkin_payload(db, current_user.id, date.today()),
    }


@app.get("/api/checkins/items")
def list_checkin_items(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_database_tables()
    _ensure_checkin_schema_migrated(db)
    _purge_system_checkin_items(db)
    today = date.today()
    start_31 = today - timedelta(days=30)
    items = db.query(HealthCheckinItem).filter(
        HealthCheckinItem.is_active == True,
        HealthCheckinItem.owner_user_id == current_user.id,
    ).order_by(HealthCheckinItem.sort_order.asc(), HealthCheckinItem.id.asc()).all()
    records = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == current_user.id,
        UserHealthCheckin.checkin_date >= start_31,
        UserHealthCheckin.checkin_date <= today,
    ).all()
    records_by_code = {}
    for record in records:
        records_by_code.setdefault(record.item_code, {})[record.checkin_date] = record
    result = [
        _build_checkin_item_view(item, records_by_code.get(item.code, {}), today)
        for item in items
    ]
    return {"items": result, "today": today.isoformat()}


@app.get("/api/checkins/history/{item_code}")
def get_checkin_history(item_code: str,
                        from_date: Optional[str] = Query(None, alias="from"),
                        to_date: Optional[str] = Query(None, alias="to"),
                        current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    item = db.query(HealthCheckinItem).filter(
        HealthCheckinItem.code == item_code,
        HealthCheckinItem.is_active == True,
        HealthCheckinItem.owner_user_id == current_user.id,
    ).first()
    if not item:
        raise HTTPException(status_code=404, detail="打卡项不存在")
    today = date.today()
    end = _parse_date_or_today(to_date) if to_date else today
    start = _parse_date_or_today(from_date) if from_date else end - timedelta(days=30)
    if start > end:
        raise HTTPException(status_code=400, detail="开始日期不能晚于结束日期")
    rows = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == current_user.id,
        UserHealthCheckin.item_code == item_code,
        UserHealthCheckin.checkin_date >= start,
        UserHealthCheckin.checkin_date <= end,
    ).order_by(UserHealthCheckin.checkin_date.asc()).all()
    return {
        "item_code": item_code,
        "from": start.isoformat(),
        "to": end.isoformat(),
        "records": [
            {"date": r.checkin_date.isoformat(), "status": r.status, "points": r.points_earned, "value": r.value_json}
            for r in rows
        ],
    }


@app.get("/api/checkins/stats/{item_code}")
def get_checkin_stats(item_code: str,
                      current_user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    today = date.today()
    start = today - timedelta(days=30)
    rows = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == current_user.id,
        UserHealthCheckin.item_code == item_code,
        UserHealthCheckin.checkin_date >= start,
    ).all()
    done = sum(1 for r in rows if r.status == "done")
    points = sum((r.points_earned or 0) for r in rows)
    return {"item_code": item_code, "days": 31, "done_days": done, "points": points}


@app.get("/api/checkins/summary")
def get_checkin_summary(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    today = date.today()
    today_payload = _build_today_checkin_payload(db, current_user.id, today)
    rows = db.query(UserHealthCheckin).filter(
        UserHealthCheckin.user_id == current_user.id,
        UserHealthCheckin.checkin_date == today,
    ).all()
    return {
        "today": today.isoformat(),
        "completed_count": sum(1 for r in rows if r.status == "done"),
        "points": sum((r.points_earned or 0) for r in rows),
        "today_payload": today_payload,
    }


@app.get("/api/home/dashboard")
def get_home_dashboard(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return _build_home_dashboard(db, current_user)

@app.post("/api/sessions")
def create_session(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_chat_schema_migrated(db)
    new_session = ChatSession(user_id=current_user.id, title="新的健康咨询")
    db.add(new_session)
    db.commit()
    db.refresh(new_session)
    return {"id": new_session.id, "title": new_session.title}


@app.get("/api/sessions")
def get_sessions(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _ensure_chat_schema_migrated(db)
    sessions = db.query(ChatSession).filter(ChatSession.user_id == current_user.id).order_by(
        ChatSession.updated_at.desc()).all()
    return [{"id": str(s.id), "title": s.title, "date": s.updated_at.strftime("%Y-%m-%d %H:%M")} for s in sessions]


def _message_image_payload(db: Session, message: ChatMessage, user_id: int) -> tuple[Optional[str], Optional[int]]:
    file_id = getattr(message, "uploaded_file_id", None)
    if file_id:
        uploaded = db.query(UploadedFile).filter(
            UploadedFile.id == file_id,
            UploadedFile.owner_user_id == user_id,
            UploadedFile.deleted_at.is_(None),
        ).first()
        return _presigned_file_url(uploaded), file_id
    if message.image_url and message.image_url.startswith("file:"):
        try:
            legacy_file_id = int(message.image_url.split(":", 1)[1])
        except (ValueError, IndexError):
            return None, None
        uploaded = db.query(UploadedFile).filter(
            UploadedFile.id == legacy_file_id,
            UploadedFile.owner_user_id == user_id,
            UploadedFile.deleted_at.is_(None),
        ).first()
        return _presigned_file_url(uploaded), legacy_file_id
    return message.image_url, None


@app.get("/api/sessions/{session_id}/messages")
def get_session_messages(session_id: int, current_user: User = Depends(get_current_user),
                         db: Session = Depends(get_db)):
    _ensure_chat_schema_migrated(db)
    session = db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id).first()
    if not session:
        raise HTTPException(status_code=404, detail="?????")
    messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(
        ChatMessage.created_at.asc()).all()
    payload = []
    for m in messages:
        image_url, file_id = _message_image_payload(db, m, current_user.id)
        payload.append({
            "role": m.role,
            "content": m.content,
            "image": image_url,
            "image_file_id": file_id,
            "run_id": getattr(m, "run_id", None),
            "created_at": m.created_at.isoformat() if m.created_at else None,
            "meta_data": m.meta_data,
        })
    return payload


async def generate_semantic_title(query: str, session_id: int):
    title = (query or "新的健康咨询").strip().replace("\n", " ")[:24] or "新的健康咨询"

    def _update_title():
        from core.database import SessionLocal
        from core.models import ChatSession
        db = SessionLocal()
        try:
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session:
                session.title = title
                db.commit()
        finally:
            db.close()

    await asyncio.to_thread(_update_title)
    return title


def _validate_rag_citations(answer: str, trace_data: dict) -> tuple[str, dict]:
    """Remove forged [E#] markers that are outside the evidence range."""
    if not isinstance(trace_data, dict):
        return answer, {}
    rag_trace = trace_data.get("rag") or {}
    if not isinstance(rag_trace, dict):
        return answer, {}
    try:
        evidence_count = int(rag_trace.get("evidence_count") or 0)
    except (TypeError, ValueError):
        evidence_count = 0
    if evidence_count <= 0:
        return answer, {}

    cited = [int(match.group(1)) for match in _re.finditer(r"\[E(\d+)\]", answer or "")]
    invalid = sorted({idx for idx in cited if idx < 1 or idx > evidence_count})
    if not cited:
        return answer, {
            "checked": True,
            "valid_evidence_count": evidence_count,
            "cited": [],
            "invalid": [],
            "removed_invalid": 0,
        }

    def _replace(match):
        idx = int(match.group(1))
        return "" if idx in invalid else match.group(0)

    cleaned_answer = _re.sub(r"\[E(\d+)\]", _replace, answer or "")
    return cleaned_answer, {
        "checked": True,
        "valid_evidence_count": evidence_count,
        "cited": cited,
        "invalid": invalid,
        "removed_invalid": len(invalid),
    }


def _trace_list(value: Any) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, str):
        return [value] if value.strip() else []
    return [value]


def _trace_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _infer_trace_agents(audit_log: list) -> list[str]:
    agents: list[str] = []
    for item in audit_log:
        if not isinstance(item, str):
            continue
        match = _re.match(r"^\[([^\]]+)\]", item.strip())
        agent = match.group(1).split("/", 1)[0].split("-", 1)[0].strip() if match else ""
        if agent and agent not in agents:
            agents.append(agent)
    return agents


def _normalize_trace_data(trace_data: Any, result: dict, route: str) -> dict:
    """Keep legacy trace fields while adding one stable contract for the C-side panel."""
    trace = dict(trace_data) if isinstance(trace_data, dict) else {}

    audit_log = _trace_list(trace.get("audit_log") or trace.get("agent_audit_log") or result.get("agent_audit_log") or result.get("audit_log"))
    if audit_log:
        trace["audit_log"] = audit_log

    scratchpad = _trace_list(trace.get("internal_scratchpad") or result.get("internal_scratchpad"))
    if scratchpad:
        trace["internal_scratchpad"] = scratchpad

    rag = _trace_dict(trace.get("rag"))
    rag_items = _trace_list(rag.get("items"))
    evidence_count = sum(1 for item in rag_items if not isinstance(item, dict) or item.get("role") != "background")
    background_count = sum(1 for item in rag_items if isinstance(item, dict) and item.get("role") == "background")
    rag.setdefault("items", rag_items)
    rag["evidence_count"] = int(rag.get("evidence_count") or evidence_count or 0)
    rag["background_count"] = int(rag.get("background_count") or background_count or 0)
    trace["rag"] = rag

    kg = _trace_dict(trace.get("kg"))
    kg_paths = _trace_list(kg.get("paths"))
    kg.setdefault("paths", kg_paths)
    kg["degraded"] = bool(kg.get("degraded", False))
    trace["kg"] = kg

    rumor = _trace_dict(trace.get("rumor"))
    rumor["scout_data"] = _trace_list(rumor.get("scout_data") or trace.get("scout_data"))
    rumor["medical_data"] = _trace_list(rumor.get("medical_data") or trace.get("medical_data"))
    rumor["critic_reasoning"] = rumor.get("critic_reasoning") or trace.get("critic_reasoning") or ""
    rumor["rumor_events"] = _trace_list(rumor.get("rumor_events") or trace.get("rumor_events"))
    rumor["verdict"] = rumor.get("verdict") or trace.get("verdict") or ""
    rumor["risk_level"] = rumor.get("risk_level") or trace.get("risk_level") or ""
    trace["rumor"] = rumor

    supplemental = _trace_dict(trace.get("supplemental"))
    legacy_sources = _trace_list(trace.get("sources"))
    existing_supplemental_sources = _trace_list(supplemental.get("sources"))
    supplemental["sources"] = existing_supplemental_sources or legacy_sources
    supplemental["notes"] = _trace_list(supplemental.get("notes"))
    if trace.get("maddx_debate") and "maddx_debate" not in supplemental:
        supplemental["maddx_debate"] = trace.get("maddx_debate")
    trace["supplemental"] = supplemental

    safety = _trace_dict(trace.get("safety_check"))
    hallucination = _trace_dict(trace.get("hallucination_check"))
    degraded = bool(safety.get("degraded") or safety.get("timeout") or hallucination.get("degraded") or hallucination.get("timeout"))
    blocked = str(safety.get("action") or hallucination.get("action") or "").lower() in {"block", "blocked", "reject"}
    safety_status = "blocked" if blocked else ("degraded" if degraded else ("warning" if safety or hallucination else "passed"))
    trace["safety_check"] = safety

    summary = _trace_dict(trace.get("trace_summary"))
    summary.setdefault("route", route or trace.get("route") or "")
    summary.setdefault("collab_mode", trace.get("collab_mode") or "")
    summary.setdefault("agents", _infer_trace_agents(audit_log))
    summary.setdefault("milvus_evidence_count", rag["evidence_count"])
    summary.setdefault("kg_constraint_count", len(kg_paths))
    summary.setdefault("external_source_count", len(rumor["scout_data"]))
    summary.setdefault("safety_status", safety_status)
    trace["trace_summary"] = summary

    return trace


def _qa_review_domain_from_route(route: str) -> str:
    normalized = (route or "").lower()
    if "medication" in normalized or "drug" in normalized:
        return "medication"
    if "symptom" in normalized or "diagnosis" in normalized:
        return "symptom"
    if "rumor" in normalized or "verification" in normalized:
        return "rumor"
    if "report" in normalized:
        return "report"
    return "general"


def _qa_review_extract_evidence_refs(trace_data: dict) -> list[dict]:
    rag = _trace_dict(trace_data.get("rag"))
    refs: list[dict] = []
    for item in _trace_list(rag.get("items")):
        if not isinstance(item, dict):
            continue
        card = item.get("knowledge_card") if isinstance(item.get("knowledge_card"), dict) else {}
        refs.append({
            "role": item.get("role") or "evidence",
            "chunk_id": item.get("chunk_id"),
            "doc_id": item.get("doc_id"),
            "source_type": item.get("source_type"),
            "source_tier": item.get("source_tier"),
            "title": item.get("title") or item.get("source_title") or card.get("card_title"),
            "section_path": item.get("section_path"),
            "locator": item.get("locator"),
            "knowledge_card": card,
            "reranker_prob": item.get("reranker_prob"),
            "rrf_score": item.get("rrf_score"),
        })
    return refs


def _qa_review_extract_external_sources(trace_data: dict) -> list:
    rumor = _trace_dict(trace_data.get("rumor"))
    supplemental = _trace_dict(trace_data.get("supplemental"))
    sources = []
    sources.extend(_trace_list(rumor.get("scout_data")))
    sources.extend(_trace_list(rumor.get("medical_data")))
    sources.extend(_trace_list(supplemental.get("sources")))
    return sources


def _qa_review_candidate_has_rag(candidate: QaReviewCandidate) -> bool:
    return bool(candidate.evidence_refs)


def _looks_like_private_qa(text_value: str) -> bool:
    text_value = text_value or ""
    private_patterns = [
        r"\b1[3-9]\d{9}\b",
        r"\b\d{17}[\dXx]\b",
        r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
        r"(我|本人|我妈|我爸|我女儿|我儿子|老婆|丈夫).{0,30}(血糖|血压|尿酸|症状|病|过敏|怀孕|月经|用药|体检|报告)",
    ]
    return any(_re.search(pattern, text_value) for pattern in private_patterns)


def _serialize_qa_candidate(candidate: QaReviewCandidate, detail: bool = False) -> dict:
    payload = {
        "id": candidate.id,
        "status": candidate.status,
        "decision": candidate.decision,
        "domain": candidate.domain,
        "route": candidate.route,
        "intent": candidate.intent,
        "sub_intent": candidate.sub_intent,
        "safety_status": candidate.safety_status,
        "question": candidate.question,
        "answer": candidate.answer if detail else (candidate.answer or "")[:260],
        "corrected_answer": candidate.corrected_answer,
        "reviewer_note": candidate.reviewer_note,
        "feedback_tags": candidate.feedback_tags or [],
        "reusable_scope": candidate.reusable_scope,
        "quality_score": candidate.quality_score,
        "user_id": candidate.user_id,
        "session_id": candidate.session_id,
        "user_message_id": candidate.user_message_id,
        "ai_message_id": candidate.ai_message_id,
        "run_id": candidate.run_id,
        "trace_summary": candidate.trace_summary or {},
        "evidence_count": len(candidate.evidence_refs or []),
        "kg_constraint_count": len(candidate.kg_constraints or []),
        "external_source_count": len(candidate.external_sources or []),
        "reviewed_by": candidate.reviewed_by,
        "reviewed_at": candidate.reviewed_at.isoformat() if candidate.reviewed_at else None,
        "promoted_insight_id": candidate.promoted_insight_id,
        "created_at": candidate.created_at.isoformat() if candidate.created_at else None,
        "updated_at": candidate.updated_at.isoformat() if candidate.updated_at else None,
    }
    if detail:
        payload.update({
            "evidence_refs": candidate.evidence_refs or [],
            "kg_constraints": candidate.kg_constraints or [],
            "external_sources": candidate.external_sources or [],
            "hallucination_status": candidate.hallucination_status or {},
            "meta_data": candidate.meta_data or {},
        })
    return payload


def _require_qa_review_admin(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    token = request.headers.get("X-QA-Review-Token") or request.headers.get("X-Admin-Service-Token")
    if token and token == QA_REVIEW_ADMIN_TOKEN:
        return None

    auth = request.headers.get("Authorization") or ""
    if auth.lower().startswith("bearer "):
        raw_token = auth.split(" ", 1)[1].strip()
        try:
            payload = jwt.decode(raw_token, SECRET_KEY, algorithms=[ALGORITHM])
            username = payload.get("sub")
            if username:
                user = db.query(User).filter(User.username == username).first()
                if user and getattr(user, "role", "") in {"admin", "super_admin", "rag_admin", "content_admin"}:
                    return user
        except JWTError:
            pass
    raise HTTPException(status_code=403, detail="QA review admin token required")


async def _create_qa_review_candidate_async(
    *,
    initial_state: dict,
    session_id: int,
    run_id: str,
    route: str,
    final_answer: str,
    trace_data: dict,
    result: dict,
) -> None:
    if not QA_REVIEW_ENABLED:
        return
    if QA_REVIEW_CAPTURE_SCOPE not in {"all", "answers"}:
        return

    def _write_candidate():
        db = SessionLocal()
        try:
            existing = db.query(QaReviewCandidate).filter(QaReviewCandidate.run_id == run_id).first()
            if existing:
                return
            run = db.query(ChatRun).filter(ChatRun.run_id == run_id).first()
            summary = _trace_dict(trace_data.get("trace_summary"))
            rag_refs = _qa_review_extract_evidence_refs(trace_data)
            kg = _trace_dict(trace_data.get("kg"))
            halluc = _trace_dict(trace_data.get("hallucination_check") or trace_data.get("safety_check"))
            candidate = QaReviewCandidate(
                status="pending",
                domain=_qa_review_domain_from_route(route),
                route=route or "",
                intent=str(result.get("intent") or result.get("main_intent") or ""),
                sub_intent=str(result.get("sub_intent") or ""),
                safety_status=str(summary.get("safety_status") or "passed"),
                question=str(initial_state.get("query") or ""),
                answer=final_answer or "",
                user_id=initial_state.get("user_id"),
                session_id=session_id,
                user_message_id=getattr(run, "user_message_id", None),
                ai_message_id=getattr(run, "ai_message_id", None),
                run_id=run_id,
                trace_summary=summary,
                evidence_refs=rag_refs,
                kg_constraints=_trace_list(kg.get("paths")),
                external_sources=_qa_review_extract_external_sources(trace_data),
                hallucination_status=halluc,
                meta_data={
                    "route": route,
                    "options": result.get("options") or [],
                    "has_rag": bool(rag_refs),
                    "trace_rag": _trace_dict(trace_data.get("rag")).get("trace"),
                },
            )
            db.add(candidate)
            db.commit()
            logger.info(f"[QAReview] captured candidate id={candidate.id} run_id={run_id}")
        except Exception as e:
            db.rollback()
            logger.warning(f"[QAReview] capture failed run_id={run_id}: {e}")
        finally:
            db.close()

    await asyncio.to_thread(_write_candidate)


@app.get("/api/admin/qa-review/candidates")
def list_qa_review_candidates(
    status_filter: Optional[str] = Query(None, alias="status"),
    domain: Optional[str] = None,
    safety_status: Optional[str] = None,
    has_rag: Optional[bool] = None,
    q: Optional[str] = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    _: Optional[User] = Depends(_require_qa_review_admin),
    db: Session = Depends(get_db),
):
    query = db.query(QaReviewCandidate)
    if status_filter:
        query = query.filter(QaReviewCandidate.status == status_filter)
    if domain:
        query = query.filter(QaReviewCandidate.domain == domain)
    if safety_status:
        query = query.filter(QaReviewCandidate.safety_status == safety_status)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(or_(QaReviewCandidate.question.like(like), QaReviewCandidate.answer.like(like)))
    rows = query.order_by(QaReviewCandidate.created_at.desc()).all()
    if has_rag is not None:
        rows = [row for row in rows if _qa_review_candidate_has_rag(row) == has_rag]
    total = len(rows)
    start = (page - 1) * page_size
    page_rows = rows[start:start + page_size]
    return {
        "items": [_serialize_qa_candidate(row) for row in page_rows],
        "total": total,
        "page": page,
        "page_size": page_size,
    }


@app.get("/api/admin/qa-review/candidates/{candidate_id}")
def get_qa_review_candidate(
    candidate_id: int,
    _: Optional[User] = Depends(_require_qa_review_admin),
    db: Session = Depends(get_db),
):
    candidate = db.query(QaReviewCandidate).filter(QaReviewCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="QA review candidate not found")
    return {"item": _serialize_qa_candidate(candidate, detail=True)}


@app.post("/api/admin/qa-review/candidates/{candidate_id}/decision")
async def decide_qa_review_candidate(
    candidate_id: int,
    payload: QaReviewDecisionPayload,
    reviewer: Optional[User] = Depends(_require_qa_review_admin),
    db: Session = Depends(get_db),
):
    candidate = db.query(QaReviewCandidate).filter(QaReviewCandidate.id == candidate_id).first()
    if not candidate:
        raise HTTPException(status_code=404, detail="QA review candidate not found")

    decision = (payload.decision or "").strip().lower()
    allowed = {"approve_success", "success", "approve", "approve_failure", "failure", "reject", "needs_fix"}
    if decision not in allowed:
        raise HTTPException(status_code=400, detail="invalid QA review decision")

    reusable_scope = (payload.reusable_scope or "shared").strip().lower()
    if reusable_scope not in {"shared", "personal"}:
        raise HTTPException(status_code=400, detail="reusable_scope must be shared or personal")

    corrected = (payload.corrected_answer or "").strip()
    note = (payload.reviewer_note or "").strip()
    promoted_id = None

    should_promote = decision in {"approve_success", "success", "approve", "approve_failure", "failure"}
    polarity = "FAILURE" if decision in {"approve_failure", "failure"} else "SUCCESS"
    if should_promote:
        text_for_privacy = f"{candidate.question}\n{corrected or candidate.answer}"
        if reusable_scope == "shared" and _looks_like_private_qa(text_for_privacy):
            raise HTTPException(status_code=400, detail="疑似包含个人健康信息，不能直接进入共享经验库")
        from core.insight_memory import add_insight
        summary_source = corrected or note or candidate.answer
        promoted_id = await add_insight(
            domain=candidate.domain,
            query=candidate.question,
            user_id=candidate.user_id if reusable_scope == "personal" else None,
            is_personal=(reusable_scope == "personal"),
            agent_path=candidate.route or candidate.domain,
            final_answer=candidate.answer,
            answer_summary=summary_source[:500],
            evidence_count=len(candidate.evidence_refs or []),
            hallucination_score=0.0 if polarity == "SUCCESS" else 0.8,
            confidence=float(payload.quality_score if payload.quality_score is not None else (0.8 if polarity == "SUCCESS" else 0.7)),
            polarity=polarity,
            tags=payload.feedback_tags or [],
            failure_analysis=note if polarity == "FAILURE" else "",
            suggested_fix=corrected if polarity == "FAILURE" else "",
            auto_classify_privacy=False,
        )

    if decision in {"reject"}:
        candidate.status = "rejected"
    elif decision == "needs_fix":
        candidate.status = "needs_fix"
    else:
        candidate.status = "approved"
    candidate.decision = decision
    candidate.corrected_answer = corrected or None
    candidate.reviewer_note = note or None
    candidate.feedback_tags = payload.feedback_tags or []
    candidate.reusable_scope = reusable_scope
    candidate.quality_score = payload.quality_score
    candidate.reviewed_by = getattr(reviewer, "id", None)
    candidate.reviewed_at = _utcnow()
    candidate.promoted_insight_id = promoted_id
    db.commit()
    db.refresh(candidate)
    return {"item": _serialize_qa_candidate(candidate, detail=True)}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _cleanup_expired_chat_uploads(limit: int = 200) -> int:
    from core.database import SessionLocal
    db = SessionLocal()
    deleted = 0
    try:
        retention_days = int(os.getenv("CHAT_UPLOAD_RETENTION_DAYS", "30"))
        cutoff = _utcnow() - timedelta(days=retention_days)
        rows = db.query(UploadedFile).filter(
            UploadedFile.purpose == "chat_image",
            UploadedFile.deleted_at.is_(None),
            UploadedFile.created_at < cutoff,
        ).limit(limit).all()
        storage = get_storage_service()
        for row in rows:
            try:
                storage.delete_object(row.storage_key, bucket=row.storage_bucket)
            except Exception as e:
                logger.warning(f"[Storage] failed to delete expired file {row.id}: {e}")
            row.deleted_at = _utcnow()
            deleted += 1
        if deleted:
            db.commit()
    except Exception as e:
        db.rollback()
        logger.warning(f"[Storage] upload cleanup skipped: {e}")
    finally:
        db.close()
    return deleted


async def _chat_upload_cleanup_loop():
    interval = int(os.getenv("CHAT_UPLOAD_CLEANUP_INTERVAL_SECONDS", "86400"))
    while True:
        await asyncio.to_thread(_cleanup_expired_chat_uploads)
        await asyncio.sleep(max(3600, interval))


def _is_stale_chat_run(run: Optional[ChatRun]) -> bool:
    if not run or run.status != "running":
        return True
    started = run.started_at or run.created_at
    if not started:
        return False
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    stale_minutes = int(os.getenv("CHAT_RUN_STALE_MINUTES", "30"))
    return (_utcnow() - started).total_seconds() > stale_minutes * 60


def _mark_chat_run_failed(run_id: str, session_id: int, error: str):
    from core.database import SessionLocal
    db = SessionLocal()
    try:
        run = db.query(ChatRun).filter(ChatRun.run_id == run_id).first()
        if run:
            run.status = "failed"
            run.error = (error or "chat run failed")[:4000]
            run.finished_at = _utcnow()
        session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
        if session and session.active_run_id == run_id:
            session.active_run_id = None
        db.commit()
    except Exception:
        db.rollback()
    finally:
        db.close()


def _resolve_chat_image_reference(
    db: Session,
    *,
    current_user: User,
    session_id: int,
    run_id: str,
    raw_image: Any,
) -> tuple[Optional[str], Optional[int], Optional[UploadedFile]]:
    if raw_image is None:
        return None, None, None
    if isinstance(raw_image, int) or (isinstance(raw_image, str) and raw_image.strip().isdigit()):
        file_id = int(raw_image)
    elif isinstance(raw_image, str) and raw_image.startswith("file:"):
        file_id = int(raw_image.split(":", 1)[1])
    elif isinstance(raw_image, str) and raw_image.startswith("chat-uploads/"):
        key = raw_image[len("chat-uploads/"):]
        uploaded = db.query(UploadedFile).filter(
            UploadedFile.owner_user_id == current_user.id,
            UploadedFile.storage_bucket == "chat-uploads",
            UploadedFile.storage_key == key,
            UploadedFile.deleted_at.is_(None),
        ).first()
        if not uploaded:
            raise HTTPException(status_code=404, detail="uploaded file not found")
        file_id = uploaded.id
    elif isinstance(raw_image, str) and raw_image.startswith("/static/uploads/"):
        return raw_image, None, None
    elif isinstance(raw_image, str) and raw_image.startswith("data:image/"):
        uploaded = _store_validated_chat_image(
            db,
            owner_user_id=current_user.id,
            raw_image=raw_image,
            session_id=session_id,
            run_id=run_id,
            commit=False,
        )
        return f"file:{uploaded.id}", uploaded.id, uploaded
    else:
        return None, None, None

    uploaded = db.query(UploadedFile).filter(
        UploadedFile.id == file_id,
        UploadedFile.owner_user_id == current_user.id,
        UploadedFile.deleted_at.is_(None),
    ).first()
    if not uploaded:
        raise HTTPException(status_code=404, detail="uploaded file not found")
    if uploaded.session_id not in (None, session_id):
        raise HTTPException(status_code=403, detail="uploaded file belongs to another session")
    uploaded.session_id = session_id
    uploaded.run_id = run_id
    return f"file:{uploaded.id}", uploaded.id, uploaded


async def _chat_sse_generator(initial_state: dict, session_id: int, run_id: str):
    def _sse(payload: dict) -> str:
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

    event_queue: asyncio.Queue = asyncio.Queue()
    collected_events: list[dict] = []
    graph_task: Optional[asyncio.Task] = None
    finalize_task: Optional[asyncio.Task] = None

    async def _run_graph():
        queue_token = set_sse_queue(event_queue)
        collector_token = set_sse_collector(collected_events)
        try:
            return await app_graph.ainvoke(initial_state)
        finally:
            reset_sse_collector(collector_token)
            reset_sse_queue(queue_token)

    def _build_completion(result: dict) -> dict:
        full_answer = (
            result.get("final_answer")
            or result.get("answer")
            or result.get("response")
            or "Sorry, no answer was generated."
        )
        route = result.get("current_route") or result.get("route") or ""
        trace_data = result.get("trace_data") or {}
        if collected_events:
            maddx_events = [e for e in collected_events if e.get("type") == "maddx_step"]
            rumor_events = [e for e in collected_events if e.get("type") == "rumor_step"]
            if maddx_events and "maddx_events" not in trace_data:
                trace_data["maddx_events"] = maddx_events
            if rumor_events and "rumor_events" not in trace_data:
                trace_data["rumor_events"] = rumor_events

        audit_log = result.get("agent_audit_log") or result.get("audit_log") or []
        if audit_log and not trace_data.get("audit_log"):
            trace_data["audit_log"] = audit_log

        scratchpad = result.get("internal_scratchpad") or []
        if scratchpad and not trace_data.get("internal_scratchpad"):
            trace_data["internal_scratchpad"] = scratchpad

        trace_data = _normalize_trace_data(trace_data, result, route)

        full_answer, citation_check = _validate_rag_citations(full_answer, trace_data)
        if citation_check.get("checked"):
            trace_data["citation_check"] = citation_check

        current_slots = result.get("current_slots") or initial_state.get("current_slots") or {}
        options = result.get("options") or []
        is_finished = result.get("is_finished", True)
        turn_count = int(initial_state.get("turn_count") or 1) + 1
        return {
            "full_answer": full_answer,
            "route": route,
            "trace_data": trace_data,
            "current_slots": current_slots,
            "options": options,
            "is_finished": is_finished,
            "turn_count": turn_count,
        }

    def _save_completion(completion: dict) -> int:
        from core.database import SessionLocal
        from core.models import ChatMessage, ChatRun, ChatSession
        db = SessionLocal()
        try:
            run = db.query(ChatRun).filter(ChatRun.run_id == run_id).first()
            ai_message = None
            if run and run.ai_message_id:
                ai_message = db.query(ChatMessage).filter(ChatMessage.id == run.ai_message_id).first()
            if not ai_message:
                ai_message = db.query(ChatMessage).filter(
                    ChatMessage.session_id == session_id,
                    ChatMessage.run_id == run_id,
                    ChatMessage.role == "ai",
                ).order_by(ChatMessage.id.asc()).first()
            already_saved = ai_message is not None

            meta_data = {
                "route": completion["route"],
                "trace_data": completion["trace_data"],
                "current_slots": completion["current_slots"],
                "options": completion["options"],
                "is_finished": completion["is_finished"],
                "turn_count": completion["turn_count"],
                "run_id": run_id,
            }
            if ai_message:
                ai_message.content = completion["full_answer"]
                ai_message.meta_data = meta_data
            else:
                ai_message = ChatMessage(
                    session_id=session_id,
                    run_id=run_id,
                    role="ai",
                    content=completion["full_answer"],
                    meta_data=meta_data,
                )
                db.add(ai_message)
                db.flush()

            if run:
                run.status = "succeeded"
                run.error = None
                run.ai_message_id = ai_message.id
                run.finished_at = _utcnow()

            state_version = 0
            session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if session:
                state_version = int(session.state_version or 0)
                if session.active_run_id in (None, run_id):
                    session.current_slots = completion["current_slots"]
                    session.current_route = completion["route"] or ""
                    session.turn_count = completion["turn_count"]
                    if session.active_run_id == run_id:
                        session.active_run_id = None
                    if not already_saved:
                        session.state_version = state_version + 1
                        state_version = session.state_version
            db.commit()
            return state_version
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    async def _persist_completion(completion: dict, result: dict) -> int:
        state_version = await asyncio.to_thread(_save_completion, completion)
        if QA_REVIEW_ENABLED:
            asyncio.create_task(_create_qa_review_candidate_async(
                initial_state=initial_state,
                session_id=session_id,
                run_id=run_id,
                route=completion["route"],
                final_answer=completion["full_answer"],
                trace_data=completion["trace_data"],
                result=result,
            ))
        return state_version

    async def _finalize_completion(result: dict) -> tuple[dict, int]:
        completion = _build_completion(result)
        state_version = await _persist_completion(completion, result)
        return completion, state_version

    async def _complete_finalize_after_disconnect(task: asyncio.Task):
        try:
            await asyncio.shield(task)
            logger.info(f"[Chat] persisted disconnected run run_id={run_id}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Chat] disconnected run persistence failed run_id={run_id}: {e}")
            _mark_chat_run_failed(run_id, session_id, str(e))

    async def _complete_after_disconnect(task: asyncio.Task):
        try:
            result = await asyncio.shield(task)
            await _finalize_completion(result)
            logger.info(f"[Chat] completed disconnected run run_id={run_id}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[Chat] disconnected run failed run_id={run_id}: {e}")
            _mark_chat_run_failed(run_id, session_id, str(e))

    try:
        graph_task = asyncio.create_task(_run_graph())
        yield _sse({"type": "status", "message": "MAS graph started"})
        while not graph_task.done():
            try:
                event = await asyncio.wait_for(event_queue.get(), timeout=0.25)
                yield _sse(event)
            except asyncio.TimeoutError:
                continue

        while not event_queue.empty():
            yield _sse(await event_queue.get())

        result = await asyncio.shield(graph_task)
        completion = _build_completion(result)
        full_answer = completion["full_answer"]

        for start in range(0, len(full_answer), 96):
            yield _sse({"type": "chunk", "content": full_answer[start:start + 96]})

        finalize_task = asyncio.create_task(_persist_completion(completion, result))
        state_version = await asyncio.shield(finalize_task)
        done_payload = {
            "type": "done",
            "answer": full_answer,
            "route": completion["route"],
            "trace_data": completion["trace_data"],
            "slots": completion["current_slots"],
            "current_slots": completion["current_slots"],
            "options": completion["options"],
            "is_finished": completion["is_finished"],
            "turn_count": completion["turn_count"],
            "run_id": run_id,
            "state_version": state_version,
        }
        yield _sse(done_payload)
    except asyncio.CancelledError:
        if finalize_task is not None:
            _track_background_chat_task(asyncio.create_task(_complete_finalize_after_disconnect(finalize_task)))
        elif graph_task is not None:
            _track_background_chat_task(asyncio.create_task(_complete_after_disconnect(graph_task)))
        else:
            _mark_chat_run_failed(run_id, session_id, "client disconnected before run started")
        raise
    except Exception as e:
        logger.error(f"??????: {e}")
        _mark_chat_run_failed(run_id, session_id, str(e))
        yield _sse({"type": "error", "message": "chat service failed"})


@app.post("/api/chat")
@limiter.limit("15/minute")
async def chat_endpoint(request: Request, body: ChatRequest, current_user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    _ensure_chat_schema_migrated(db)
    session_id = body.session_id
    query = body.query
    chat_session = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == current_user.id,
    ).with_for_update().first()
    if not chat_session:
        raise HTTPException(status_code=404, detail="?????")

    if chat_session.active_run_id:
        active_run = db.query(ChatRun).filter(ChatRun.run_id == chat_session.active_run_id).first()
        if active_run and active_run.status == "running" and not _is_stale_chat_run(active_run):
            raise HTTPException(status_code=409, detail="该会话正在生成中，请等待当前回复完成")
        if active_run and active_run.status == "running":
            active_run.status = "failed"
            active_run.error = "stale chat run cleared before starting a new run"
            active_run.finished_at = _utcnow()
        chat_session.active_run_id = None

    run_id = uuid.uuid4().hex
    chat_run = ChatRun(
        run_id=run_id,
        session_id=session_id,
        user_id=current_user.id,
        status="running",
    )
    db.add(chat_run)
    chat_session.active_run_id = run_id

    image_path_for_db = None
    image_file_id = None
    image_file_record = None
    try:
        image_path_for_db, image_file_id, image_file_record = _resolve_chat_image_reference(
            db,
            current_user=current_user,
            session_id=session_id,
            run_id=run_id,
            raw_image=body.image_data,
        )
    except HTTPException:
        db.rollback()
        raise
    except Exception as e:
        db.rollback()
        logger.warning(f"image resolution failed: {e}")
        raise HTTPException(status_code=400, detail="invalid uploaded image")

    is_first_msg = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).count() == 0
    user_msg = ChatMessage(
        session_id=session_id,
        run_id=run_id,
        role="user",
        content=query,
        image_url=image_path_for_db,
        uploaded_file_id=image_file_id,
    )
    db.add(user_msg)
    db.flush()
    chat_run.user_message_id = user_msg.id
    if image_file_record:
        image_file_record.message_id = user_msg.id
    if is_first_msg:
        chat_session.title = "正在分析咨询主题..."
    db.commit()
    if is_first_msg:
        asyncio.create_task(generate_semantic_title(query, session_id))

    db_messages = db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(
        ChatMessage.created_at.asc()).all()
    history_dicts = [
        {"role": "assistant" if m.role == "ai" else "user", "content": m.content}
        for m in db_messages[:-1]
    ]
    profile = db.query(HealthProfile).filter(HealthProfile.user_id == current_user.id).first()
    image_url_for_graph = _presigned_file_url(image_file_record) if image_file_record else image_path_for_db
    server_slots = chat_session.current_slots if chat_session.current_slots is not None else (body.current_slots or {})
    server_turn_count = chat_session.turn_count if chat_session.turn_count is not None else (body.turn_count or 1)
    server_route = chat_session.current_route if chat_session.current_route is not None else (body.current_route or "")
    initial_state = {
        "session_id": session_id,
        "user_id": current_user.id,
        "query": query,
        "messages_history": history_dicts,
        "image_url": image_url_for_graph,
        "patient_profile": profile.profile_data if profile and profile.profile_data else {},
        "current_slots": server_slots or {},
        "turn_count": server_turn_count or 1,
        "current_route": server_route or "",
        "vision_context": body.vision_context or "",
        "med_precheck_result": body.med_precheck or {},
        "internal_scratchpad": [],
        "agent_audit_log": [],
        "trace_data": {},
        "response_images": [],
        "options": [],
        "is_finished": True,
    }
    return StreamingResponse(
        _chat_sse_generator(initial_state, session_id, run_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


_ENTITY_NORM_CACHE: dict[str, dict] = {}
_NEO4J_WARNED = False

GRAPH_REL_DISPLAY_NAMES = {
    "TREATS": "治疗",
    "HAS_SYMPTOM": "伴随症状",
    "BELONGS_TO": "就诊科室",
    "CONTRAINDICATED_FOR": "禁忌",
    "DO_EAT": "宜吃",
    "NOT_EAT": "忌吃",
    "RECOMMAND_EAT": "推荐食谱",
    "COMMON_DRUG": "常用药",
    "RECOMMAND_DRUG": "推荐用药",
    "NEED_CHECK": "所需检查",
    "ACOMPANY_WITH": "并发症",
    "CURE_WAY": "治疗方法",
    "PRODUCED_BY": "生产厂商",
    "DRUGS_OF": "在售药品",
    "DEPT_PARENT": "上级科室",
    "RELATED_TO": "相关",
    "RELATIONSHIP": "关联",
    "HAS_SECTION": "文档章节",
    "HAS_FIELD": "字段",
}

GRAPH_NODE_DISPLAY_NAMES = {
    "Disease": "疾病",
    "Symptom": "症状",
    "Drug": "药物",
    "Department": "科室",
    "Food": "食物",
    "Check": "检查项目",
    "Cure": "治疗方式",
    "Producer": "生产厂商",
}

DISEASE_PROP_LABELS = {
    "desc": "概述",
    "cause": "病因",
    "prevent": "预防",
    "cure_lasttime": "治疗周期",
    "cured_prob": "治愈概率",
    "easy_get": "易感人群",
    "get_prob": "发病率",
    "get_way": "传播方式",
    "yibao_status": "医保状态",
    "cost_money": "治疗费用",
}

GRAPH_INTERNAL_PROP_KEYS = {
    "embedding",
    "license",
    "source_tier",
    "source_name",
    "source_id",
    "source",
    "source_url",
    "data_source",
    "raw_source",
    "provenance",
    "updated_at",
    "created_at",
    "imported_at",
}

GRAPH_PUBLIC_PROP_LABELS = {
    "class": "分类",
    "level": "层级",
}

GRAPH_DISPLAY_NAME_MAX_LEN = 36
GRAPH_DISPLAY_BAD_PATTERNS = (
    "本品",
    "患者",
    "禁用",
    "禁用于",
    "慎用",
    "不推荐",
    "不应",
    "不得",
    "过敏",
    "使用",
    "服用",
    "应用",
    "治疗",
    "诊断",
    "排除",
    "病史",
    "临床试验",
    "资料",
    "尚无",
    "未明确",
    "禁忌",
    "避免",
)


def _graph_node_payload(node) -> dict:
    data = dict(node)
    labels = list(node.labels)
    return {
        "id": node.element_id,
        "name": data.get("name") or data.get("title") or data.get("id") or "?????",
        "label": labels[0] if labels else "",
        "labels": labels,
        "properties": data,
    }


def _graph_relationship_payload(rel) -> dict:
    rel_type = rel.type
    return {
        "id": rel.element_id,
        "source": rel.start_node.element_id,
        "target": rel.end_node.element_id,
        "type": rel_type,
        "label": rel_type,
        "relationship": rel_type,
        "display_label": GRAPH_REL_DISPLAY_NAMES.get(rel_type, rel_type),
        "properties": dict(rel),
    }


def _non_empty_str(value, max_len: int = 500) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return ""
    text = str(value).strip()
    if not text or text.lower() == "nan":
        return ""
    return text[:max_len]


def _is_displayable_graph_node_payload(payload: dict) -> bool:
    name = _non_empty_str((payload or {}).get("name"), 500)
    if not name:
        return False
    if len(name) > GRAPH_DISPLAY_NAME_MAX_LEN:
        return False
    if _re.search(r"[\n\r。！？!?：:；;|]", name):
        return False
    if any(pattern in name for pattern in GRAPH_DISPLAY_BAD_PATTERNS):
        return False
    return True


def _iter_public_graph_props(props: dict, max_items: int = 8):
    for key, value in (props or {}).items():
        if key in GRAPH_INTERNAL_PROP_KEYS or key.startswith("_") or key == "name":
            continue
        clean = _non_empty_str(value, 160)
        if not clean:
            continue
        yield GRAPH_PUBLIC_PROP_LABELS.get(key, key), clean
        max_items -= 1
        if max_items <= 0:
            return


def _sanitize_graph_explanation(text: str) -> str:
    cleaned = (text or "").strip()
    if not cleaned:
        return cleaned
    blocked_tokens = (
        "source_tier",
        "license",
        "local_review_required",
        "source_name",
        "local_diseasekg_json",
        "updated_at",
        "created_at",
        "T3级别",
        "T3 级别",
        "基础医疗资源",
        "本地审核",
    )
    parts = _re.split(r"(?<=[。！？\n])", cleaned)
    kept = [part for part in parts if not any(token in part for token in blocked_tokens)]
    cleaned = "".join(kept).strip()
    return cleaned or "该节点是医疗知识图谱中的实体，相关说明可结合右侧图谱关联邻居查看。"


def _build_graph_node_fallback_explanation(node_payload: dict, neighbor_buckets: dict[str, list[str]]) -> str:
    props = node_payload.get("properties") or {}
    name = node_payload.get("name") or "该节点"
    label = node_payload.get("label") or ""
    label_cn = GRAPH_NODE_DISPLAY_NAMES.get(label, label or "实体")
    lines = [f"{name}是图谱中的{label_cn}节点。"]

    if label == "Disease":
        reference_name = _non_empty_str(props.get("_reference_disease_name"), 80)
        if reference_name and reference_name != name:
            lines.append(f"该说明参考 DiseaseKG 中“{reference_name}”条目的结构化字段生成。")
        desc = _non_empty_str(props.get("desc"), 220)
        cause = _non_empty_str(props.get("cause"), 160)
        prevent = _non_empty_str(props.get("prevent"), 160)
        meta_parts = []
        for key in ("easy_get", "get_prob", "cure_lasttime", "cured_prob", "yibao_status", "cost_money"):
            value = _non_empty_str(props.get(key), 80)
            if value:
                meta_parts.append(f"{DISEASE_PROP_LABELS[key]}：{value}")
        if desc:
            lines.append(desc)
        if cause:
            lines.append(f"图谱病因字段提示：{cause}")
        if prevent:
            lines.append(f"预防相关字段提示：{prevent}")
        if meta_parts:
            lines.append("；".join(meta_parts[:4]) + "。")
    elif label == "Drug":
        drug_class = _non_empty_str(props.get("class"), 100)
        if drug_class:
            lines.append(f"图谱记录的药品分类为：{drug_class}。")
    elif label == "Department":
        level = _non_empty_str(props.get("level"), 40)
        if level:
            lines.append(f"图谱记录的科室层级为：{level}。")
    else:
        public_props = [f"{key}：{value}" for key, value in _iter_public_graph_props(props, max_items=3)]
        if public_props:
            lines.append("；".join(public_props) + "。")

    rel_parts = []
    for rel, items in neighbor_buckets.items():
        display = GRAPH_REL_DISPLAY_NAMES.get(rel, rel)
        sample = "、".join(items[:5])
        suffix = f"等 {len(items)} 项" if len(items) > 5 else f"{len(items)} 项"
        rel_parts.append(f"{display}包含{sample}{suffix}")
    if rel_parts:
        lines.append("关联关系方面，" + "；".join(rel_parts[:4]) + "。")
    return _sanitize_graph_explanation("\n\n".join(lines))


async def _llm_polish_graph_node_explanation(node_payload: dict, neighbor_buckets: dict[str, list[str]]) -> tuple[str, bool]:
    fallback = _build_graph_node_fallback_explanation(node_payload, neighbor_buckets)
    props = node_payload.get("properties") or {}
    name = node_payload.get("name") or ""
    label = node_payload.get("label") or ""
    field_lines = []
    if label == "Disease":
        reference_name = _non_empty_str(props.get("_reference_disease_name"), 80)
        if reference_name and reference_name != name:
            field_lines.append(f"参考疾病条目: {reference_name}")
        for key, field_label in DISEASE_PROP_LABELS.items():
            value = _non_empty_str(props.get(key), 260)
            if value:
                field_lines.append(f"{field_label}: {value}")
    elif label == "Drug":
        value = _non_empty_str(props.get("class"), 160)
        if value:
            field_lines.append(f"药品分类: {value}")
    else:
        for key, value in _iter_public_graph_props(props, max_items=8):
            field_lines.append(f"{key}: {value}")

    relation_lines = []
    for rel, items in neighbor_buckets.items():
        display = GRAPH_REL_DISPLAY_NAMES.get(rel, rel)
        relation_lines.append(f"{display}({rel}): {'、'.join(items[:10])}")

    if not field_lines and not relation_lines:
        return fallback, False
    if label not in {"Disease", "Drug", "Department"} and not field_lines:
        return fallback, False

    prompt = (
        "你是医疗知识图谱节点说明生成器。只允许根据给定图谱字段和邻居关系润色，"
        "不要补充外部医学知识，不要给诊断或用药建议。输出 120-220 字中文说明，"
        "先介绍节点含义，再概括图谱中能观察到的关联。"
    )
    user_content = (
        f"节点名称：{name}\n"
        f"节点类型：{GRAPH_NODE_DISPLAY_NAMES.get(label, label or '实体')}\n"
        f"节点字段：\n" + ("\n".join(field_lines) if field_lines else "无") + "\n"
        f"邻居关系：\n" + ("\n".join(relation_lines) if relation_lines else "无")
    )
    try:
        resp = await asyncio.wait_for(
            shared_client.chat.completions.create(
                model=FAST_MODEL,
                temperature=0.2,
                max_tokens=420,
                messages=[
                    {"role": "system", "content": prompt + " 严禁输出数据治理字段、来源层级、license、source_tier、source_name、updated_at 等内部元数据。"},
                    {"role": "user", "content": user_content},
                ],
            ),
            timeout=10,
        )
        content = (resp.choices[0].message.content or "").strip()
        content = _sanitize_graph_explanation(content)
        return (content or fallback), bool(content)
    except Exception as exc:
        logger.warning(f"Graph node LLM explanation failed: {exc}")
        return fallback, False


def _parse_graph_type_list(raw: Optional[str]) -> list[str]:
    if not raw:
        return []
    ignored = {"all", "全部", "鍏ㄩ儴", "*"}
    return [
        item.strip()
        for item in raw.split(",")
        if item.strip() and item.strip().lower() not in ignored
    ]


def _graph_related_search_terms(term: str) -> list[str]:
    clean = (term or "").strip()
    if not clean:
        return []
    terms = [clean]
    for prefix in ("急性", "慢性"):
        if clean.startswith(prefix) and len(clean) > len(prefix) + 1:
            base = clean[len(prefix):].strip()
            if base and base not in terms:
                terms.append(base)
    return terms


def _has_disease_detail(node_payload: dict) -> bool:
    if (node_payload or {}).get("label") != "Disease":
        return False
    props = (node_payload or {}).get("properties") or {}
    return any(_non_empty_str(props.get(key), 20) for key in DISEASE_PROP_LABELS)


def _enrich_disease_payload_from_base(node_payload: dict) -> tuple[dict, Optional[str]]:
    if _has_disease_detail(node_payload):
        return node_payload, None
    name = (node_payload or {}).get("name") or ""
    related_terms = _graph_related_search_terms(name)
    if len(related_terms) < 2:
        return node_payload, None
    base_name = related_terms[1]
    rows = _run_neo4j_read(
        """
        MATCH (n:Disease)
        WHERE toLower(coalesce(n.name, '')) = toLower($base_name)
        WITH n,
             CASE
               WHEN coalesce(toString(n.desc), '') <> '' OR coalesce(toString(n.cause), '') <> '' THEN 0
               ELSE 1
             END AS detail_rank,
             size([(n)--() | 1]) AS degree
        RETURN n
        ORDER BY detail_rank ASC, degree DESC
        LIMIT 1
        """,
        base_name=base_name,
    )
    if not rows:
        return node_payload, None
    reference_payload = _graph_node_payload(rows[0]["n"])
    if not _has_disease_detail(reference_payload):
        return node_payload, None
    merged_props = dict(reference_payload.get("properties") or {})
    for key, value in (node_payload.get("properties") or {}).items():
        if _non_empty_str(value, 300):
            merged_props[key] = value
    merged_props["_reference_disease_name"] = reference_payload.get("name") or base_name
    enriched = {**node_payload, "properties": merged_props}
    return enriched, merged_props["_reference_disease_name"]


def _run_neo4j_read(cypher: str, **params):
    global _NEO4J_WARNED
    try:
        with neo4j_driver.session() as session:
            return list(session.run(cypher, **params))
    except Exception as e:
        if not _NEO4J_WARNED:
            logger.warning(f"Neo4j read failed: {e}")
            _NEO4J_WARNED = True
        else:
            logger.debug(f"Neo4j read failed: {e}")
        return []


async def _normalize_entity_llm(raw_keyword: str, main_type: str = "Entity") -> dict:
    keyword = (raw_keyword or "").strip()
    if not keyword:
        return {"keyword": "", "normalized": "", "type": main_type}
    cache_key = f"{main_type}::{keyword}"
    if cache_key not in _ENTITY_NORM_CACHE:
        _ENTITY_NORM_CACHE[cache_key] = {"keyword": keyword, "normalized": keyword, "type": main_type}
    return _ENTITY_NORM_CACHE[cache_key]


@app.get("/api/graph/popular")
def get_graph_popular(limit: int = Query(default=20, ge=1, le=100)):
    rows = _run_neo4j_read(
        """
        MATCH (n)
        WITH n, size([(n)--() | 1]) AS degree
        RETURN n, degree
        ORDER BY degree DESC
        LIMIT $limit
        """,
        limit=limit,
    )
    return {
        "nodes": [
            {**_graph_node_payload(row["n"]), "degree": row.get("degree", 0)}
            for row in rows
        ]
    }


@app.get("/api/graph/search")
async def search_graph(
    keyword: str = Query(..., min_length=1),
    node_type: str = Query(default="Entity"),
    main_type: Optional[str] = Query(default=None),
    target_types: Optional[str] = Query(default=None),
    depth: int = Query(default=1, ge=1, le=3),
    limit: int = Query(default=20, ge=1, le=50),
):
    effective_type = main_type or node_type
    normalized = await _normalize_entity_llm(keyword, effective_type)
    term = normalized.get("normalized") or keyword
    main_types = _parse_graph_type_list(effective_type if effective_type != "Entity" else "")
    target_type_list = _parse_graph_type_list(target_types)
    search_terms = _graph_related_search_terms(term)
    search_terms_lc = [item.lower() for item in search_terms]
    primary_term_lc = (search_terms_lc[0] if search_terms_lc else term.lower())
    secondary_terms_lc = search_terms_lc[1:]
    rows = _run_neo4j_read(
        """
        MATCH (n)
        WITH n, toLower(coalesce(n.name, '')) AS lname
        WHERE any(search_term IN $search_terms_lc WHERE lname CONTAINS search_term)
        WITH n, lname
        WHERE size($main_types) = 0 OR any(lbl IN labels(n) WHERE lbl IN $main_types)
        WITH n, lname,
             size([(n)--() | 1]) AS degree,
             CASE
               WHEN lname = $primary_term_lc THEN 0
               WHEN size($secondary_terms_lc) > 0 AND any(search_term IN $secondary_terms_lc WHERE lname = search_term) THEN 1
               WHEN lname STARTS WITH $primary_term_lc THEN 2
               WHEN size($secondary_terms_lc) > 0 AND any(search_term IN $secondary_terms_lc WHERE lname STARTS WITH search_term) THEN 3
               ELSE 4
             END AS name_rank,
             CASE
               WHEN coalesce(toString(n.desc), '') <> '' OR coalesce(toString(n.cause), '') <> '' THEN 0
               ELSE 1
             END AS detail_rank
        RETURN n, degree
        ORDER BY name_rank ASC, detail_rank ASC, degree DESC
        LIMIT $limit
        """,
        search_terms_lc=search_terms_lc or [term.lower()],
        primary_term_lc=primary_term_lc,
        secondary_terms_lc=secondary_terms_lc,
        main_types=main_types,
        limit=limit,
    )
    node_by_id = {}
    for row in rows:
        payload = {**_graph_node_payload(row["n"]), "degree": row.get("degree", 0)}
        if _is_displayable_graph_node_payload(payload):
            node_by_id[payload["id"]] = payload

    links_by_id = {}
    seed_ids = list(node_by_id.keys())
    if seed_ids:
        rel_limit = max(180, limit * 18)
        direct_rows = _run_neo4j_read(
            """
            MATCH (n)-[r]-(m)
            WHERE elementId(n) IN $seed_ids
            WITH n, r, m, labels(m) AS m_labels, type(r) AS rel_type
            WHERE size($target_types) = 0 OR any(lbl IN m_labels WHERE lbl IN $target_types)
            WITH
              CASE
                WHEN size($target_types) = 0 THEN coalesce(m_labels[0], 'Entity')
                ELSE head([lbl IN m_labels WHERE lbl IN $target_types])
              END AS target_label,
              rel_type, n, r, m,
              size([(m)--() | 1]) AS degree
            ORDER BY target_label ASC, rel_type ASC, degree DESC
            WITH target_label, rel_type, collect({n: n, r: r, m: m}) AS rows
            UNWIND rows[0..$per_bucket_limit] AS row
            RETURN [row.n, row.m] AS path_nodes, row.r AS r
            LIMIT $rel_limit
            """,
            seed_ids=seed_ids,
            target_types=target_type_list,
            per_bucket_limit=16,
            rel_limit=rel_limit,
        )
        rel_rows = direct_rows
        if depth > 1:
            rel_rows += _run_neo4j_read(
                f"""
                MATCH p=(n)-[*2..{depth}]-(m)
                WHERE elementId(n) IN $seed_ids
                WITH p, m
                WHERE size($target_types) = 0 OR any(lbl IN labels(m) WHERE lbl IN $target_types)
                WITH p LIMIT $rel_limit
                UNWIND nodes(p) AS pn
                WITH p, collect(DISTINCT pn) AS path_nodes
                UNWIND relationships(p) AS r
                RETURN path_nodes, r
                LIMIT $rel_limit
                """,
                seed_ids=seed_ids,
                target_types=target_type_list,
                rel_limit=rel_limit,
            )
        for row in rel_rows:
            path_payloads = [_graph_node_payload(node) for node in (row.get("path_nodes") or [])]
            if any(not _is_displayable_graph_node_payload(payload) for payload in path_payloads):
                continue
            for payload in path_payloads:
                node_by_id.setdefault(payload["id"], payload)
            rel_payload = _graph_relationship_payload(row["r"])
            links_by_id[rel_payload["id"]] = rel_payload

    return {
        "status": "success",
        "keyword": keyword,
        "normalized": normalized,
        "normalized_from": keyword,
        "actual_keyword": term,
        "data": {
            "nodes": list(node_by_id.values()),
            "links": list(links_by_id.values()),
        },
    }


@app.get("/api/graph/explain")
async def explain_graph(
    source: Optional[str] = Query(default=None),
    target: Optional[str] = Query(default=None),
    name: Optional[str] = Query(default=None),
    node_id: Optional[str] = Query(default=None),
    label: Optional[str] = Query(default=None),
    max_hops: int = Query(default=2, ge=1, le=4),
):
    source_value = (source or name or "").strip()
    target_value = (target or "").strip()
    node_id_value = (node_id or "").strip()
    if not source_value and not node_id_value:
        raise HTTPException(status_code=400, detail="source/name is required")
    if not target_value and not name and not node_id_value:
        raise HTTPException(status_code=400, detail="target is required")

    if (name or node_id_value) and not target_value:
        if node_id_value:
            neighbor_rows = _run_neo4j_read(
                """
                MATCH (n)
                WHERE elementId(n) = $node_id
                WITH n
                OPTIONAL MATCH (n)-[r]-(m)
                RETURN n, r, m
                LIMIT 120
                """,
                node_id=node_id_value,
            )
        else:
            label_list = _parse_graph_type_list(label or "")
            neighbor_rows = _run_neo4j_read(
                """
                MATCH (n)
                WHERE toLower(coalesce(n.name, '')) CONTAINS toLower($name)
                WITH n
                WHERE size($labels) = 0 OR any(lbl IN labels(n) WHERE lbl IN $labels)
                WITH n, size([(n)--() | 1]) AS degree
                ORDER BY degree DESC
                LIMIT 1
                OPTIONAL MATCH (n)-[r]-(m)
                RETURN n, r, m
                LIMIT 120
                """,
                name=source_value,
                labels=label_list,
            )
        buckets: dict[str, list[str]] = {}
        node_payload = None
        for row in neighbor_rows:
            node_payload = node_payload or _graph_node_payload(row["n"])
            neighbor = row.get("m")
            rel = row.get("r")
            if neighbor is None or rel is None:
                continue
            bucket = rel.type
            buckets.setdefault(bucket, [])
            neighbor_name = _graph_node_payload(neighbor)["name"]
            if neighbor_name not in buckets[bucket]:
                buckets[bucket].append(neighbor_name)
        if not node_payload:
            return {
                "status": "not_found",
                "name": source_value,
                "label": label or "",
                "explanation": "未在当前图谱中找到该节点。",
                "neighbor_count": 0,
                "neighbor_buckets": {},
                "node_properties": {},
                "used_llm": False,
                "explanation_source": "not_found",
            }
        neighbor_count = sum(len(items) for items in buckets.values())
        node_name = node_payload["name"]
        node_label = label or node_payload.get("label") or ""
        reference_name = None
        if node_payload.get("label") == "Disease":
            node_payload, reference_name = _enrich_disease_payload_from_base(node_payload)
        explanation, used_llm = await _llm_polish_graph_node_explanation(node_payload, buckets)
        relation_summary = [
            {
                "type": rel,
                "display_label": GRAPH_REL_DISPLAY_NAMES.get(rel, rel),
                "count": len(items),
                "samples": items[:12],
            }
            for rel, items in buckets.items()
        ]
        return {
            "status": "success",
            "name": node_name,
            "label": node_label,
            "explanation": explanation,
            "neighbor_count": neighbor_count,
            "neighbor_buckets": buckets,
            "relation_summary": relation_summary,
            "node_properties": node_payload.get("properties") or {},
            "used_llm": used_llm,
            "explanation_source": "llm" if used_llm else "graph_fallback",
            "reference_node": reference_name,
        }

    rows = _run_neo4j_read(
        f"""
        MATCH (a), (b)
        WHERE toLower(coalesce(a.name, '')) CONTAINS toLower($source)
          AND toLower(coalesce(b.name, '')) CONTAINS toLower($target)
        MATCH p = shortestPath((a)-[*1..{max_hops}]-(b))
        RETURN p
        LIMIT 5
        """,
        source=source_value,
        target=target_value,
    )
    paths = []
    for row in rows:
        pth = row["p"]
        paths.append({
            "nodes": [_graph_node_payload(n) for n in pth.nodes],
            "relationships": [
                {"type": r.type, "start": r.start_node.element_id, "end": r.end_node.element_id, "properties": dict(r)}
                for r in pth.relationships
            ],
        })
    return {"status": "success", "source": source_value, "target": target_value, "paths": paths}


if __name__ == "__main__":
    logger.info("Starting API Gateway")
    uvicorn.run(app, host="0.0.0.0", port=8000)
