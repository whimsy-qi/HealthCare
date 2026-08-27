# scripts/tools_search.py
import os
import json
import asyncio
import logging
import numpy as np
from dotenv import load_dotenv, find_dotenv
from tavily import AsyncTavilyClient
from openai import AsyncOpenAI
import dashscope

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

load_dotenv(find_dotenv(usecwd=True))

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

llm_client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE")
)

# 🌟 新增：内容安全拦截词库 (可根据业务需要扩充)
SENSITIVE_WORDS = [
    "色情", "博彩", "赌场", "迷药", "代孕", "代开", "包小姐",
    "枪支", "暴恐", "砍人", "裸聊", "私服", "外挂", "刷单"
]

DOMAIN_MAP = {
    "UGC": ["zhihu.com", "xiaohongshu.com", "weibo.com", "tieba.baidu.com", "douban.com", "bilibili.com", "guokr.com",
            "hupu.com"],
    "NEWS": ["toutiao.com", "thepaper.cn", "caixin.com", "xinhuanet.com", "people.com.cn", "chinanews.com.cn",
             "163.com", "sina.com.cn", "qq.com","baidu.com"],
    "AUTHORITATIVE": ["chinacdc.cn", "nhc.gov.cn", "nmpa.gov.cn", "cma.org.cn", "who.int", "fda.gov", "nih.gov",
                      "thelancet.com", "nejm.org", "jamanetwork.com", "bmj.com", "nature.com", "science.org"],
    "GENERAL": ["dxy.cn", "haodf.com", "youlai.cn", "xywy.com", "120ask.com", "chunyuyisheng.com", "medsci.cn",
                "baikemy.com", "mayoclinic.org", "webmd.com"]
}


def calculate_cosine_similarity(vec1: list, vec2: list) -> float:
    v1, v2 = np.array(vec1), np.array(vec2)
    norm1, norm2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if norm1 == 0 or norm2 == 0:
        return 0.0
    return float(np.dot(v1, v2) / (norm1 * norm2))


# 🌟 新增：轻量级安全过滤函数
def is_safe_content(text: str) -> bool:
    if not text:
        return True
    for word in SENSITIVE_WORDS:
        if word in text:
            return False
    return True


async def semantic_rerank(query: str, results: list, top_k: int = 3, min_threshold: float = 0.25) -> list:
    if not results:
        return []

    logger.info(f"🚀 启动 qwen3-rerank 交叉编码器清洗，提取 Top-{top_k} (保底阈值: {min_threshold})")

    # 1. 过滤掉空内容，提取所有需要排序的文本
    valid_results = [res for res in results if res.get('content')]
    if not valid_results:
        return []

    # 🌟 放开截断限制！qwen3-rerank 支持单条 4000 tokens。
    # 这里保守截取前 2000 字符，既能覆盖绝大多数网页的核心正文，又能防止触发 120,000 的请求总 Token 上限
    documents = [res['content'][:2000] for res in valid_results]

    def _call_reranker():
        # dashscope 的 TextReRank 是同步接口，需要包裹在 _call_reranker 中给异步线程跑
        resp = dashscope.TextReRank.call(
            model="qwen3-rerank",
            query=query,
            documents=documents,
            top_n=top_k,
            return_documents=False,  # 节省带宽，我们直接拿 index 映射
            instruct="Given a web search query, retrieve relevant passages that answer the query."  # 明确问答任务
        )
        return resp

    # 2. 发起【单次】线程调用，彻底消灭 N+1 API 轰炸
    resp = await asyncio.to_thread(_call_reranker)

    if resp.status_code == 200:
        scored_results = []

        # 官方接口返回的 results 已经是按 relevance_score 从高到低排好序的了
        for item in resp.output.results:
            score = item.relevance_score
            idx = item.index

            original_res = valid_results[idx]
            title = original_res.get('title', '未知')[:15].replace('\n', '')
            logger.debug(f"[Rerank] {title}... | 交叉验证得分: {score:.4f}")

            # 过滤掉低于保底阈值的垃圾网页
            if score >= min_threshold:
                scored_results.append(original_res)

        logger.info(f"🎯 清洗完成：输入 {len(valid_results)} 条，经 qwen3-rerank 严选保留 {len(scored_results)} 条。")
        return scored_results
    else:
        logger.error(f"❌ qwen3-rerank API 调用失败: {resp.message}")
        logger.warning("⚠️ 降级兜底：返回原始搜索列表的前 K 条。")
        return valid_results[:top_k]

