# agents/symptom_controller.py
import os
import logging
import asyncio
from typing import List, Dict, Any
from dotenv import load_dotenv, find_dotenv
from core.llm_client import shared_client as client, DEFAULT_MODEL

from agents.symptom_agent import analyze_and_clarify_symptom, SymptomAnalysisResult
from scripts.kg_pruner import KnowledgeGraphPruner
from scripts.vision_tool import analyze_image_with_vision
from scripts.main_agent import get_multimodal_context  # 🌟 引入本地知识库检索工具
from agents.maddx.integration import run_maddx_for_symptom_report  # 🆕 MADDx 集成层
from core.evidence import build_chain, dedupe_refs
from core.blackboard import Blackboard
from core.sse_emitter import emit as sse_emit

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("SymptomController")

# 🆕 MADDx 开关：默认开启；如需回退传统单 LLM 路径，设 USE_MADDX=false。
USE_MADDX = os.getenv("USE_MADDX", "true").lower() == "true"

kg_pruner_instance = KnowledgeGraphPruner()


async def generate_final_diagnosis(
        current_slots: Dict[str, str],
        kg_context: str,
        local_guide_context: str,
        vision_context: str = None,
        med_precheck: dict = None,
        patient_profile: dict = None,
        insight_text: str = "",  # 🧠 见解知识库注入
        collab_models: list = None,  # 🆕 跨模型辩论
) -> Any:
    """
    生成最终诊断报告。
    返回：
      - USE_MADDX=False 时：str（纯 Markdown，兼容旧调用）
      - USE_MADDX=True  时：dict {"markdown": str, "blackboard_trace": dict}（含辩论溯源）
    """
    logger.info("👨‍⚕️ [Final Diagnosis] 正在融合多模态证据链，生成最终诊断报告...")

    # ========== 🆕 MADDx 分支 ==========
    if USE_MADDX:
        logger.info("🔀 [MADDx] 已启用多智能体鉴别诊断辩论")
        try:
            # 🆕 将 collab_models 映射为 MADDx agent_models [Proposer, Critic, Defender, Moderator]
            _MODEL_KEY_TO_ID = {
                "deepseek": "deepseek-v4-pro",
                "qwen": "qwen-max",
                "glm": "glm-5.1",
            }
            maddx_agent_models = None
            if collab_models and len(collab_models) >= 2:
                maddx_agent_models = [
                    _MODEL_KEY_TO_ID.get(collab_models[0], None),  # Proposer
                    _MODEL_KEY_TO_ID.get(collab_models[1], None),  # Critic: 跨模型
                    None,  # Defender: 默认 REASONING_MODEL
                    None,  # Moderator: 默认 REASONING_MODEL
                ]

            markdown, bb, events_log = await run_maddx_for_symptom_report(
                slots=current_slots,
                patient_profile=patient_profile or {},
                kg_context=kg_context or "",
                local_guide_context=local_guide_context or "",
                vision_context=vision_context,
                med_precheck=med_precheck,
                agent_models=maddx_agent_models,
            )
            return {
                "markdown": markdown,
                "blackboard_trace": bb.to_trace_dag(),
                "maddx_events": events_log,
            }
        except Exception as e:
            logger.error(f"⚠️ MADDx 执行失败，降级到传统单 LLM 路径: {e}", exc_info=True)
            # 继续往下走传统路径

    system_prompt = """
    【角色】
    你是三甲医院全科主任医师，在门诊一线工作了 20 年，带过无数住院医。
    你的风格：不卖弄术语。复杂的病理过程，你会用生活化的比喻解释清楚。
    你问诊时温和但高效，患者离开诊室时不仅知道「我可能是什么病」，
    还知道「接下来该做什么」、「什么情况需要立刻回来」。

    【工作流 — 请逐步思考】
    Step 1: 梳理患者的核心症状模式——哪几个症状是「一家的」？哪个是最需要警惕的？
    Step 2: 结合辅助证据——图谱推理路径提示了什么？影像有无异常？用药是否冲突？
    Step 3: 给出综合判断——最可能的诊断方向是什么？还需要排除什么？
    Step 4: 提出行动计划——挂号什么科？做什么检查？什么情况必须立即就医？

    【输出格式】
    ### 🩺 会诊意见

    用 2-3 句通俗的话，总结你看到的症状模式和你的总体判断。不要用「证据链」这种词，
    用「根据你描述的...来看」这种自然的医生口吻。

    ### 🔬 可能的诊断方向
    - **{疾病名}（可能性较高/中等/需排除）**：一句话解释为什么，用患者能听懂的语言
    - 列出 2-3 个方向，按可能性排序

    ### 🏥 接下来你可以这样做
    - **挂号建议**：挂什么科
    - **检查建议**：可能需要做什么检查，每项一句话说明目的
    - **⚠️ 需要警惕的信号**：列出 2-3 个危险信号，出现任何一个就要立即就医

    ### 💊 关于用药
    （仅当上下文中有用药核查报告时输出此项。告知患者当前涉及的药物能否继续服用。）

    总字数 400-550 字。用 Markdown 但不要用 H1(#) 或 H2(##)，统一用 H3(###) 作为小节标题。
    关键医学术语和诊断名用 **加粗** 突出，方便患者阅读。

    【约束】
    - 绝不说"知识图谱"、"向量库"、"系统设定"、"证据链"等内部术语
    - 完全以医生的口吻输出，像在诊室里对患者说话
    - 不确定的地方诚实说"需要进一步检查才能确定"，但不要回避给出判断
    - 不用"患者"称呼，用"你"
    - 开头先用 1 句话共情（如"头疼确实很折磨人"），再进入分析
    """

    # 构建强大的多模态上下文沙盒
    user_prompt = f"【患者已确认症状】\n{current_slots}\n\n【内部图谱推理路径】\n{kg_context}\n\n【本地权威医学指南】\n{local_guide_context}"
    if insight_text:
        user_prompt += f"\n\n{insight_text}"

    if vision_context:
        user_prompt += f"\n\n【多模态视觉提取特征】\n{vision_context}"
    if med_precheck:
        user_prompt += f"\n\n【用药安全核查报告】\n知识图谱红线：{med_precheck.get('kg_warnings', '无')}\n说明书摘要：{med_precheck.get('manual_summary', '无')}"

    try:
        response = await client.chat.completions.create(
            model=DEFAULT_MODEL,
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}],
            temperature=0.3
        )
        return response.choices[0].message.content
    except Exception as e:
        logger.error(f"❌ 最终诊断生成失败: {e}")
        return "抱歉，系统在生成最终诊断报告时出现波动，请稍后再试或直接前往医院就诊。"


