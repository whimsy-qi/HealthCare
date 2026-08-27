# agents/medication_agent.py
import os
import json
import logging
import asyncio
import datetime
from typing import List, Dict, Any, Optional, Tuple
from dotenv import load_dotenv, find_dotenv
import dashscope
from neo4j import GraphDatabase

from scripts.vision_tool import analyze_image_with_vision
from scripts.tools_search import search_dynamic_medical_info
from core.llm_client import shared_client as client, DEFAULT_MODEL, FAST_MODEL
from core.sse_emitter import emit as sse_emit

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("MedicationAgent")

# ==========================================
# Lazy init：首次调用时才初始化外部服务连接，
# 避免 import 时因服务未启动导致整个后端崩溃
# ==========================================
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

_dv_client = None
_collection = None
_neo4j_driver = None


def _get_collection():
    raise RuntimeError("DashVector fallback is disabled; use RAG_BACKEND=medical_graphrag")


def _get_neo4j_driver():
    global _neo4j_driver
    if _neo4j_driver is None:
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7714")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "")
        timeout = float(os.getenv("NEO4J_QUERY_TIMEOUT_SECONDS", os.getenv("RAG_GRAPHRAG_TIMEOUT_SECONDS", "1.0")))
        _neo4j_driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            connection_timeout=timeout,
            max_connection_lifetime=60,
        )
    return _neo4j_driver

# ==========================================
# 用药审查日志沉淀器 (Reflection Logger)
# ==========================================
async def log_medication_reflection_data(query: str, intent: str, drugs: list, report: dict, has_profile: bool):
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "medication_reflection.jsonl")

    reflection_entry = {
        "timestamp": datetime.datetime.now().isoformat(),
        "query": query,
        "intent": intent,
        "extracted_drugs": drugs,
        "has_patient_profile": has_profile,
        "verdict_risk_level": report.get("risk_level", "Unknown"),
        "conflict_detected": report.get("conflict_detected", "无")
    }
    try:
        def _write():
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(reflection_entry, ensure_ascii=False) + "\n")

        await asyncio.to_thread(_write)
        logger.debug(f"📝 用药审查记忆已成功沉淀至: {log_file}")
    except Exception as e:
        logger.error(f"反思数据写入失败: {e}")


