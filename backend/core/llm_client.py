"""
共享 AsyncOpenAI 客户端单例
所有 Agent 统一从此处导入，复用同一个 HTTP 连接池，避免重复建连。

🌟 模型集中配置（解决 30+ 处 hardcode 的工程债）：
   - LLM_MODEL_DEFAULT      （主力模型，所有 agent 默认用）
   - LLM_MODEL_FAST         （轻量任务：意图分类、标题生成、内存抽取）
   - LLM_MODEL_SUMMARY      （证据摘要，默认跟随 FAST）
   - LLM_MODEL_REASONING    （重推理：MADDx 辩论、Rumor 裁决，可选）
   未在 .env 配置时全部回退到 DEFAULT，保证向后兼容。
"""
import os
from openai import AsyncOpenAI
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True))

# ==========================================
# 🌟 模型 ID 集中表（切模型只改这里 + .env）
# ==========================================
DEFAULT_MODEL = os.getenv("LLM_MODEL_DEFAULT", "deepseek-v4-pro")
FAST_MODEL = os.getenv("LLM_MODEL_FAST", "deepseek-chat")
SUMMARY_MODEL = os.getenv("LLM_MODEL_SUMMARY", FAST_MODEL)
REASONING_MODEL = os.getenv("LLM_MODEL_REASONING", "deepseek-v4-pro")

shared_client = AsyncOpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE")
)
