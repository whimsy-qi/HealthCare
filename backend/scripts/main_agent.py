# scripts/main_agent.py
import os
import re
import asyncio
import logging
import dashscope
from dotenv import load_dotenv
from dashvector import Client
from openai import AsyncOpenAI  # 🌟 必须改为异步客户端

# 1. 环境初始化
load_dotenv()
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['ALL_PROXY'] = ''

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("MainAgent")

# 2. 初始化 DashVector (2560 维空间)
dv_client = Client(
    api_key=os.getenv("DASHVECTOR_API_KEY"),
    endpoint=os.getenv("DASHVECTOR_ENDPOINT")
)
collection = dv_client.get("multimodal_medical_db")

# 3. 🌟 初始化 DeepSeek 异步推理引擎
llm_client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE")
)

# ==========================================
# 🌟 核心改造：完全异步化，内置全局高并发脱水机
# ==========================================
async def get_multimodal_context(user_query: str, top_k: int = 6):
    """
    全模态检索：提取最新的 disease 和 dept 元数据，构建前端可视化卡片，
    并在底层直接利用大模型并发清洗乱码与长文本！
    """
    try:
        from rag.service import get_multimodal_context_v2

        return await get_multimodal_context_v2(user_query, top_k=top_k)
    except Exception as e:
        logger.warning(f"[RAGv2] 统一检索入口异常，回退 legacy DashVector 检索: {e}")

    logger.info(f"🔍 [Main Agent] 正在对本地知识库进行向量检索: {user_query}")

    def _fetch_vector():
        import time  # 确保能使用休眠退避
        max_retries = 3

        for attempt in range(max_retries):
            try:
                resp = dashscope.MultiModalEmbedding.call(
                    model="qwen3-vl-embedding",
                    input=[{'text': user_query}]
                )
                if resp.status_code == 200:
                    query_vec = resp.output['embeddings'][0]['embedding']
                    return collection.query(
                        vector=query_vec,
                        topk=top_k,  # 🌟 适度缩减至 Top 6，加快并发清洗速度
                        output_fields=["content", "source", "file_path", "disease", "dept"]
                    )
                else:
                    logger.error(f"❌ [Main Agent] DashScope 向量化接口报错: {resp.message}")
                    return None

            except Exception as e:
                logger.warning(f"⚠️ [Main Agent] 阿里云 SSL 网络连接异常/中断 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(1.5)  # 强行退避 1.5 秒，等云端网关恢复
                else:
                    logger.error("❌ [Main Agent] 向量检索彻底失败，已超最大重试次数！")
                    return None
        return None

    # 将同步的 DashVector 搜索扔进线程池，防阻塞
    search_res = await asyncio.to_thread(_fetch_vector)

    if not search_res:
        return "（暂无相关参考资料）", [], []

    visual_refs = []

    # ==========================================
    # 🌟 架构级防爆阀：强制将大模型的并发量压制在 2！
    # ==========================================
    sem = asyncio.Semaphore(2)

    # ==========================================
    # 🌟 底层并发数据清洗器 (Global Data Washer)
    # ==========================================
    async def wash_card(i, doc):
        src = doc.fields.get('source', '')
        content = doc.fields.get('content', '')
        disease = doc.fields.get('disease', '未知疾病')
        dept = doc.fields.get('dept', '通用科室')

        if src == "expert_guide":
            tag = "【权威指南】"
            display_title = f"🏥 内部知识库: {disease if disease != '未知疾病' else '临床指南'}"
            card_type = "guide"
        elif src == "medical_kg":
            tag = f"【{dept} 图谱】"
            display_title = f"🧬 临床逻辑: {disease}"
            card_type = "kg"
        elif src == "visual_stream":
            tag = "【视觉参考图】"
            display_title = f"🖼️ 临床影像: {disease}"
            card_type = "visual"
            file_path = doc.fields.get('file_path')
            if file_path:
                visual_refs.append(file_path)
        else:
            tag = "【医药百科】"
            display_title = f"📖 本地百科: 通用医学"
            card_type = "general"

        clean_content = re.sub(r'【.*?】', '', content).strip()

        # 🌟 触发 LLM 核心清洗动作 (字数大于 60 的切片才清洗，节约算力)
        if len(clean_content) > 60:
            prompt = f"""
            你是一个严谨的医疗数据清洗专家。请将以下从本地医学库提取的原始文献切片，剔除所有的无意义字符、OCR乱码、页码、以及"优秀实践声明(GPS)"等无关格式。
            请提炼出最核心的医学病理或临床指南事实，浓缩成一段通顺、密集的纯文本摘要（约 80-100 字）。
            绝对禁止捏造原文没有的信息。直接输出结果，不要废话。
            【原始文本】：
            {clean_content[:1500]}
            """

            # 🌟 核心拦截机制：在这里拿令牌！没排到队的数据卡片必须在这里等，绝不能去骚扰大模型网关
            async with sem:
                try:
                    wash_resp = await llm_client.chat.completions.create(
                        model="deepseek-chat",
                        messages=[{"role": "user", "content": prompt}],
                        temperature=0.1,
                        max_tokens=200
                    )
                    clean_content = wash_resp.choices[0].message.content.strip()
                except Exception as e:
                    logger.warning(f"⚠️ 知识库单点清洗异常，保留原文: {e}")

        text_for_llm = f"引用 {i + 1} -> {tag} 疾病: {disease} | 内容: {clean_content}"

        card_dict = {
            "id": i + 1,
            "title": display_title,
            "content": clean_content,
            "disease": disease,
            "department": dept,
            "type": card_type,
            "is_internal": True
        }
        return text_for_llm, card_dict

    # 🚀 并发启动所有洗牌任务 (现在有 Semaphore 护航，再多任务也不会限流)
    logger.info(f"🚿 [Main Agent] 命中 {len(search_res)} 条数据，启动受限并发 (Max=2) LLM 脱水清洗...")
    wash_tasks = [wash_card(i, doc) for i, doc in enumerate(search_res)]
    results = await asyncio.gather(*wash_tasks)

    context_text = [r[0] for r in results]
    structured_sources = [r[1] for r in results]

    return "\n\n".join(context_text), structured_sources, list(set(visual_refs))
