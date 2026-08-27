# agents/report_agent.py
import os
import re
import json
import logging
from urllib.parse import urlparse
from typing import Dict, Any, List, Optional, TypedDict
from abc import ABC, abstractmethod
from openai import AsyncOpenAI
from dotenv import load_dotenv, find_dotenv

from scripts.vision_tool import analyze_image_with_vision
from scripts.main_agent import get_multimodal_context  # 🌟 接入真实的全局检索引擎
from core.evidence import build_chain, dedupe_refs
from core.sse_emitter import emit as sse_emit
from core.llm_client import DEFAULT_MODEL

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger(__name__)


# ==========================================
# 🌟 规则前置：判断 query 是否真的需要走"化验单解读"全流水线
# ==========================================

# 化验单/影像/指标相关关键词（命中 → 真的有解读需求）
_REPORT_KEYWORDS = [
    # 报告类型
    "化验", "化验单", "报告单", "体检报告", "检查报告", "病理报告", "影像", "CT", "MRI",
    "B超", "彩超", "X光", "心电图", "尿检", "血常规", "肝功", "肾功", "甲功",
    # 常见指标
    "尿酸", "血糖", "血压", "胆固醇", "甘油三酯", "白细胞", "红细胞", "血红蛋白",
    "血小板", "肌酐", "尿素氮", "ALT", "AST", "TSH", "HbA1c", "糖化",
    "甲胎蛋白", "CEA", "PSA", "肿瘤标志物",
    # 异常表述
    "偏高", "偏低", "异常", "阳性", "阴性", "超标", "不达标",
]
# 数字+单位 pattern（如 "520 umol/L"、"7.5mmol"）—— 强信号
_INDICATOR_NUM_PATTERN = re.compile(
    r"\d+(\.\d+)?\s*(mmol|umol|μmol|mg|g|ng|pg|U|IU|mmHg|mol|%|×10)",
    re.IGNORECASE,
)


def needs_report_pipeline(query: str, image_url: Optional[str]) -> tuple[bool, str]:
    """
    规则前置：决定是否走完整的 Vision+OCR+检索流水线。

    Returns:
        (needs_pipeline, reason)
    """
    # 规则 1：有图片 → 一定走流水线（OCR 是核心价值）
    if image_url and image_url.strip():
        return True, "has_image"

    # 规则 2：无图片但 query 命中报告关键词 → 走流水线（用户在描述指标）
    q = query or ""
    hit_kw = next((kw for kw in _REPORT_KEYWORDS if kw in q), None)
    if hit_kw:
        return True, f"keyword_hit:{hit_kw}"

    # 规则 3：query 含"数字 + 医学单位" → 用户在描述指标
    if _INDICATOR_NUM_PATTERN.search(q):
        return True, "numeric_indicator"

    # 都不命中 → 跳过流水线
    return False, "no_image_no_keyword"


# ==========================================
# 定义严谨的 TypedDict 数据结构契约
# ==========================================
class ExtractedIndicator(TypedDict):
    name: str
    value: str
    unit: str
    status: str


class ReportResult(TypedDict, total=False):
    answer: str
    extracted_data: List[ExtractedIndicator]
    guidelines: str
    sources: list             # 🌟 强制要求将底层检索文献透传给上层
    format: str
    evidence_chain: dict      # 🌟 D5 新增：统一证据链（schema 见 core/evidence.py）
    skipped_reason: str       # 规则前置跳过原因（早退分支才有）


# ==========================================
# 抽象检索接口，解耦模拟数据与真实向量库
# ==========================================
class GuidelineRetriever(ABC):
    @abstractmethod
    # 🌟 契约升级：要求返回 (文本上下文, 结构化卡片列表)
    async def retrieve(self, indicators: List[ExtractedIndicator], query: str) -> tuple[str, list]:
        pass


class VectorGuidelineRetriever(GuidelineRetriever):
    """真实生产环境：接入 DashVector 本地知识库与脱水中间件"""

    async def retrieve(self, indicators: List[ExtractedIndicator], query: str) -> tuple[str, list]:
        # 构建高密度检索词：优先使用 OCR 提取出来的异常指标名
        search_terms = [item.get("name", "") for item in indicators if item.get("name")]

        search_query = " ".join(search_terms) if search_terms else query
        if not search_query.strip():
            return "无有效检索词，无法进行精确指南核对。", []

        logger.info(f"📚 [Medical Agent] 启动真实向量检索，靶向关键词: {search_query}")

        # 🌟 直接调用全局检索与脱水接口
        ctx, sources, _ = await get_multimodal_context(search_query, top_k=3)
        return ctx, sources