# ==========================================
# 🔗 D8-D9：症状追踪证据链构造器
# 多轮覆盖语义：每轮的 chain 独立、不累加；triples 反映累积的槽位快照。
# ==========================================
def _parse_kg_insight(kg_text: str, local_cards: list) -> dict:
    """从 KGPruner 文本输出中提取结构化数据，供前端知识图谱卡片渲染。"""
    import re
    insight = {"diseases": [], "drugs": [], "symptoms": [], "department": ""}

    if not kg_text:
        logger.debug("[KG Insight] kg_text 为空，跳过解析")
        return insight

    # 提取疾病: "核心目标 N: [疾病名] (类型: 疾病, 联合置信度: X.XX)"
    disease_pattern = re.findall(r'核心目标 \d+: \[(.+?)\] \(类型: 疾病, 联合置信度: ([\d.]+)\)', kg_text)
    for name, score in disease_pattern:
        insight["diseases"].append({"name": name, "score": float(score)})

    # 提取药物: "核心目标 N: [药物名] (类型: 药物, 联合置信度: X.XX)"
    drug_pattern = re.findall(r'核心目标 \d+: \[(.+?)\] \(类型: 药物, 联合置信度: ([\d.]+)\)', kg_text)
    for name, score in drug_pattern:
        insight["drugs"].append({"name": name, "score": float(score)})

    logger.info(
        f"[KG Insight] 解析结果: diseases={len(insight['diseases'])}, "
        f"drugs={len(insight['drugs'])}, symptoms={len(insight['symptoms'])}, "
        f"department={insight['department']!r}"
    )

    # 提取症状: "搏动性头痛 -[HAS_SYMPTOM]-> 偏头痛"
    symptom_pattern = re.findall(r'(\S+) -\[HAS_SYMPTOM\]->', kg_text)
    insight["symptoms"] = list(set(symptom_pattern))[:8]

    # 科室: 从 local_cards 中提取
    for card in (local_cards or [])[:3]:
        dept = card.get("department", "") if isinstance(card, dict) else ""
        if dept and dept not in insight["department"]:
            insight["department"] = dept
            break

    return insight