# ==========================================
# 底层工具函数
# ==========================================
async def classify_medication_intent(query: str) -> str:
    prompt = """
    你是一个三甲医院的分诊药师。请判断用户的提问意图，严格输出 JSON。
    【分类标准】
    - "General_Inquiry"（通用科普）：单纯询问药物的作用、吃法、副作用、禁忌等客观知识。
    - "Safety_Check"（用药审查）：结合自身情况询问能否吃某药，或带有明确的个人指向代词。
    输出格式：{"intent": "General_Inquiry" 或 "Safety_Check"}
    """
    try:
        resp = await client.chat.completions.create(
            model=FAST_MODEL,
            messages=[{"role": "system", "content": prompt}, {"role": "user", "content": query}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        return json.loads(resp.choices[0].message.content).get("intent", "Safety_Check")
    except Exception as e:
        logger.error(f"意图嗅探异常，降级为安全审查模式: {e}")
        return "Safety_Check"


async def search_kg_contraindications(drugs: List[str]) -> Tuple[str, list, list]:
    """
    返回三元组：(summary_warning, kg_cards, kg_triples)
    - summary_warning: LLM 压缩后的 ≤80 字警告文案（兼容旧调用方）
    - kg_cards: 旧版溯源卡片（保留以避免改 sources 渲染）
    - kg_triples: 结构化三元组列表，每条形如
        {"head": 药名, "relation": "禁忌于", "tail": 冲突对象, "tail_type": Disease/...,
         "source_id": "kg:<conflict_id>", "drug_id": "kg:<drug_id>", "confidence": 1.0}
    """
    if not drugs:
        return "", [], []

    try:
        from rag.graph import retrieve_graph_evidence

        graph_result = await retrieve_graph_evidence(
            " ".join(drugs),
            intent="medication_safety",
            entities=drugs,
            top_k=8,
            max_hops=2,
            filters={"use_vector": True},
        )
        graph_cards = []
        graph_triples = []
        for item in graph_result.items:
            locator = item.locator or {}
            relation_types = [
                rel for rel in str(item.metadata.get("relation_types") or "").split(",") if rel
            ]
            if not relation_types:
                continue
            tail = item.metadata.get("node_name") or item.title.replace("KG: ", "")
            graph_cards.append({
                "ref_id": f"kg:graph:{locator.get('neo4j_element_id', item.chunk_id)}",
                "type": "kg",
                "evidence_role": "constraint",
                "citation_allowed": False,
                "title": item.title,
                "label": f"知识图谱路径 · {tail}",
                "content": item.text,
                "snippet": item.text[:300],
                "locator": locator,
                "url": "Neo4j Knowledge Graph",
                "is_internal": True,
                "source_tier": item.source_tier,
            })
            graph_triples.append({
                "head": locator.get("anchor_entity") or "",
                "relation": "|".join(relation_types),
                "tail": tail,
                "tail_type": item.metadata.get("node_label", ""),
                "source_id": f"kg:{locator.get('neo4j_element_id', item.chunk_id)}",
                "confidence": item.scores.get("graph", 0.0),
                "evidence_role": "constraint",
                "citation_allowed": False,
                "locator": locator,
            })
        if graph_cards:
            return graph_result.context_text, graph_cards, graph_triples
    except Exception as e:
        logger.debug(f"GraphRAG medication lookup fallback to legacy Cypher: {e}")

    def _fetch_neo4j():
        # 🌟 新增 elementId 与 type(r)，保留结构化三元组
        query = """
        UNWIND $drugs AS keyword
        MATCH (d:Drug)-[r]->(c)
        WHERE type(r) = 'CONTRAINDICATED_FOR' AND (d.name CONTAINS keyword OR keyword CONTAINS d.name)
        RETURN DISTINCT keyword,
               d.name AS drug, elementId(d) AS drug_id,
               c.name AS conflict, elementId(c) AS conflict_id,
               labels(c)[0] AS type, type(r) AS rel
        """
        grouped = {}
        raw_triples = []
        with _get_neo4j_driver().session() as session:
            result = session.run(query, drugs=drugs)
            for record in result:
                keyword = record["keyword"]
                drug_name = record["drug"]
                drug_id = record["drug_id"]
                conflict_name = record["conflict"]
                conflict_id = record["conflict_id"]
                conflict_type = record["type"]

                type_cn = {"Disease": "疾病", "Symptom": "症状", "Drug": "药物"}.get(conflict_type, conflict_type)

                if drug_name not in grouped:
                    grouped[drug_name] = {"keyword": keyword, "conflicts": []}
                grouped[drug_name]["conflicts"].append(f"{conflict_name}({type_cn})")

                raw_triples.append({
                    "head": drug_name,
                    "relation": "禁忌于",
                    "tail": conflict_name,
                    "tail_type": conflict_type,
                    "source_id": f"kg:{conflict_id}",
                    "drug_id": f"kg:{drug_id}",
                    "confidence": 1.0,
                })
        return grouped, raw_triples

    try:
        grouped_conflicts, kg_triples = await asyncio.wait_for(
            asyncio.to_thread(_fetch_neo4j),
            timeout=float(os.getenv("RAG_GRAPHRAG_TIMEOUT_SECONDS", "1.0")),
        )

        if not grouped_conflicts:
            return "", [], []

        raw_warnings = []
        kg_cards = []
        for drug_name, data in grouped_conflicts.items():
            keyword = data["keyword"]
            conflicts = data["conflicts"]
            total_count = len(conflicts)
            display_conflicts = "、".join(conflicts[:5])
            if total_count > 5:
                display_conflicts += f" 等共 {total_count} 项"

            raw_warnings.append(f"【{drug_name}】存在禁忌：{display_conflicts}")

            kg_cards.append({
                # 🌟 新增 ref_id / locator / snippet，方便证据链组件 dedupe + 跳转
                "ref_id": f"kg:drug:{drug_name}",
                "type": "kg",
                "evidence_role": "constraint",
                "citation_allowed": False,
                "title": f"🚨 禁忌症触发：{drug_name}",
                "label": f"知识图谱·{drug_name} 禁忌约束 ({total_count} 项)",
                "content": f"系统知识图谱硬性约束：【{drug_name}】存在 {total_count} 项明确禁忌对象，包含：{display_conflicts}。",
                "snippet": "、".join(conflicts[:10]),
                "locator": {"drug_name": drug_name, "conflict_count": total_count},
                "url": "Neo4j Knowledge Graph",
                "is_internal": True
            })

        raw_text = "\n".join(raw_warnings)

        prompt = f"""
        请作为专业的临床药师，将以下从知识图谱中抽取的关于药物【{', '.join(drugs)}】的多个衍生剂型的绝对禁忌记录，压缩成一段高度精炼的警告文案（80字以内）。
        【要求】：
        1. 合并同类项（比如不要重复罗列缓释片、肠溶片，统称该药物即可）。
        2. 提取最高频、最核心的3-4个禁忌疾病，其他的用"等"概括。
        3. 语气要严肃专业，直接输出结论，不要废话。
        【原始图谱记录】：
        {raw_text}
        """

        summary_resp = await client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1
        )
        summary_warning = summary_resp.choices[0].message.content.strip()

        return summary_warning, kg_cards, kg_triples
    except Exception as e:
        logger.warning(f"Neo4j contraindication lookup degraded: {type(e).__name__}: {e}")
        return "", [], []


async def search_drug_manual(query_text: str, top_k: int = 1) -> tuple[str, list]:
    docs_text, source_cards = [], []

    # 0. 优先使用统一 RAG v2：药品标签/指南/文献按意图分层检索。
    try:
        from rag.service import retrieve_medical_evidence

        rag_result = await retrieve_medical_evidence(
            query_text,
            intent="medication_safety",
            top_k=max(top_k, 3),
        )
        if rag_result.debug.get("unsafe_to_answer"):
            logger.warning(
                f"⚠️ RAG v2 未命中 drug_label_v2，停止用药安全 legacy 兜底: {query_text}; debug={rag_result.debug}"
            )
            return "本地权威药品标签证据不足，不能仅凭指南、旧库或公网结果回答该用药安全问题。", []
        if rag_result.items:
            docs_text = [
                f"【{item.source_tier}/{item.source_type}】{item.title}\n{item.text}"
                for item in rag_result.items
            ]
            source_cards = [item.to_source_card(i + 1) for i, item in enumerate(rag_result.items)]
            logger.info(f"✅ RAG v2 成功获取【{query_text}】用药证据 {len(source_cards)} 条。")
            return "\n\n---\n\n".join(docs_text), source_cards
    except Exception as e:
        logger.warning(f"⚠️ RAG v2 用药检索异常，停止高风险用药 legacy 兜底: {e}")
        return "本地权威药品标签检索异常，不能仅凭旧库或公网结果回答该用药安全问题。", []

    logger.warning(f"RAG v2 returned no drug-label evidence; DashVector fallback is disabled: {query_text}")
    return "本地权威药品标签证据不足，不能仅凭旧向量库或公网资料回答该用药安全问题。", []

    # 1. Legacy DashVector fallback is intentionally unreachable after Milvus full-switch.
    try:
        def _fetch_local():
            resp = dashscope.MultiModalEmbedding.call(
                model="qwen3-vl-embedding",
                input=[{'text': f"药品查询：{query_text}"}]
            )
            if resp.status_code == 200:
                vec = resp.output['embeddings'][0]['embedding']
                return _get_collection().query(vector=vec, topk=top_k, filter="source = 'drug_manual'")
            return None

        search_res = await asyncio.to_thread(_fetch_local)

        if search_res and search_res.output:
            for doc in search_res.output:
                content = doc.fields.get('content', '')
                drug_name = doc.fields.get('drug_name', query_text)
                doc_id = getattr(doc, "id", None) or doc.fields.get("id", "unknown")
                if content:
                    docs_text.append(content)
                    source_cards.append({
                        # 🌟 新增 ref_id / type / locator / snippet
                        "ref_id": f"doc:drug_manual#{doc_id}",
                        "type": "pdf",
                        "title": f"💊 《{drug_name}》官方说明书",
                        "label": f"《{drug_name}》官方说明书",
                        "content": content[:150] + "..." if len(content) > 150 else content,
                        "snippet": content[:300],
                        "locator": {"vector_id": doc_id, "drug_name": drug_name, "collection": "multimodal_medical_db"},
                        "url": "#",
                        "is_internal": True
                    })
    except Exception as e:
        logger.error(f"⚠️ 本地向量检索药品说明书异常或被阻断: {e}")

    if docs_text:
        logger.info(f"✅ 从本地 DashVector 向量库成功获取【{query_text}】说明书。")
        return "\n\n---\n\n".join(docs_text), source_cards

    # 2. 第二道防线：公网搜索引擎兜底逻辑 + LLM 榨汁机
    logger.warning(f"⚠️ 本地库缺失或检索失败，正在对【{query_text}】触发公网搜索兜底...")
    try:
        search_query = f"{query_text} 药品说明书 禁忌症 不良反应"

        # 🌟 核心修复 2：彻底接入系统级 Qwen Rerank 大动脉，并强约束域名
        web_context, web_sources = await search_dynamic_medical_info(
            query=search_query,
            raw_fetch_count=6,
            final_top_k=2,
            force_domain="AUTHORITATIVE,GENERAL"  # 🚨 强制要求只去正规医疗网或政府官网查药
        )

        if web_sources:
            logger.info(f"🌐 公网权威库兜底检索成功！正在启动 LLM 榨汁机进行微总结降噪...")

            prompt = f"""
            你是一个专业的临床药师。请将以下从搜索引擎抓取的关于【{query_text}】的用药资料，压缩成一段50字以内、通顺且专业的用药安全精炼结论。
            【严格要求】：直接输出结论，不要任何前缀废话，不要输出HTML标签。

            【搜索抓取片段】：
            {web_context}
            """

            try:
                summary_resp = await client.chat.completions.create(
                    model=DEFAULT_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=100
                )
                clean_summary = summary_resp.choices[0].message.content.strip()
            except Exception as e:
                logger.error(f"❌ 摘要压缩失败，使用截断降级: {e}")
                clean_summary = web_context[:100] + "..."

            docs_text.append(f"[🌐 权威平台实时检索兜底]\n{clean_summary}")

            # 使用 web_sources 中最相关的一条作为溯源卡片信息
            top_source = web_sources[0]
            top_url = top_source.get("url", "#")
            url_hash = hex(abs(hash(top_url)) & 0xFFFFFFFF)[2:]
            source_cards.append({
                # 🌟 新增 ref_id / type / locator / snippet
                "ref_id": f"web:{url_hash}",
                "type": "web",
                "title": f"🌐 【{query_text}】权威平台用药核查",
                "label": f"公网兜底·{query_text}",
                "content": clean_summary,
                "snippet": clean_summary,
                "locator": {"url": top_url, "query": query_text},
                "url": top_url,
                "is_internal": False
            })
            return "\n\n---\n\n".join(docs_text), source_cards

    except Exception as e:
        logger.error(f"❌ 公网搜索兜底也失败了: {e}")

    return "", []


# ==========================================
# 🌟 节点 1：药剂提取员 (Extractor)
# ==========================================
async def run_med_extractor(query: str, image_url: Optional[str], triage_drugs: List[str]) -> Tuple[
    str, List[str], List[str]]:
    logger.info("💊 [Node 1: Extractor] 启动...")
    audit_logs = ["[Med-Extractor] 药剂提取员已接管，正在嗅探意图并提取图文实体..."]
    await sse_emit("agent_step", agent="medication", phase="extract_start",
                   message="💊 药剂提取员开始识别药物实体…")
    extracted_drugs = list(triage_drugs)

    if image_url:
        audit_logs.append("[Med-Extractor] 检测到用户上传了图片，启动视觉多模态识别模块。")
        try:
            vision_prompt = """识别图片中的药品名称（通用名或商品名）。严格输出 JSON: {"drugs": ["药品名称1"]}，无药品输出 {"drugs": []}"""
            raw_json = await analyze_image_with_vision(image_url, vision_prompt)
            image_drugs = json.loads(raw_json).get("drugs", [])
            if image_drugs:
                extracted_drugs.extend(image_drugs)
                audit_logs.append(f"[Med-Extractor] 视觉识别成功，提取出靶向药物：{', '.join(image_drugs)}")
        except Exception as e:
            audit_logs.append("[Med-Extractor] 视觉识别失败。")
            logger.error(f"❌ 药盒图片识别失败: {e}")

    unique_drugs = list(set(extracted_drugs))

    if not unique_drugs:
        audit_logs.append(
            "[Med-Extractor] ⚠️ 未检测到任何明确的靶向药物。用户可能是在求取处方推荐。药师拒绝越权开药，强制将意图标记为【转交全科】。")
        await sse_emit("agent_step", agent="medication", phase="extract_done",
                       message="⚠️ 未识别到明确药名，转交全科兜底",
                       n_drugs=0, transfer=True)
        return "TRANSFER_TO_GENERAL", [], audit_logs

    intent = await classify_medication_intent(query)
    await sse_emit("agent_step", agent="medication", phase="extract_done",
                   message=f"✅ 提取到 {len(unique_drugs)} 个药物：{', '.join(unique_drugs[:3])}",
                   n_drugs=len(unique_drugs), drugs=unique_drugs, intent=intent)

    if intent == "General_Inquiry":
        audit_logs.append("[Med-Extractor] 意图嗅探为【通用科普】：已主动隔离患者隐私档案，防止发生错误关联警告。")
    else:
        audit_logs.append("[Med-Extractor] 意图嗅探为【安全审查】：已挂载患者个人健康档案，开启最高级别用药风控。")

    return intent, unique_drugs, audit_logs


# ==========================================
# 🌟 节点 2：审方药师 (Pharmacist)
# ==========================================
async def run_med_pharmacist(query: str, unique_drugs: List[str]) -> Tuple[str, dict, list, list, list]:
    """
    返回：(kg_context, vector_context, all_sources, audit_logs, kg_triples)
    """
    logger.info("💊 [Node 2: Pharmacist] 启动...")
    audit_logs = ["[Med-Pharmacist] 审方药师已接管，准备开展 Graph-RAG 双路资料检索..."]
    await sse_emit("agent_step", agent="medication", phase="pharmacist_start",
                   message="🔬 审方药师启动 Graph-RAG 双路检索…")
    all_sources, kg_context, vector_context = [], "", {}
    kg_triples: list = []

    if unique_drugs:
        audit_logs.append(f"[Med-Pharmacist] 启动 Graph-RAG 双路搜索引擎，查询目标: {', '.join(unique_drugs)}")

        await sse_emit("agent_step", agent="medication", phase="pharmacist_kg",
                       message=f"🧬 查询知识图谱禁忌：{', '.join(unique_drugs[:3])}")
        kg_text, kg_cards, kg_triples = await search_kg_contraindications(unique_drugs)

        if kg_text:
            kg_context = kg_text
            all_sources.extend(kg_cards)
            audit_logs.append(f"[Med-Pharmacist] 🚨 知识图谱雷达响应：命中 {len(kg_triples)} 条绝对禁忌三元组！")
            await sse_emit("agent_step", agent="medication", phase="pharmacist_kg_done",
                           message=f"🚨 KG 命中 {len(kg_triples)} 条绝对禁忌！", n_triples=len(kg_triples))
        else:
            audit_logs.append("[Med-Pharmacist] ✅ 知识图谱扫描通过：未命中绝对禁忌红线。")
            await sse_emit("agent_step", agent="medication", phase="pharmacist_kg_done",
                           message="✅ KG 扫描通过，无绝对禁忌", n_triples=0)

        await sse_emit("agent_step", agent="medication", phase="pharmacist_vector",
                       message=f"📖 向量检索 {len(unique_drugs)} 份药品说明书…")
        for drug in unique_drugs:
            manual_text, sources = await search_drug_manual(drug, 1)
            if manual_text:
                vector_context[drug] = manual_text
                all_sources.extend(sources)
        audit_logs.append("[Med-Pharmacist] 双路库响应：已提取药品核心说明书/禁忌原文。")
        await sse_emit("agent_step", agent="medication", phase="pharmacist_done",
                       message=f"✅ 双路检索完成：{len(kg_triples)} 条禁忌 + {len(vector_context)} 份说明书",
                       n_kg=len(kg_triples), n_vec=len(vector_context))
    else:
        audit_logs.append("[Med-Pharmacist] ⚠️ 未提取到特定药名，降级为全局模糊向量检索...")
        await sse_emit("agent_step", agent="medication", phase="pharmacist_fallback",
                       message="⚠️ 无药名，降级模糊向量检索")
        manual_text, sources = await search_drug_manual(query, 2)
        if manual_text:
            vector_context["综合检索结果"] = manual_text
            all_sources.extend(sources)

    if not vector_context:
        vector_context = {"提示": f"本地药典未命中，请调用内置常识。"}

    return kg_context, vector_context, all_sources, audit_logs, kg_triples


# ==========================================
# 🌟 节点 3：安全终审官 (Reviewer)
# ==========================================
async def run_med_reviewer(
        query: str, intent: str, patient_profile: dict,
        kg_context: str, vector_context: dict,
        extracted_drugs: list,
        act_intent: str = "",     # 🆕 二维内容轴
        attr_intent: str = "",
) -> Tuple[dict, list]:
    logger.info("💊 [Node 3: Reviewer] 启动...")
    audit_logs = ["[Med-Reviewer] 安全终审官已接管，准备开庭宣判..."]
    await sse_emit("agent_step", agent="medication", phase="reviewer_start",
                   message="⚖️ 安全终审官综合证据宣判中…")

    # 🆕 二维内容轴：让 prompt 偏重对应的内容侧（如 attr=BASIC 走科普口吻；attr=CAUTION 严抓禁忌）
    try:
        from core.intent_ontology import render_attr_focus as _focus, describe as _desc
        _attr_focus_text = _focus(attr_intent)
        if attr_intent or act_intent:
            audit_logs.append(f"🎯 [Med/Intent] {_desc(act_intent, attr_intent)}")
    except Exception:
        _attr_focus_text = ""

    is_general_inquiry = (intent == "General_Inquiry")

    if is_general_inquiry:
        profile_context_str = "【当前为通用百科科普模式，已主动屏蔽患者个人病史与档案。】"
        system_prompt = """
        你是一位三甲医院药剂科主任药师，有 20 年临床药学经验，也是医院里最受欢迎的用药科普讲师。
        你的风格：能把复杂的药理机制讲得像「装修指南」一样好懂——受体是锁，药物是钥匙，副作用是钥匙不小心捅错了锁孔。
        患者来问药，很多时候是第一次接触这个药名，心里没底。你的任务不是背说明书，而是帮患者建立对这个药的基本认知和安全感。

        当前处于【通用科普模式】——患者只是问客观知识，不是咨询个人用药方案。

        【工作流 — 请逐步思考】
        Step 1: 这是什么药？属于哪一类？（用一句话说清）
        Step 2: 它怎么起作用的？（用一个比喻，不要超过 3 句）
        Step 3: 吃这个药需要注意什么？（最常见 2-3 条注意事项）
        Step 4: 有什么常见误区需要提醒？

        【输出格式】
        ### 💊 {药名}小百科

        **一句话认识它**：{一句话}

        #### 🔬 它是怎么起作用的
        {2-3 句，用比喻解释}

        #### ⚠️ 服用时需要注意
        - 注意 1
        - 注意 2
        - 注意 3

        #### 💡 一个小提醒
        {一条常见误区或患者最关心的问题}

        总字数 300-400 字。

        【约束】
        - 绝对不用"警告"、"极高危"等恐吓性词汇（这是科普，不是看急诊）
        - 禁忌症用"不适合以下人群"替代"禁用"
        - 不用"KG"、"图谱"、"向量"等内部术语
        - 每条注意事项不超过 2 句

        【输出 JSON 格式】
        {
            "intent": "通用科普",
            "risk_level": "科普说明",
            "conflict_detected": "无",
            "pharmacist_advice": "按上述格式撰写的科普内容",
            "confidence_score": 0.95
        }
        """
    else:
        profile_context_str = json.dumps(patient_profile if patient_profile else {"提示": "该患者尚未填写健康档案。"},
                                         ensure_ascii=False)
        system_prompt = """
        你是一位三甲医院药剂科主任药师，专攻临床用药安全检测，有 20 年经验。
        你的核心职责是：帮这个具体的患者确认——ta 正在考虑或已经在吃的药，对 ta 是否安全。
        你既要有发现风险时的果断和严肃，也要在没有风险时让患者放心。

        当前处于【个人用药审查模式】——你拿到了患者的健康档案，请认真对照。

        【工作流 — 请逐步思考】
        Step 1: 查过敏——档案里有没有提到对这个药或同类药的过敏？
        Step 2: 查禁忌——KG 红线里有没有列出患者已有的疾病？
        Step 3: 查相互作用——这个药和患者档案里提到的其他药/食物有没有冲突？
        Step 4: 综合判定——给出风险等级和具体理由

        【安全风控（最高优先级）】
        1. 过敏史直判：患者对该药或核心成分有过敏史 → 直接 '极高危禁用'，不要犹豫
        2. 图谱红线直判：KG 禁忌表中列出患者已有疾病 → 直接 '极高危禁用'
        3. 以上两条未触发 → 综合说明书和档案给出评级

        【输出格式】
        ### 🛡️ 用药安全评估

        **药物**：{药名}
        **风险等级**：{🟢 安全可用 / 🟡 中等风险 / 🔴 极高危禁用}

        #### 📋 评估详情
        - **过敏排查**：{结论}
        - **禁忌排查**：{结论}
        - **相互作用**：{结论}

        #### 💊 药师建议
        {2-3 条，如果是高危，第一句必须明确告诉患者停止用药；如果是安全，告诉患者正常服用但注意观察}

        > ⚠️ 本评估基于当前知识库和患者档案生成，不能替代线下药学门诊。

        总字数 300-450 字。

        【输出 JSON 格式】
        {
            "intent": "用药审查",
            "risk_level": "极高危禁用" | "中等风险慎用" | "安全可用",
            "conflict_detected": "具体冲突点，无冲突填 '无'",
            "pharmacist_advice": "按上述格式撰写的评估内容",
            "confidence_score": 0.95
        }
        """

    drugs_str = ", ".join(extracted_drugs) if extracted_drugs else "未提取到明确药物"

    user_prompt = f"""
    患者咨询内容：{query}
    【系统已提取识别的药物】：{drugs_str}

    【患者健康档案】：
    {profile_context_str}

    【知识图谱绝对禁忌红线】：
    {kg_context if kg_context else "无图谱硬性约束记录"}

    【药典说明书详情】：
    {json.dumps(vector_context, ensure_ascii=False)}
    {_attr_focus_text}
    """

    try:
        response = await client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        result = json.loads(response.choices[0].message.content)
        await sse_emit("agent_step", agent="medication", phase="reviewer_done",
                       message=f"⚖️ 终审完成：风险等级【{result.get('risk_level', '未知')}】",
                       risk_level=result.get("risk_level"),
                       conflict=result.get("conflict_detected"))
        return result, audit_logs
    except Exception as e:
        logger.error(f"❌ 用药审查异常: {e}")
        await sse_emit("agent_step", agent="medication", phase="reviewer_error",
                       message="❌ 终审异常，启用兜底", error=str(e))
        return {"risk_level": "未知", "conflict_detected": "解析异常", "pharmacist_advice": "系统波动，请线下就医。",
                "confidence_score": 0.0}, audit_logs