# ==========================================
# 高度解耦的报告智能体总控类
# ==========================================
class ReportAgent:
    """
    多模态化验单与影像解读智能体。
    负责协调 Vision(视觉)、Medical(检索) 和 Editor(编辑) 阶段的流水线。
    """

    def __init__(
            self,
            retriever: GuidelineRetriever,
            llm_client: AsyncOpenAI,
            editor_model: str = DEFAULT_MODEL
    ):
        self.retriever = retriever
        self.llm_client = llm_client
        self.editor_model = editor_model

    def _is_safe_image_url(self, url: str) -> bool:
        if not url: return False
        if url.startswith("data:image/"):
            return True
        try:
            parsed = urlparse(url)
            return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
        except Exception:
            return False

    async def _text_fallback_extract(self, query: str) -> List[ExtractedIndicator]:
        logger.info("👁️ [Text Extraction] 无有效图片，尝试从文本解析结构化指标...")
        prompt = f"""
        请从用户的描述中提取医学化验指标。
        用户描述: "{query}"
        请输出 JSON，格式：{{"abnormal_items": [{{"name": "尿酸", "value": "520", "unit": "", "status": "偏高"}}]}}
        如果没提到具体指标，输出空列表。
        """
        try:
            response = await self.llm_client.chat.completions.create(
                model=self.editor_model,
                messages=[{"role": "user", "content": prompt}],
                response_format={"type": "json_object"},
                temperature=0.1
            )
            data = json.loads(response.choices[0].message.content)
            return data.get("abnormal_items", [])
        except Exception as e:
            logger.warning(f"⚠️ 文本指标提取失败: {e}")
            return []

    async def _vision_extract(self, query: str, image_url: Optional[str]) -> List[ExtractedIndicator]:
        if image_url and self._is_safe_image_url(image_url):
            logger.info("👁️ [Vision Agent] 启动图片 OCR 识别...")
            await sse_emit("agent_step", agent="report", phase="vision_start",
                           message="👁️ 视觉模型正在 OCR 化验单…")
            prompt = """
            提取化验单中【异常（偏高/偏低）】的指标。
            严格输出 JSON: {"abnormal_items": [{"name": "指标", "value": "数值", "unit": "单位", "status": "偏高/偏低"}]}
            """
            try:
                raw_json = await analyze_image_with_vision(image_url, prompt)
                data = json.loads(raw_json)
                items = data.get("abnormal_items", []) or []
                await sse_emit("agent_step", agent="report", phase="vision_done",
                               message=f"✅ OCR 提取到 {len(items)} 项异常指标", n=len(items))
                return items
            except Exception as e:
                logger.error(f"❌ [Vision Agent] 图片读取失败，降级至文本提取。错误: {e}")
                await sse_emit("agent_step", agent="report", phase="vision_fallback",
                               message="⚠️ OCR 失败，降级为文本解析")
                return await self._text_fallback_extract(query)
        else:
            if image_url:
                logger.warning("⚠️ 检测到不安全的 Image URL，已拒绝访问。")
            await sse_emit("agent_step", agent="report", phase="text_extract",
                           message="📝 从文本描述中解析指标…")
            return await self._text_fallback_extract(query)

    async def _medical_retrieve(self, indicators: List[ExtractedIndicator], query: str) -> tuple[str, list]:
        logger.info("📚 [Medical Agent] 检索临床指南...")
        names = [i.get("name", "") for i in indicators if i.get("name")]
        await sse_emit("agent_step", agent="report", phase="retrieve_start",
                       message=f"📚 检索临床指南：{', '.join(names[:3]) or '(综合检索)'}")
        try:
            guidelines, sources = await self.retriever.retrieve(indicators, query)
            if not guidelines or guidelines == "（暂无相关参考资料）":
                await sse_emit("agent_step", agent="report", phase="retrieve_empty",
                               message="📚 本地库无直接匹配，启用大模型内置常识")
                return "本地知识库暂无直接匹配，请依赖医学常识进行客观解答。", []
            await sse_emit("agent_step", agent="report", phase="retrieve_done",
                           message=f"✅ 命中 {len(sources)} 条指南卡片", n_cards=len(sources))
            return guidelines, sources
        except Exception as e:
            logger.error(f"❌ [Medical Agent] 知识库检索异常: {e}")
            await sse_emit("agent_step", agent="report", phase="retrieve_error",
                           message="⚠️ 指南库不可用，启用兜底", error=str(e))
            return "⚠️ 指南库暂时不可用，以下分析将基于大模型内置通用医学常识。", []

    async def _editor_generate(self, query: str, indicators: List[ExtractedIndicator], guidelines: str) -> str:
        logger.info("✍️ [Editor Agent] 正在生成降维通俗报告...")
        await sse_emit("agent_step", agent="report", phase="editor_start",
                       message="✍️ 主任医师正在生成通俗解读报告…")
        prompt = """
        你是一位极具亲和力的三甲医院主任医师。将晦涩的指标和指南翻译为通俗的 Markdown 报告。
        ### 📑 智能化验单解读报告
        **1. 👁️ 识别结果：** (列出异常指标)
        **2. 🩺 初步解读：** (打比方通俗解释这些指标意味着什么)
        **3. 🍎 饮食与复查建议：** (基于下面提供的指南库参考内容给出建议)

        【🚨 排版红线】：不要过度使用加粗和横线，严格保持 H3 (###) 和粗体 (**) 级别的克制。
        """
        try:
            response = await self.llm_client.chat.completions.create(
                model=self.editor_model,
                messages=[
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": f"疑问：{query}\n\n指标：{indicators}\n\n指南参考：{guidelines}"}
                ],
                temperature=0.3
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"❌ [Editor Agent] 报告生成彻底失败: {e}")
            return f"⚠️ 系统生成报告时出现波动。已成功提取以下异常指标，请交由线下医生评估：\n{json.dumps(indicators, ensure_ascii=False)}"

    async def analyze_medical_report(self, query: str, image_url: Optional[str] = None) -> ReportResult:
        # 🌟 规则前置：避免对"血压怎么测"这类无图片无指标问题白跑流水线
        needs, reason = needs_report_pipeline(query, image_url)
        if not needs:
            logger.info(f"⏭️ [Report Agent] 规则前置拦截：{reason} → 跳过 OCR+检索流水线")
            short_answer = (
                "### 📑 智能化验单解读助手\n\n"
                "> 您的提问似乎不是化验单 / 影像报告解读类问题。\n\n"
                "**如果您想让我解读化验单，建议您：**\n"
                "- 📷 直接上传化验单照片，我会自动 OCR 提取异常指标。\n"
                "- 或在文字中描述具体指标，例如：「我尿酸 520 umol/L 偏高」。\n\n"
                "**如果您只是有一般健康疑问**，建议改回主咨询入口，由全科大夫解答会更对口。\n"
            )
            return {
                "answer": short_answer,
                "extracted_data": [],
                "guidelines": "",
                "sources": [],
                "format": "markdown",
                "evidence_chain": build_chain(final_claim="（提问不属于报告解读范畴，已早退）", confidence=1.0),
                "skipped_reason": reason,
            }

        logger.info(f"📑 [Report Agent] 规则前置通过：{reason} → 启动完整流水线")
        indicators = await self._vision_extract(query, image_url)
        guidelines, sources = await self._medical_retrieve(indicators, query)
        final_answer = await self._editor_generate(query, indicators, guidelines)

        # ==========================================
        # 🌟 D5: 组装证据链 EvidenceChain
        # ==========================================
        evidence_chain = self._build_evidence_chain(
            query=query,
            image_url=image_url,
            indicators=indicators,
            sources=sources,
            final_answer=final_answer,
            extract_reason=reason,
        )

        return {
            "answer": final_answer,
            "extracted_data": indicators,
            "guidelines": guidelines,
            "sources": sources,
            "format": "markdown",
            "evidence_chain": evidence_chain,
        }

    # ==========================================
    # 🌟 D5: 证据链组装器（独立函数，便于单测）
    # ==========================================
    def _build_evidence_chain(
        self,
        query: str,
        image_url: Optional[str],
        indicators: List[ExtractedIndicator],
        sources: list,
        final_answer: str,
        extract_reason: str,
    ) -> dict:
        # ----- refs 池：来源卡片 + 影像 OCR + 文本提取 -----
        chain_refs = []

        # 1) 影像 OCR / 文本提取 ref
        if image_url:
            chain_refs.append({
                "ref_id": "image:report_scan",
                "type": "image",
                "label": "用户上传的化验单/影像",
                "locator": {"image_url": image_url[:120] + "…" if len(image_url) > 120 else image_url},
                "snippet": f"识别得到 {len(indicators)} 项异常指标",
            })
        else:
            chain_refs.append({
                "ref_id": "profile:user_query",
                "type": "profile",
                "label": "用户文字描述的指标",
                "locator": {"reason": extract_reason},
                "snippet": (query or "")[:200],
            })

        # 2) 检索源卡片：补 ref_id / locator / snippet（兼容 main_agent 的旧字段）
        for idx, src in enumerate(sources or []):
            if not isinstance(src, dict):
                continue
            sid = src.get("id", idx + 1)
            stype = src.get("type", "general")
            disease = src.get("disease", "")
            content = src.get("content", "")
            ref_type = {
                "guide":   "pdf",
                "kg":      "kg",
                "visual":  "image",
                "general": "pdf",
            }.get(stype, "pdf")
            ref_id = f"doc:report_guideline#{stype}_{sid}"
            chain_refs.append({
                "ref_id": ref_id,
                "type": ref_type,
                "label": src.get("title", f"指南片段 #{sid}"),
                "locator": {
                    "card_id": sid,
                    "card_type": stype,
                    "disease": disease,
                    "department": src.get("department", ""),
                },
                "snippet": content[:300] if content else "",
            })
            # 给原 source 卡片补上 ref_id，方便前端跨视图跳转
            src["ref_id"] = ref_id

        chain_refs = dedupe_refs(chain_refs)

        # ----- triples：指标异常 + 临床关联 -----
        chain_triples = []
        # 1) 指标三元组：(指标名, 状态, 数值+单位)
        primary_ref = "image:report_scan" if image_url else "profile:user_query"
        for ind in indicators or []:
            name = (ind.get("name") or "").strip()
            value = (ind.get("value") or "").strip()
            unit = (ind.get("unit") or "").strip()
            status = (ind.get("status") or "异常").strip()
            if not name:
                continue
            tail = f"{value} {unit}".strip() or status
            # 状态归一到受控词表
            rel = "偏高" if "高" in status else "偏低" if "低" in status else \
                  "阳性" if "阳" in status else "阴性" if "阴" in status else "异常"
            chain_triples.append({
                "head": name,
                "relation": rel,
                "tail": tail,
                "tail_type": "Indicator",
                "source_id": primary_ref,
                "confidence": 0.95 if image_url else 0.75,
            })

        # 2) 指标 → 关联指南：每个指标关联到首个非影像 ref（启发式，便于前端连线）
        guideline_refs = [r for r in chain_refs if r["type"] in ("pdf", "kg")]
        if guideline_refs and chain_triples:
            top_ref = guideline_refs[0]
            for ind in indicators[:3]:  # 只关联前 3 个指标，避免链路爆炸
                name = (ind.get("name") or "").strip()
                if not name:
                    continue
                disease = (top_ref.get("locator") or {}).get("disease") or "相关临床要点"
                chain_triples.append({
                    "head": name,
                    "relation": "参考依据",
                    "tail": disease,
                    "tail_type": "Guideline",
                    "source_id": top_ref["ref_id"],
                    "confidence": 0.7,
                })

        # ----- reasoning_path：三步固定流水 -----
        step1_actor = "report.vision_ocr" if image_url else "report.text_extract"
        chain_path = [
            {
                "step": 1, "actor": step1_actor,
                "action": "提取异常指标",
                "input_summary": f"图片={'有' if image_url else '无'} | 文本={(query or '')[:60]}",
                "output_summary": f"提取到 {len(indicators)} 项异常指标",
                "cited_refs": [primary_ref],
            },
            {
                "step": 2, "actor": "report.medical_retrieve",
                "action": "检索临床指南",
                "input_summary": "靶向关键词=" + " ".join(i.get("name", "") for i in indicators[:5]),
                "output_summary": f"命中 {len(guideline_refs)} 条指南/图谱卡片",
                "cited_refs": [r["ref_id"] for r in guideline_refs],
            },
            {
                "step": 3, "actor": "report.editor",
                "action": "降维通俗化解读",
                "input_summary": "异常指标 + 指南上下文",
                "output_summary": (final_answer or "")[:80],
                "cited_refs": [r["ref_id"] for r in chain_refs],
            },
        ]

        # ----- final_claim -----
        if indicators:
            top_ind = indicators[0]
            claim = f"识别 {len(indicators)} 项异常指标（如 {top_ind.get('name')} {top_ind.get('status', '')}），已结合临床指南给出解读"
        else:
            claim = "未识别到明确异常指标，建议结合医生面诊判断"

        # 整链置信度：有图 OCR > 文本提取；有指南 > 无指南
        base_conf = 0.9 if image_url else 0.7
        if not guideline_refs:
            base_conf -= 0.1
        if not indicators:
            base_conf -= 0.2
        base_conf = max(0.4, min(1.0, base_conf))

        return build_chain(
            triples=chain_triples,
            reasoning_path=chain_path,
            refs=chain_refs,
            final_claim=claim,
            confidence=base_conf,
        )