SLOT_LABEL_CN = {
    "location": "位置",
    "character": "性质",
    "duration": "持续时间",
    "trigger": "诱因",
    "radiation": "放射部位",
    "alleviating_factors": "缓解因素",
    "associated_symptoms": "伴随症状",
}


def _slots_main_symptom(slots: Dict[str, str], fallback_query: str) -> str:
    """从槽位/查询里提取一个'主症状'作为 triple 的 head。"""
    for k in ("character", "associated_symptoms", "location"):
        v = slots.get(k)
        if v and isinstance(v, str):
            return v[:20]
    return (fallback_query or "主诉症状")[:20]


def _slot_triples(slots: Dict[str, str], main_sym: str) -> List[Dict[str, Any]]:
    """把已填槽位转成 (主症状, 观察特征, 字段=值) 三元组。"""
    triples: List[Dict[str, Any]] = []
    for k, v in (slots or {}).items():
        if not v or not isinstance(v, str):
            continue
        label = SLOT_LABEL_CN.get(k, k)
        triples.append({
            "head": main_sym,
            "relation": "观察特征",
            "tail": f"{label}={v[:30]}",
            "tail_type": "Slot",
            "source_id": "profile:symptom_slots",
            "confidence": 0.95,
        })
    return triples


def _local_card_to_ref(card: Dict[str, Any], idx: int) -> Dict[str, Any]:
    """本地指南卡 → EvidenceRef。"""
    title = (card.get("title") or card.get("disease") or f"指南片段 #{idx}")
    snippet = (card.get("content") or "")[:300]
    sid = card.get("id", idx + 1)
    return {
        "ref_id": f"doc:symptom_guideline#{card.get('type', 'general')}_{sid}",
        "type": "kg" if card.get("type") == "kg" else "pdf",
        "label": str(title)[:60],
        "locator": {
            "card_id": sid,
            "card_type": card.get("type", "general"),
            "disease": card.get("disease", ""),
            "department": card.get("department", ""),
        },
        "snippet": snippet,
    }