async def rewrite_query_and_route(user_query: str) -> dict:
    logger.info(f"分析检索策略: '{user_query}'")
    system_prompt = """
    你是一个专业的医学舆情检索策略专家，负责将用户口语化提问重写为高信息密度的搜索引擎关键词，并决定最佳的检索域。你的输出将直接用于召回社交媒体讨论，因此必须严格遵守以下规则：
    ### 一、核心目标
    生成一组关键词（空格分隔），能够准确反映用户查询中的医学实体和核心意图。同时，决定去哪一类网站（UGC、AUTHORITATIVE、GENERAL）检索最合适。
    ### 二、执行步骤
    1. **实体提取**：识别查询中所有可能的医学相关实体。若用户原词明显是误写或方言，同时保留原词和标准词。
    2. **降噪**：去除语气词、无意义连接词，保留体现信息来源的词汇。
    3. **意图识别与增强**：分析主要意图补充检索词（求证类补"证据 分析"，辟谣补"辟谣 骗局"，求医补"疗法 临床"）。
    4. **动态域路由选择**：
       - "UGC"：求证/辟谣类、偏方、副作用。
       - "AUTHORITATIVE"：权威求医/治疗类。
       - "GENERAL"：普通的疾病科普、症状对应。
    ### 三、输出格式（绝对严格 JSON）
    包含：thinking(思考), keywords(关键词), domain_category(路由)。
    """
    try:
        response = await llm_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": user_query}],
            response_format={"type": "json_object"},
            temperature=0.1
        )
        result = json.loads(response.choices[0].message.content.strip())
        return result
    except Exception as e:
        logger.error(f"策略大脑解析失败: {e}")
        return {"keywords": user_query, "domain_category": "GENERAL"}


async def search_dynamic_medical_info(query: str, raw_fetch_count: int = 10, final_top_k: int = 3,
                                      force_domain: str = None) -> tuple[str, list]:
    strategy = await rewrite_query_and_route(query)
    optimized_query = strategy.get("keywords", query)

    target_domains = []
    if force_domain:
        domain_keys = [k.strip() for k in force_domain.split(',')]
        for k in domain_keys:
            if k in DOMAIN_MAP:
                target_domains.extend(DOMAIN_MAP[k])
    else:
        domain_cat = strategy.get("domain_category", "GENERAL")
        target_domains = DOMAIN_MAP.get(domain_cat, DOMAIN_MAP["GENERAL"])

    client = AsyncTavilyClient(api_key=TAVILY_API_KEY)

    try:
        response = await client.search(
            query=optimized_query,
            search_depth="basic",
            max_results=raw_fetch_count,
            include_domains=target_domains
        )

        raw_results = response.get("results", [])

        # 🌟 安全拦截网：在进行昂贵的向量嵌入前，过滤掉涉黄赌毒的内容
        safe_raw_results = []
        for r in raw_results:
            if is_safe_content(r.get('title', '')) and is_safe_content(r.get('content', '')):
                safe_raw_results.append(r)
            else:
                logger.warning(f"🚫 拦截到敏感不安全内容，已丢弃该条检索结果: {r.get('title', '')}")

        if not safe_raw_results:
            return "未在目标站群中找到符合安全规范的讨论。", []

        final_results = await semantic_rerank(
            query=optimized_query,
            results=safe_raw_results,  # 传入清洗后的安全数据
            top_k=final_top_k,
            min_threshold=0.25
        )

        if not final_results:
            return "未发现相关度达标的高价值内容。", []

        context_text = f"针对提问【{query}】，以下是系统经过多维语义过滤后的高质量参考摘要：\n\n"
        structured_sources = []

        for i, res in enumerate(final_results):
            title = res.get('title', '未知')
            content = res.get('content', '无内容')
            url = res.get('url', '#')

            context_text += f"🔍 【信源 {i + 1}】\n标题: {title}\n内容: {content}\n来源: {url}\n" + "-" * 40 + "\n"
            structured_sources.append({
                "title": title,
                "content": content,
                "url": url,
                "is_internal": False
            })

        return context_text, structured_sources

    except Exception as e:
        logger.error(f"Tavily 检索系统级故障: {str(e)}")
        return f"Tavily 检索失败: {str(e)}", []