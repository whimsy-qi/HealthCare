# backend/agents/memory_agent.py
import os
import json
import copy
import logging
import asyncio
from typing import Optional, Dict, Any
from dotenv import load_dotenv, find_dotenv
from core.llm_client import shared_client as client, FAST_MODEL

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("MemoryAgent")

LIST_MEMORY_FIELDS = ("diseases", "allergies", "surgeries", "medications")
APPEND_MEMORY_FIELDS = ("family_history",)
DICT_MEMORY_FIELDS = ("lifestyle",)


def _stable_unique_list(items):
    """Deduplicate while preserving the user's first-seen order."""
    seen = set()
    result = []
    for item in items or []:
        if item in (None, "", [], {}):
            continue
        try:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True)
        except TypeError:
            key = str(item)
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


# ==========================================
# 📝 公共 system prompt（extract 与 consolidator 共用）
# ==========================================

_MEMORY_SYSTEM_PROMPT = """
    你是一个极其敏锐的患者健康档案提取员。
    你的任务是：从用户说的话中，精准提取出他**明确陈述的、属于他自己的**新增健康档案信息。

    【提取范围（6 类）】：
    1. 慢性疾病/现病史 (diseases) — "我昨天查出高血压" → ["高血压"]
    2. 过敏史 (allergies) — "我对头孢过敏" → ["头孢"]
    3. 手术史 (surgeries) — "上个月刚割了阑尾" → ["阑尾切除术"]
    4. 用药史 (medications) — "我在吃降压药" → ["降压药"]；"每天一片阿司匹林" → ["阿司匹林"]
    5. 家族史 (family_history) — "我爸有糖尿病" → [{"relative":"父亲","condition":"糖尿病"}]；注意：这是亲属的疾病，不是用户本人的
    6. 生活习惯 (lifestyle) — "我每天抽一包烟" → {"smoking":"heavy"}；"一周跑三次步" → {"exercise":"regular"}；"睡眠不好" → {"sleep":"poor"}

    【严格原则】：
    1. 必须是明确陈述（"我有..."，"查出了..."）。疑问句（"我会不会得高血压？"）绝对不能提取！
    2. 必须是用户本人的（family_history 除外——那本来就是记录亲属的）。
    3. medications 只提取正在服用的药，不提取"我听说 XX 药"这种询问性质的。
    4. lifestyle 仅在用户明确描述自己的习惯时提取，不推测。
    5. 如果没有发现任何新增的档案信息，设置 "has_update" 为 false。

    【输出格式】（严格 JSON）：
    {
        "has_update": true/false,
        "updates": {
            "diseases": [],
            "allergies": [],
            "surgeries": [],
            "medications": [],
            "family_history": [],
            "lifestyle": {}
        }
    }
    """


# ==========================================
# 🆕 步骤 1：纯抽取（不写库）—— 给本轮注入用
# ==========================================