def _build_symptom_evidence_chain(
    *,
    phase: str,                              # "CLARIFYING" / "READY" / "ERROR"
    turn_count: int,
    main_symptom: str,
    current_slots: Dict[str, str],
    missing_slots: List[str],
    extracted_keywords: List[str],
    vision_context: str = None,
    med_precheck: dict = None,
    kg_reasoning_context: str = None,
    local_cards: List[dict] = None,
    final_markdown: str = None,
    has_maddx: bool = False,
) -> dict:
    refs: List[Dict[str, Any]] = []
    triples: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []

    # ---------- 1) 槽位快照（每轮都有）----------
    if current_slots:
        slot_summary = " / ".join(
            f"{SLOT_LABEL_CN.get(k, k)}={v}" for k, v in current_slots.items() if v
        )[:280]
        refs.append({
            "ref_id": "profile:symptom_slots",
            "type": "profile",
            "label": f"已收集症状槽位（{len(current_slots)} 项）",
            "locator": {"slot_keys": list(current_slots.keys()), "turn": turn_count},
            "snippet": slot_summary,
        })
        triples.extend(_slot_triples(current_slots, main_symptom))

    # ---------- 2) 跨模态注入（仅首轮 controller 有）----------
    if vision_context:
        refs.append({
            "ref_id": "image:symptom_vision",
            "type": "image",
            "label": "上游视觉模型提取的体征",
            "locator": {"injected_turn": turn_count},
            "snippet": str(vision_context)[:280],
        })
    if med_precheck and isinstance(med_precheck, dict):
        kg_warn = (med_precheck.get("kg_warnings") or "").strip()
        manual = (med_precheck.get("manual_summary") or "").strip()
        snip = (kg_warn + ("\n\n" + manual if manual else ""))[:280]
        if snip:
            refs.append({
                "ref_id": "doc:symptom_med_precheck",
                "type": "pdf",
                "label": "上游用药红线初筛",
                "locator": {"injected_turn": turn_count},
                "snippet": snip,
            })

    # ---------- 3) Step 1: 槽位分析（每轮都有）----------
    step1_out = (
        f"已填 {len(current_slots or {})} 项 / 缺失 {len(missing_slots or [])} 项 → {phase}"
    )
    steps.append({
        "step": 1,
        "actor": "symptom.slot_analysis",
        "action": "槽位填充与缺失评估",
        "input_summary": f"第 {turn_count} 轮 · main={main_symptom}",
        "output_summary": step1_out,
        "cited_refs": ["profile:symptom_slots"] if current_slots else [],
    })

    if phase != "READY":
        # CLARIFYING / ERROR：到此为止
        confidence = 0.5 if phase == "CLARIFYING" else 0.4
        next_q = "、".join(missing_slots[:3]) if missing_slots else "更多细节"
        return build_chain(
            triples=triples,
            reasoning_path=steps,
            refs=dedupe_refs(refs),
            final_claim=f"第 {turn_count} 轮 · 已收集 {len(current_slots or {})} 项症状特征，正在追问：{next_q}",
            confidence=confidence,
        )

    # ---------- READY：诊断完成，链路扩展 ----------

    # Step 2: KG 推理
    kg_ref_id = None
    if kg_reasoning_context:
        kg_hash = hex(abs(hash(kg_reasoning_context)) & 0xFFFFFFFF)[2:]
        kg_ref_id = f"kg:symptom_reasoning_{kg_hash}"
        refs.append({
            "ref_id": kg_ref_id,
            "type": "kg",
            "label": f"症状图谱多跳推理（关键词 {len(extracted_keywords)} 个）",
            "locator": {"keywords": extracted_keywords},
            "snippet": str(kg_reasoning_context)[:300],
        })
        steps.append({
            "step": len(steps) + 1,
            "actor": "symptom.kg_reasoning",
            "action": "Neo4j 图谱多跳推理",
            "input_summary": "关键词=" + ",".join(extracted_keywords[:5]),
            "output_summary": "命中候选疾病/科室路径",
            "cited_refs": [kg_ref_id],
        })

    # Step 3: 本地指南检索
    if local_cards:
        card_refs = [_local_card_to_ref(c, i) for i, c in enumerate(local_cards) if isinstance(c, dict)]
        refs.extend(card_refs)
        steps.append({
            "step": len(steps) + 1,
            "actor": "symptom.guideline_retrieval",
            "action": "DashVector 临床指南检索",
            "input_summary": "靶向关键词=" + " ".join(extracted_keywords[:5]),
            "output_summary": f"召回 {len(card_refs)} 条权威指南",
            "cited_refs": [r["ref_id"] for r in card_refs],
        })

    # Step 4: 综合诊断（或 MADDx 辩论）
    if has_maddx:
        steps.append({
            "step": len(steps) + 1,
            "actor": "symptom.maddx_debate",
            "action": "多智能体鉴别诊断辩论",
            "input_summary": "症状槽位 + KG + 指南 + 跨模态",
            "output_summary": (final_markdown or "")[:80],
            "cited_refs": [r["ref_id"] for r in refs if r["type"] in ("kg", "pdf")],
        })
        # 给 MADDx 一条诊断方向 triple（粗粒度）
        triples.append({
            "head": main_symptom,
            "relation": "辩论裁决",
            "tail": "已生成多智能体鉴别诊断报告",
            "tail_type": "Verdict",
            "source_id": kg_ref_id or "profile:symptom_slots",
            "confidence": 0.85,
        })
    else:
        steps.append({
            "step": len(steps) + 1,
            "actor": "symptom.diagnosis_synthesis",
            "action": "全科主任综合会诊",
            "input_summary": "症状槽位 + KG + 指南 + 跨模态",
            "output_summary": (final_markdown or "")[:80],
            "cited_refs": [r["ref_id"] for r in refs if r["type"] in ("kg", "pdf")],
        })

    # 给一条粗粒度的"诊断方向" triple，head=主症状，tail=候选疾病（从 local_cards 启发）
    candidate_diseases = []
    for c in (local_cards or [])[:3]:
        if isinstance(c, dict):
            d = c.get("disease")
            if d and isinstance(d, str) and d not in candidate_diseases and d != "未知疾病":
                candidate_diseases.append(d)
    for dz in candidate_diseases[:2]:
        triples.append({
            "head": main_symptom,
            "relation": "诊断方向",
            "tail": dz,
            "tail_type": "Disease",
            "source_id": kg_ref_id or "profile:symptom_slots",
            "confidence": 0.7,
        })

    confidence = 0.9 if has_maddx else 0.85
    if not local_cards and not kg_reasoning_context:
        confidence = 0.55  # 无外部证据，降级

    return build_chain(
        triples=triples,
        reasoning_path=steps,
        refs=dedupe_refs(refs),
        final_claim=(final_markdown or "")[:120] or f"已收集 {len(current_slots)} 项症状特征，综合诊断报告已生成",
        confidence=confidence,
    )


