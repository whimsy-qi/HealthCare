# scripts/vision_tool.py
import os
import logging
from openai import AsyncOpenAI
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("VisionTool")


class OpenAIVisionClient:
    def __init__(self):
        # 默认优先使用专门的视觉大模型配置
        self.api_key = os.getenv("VISION_API_KEY", os.getenv("OPENAI_API_KEY"))
        self.base_url = os.getenv("VISION_API_BASE", os.getenv("OPENAI_API_BASE"))
        self.model = os.getenv("VISION_MODEL", "qwen-vl-plus")

        self.client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)

    async def extract_from_image(self, image_url: str, prompt: str) -> str:
        logger.info(f"👁️ [Vision Tool] 调用视觉大模型 ({self.model}) 分析图像...")

        # 构建基础参数
        params = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_url}}
                    ]
                }
            ],
            "temperature": 0.1
        }

        # 🌟 核心修复：智能路由输出格式
        # 只有在 prompt 中明确要求 JSON 时，才附加 response_format。
        # 否则默认输出纯文本/Markdown，避免模型“精神分裂”产生幻觉。
        if "json" in prompt.lower():
            params["response_format"] = {"type": "json_object"}

        response = await self.client.chat.completions.create(**params)
        return response.choices[0].message.content


# 全局单例工具实例
vision_tool = OpenAIVisionClient()


# 暴露给各个智能体调用的快捷函数
async def analyze_image_with_vision(image_url: str, prompt: str) -> str:
    return await vision_tool.extract_from_image(image_url, prompt)