def _normalize_health_updates(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize the supported HealthProfile delta fields before merge/persist."""
    if not isinstance(updates, dict):
        return {}

    normalized: Dict[str, Any] = {}
    for key in LIST_MEMORY_FIELDS + APPEND_MEMORY_FIELDS:
        val = updates.get(key)
        if isinstance(val, list):
            cleaned = [item for item in val if item]
            if cleaned:
                normalized[key] = cleaned

    for key in DICT_MEMORY_FIELDS:
        val = updates.get(key)
        if isinstance(val, dict):
            cleaned = {k: v for k, v in val.items() if v not in (None, "", [], {})}
            if cleaned:
                normalized[key] = cleaned

    return normalized


async def extract_health_updates(query: str) -> Optional[Dict[str, Any]]:
    """
    从用户本轮发言中抽取健康档案增量，但**不写数据库**。
    返回 {"diseases": [...], "allergies": [...], "surgeries": [...]} 或 None。

    设计动机：原 shadow_memory_consolidator 是 fire-and-forget，本轮 agent 看到的
    profile 不包含本轮提取的更新（要等下一轮才生效）。把抽取与持久化分离后，
    api_server 可 `await extract` 把增量合并进当轮 patient_profile，
    再把持久化丢后台 → 实现"本轮即生效"的记忆闭环。
    """
    try:
        response = await client.chat.completions.create(
            model=FAST_MODEL,
            messages=[
                {"role": "system", "content": _MEMORY_SYSTEM_PROMPT},
                {"role": "user", "content": f"用户原话：{query}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        result = json.loads(response.choices[0].message.content)
        if not result.get("has_update"):
            return None
        updates = result.get("updates") or {}
        updates = _normalize_health_updates(updates)
        return updates if updates else None
    except Exception as e:
        logger.error(f"❌ [Memory/Extract] 抽取失败: {e}")
        return None


def merge_updates_into_profile(profile_data: dict, updates: Dict[str, Any]) -> dict:
    """把 extract_health_updates 的结果合并进 in-memory profile（去重）。返回新 dict。"""
    updates = _normalize_health_updates(updates)
    merged = copy.deepcopy(profile_data) if profile_data else {}
    # 列表类字段 — 去重合并
    for key in LIST_MEMORY_FIELDS:
        if updates.get(key):
            merged[key] = _stable_unique_list((merged.get(key) or []) + updates[key])
    # 家族史 — 追加
    for key in APPEND_MEMORY_FIELDS:
        if updates.get(key):
            merged[key] = _stable_unique_list((merged.get(key) or []) + updates[key])
    # 生活习惯 — 覆盖合并（最新陈述为准）
    for key in DICT_MEMORY_FIELDS:
        if updates.get(key) and isinstance(updates[key], dict):
            merged[key] = {**(merged.get(key) or {}), **updates[key]}
    return merged


# ==========================================
# 🆕 步骤 2：持久化（不依赖 LLM）—— 丢后台异步执行
# ==========================================

async def persist_health_updates(user_id: int, updates: Dict[str, Any]):
    """
    把已抽取出的 updates 合并进 DB 中该用户的 HealthProfile。
    DB I/O 是同步操作，用 asyncio.to_thread 调度到线程池避免阻塞事件循环。
    """
    updates = _normalize_health_updates(updates)
    if not updates:
        return
    logger.info(f"✨ [Shadow Memory] 持久化健康档案增量：{updates}")

    def _do_persist():
        from core.database import SessionLocal
        from core.models import HealthProfile
        from sqlalchemy.orm.attributes import flag_modified
        from sqlalchemy.exc import IntegrityError

        db = SessionLocal()
        try:
            profile = (
                db.query(HealthProfile)
                .filter(HealthProfile.user_id == user_id)
                .with_for_update()
                .first()
            )
            if profile:
                current_data = copy.deepcopy(profile.profile_data) if profile.profile_data else {}
                current_data = merge_updates_into_profile(current_data, updates)
                profile.profile_data = current_data
                flag_modified(profile, "profile_data")
                db.add(profile)
                db.commit()
                logger.info("💾 [Shadow Memory] 增量健康档案已合并写入 DB。")
            else:
                try:
                    new_profile = HealthProfile(user_id=user_id, profile_data=updates)
                    db.add(new_profile)
                    db.commit()
                    logger.info("💾 [Shadow Memory] 首次创建健康档案。")
                except IntegrityError:
                    db.rollback()
                    logger.warning("⚠️ [Shadow Memory] 并发创建冲突，重试更新...")
                    retry = (
                        db.query(HealthProfile)
                        .filter(HealthProfile.user_id == user_id)
                        .with_for_update()
                        .first()
                    )
                    if retry:
                        retry_data = copy.deepcopy(retry.profile_data) if retry.profile_data else {}
                        retry_data = merge_updates_into_profile(retry_data, updates)
                        retry.profile_data = retry_data
                        flag_modified(retry, "profile_data")
                        db.commit()
                        logger.info("💾 [Shadow Memory] 并发冲突已解决。")
        except Exception as inner_e:
            db.rollback()
            logger.error(f"❌ [Shadow Memory] DB 操作失败: {inner_e}")
        finally:
            db.close()

    await asyncio.to_thread(_do_persist)


# ==========================================
# 旧入口（向后兼容）：抽取 + 持久化串联
# ==========================================

async def shadow_memory_consolidator(query: str, user_id: int):
    """
    影子记忆归档员（兼容旧调用方）：抽取 + 持久化一条龙。
    新代码建议改用 extract_health_updates + persist_health_updates 以实现本轮闭环。
    """
    logger.info("🕵️ [Shadow Memory Agent] 启动旁路监听，扫描增量健康档案...")
    updates = await extract_health_updates(query)
    if updates:
        await persist_health_updates(user_id, updates)
    else:
        logger.debug("👻 [Shadow Memory] 未发现档案更新，静默退出。")