async def run_symptom_track(
        messages_history: List[Dict[str, str]],
        turn_count: int = 1,
        current_slots: Dict[str, str] = None,
        vision_context: str = None,  # 🌟 新增：由预处理节点传来的视觉上下文
        med_precheck: dict = None,  # 🌟 新增：由预处理节点传来的用药初筛上下文
        patient_profile: dict = None,  # 🆕 MADDx 所需的患者基础档案
        act_intent: str = "",   # 🆕 二维内容轴
        attr_intent: str = "",
        blackboard: Any = None,
        bb_parent_version: int = 0,
        insight_text: str = "",
        collab_models: list = None,     # 🆕
) -> Dict[str, Any]:
    if current_slots is None:
        current_slots = {}

    audit_logs = []

    logger.info(f"🚀 触发【复合问诊主干：症状分析】| 当前轮次: {turn_count}")
    audit_logs.append(f"[Symptom] 启动第 {turn_count} 轮症状追踪分析。")
    await sse_emit("agent_step", agent="symptom", phase="slot_start",
                   message=f"📋 第 {turn_count} 轮症状槽位追踪…", turn=turn_count,
                   filled=list((current_slots or {}).keys()))

    # 🆕 二维内容轴：审计日志（实际偏重通过参数透传给 analyze_and_clarify_symptom）
    try:
        from core.intent_ontology import describe as _desc_intent
        if act_intent or attr_intent:
            audit_logs.append(f"🎯 [Symptom/Intent] {_desc_intent(act_intent, attr_intent)}")
    except Exception:
        pass

    # ==========================================
    # 🌟 核心升级 1：首轮静默上下文注入
    # 替代了原来在这里直接调视觉模型的逻辑，改为接收上游处理好的多模态数据
    # ==========================================
    if turn_count == 1:
        context_injection = ""
        if vision_context:
            audit_logs.append("[Symptom] 接收到上游并发传来的视觉体征，已注入上下文。")
            context_injection += f"\n\n[系统辅助信息：多模态视觉模型已从患者上传的图片中提取到体征：{vision_context}]"

        if med_precheck:
            audit_logs.append("[Symptom] 接收到上游并发传来的用药初筛结果，已注入上下文。")
            # 提取图谱警告和简短的说明书摘要
            warnings = med_precheck.get('kg_warnings', '')
            manual = med_precheck.get('manual_summary', '')[:150]
            context_injection += f"\n\n[系统辅助信息：患者提及的药物初筛结果 - 禁忌警告：{warnings if warnings else '无'} | 说明书摘要：{manual}...]"

        if context_injection:
            # 🌟 核心修复：绝对不能直接拼接在用户的 content 后面（会导致触发 500 字符截断）
            # 而是作为一个独立的 system 角色，安插在用户发话的前面！
            messages_history.insert(-1, {"role": "system", "content": context_injection})

    # ==========================================
    # 🌟 核心逻辑 2：调用槽位分析大模型进行追踪
    # ==========================================
    analysis_res: SymptomAnalysisResult = await analyze_and_clarify_symptom(
        messages_history=messages_history, turn_count=turn_count, current_slots=current_slots,
        act_intent=act_intent, attr_intent=attr_intent,  # 🆕 透传二维内容轴
    )

    status = analysis_res.get("status")
    new_slots = analysis_res.get("filled_slots", current_slots)
    missing = analysis_res.get("missing_slots", [])

    # 兜底：模型说 CLARIFYING 但没缺失槽位 → 自动推为 READY
    if status == "CLARIFYING" and not missing:
        logger.warning("[Symptom] CLARIFYING 但 missing_slots 为空，自动升级为 READY")
        status = "READY"

    if status == "CLARIFYING":
        audit_logs.append(
            f"[Symptom] 临床信息尚未充足。已收集槽位: {list(new_slots.keys())}。准备向患者追问缺失信息: {missing}。")
        await sse_emit("agent_step", agent="symptom", phase="clarifying",
                       message=f"📋 已收集 {len(new_slots)} 项 / 仍需追问 {len(missing)} 项",
                       turn=turn_count, filled=list(new_slots.keys()), missing=missing)
        # 🔗 D8-D9：CLARIFYING 轮证据链（仅含槽位快照 + slot_analysis 单步）
        last_user_msg = ""
        for m in reversed(messages_history):
            if m.get("role") == "user":
                last_user_msg = m.get("content", "")
                break
        evidence_chain = _build_symptom_evidence_chain(
            phase="CLARIFYING",
            turn_count=turn_count,
            main_symptom=_slots_main_symptom(new_slots, last_user_msg),
            current_slots=new_slots,
            missing_slots=missing,
            extracted_keywords=analysis_res.get("extracted_keywords", []) or [],
            vision_context=vision_context if turn_count == 1 else None,
            med_precheck=med_precheck if turn_count == 1 else None,
        )
        # 🗒️ 黑板：CLARIFYING 单条 entry，记录槽位快照与缺失项
        if isinstance(blackboard, Blackboard):
            try:
                await blackboard.append(
                    "symptom_slots",
                    {
                        "turn": turn_count,
                        "phase": "CLARIFYING",
                        "filled": new_slots,
                        "missing": missing,
                        "n_options": len(analysis_res.get("options", []) or []),
                    },
                    agent_id="symptom.slot_analysis",
                    parent_refs=[bb_parent_version] if bb_parent_version else [],
                )
            except Exception as e:
                logger.warning(f"[Symptom/BB] CLARIFYING append 失败: {e}")
        return {
            "is_finished": False,
            "answer": analysis_res.get("doctor_reply"),
            "options": analysis_res.get("options", []),
            "turn_count": turn_count + 1,
            "current_slots": new_slots,
            "audit_logs": audit_logs,
            "evidence_chain": evidence_chain,
        }

    # ==========================================
    # 🌟 核心逻辑 3：信息收集完毕，并发击穿图谱与向量库
    # （完美保留了你原来的极客并发逻辑）
    # ==========================================
    logger.info("✅ 症状信息已满足阈值，正在并发击穿【图谱层】与【本地指南库】...")
    audit_logs.append("[Symptom] 症状信息已满足阈值，停止追问，进入临床决策阶段！")
    extracted_keywords = analysis_res.get("extracted_keywords", list(new_slots.values()))
    audit_logs.append(f"[Symptom] 提取底层检索靶向关键词：{extracted_keywords}")

    await sse_emit("agent_step", agent="symptom", phase="ready",
                   message=f"✅ 槽位齐备，并发检索 KG + 指南库（关键词 {len(extracted_keywords)} 个）",
                   keywords=extracted_keywords)

    audit_logs.append("[Symptom] 正在并发执行 Neo4j 图谱深度推演与 DashVector 靶向检索...")
    search_query = " ".join(extracted_keywords)

    try:
        # 将极其耗时的同步图谱搜索踢到后台线程池
        kg_task = asyncio.to_thread(
            kg_pruner_instance.execute_pruning,
            extracted_keywords=extracted_keywords,
            top_k=2,
            base_threshold=10.0
        )

        vector_task = get_multimodal_context(search_query)

        # 🚀 齐头并进：gather 会同时发起两个请求，取最慢的一个作为最终耗时
        kg_reasoning_context, vector_res = await asyncio.gather(kg_task, vector_task)

        # 解包向量库的结果并做防爆窗截断
        local_context, local_cards, images = vector_res
        local_cards = local_cards[:5]

        logger.info(f"📚 双路并发召回成功！图谱已穿透，本地指南召回 {len(local_cards)} 条！")
        audit_logs.append(f"[Symptom] 双轨检索并发完成！成功截取置信度最高的 {len(local_cards)} 条本地指南参考数据。")
        await sse_emit("agent_step", agent="symptom", phase="retrieve_done",
                       message=f"📚 双路检索完成：KG 路径 + {len(local_cards)} 条指南",
                       n_cards=len(local_cards),
                       has_kg=bool(kg_reasoning_context))

    except Exception as e:
        logger.error(f"⚠️ 并发知识库检索失败，将降级仅使用通用模型兜底: {e}")
        audit_logs.append("[Symptom] 并发检索出现异常，系统自动降级处理。")
        kg_reasoning_context = ""
        local_context, local_cards = "未检索到相关的本地临床指南。", []

    # ==========================================
    # 🌟 核心升级 4：生成最终的宏大报告（传入全部跨模态上下文）
    # ==========================================
    audit_logs.append("[Symptom] 双轨数据源与跨模态上下文已齐备，正在生成最终的 AI 综合会诊报告...")
    await sse_emit("agent_step", agent="symptom", phase="diagnosis_start",
                   message="👨‍⚕️ 全科主任正在生成综合会诊报告…")

    final_result = await generate_final_diagnosis(
        current_slots=new_slots,
        kg_context=kg_reasoning_context,
        local_guide_context=local_context,
        vision_context=vision_context,
        med_precheck=med_precheck,
        patient_profile=patient_profile,
        insight_text=insight_text,
        collab_models=collab_models,  # 🆕
    )

    # 🆕 兼容两种返回类型：str（传统路径） / dict（MADDx 路径）
    maddx_events = None
    if isinstance(final_result, dict):
        final_markdown = final_result["markdown"]
        blackboard_trace = final_result.get("blackboard_trace")
        maddx_events = final_result.get("maddx_events")
        audit_logs.append("[Symptom] MADDx 多智能体辩论完成，诊断报告已生成。")
    else:
        final_markdown = final_result
        blackboard_trace = None

    audit_logs.append("[Symptom] 诊断报告生成完毕，系统放行下发。")

    # 🗒️ 黑板：READY 路径 4 条 entry，按因果链 slots → (kg ∥ gl) → diagnosis 串起
    bb_versions: Dict[str, int] = {}
    if isinstance(blackboard, Blackboard):
        try:
            v_slots = await blackboard.append(
                "symptom_slots",
                {
                    "turn": turn_count,
                    "phase": "READY",
                    "filled": new_slots,
                    "extracted_keywords": extracted_keywords,
                },
                agent_id="symptom.slot_analysis",
                parent_refs=[bb_parent_version] if bb_parent_version else [],
            )
            bb_versions["slots"] = v_slots

            if kg_reasoning_context:
                v_kg = await blackboard.append(
                    "symptom_kg_path",
                    {
                        "n_keywords": len(extracted_keywords),
                        "preview": str(kg_reasoning_context)[:200],
                    },
                    agent_id="symptom.kg_reasoning",
                    parent_refs=[v_slots] if v_slots else [],
                )
                bb_versions["kg"] = v_kg

            if local_cards:
                v_gl = await blackboard.append(
                    "symptom_guidelines",
                    {
                        "n_cards": len(local_cards),
                        "card_titles": [c.get("title", "") for c in local_cards[:5] if isinstance(c, dict)],
                        "diseases": list({
                            c.get("disease", "") for c in local_cards
                            if isinstance(c, dict) and c.get("disease") and c.get("disease") != "未知疾病"
                        })[:5],
                    },
                    agent_id="symptom.guideline_retrieval",
                    parent_refs=[v_slots] if v_slots else [],
                )
                bb_versions["gl"] = v_gl

            diag_parents = [v for v in (bb_versions.get("kg"), bb_versions.get("gl")) if v]
            if not diag_parents and v_slots:
                diag_parents = [v_slots]
            if blackboard_trace and isinstance(blackboard_trace, dict):
                child_nodes = blackboard_trace.get("nodes", []) or []
                child_edges = blackboard_trace.get("edges", []) or []
                child_edge_from = {e.get("from") for e in child_edges if isinstance(e, dict)}
                child_edge_to = {e.get("to") for e in child_edges if isinstance(e, dict)}
                start_nodes = [
                    n for n in child_nodes
                    if isinstance(n, dict) and n.get("id") not in child_edge_to
                ][:3]
                end_nodes = [
                    n for n in child_nodes
                    if isinstance(n, dict) and n.get("id") not in child_edge_from
                ][:3]
                v_maddx = await blackboard.append(
                    "symptom_maddx_child_dag",
                    {
                        "n_child_nodes": len(child_nodes),
                        "n_child_edges": len(child_edges),
                        "start_nodes": [
                            {"id": n.get("id"), "label": n.get("label"), "agent": n.get("agent")}
                            for n in start_nodes
                        ],
                        "end_nodes": [
                            {"id": n.get("id"), "label": n.get("label"), "agent": n.get("agent")}
                            for n in end_nodes
                        ],
                    },
                    agent_id="symptom.maddx_bridge",
                    parent_refs=diag_parents,
                )
                if v_maddx:
                    bb_versions["maddx_bridge"] = v_maddx
                    diag_parents = [v_maddx]
            await blackboard.append(
                "symptom_diagnosis",
                {
                    "preview": (final_markdown or "")[:160] if isinstance(final_markdown, str)
                               else str(final_markdown)[:160],
                    "use_maddx": bool(blackboard_trace),
                },
                agent_id="symptom.maddx_debate" if blackboard_trace else "symptom.synthesis",
                parent_refs=diag_parents,
            )
        except Exception as e:
            logger.warning(f"[Symptom/BB] READY append 失败（不阻断）: {e}")

    # 🔗 D8-D9：READY 完成轮证据链（完整链路：slot → KG → 指南 → 综合/MADDx）
    last_user_msg = ""
    for m in reversed(messages_history):
        if m.get("role") == "user":
            last_user_msg = m.get("content", "")
            break
    try:
        evidence_chain = _build_symptom_evidence_chain(
            phase="READY",
            turn_count=turn_count,
            main_symptom=_slots_main_symptom(new_slots, last_user_msg),
            current_slots=new_slots,
            missing_slots=[],
            extracted_keywords=extracted_keywords or [],
            vision_context=vision_context,
            med_precheck=med_precheck,
            kg_reasoning_context=kg_reasoning_context,
            local_cards=local_cards,
            final_markdown=final_markdown,
            has_maddx=bool(blackboard_trace),
        )
        audit_logs.append(
            f"[Symptom/Chain] 证据链：{len(evidence_chain.get('triples', []))} triples / "
            f"{len(evidence_chain.get('refs', []))} refs / "
            f"{len(evidence_chain.get('reasoning_path', []))} steps"
        )
    except Exception as e:
        logger.warning(f"[Symptom/Chain] 证据链组装异常（不阻断）: {e}")
        evidence_chain = {}

    # 🆕 从 KG 文本中提取结构化 insight 给前端展示
    kg_insight = _parse_kg_insight(kg_reasoning_context, local_cards)

    return {
        "is_finished": True,
        "answer": final_markdown,
        "options": [],
        "turn_count": turn_count,
        "current_slots": new_slots,
        "debug_kg_path": kg_reasoning_context,
        "local_cards": local_cards,
        "kg_insight": kg_insight,                  # 🆕 KG 结构化洞察
        "audit_logs": audit_logs,
        "blackboard_trace": blackboard_trace,
        "maddx_events": maddx_events,
        "evidence_chain": evidence_chain,
    }
