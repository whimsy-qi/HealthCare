import argparse
import json
import os
import random
import re
import shutil
import time
from http import HTTPStatus
from pathlib import Path
from typing import Any

import dashscope
import requests
from dotenv import find_dotenv, load_dotenv
from openai import OpenAI

from core.database import SessionLocal
from core.models import Article


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_TOPICS_PATH = BASE_DIR / "data" / "article_topics.json"
VALID_CATEGORIES = {"辟谣粉碎机", "硬核诊疗局", "用药红绿灯", "时令与养生"}
DEFAULT_SOURCES = {
    "辟谣粉碎机": ["国家卫生健康委健康科普", "中国疾病预防控制中心健康提示", "权威医学指南与共识"],
    "硬核诊疗局": ["国家卫生健康委健康科普", "权威临床指南与专家共识", "三甲医院健康教育资料"],
    "用药红绿灯": ["国家药品监督管理局公开资料", "药品说明书", "权威用药指南与专家共识"],
    "时令与养生": ["中国疾病预防控制中心健康提示", "国家卫生健康委健康科普", "权威营养与运动指南"],
}

load_dotenv(find_dotenv(usecwd=True))
load_dotenv(BASE_DIR / ".env", override=False)

TEXT_MODEL = os.getenv("ARTICLE_TEXT_MODEL", "deepseek-chat")
TEXT_TIMEOUT_SEC = float(os.getenv("ARTICLE_TEXT_TIMEOUT_SEC", "60"))
WANX_IMAGE_MODEL = os.getenv("WANX_IMAGE_MODEL") or dashscope.ImageSynthesis.Models.wanx_v1
WANX_IMAGE_SIZE = os.getenv("WANX_IMAGE_SIZE", "1024*1024")
WANX_TIMEOUT_SEC = float(os.getenv("WANX_TIMEOUT_SEC", "90"))
WANX_POLL_INTERVAL_SEC = float(os.getenv("WANX_POLL_INTERVAL_SEC", "3"))
IMAGE_SAVE_DIR = BASE_DIR / "covers"
LOCAL_IMG_BASE_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8000").rstrip("/") + "/static/covers/"
IMAGE_SAVE_DIR.mkdir(parents=True, exist_ok=True)

text_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE"),
    timeout=TEXT_TIMEOUT_SEC,
    max_retries=2,
)
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

WANX_CATEGORY_COVER_HINTS = {
    "辟谣粉碎机": "盾牌、放大镜、核查清单、事实校验卡片、抽象健康符号",
    "硬核诊疗局": "医生咨询场景、检查报告、听诊器、诊室窗口、健康数据面板",
    "用药红绿灯": "药盒、药片、服药提醒卡、安全信号灯、剂量清单",
    "时令与养生": "季节食物、睡眠时钟、运动姿态、阳光、绿植、生活习惯清单",
    "实时热点追踪": "新闻卡片、心电线、核查徽章、健康提醒、事实追踪面板",
}

COVER_STYLE_PRESETS = {
    "flat_medical": {
        "name": "扁平医疗插画",
        "prompt": (
            "统一品牌风格的医疗健康科普封面，扁平矢量插画，简洁几何构图，"
            "低饱和薄荷绿、青绿、浅黄、米白配色，干净明亮，专业可信，移动端知识卡片风格。"
            "允许少量简化人物剪影或医生形象，但必须是扁平矢量，不要写实五官特写。"
            "背景为简洁诊室、健康管理面板或生活方式场景，整体与现代健康 App 插画统一。"
        ),
        "negative": (
            "禁止：水彩、铅笔画、油画、摄影、3D、童话风、儿童肖像、真实人物、复杂纹理、"
            "任何文字、汉字、英文、数字、logo、水印、血液、伤口、手术针头、恐怖医疗画面。"
        ),
    },
    "editorial_flat": {
        "name": "编辑部健康海报",
        "prompt": (
            "健康科普编辑部封面海报，现代扁平插画与轻微颗粒质感，"
            "大色块构图，薄荷绿、孔雀蓝、暖黄色、白色留白，"
            "画面像专业健康杂志栏目插图，包含简化医学图标、健康清单、信息卡片和生活场景。"
            "构图更有设计感，但仍保持清晰、克制、可信。"
        ),
        "negative": (
            "禁止：写实摄影、写实人物脸、儿童童话插画、水彩晕染、油画厚涂、3D渲染、"
            "任何文字、logo、水印、血液、伤口、手术场面、恐怖或夸张病痛表现。"
        ),
    },
    "soft_clinic": {
        "name": "柔和诊室图标插画",
        "prompt": (
            "柔和友好的诊室主题健康插画，半扁平图标化风格，圆润形状，"
            "浅绿、浅蓝、奶白、淡橙配色，医生、药盒、检查报告、时钟、植物等元素以图标方式出现。"
            "画面稳定、简洁、亲和，适合移动端健康管理 App 的文章封面。"
        ),
        "negative": (
            "禁止：真实人物肖像、儿童主角、童话场景、水彩、摄影、3D、复杂阴影、"
            "任何文字、logo、水印、血液、伤口、针头特写、恐怖医疗画面。"
        ),
    },
    "fresh_realistic": {
        "name": "写实清新健康摄影",
        "prompt": (
            "【任务类型】生成一张移动端健康知识文章封面图，画面用于文章卡片和详情页顶部封面，"
            "要求清晰、专业、可信，不是广告海报。"
            "【核心主体】围绕文章主题进行视觉表达。请从标题和摘要中提炼一个最直观的健康行为、"
            "生活细节或医学认知冲突作为画面主体，不要加入文章分类名称，也不要套用固定分类视觉元素。"
            "【场景环境】场景应接近日常健康管理、家庭生活或轻医疗咨询环境，真实、干净、克制。"
            "画面需要让用户一眼感到这是健康科普内容，但不要制造疾病恐惧或医院压迫感。"
            "【构图镜头】方形构图，主体明确，适合 1024x1024 封面。中景或近景拍摄，"
            "主体位于画面中央或三分线附近，背景简洁，有适度留白。避免杂乱陈设，"
            "避免多个无关主体抢焦点。移动端裁切后仍能看清核心主体。"
            "【光线氛围】自然柔和日光，明亮、清新、安静，整体低对比不过曝。"
            "氛围理性、温和、可信，不要戏剧化、暗黑、恐怖、焦虑营销。"
            "【材质风格】写实清新摄影风格，真实相机拍摄质感，细节自然，色彩干净但不过度滤镜化。"
            "不要插画、不要水彩、不要卡通、不要 3D 渲染、不要儿童绘本风、不要商业广告大片感。"
        ),
        "negative": (
            "【限制要求】画面中不得出现任何文字、汉字、英文、数字、logo、水印、品牌包装文字、屏幕文字。"
            "不得出现可识别真实人脸、儿童肖像、血液、伤口、手术场面、针头特写、恐怖医疗画面。"
            "可以出现手部、背影、局部动作或非识别人物轮廓，但人物不能成为肖像主体。"
            "不要主动添加与文章无关的药盒、检查报告、时钟、植物等固定道具。"
        ),
    },
    "entity_realistic": {
        "name": "内容实体写实封面",
        "uses_entity_context": True,
        "prompt": (
            "【任务类型】生成一张移动端健康知识 App 的文章封面图，用于文章卡片流和详情页顶部封面。"
            "【核心主体】根据文章内容实体生成真实感健康科普画面。先理解实体线索和摘要，选择其中最容易视觉化的 1-3 个实体作为主体。"
            "不要把抽象疾病名、风险等级或科普概念直接画成文字；要转换为真实生活物体、健康行为、就医咨询场景或自然静物。"
            "【场景环境】优先使用真实生活方式、干净桌面、自然光、轻医疗咨询、健康饮食、运动恢复、家庭健康管理等可信场景。"
            "允许出现医学道具或药品外观，但必须克制、干净、无品牌、无文字，不要堆满画面。"
            "【构图镜头】方形构图，适合 1024x1024 移动端封面。主体明确，中景或近景，背景简洁，有留白。"
            "移动端横向卡片裁切后仍能看清主体。避免杂乱陈设和多主体抢焦点。"
            "【光线氛围】自然柔和日光，明亮、安静、可信，低对比不过曝。不要焦虑营销、恐怖医疗、暗黑氛围。"
            "【材质风格】高质量真实感摄影或精致写实插画，真实相机质感，细节自然，色彩干净。"
            "不是卡通、不是儿童绘本、不是 3D 渲染、不是水彩涂抹。"
            "【人物要求】可以出现手部、背影、局部动作或远景人物，但不要可识别真实人脸，不要儿童肖像。"
        ),
        "negative": (
            "【限制要求】画面中不得出现任何文字、汉字、英文、数字、logo、水印、品牌包装文字、屏幕文字。"
            "不得出现血液、伤口、手术场面、针头特写、恐怖医疗画面。"
            "不得出现夸张痛苦表情、病人隐私部位、诊断报告文字、药品品牌名。"
        ),
    },
    "healing_lifestyle": {
        "name": "治愈绘本自然图集",
        "uses_article_context": False,
        "prompt": (
            "【任务类型】生成一张移动端健康知识 App 的封面插画，用于文章卡片流和详情页顶部封面。"
            "封面不解释具体文章，只提供安静、治愈、让人愿意停留的视觉氛围。"
            "【核心主体】治愈绘本风自然场景，画面像温柔的儿童绘本或独立插画书内页，但不要幼稚。"
            "主体可以是星空草地、月光、发光云朵、花墙、蓝天、森林、树下冥想、海边、湖面、"
            "盛开的花、柔软草丛、微光萤火、安静小路、远处小人物或小动物。"
            "【场景环境】以自然景色和梦幻户外空间为主，允许轻微童话感和想象力。"
            "可以出现夜空、月亮、星星、发光草地、盛夏蓝天、花丛、白墙、树荫、绿意和开阔天空。"
            "不要运动器材感，不要健身广告感，不要食物摆盘广告感，不要医院或医学道具。"
            "【构图镜头】方形构图，适合 1024x1024 移动端封面。大面积色块和留白，主体少而明确。"
            "画面层次简单，远中近景清楚，移动端横向卡片裁切后仍然完整。"
            "允许下方或一侧有丰富草丛、花丛、树冠纹理，上方保留天空或光线空间。"
            "【光线氛围】柔和、梦幻、治愈。可以是深蓝夜空下的月光，也可以是晴朗蓝天下的明亮日光，"
            "整体要有空气感、微光感、安静感。色彩以高质感蓝色、绿色、奶白、浅粉、暖黄微光为主，"
            "可以有少量金色星点或萤火光。"
            "【材质风格】手绘绘本插画，水粉、水彩与丙烯混合质感，能看到柔和笔触、纸张纹理、"
            "轻微颗粒和手工涂抹感。边缘柔软，色块饱满，画面干净唯美。"
            "参考感觉：治愈系绘本、梦幻自然插画、温柔童话感、蓝绿主色、发光细节。"
            "【人物要求】可以出现很小比例的人物背影、闭眼侧脸、睡眠姿态或冥想姿态，但人物不是肖像主体。"
            "五官要极简、柔和、不可识别。也可以完全不出现人物。"
        ),
        "negative": (
            "【限制要求】不得出现任何文字、汉字、英文、数字、logo、水印、品牌包装文字。"
            "不得出现血液、伤口、手术场面、针头特写、恐怖医疗画面。"
            "不要出现药盒、检查报告、医院床位、诊断单、听诊器等医学道具。"
            "不要出现真实摄影、3D 渲染、商业海报、健身广告、食物广告、过度写实人物脸、复杂城市街景。"
            "不要出现低质卡通、粗糙线稿、脏灰色、暗黑恐怖、强烈赛博风、塑料质感。"
        ),
    },
}

HEALING_LIFESTYLE_SCENE_SEEDS = [
    "深蓝夜空下，一朵发光的奶白云朵落在草地上，月亮和少量星点在上方留白",
    "晴朗蓝天下的白色花墙与盛开的粉色花丛，两只小动物背影坐在墙边看远方",
    "巨大绿色树冠下，一个很小的人物闭眼冥想，周围有柔和萤火微光",
    "夏日蓝天、白墙和大片蔷薇花，画面明亮干净，留白充足",
    "夜晚草海里漂浮着金色微光，远处有一轮弯月，整体安静梦幻",
    "湖边浅绿色草坡和柔和晨雾，小人物坐在远处看水面",
    "森林小路被树叶光斑覆盖，远处有温暖光源，画面宁静治愈",
    "海边浅蓝天空和柔和浪花，一只小动物坐在岸边看海",
    "花草围绕的白色石墙与明亮天空，画面像夏天绘本内页",
    "大树、草地和漂浮光点组成的安静冥想场景，人物极小且无可识别五官",
    "夜色草地上柔软发光的星形云朵，画面以深蓝和奶白为主",
    "山坡花草、蓝天和微风，远处小路通向明亮天空",
    "清澈湖面、草地和白色小花，柔和日光形成治愈氛围",
    "绿色树荫下的浅色长椅和花丛，没有人物，画面安静唯美",
    "粉色花丛、白墙、蓝天和远处飞鸟，构图清爽明亮",
    "星空、月亮、深蓝草丛和少量金色星点，画面梦幻但不恐怖",
]

COVER_ENTITY_STOPWORDS = {
    "健康", "科普", "真的", "到底", "为什么", "怎么办", "如何", "需要", "可以", "不能", "一定",
    "必须", "是不是", "是什么", "真相", "误区", "建议", "注意", "指南", "核心", "解读",
}

VISUAL_ENTITY_TERMS = [
    "骨头汤", "牛奶", "豆制品", "维生素C", "电子烟", "空腹", "蜂蜜水", "牙刷", "洗牙", "输液",
    "感冒", "病毒", "阿莫西林", "抗生素", "布洛芬", "对乙酰氨基酚", "头孢", "降压药", "降糖药",
    "血压计", "血糖仪", "药盒", "药片", "滴眼液", "创可贴", "安眠药", "褪黑素", "中药", "绿茶", "咖啡",
    "眼皮跳", "眼睑痉挛", "疲劳", "压力", "烧伤", "烫伤", "牙膏", "酱油", "伤口", "流鼻血",
    "味精", "食品添加剂", "脱发", "致癌",
    "甲状腺结节", "乳房结节", "宫颈癌筛查", "乙肝", "尿酸", "胆固醇", "心电图", "血糖", "血压",
    "幽门螺杆菌", "胃镜", "体检报告", "检查报告", "过敏", "花粉", "鼻炎", "哮喘", "蜱虫", "春笋",
    "熬夜", "补觉", "睡眠", "晨练", "轻断食", "一万步", "膝盖", "隔夜菜", "无糖饮料", "打呼噜",
    "痛经", "红糖水", "防晒", "运动", "拉伸", "热身", "水果", "蔬菜", "坚果", "燕麦", "清水",
]


def _strip_markdown(value: str, max_len: int = 360) -> str:
    text = re.sub(r"```.*?```", " ", value or "", flags=re.S)
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", text)
    text = re.sub(r"\[[^\]]+\]\([^)]+\)", " ", text)
    text = re.sub(r"[#>*_`~\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def _extract_cover_entities(
    title: str = "",
    summary: str = "",
    content: str = "",
    tags: Any = None,
    related_entities: Any = None,
    max_items: int = 10,
) -> list[str]:
    candidates: list[str] = []
    candidates.extend(_as_list(related_entities))
    candidates.extend(_as_list(tags))

    text = "。".join([title or "", summary or "", _strip_markdown(content or "", 240)])
    for term in VISUAL_ENTITY_TERMS:
        if term in text:
            candidates.append(term)

    for quoted in re.findall(r"[《「“](.{2,12}?)[》」”]", text):
        candidates.append(quoted)

    deduped: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        item = str(item).strip()
        if not item or item in seen:
            continue
        seen.add(item)
        deduped.append(item)
        if len(deduped) >= max_items:
            break

    if len(deduped) < 2:
        for match in re.findall(r"[A-Za-z][A-Za-z0-9＋+.-]{1,18}|[\u4e00-\u9fff]{2,6}", text):
            item = match.strip("的了和与或是吗呢啊：:，,。！？?、；;（）()《》「」“”\"'")
            if len(item) < 2 or item in COVER_ENTITY_STOPWORDS or item in seen:
                continue
            if any(stop in item for stop in COVER_ENTITY_STOPWORDS):
                continue
            if any(noise in item for noise in ("作为", "医生", "患者", "本文", "我们", "一个", "临床", "流传", "误区", "角度", "分析", "常见", "清晰", "对比")):
                continue
            seen.add(item)
            deduped.append(item)
            if len(deduped) >= max_items:
                break
    return deduped


def _as_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [part.strip() for part in re.split(r"[,，、]", value) if part.strip()]
    return []


def _bounded_int(value: Any, default: int, min_value: int, max_value: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(min_value, min(max_value, parsed))


def _normalize_topic(raw: dict[str, Any]) -> dict[str, Any]:
    category = str(raw.get("category", "")).strip()
    title = str(raw.get("title", "")).strip()
    if category not in VALID_CATEGORIES:
        raise ValueError(f"非法分类: {category!r}，title={title!r}")
    if not title:
        raise ValueError(f"选题缺少标题: {raw!r}")
    return {
        "category": category,
        "title": title,
        "tags": _as_list(raw.get("tags")),
        "related_entities": _as_list(raw.get("related_entities")),
        "audience": str(raw.get("audience", "大众健康人群")).strip() or "大众健康人群",
        "risk_level": str(raw.get("risk_level", "low")).strip() or "low",
    }


def load_topics(path: Path = DEFAULT_TOPICS_PATH) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    raw_topics: list[dict[str, Any]] = []
    if isinstance(data, dict):
        categories = data.get("categories", data)
        if not isinstance(categories, dict):
            raise ValueError("题库 JSON 的 categories 必须是对象")
        for category, topics in categories.items():
            if not isinstance(topics, list):
                raise ValueError(f"分类 {category!r} 的题库必须是数组")
            for item in topics:
                if isinstance(item, str):
                    raw_topics.append({"category": category, "title": item})
                elif isinstance(item, dict):
                    raw_topics.append({"category": category, **item})
                else:
                    raise ValueError(f"不支持的题库项: {item!r}")
    elif isinstance(data, list):
        raw_topics = data
    else:
        raise ValueError("题库 JSON 必须是数组或分类对象")

    seen: set[tuple[str, str]] = set()
    topics: list[dict[str, Any]] = []
    for raw in raw_topics:
        topic = _normalize_topic(raw)
        key = (topic["category"], topic["title"])
        if key in seen:
            continue
        seen.add(key)
        topics.append(topic)
    return topics


def build_wanx_cover_prompt(
    title: str,
    category: str = "",
    summary: str = "",
    content: str = "",
    tags: Any = None,
    related_entities: Any = None,
    cover_style: str = "flat_medical",
    variation_index: int | None = None,
) -> str:
    summary_part = f"文章摘要：{summary[:120]}。" if summary else ""
    preset = COVER_STYLE_PRESETS.get(cover_style, COVER_STYLE_PRESETS["flat_medical"])
    if preset.get("uses_article_context") is False:
        idx = variation_index or 1
        scene = HEALING_LIFESTYLE_SCENE_SEEDS[(idx - 1) % len(HEALING_LIFESTYLE_SCENE_SEEDS)]
        return (
            f"{preset['prompt']}"
            f"【变化种子】第 {idx} 张封面，场景意象：{scene}。"
            "只根据该场景意象调整画面，不要加入文章标题、文章摘要、文章分类或具体医学主题。"
            f"{preset['negative']}"
        )
    if preset.get("uses_entity_context"):
        entities = _extract_cover_entities(
            title=title,
            summary=summary,
            content=content,
            tags=tags,
            related_entities=related_entities,
        )
        entity_part = "、".join(entities) if entities else title
        content_part = _strip_markdown(content or summary or title, 260)
        return (
            f"{preset['prompt']}"
            f"【文章标题】{title}。"
            f"【内容实体】{entity_part}。"
            f"【摘要线索】{summary[:160]}。"
            f"【正文线索】{content_part}。"
            "请基于以上实体和线索生成一个真实感画面，只呈现可视觉化对象和场景，不要出现任何文字。"
            f"{preset['negative']}"
        )
    return (
        f"{preset['prompt']}"
        f"标题：《{title}》。{summary_part}"
        f"{preset['negative']}"
    )


def _response_get(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _extract_wanx_image_url(response: Any) -> str:
    output = _response_get(response, "output", {}) or {}
    results = _response_get(output, "results", None)
    if results:
        first = results[0]
        for key in ("url", "actual_image_url", "orig_prompt_url"):
            url = _response_get(first, key, "")
            if url:
                return str(url)

    choices = _response_get(output, "choices", None)
    if choices:
        message = _response_get(choices[0], "message", {}) or {}
        content = _response_get(message, "content", None)
        if isinstance(content, list):
            for item in content:
                url = _response_get(item, "image", "") or _response_get(item, "url", "")
                if url:
                    return str(url)

    return ""


def _wanx_error_detail(response: Any) -> str:
    output = _response_get(response, "output", {}) or {}
    detail = {
        "code": _response_get(response, "code", ""),
        "message": _response_get(response, "message", ""),
        "request_id": _response_get(response, "request_id", ""),
        "output_keys": list(output.keys()) if isinstance(output, dict) else [],
        "task_status": _response_get(output, "task_status", ""),
        "task_id": _response_get(output, "task_id", ""),
        "output_code": _response_get(output, "code", ""),
        "output_message": _response_get(output, "message", ""),
    }
    return json.dumps(detail, ensure_ascii=False)


def _wait_for_wanx_result(task_response: Any, timeout_sec: float = WANX_TIMEOUT_SEC) -> Any:
    deadline = time.monotonic() + timeout_sec
    current = task_response
    while True:
        output = _response_get(current, "output", {}) or {}
        status = str(_response_get(output, "task_status", "") or "").upper()
        if status in {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}:
            return current
        if _extract_wanx_image_url(current):
            return current
        if time.monotonic() >= deadline:
            raise TimeoutError(f"万相封面生成超时，超过 {timeout_sec:.0f} 秒")
        time.sleep(WANX_POLL_INTERVAL_SEC)
        current = dashscope.ImageSynthesis.fetch(current)


def generate_medical_content(topic: dict[str, Any]) -> dict[str, Any] | None:
    category = topic["category"]
    title = topic["title"]
    tags = "、".join(topic.get("tags") or [])
    entities = "、".join(topic.get("related_entities") or [])
    prompt = (
        "你是一位专业、谨慎、表达清楚的健康科普作者。"
        "请为健康知识专区生成一篇中文科普文章，只返回严格 JSON，不要 Markdown 代码块。\n"
        "JSON 字段必须包含："
        '{"title":"文章标题","summary":"60字以内摘要","content":"Markdown正文",'
        '"tags":["标签"],"related_entities":["实体"],"sources":["来源类型"],'
        '"risk_level":"low或medium或high","audience":"适用人群","reading_time":3}\n'
        "写作要求：正文 700-1000 字，包含分段标题、误区提醒、可执行建议和就医边界。"
        "不得给出诊断结论，不得开处方，不得承诺疗效。"
        "涉及药物时提醒遵医嘱和阅读说明书；涉及急症信号时提示及时就医。"
        "sources 写来源类型，不要编造具体论文、链接或机构发布细节。\n"
        f"分类：{category}\n"
        f"选题：{title}\n"
        f"建议标签：{tags}\n"
        f"相关实体：{entities}\n"
        f"默认受众：{topic.get('audience', '大众健康人群')}\n"
        f"默认风险等级：{topic.get('risk_level', 'low')}"
    )
    try:
        res = text_client.chat.completions.create(
            model=TEXT_MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.45,
        )
        raw = re.sub(r"```json|```", "", res.choices[0].message.content).strip()
        return json.loads(raw)
    except Exception as err:
        print(f"文章生成失败 [{category}] {title}: {err}")
        return None


def normalize_article_payload(data: dict[str, Any], topic: dict[str, Any]) -> dict[str, Any]:
    category = topic["category"]
    content = str(data.get("content") or "").strip()
    # Keep the topic title canonical so title + category de-duplication remains stable across reruns.
    title = topic["title"]
    summary = str(data.get("summary") or "").strip()
    tags = _as_list(data.get("tags")) or topic.get("tags") or [category]
    related_entities = _as_list(data.get("related_entities")) or topic.get("related_entities") or []
    sources = _as_list(data.get("sources")) or DEFAULT_SOURCES.get(category, [])
    risk_level = str(data.get("risk_level") or topic.get("risk_level") or "low").strip().lower()
    if risk_level not in {"low", "medium", "high"}:
        risk_level = "low"

    return {
        "title": title[:255],
        "category": category,
        "summary": summary[:500],
        "content": content,
        "tags": tags[:8],
        "related_entities": related_entities[:12],
        "sources": sources[:6],
        "reading_time": _bounded_int(data.get("reading_time"), default=3, min_value=1, max_value=20),
        "risk_level": risk_level,
        "audience": str(data.get("audience") or topic.get("audience") or "大众健康人群").strip()[:100],
    }


def generate_and_save_cover(
    title: str,
    article_id: int,
    category: str = "",
    summary: str = "",
    content: str = "",
    tags: Any = None,
    related_entities: Any = None,
    force: bool = False,
    cover_style: str = "flat_medical",
    preview: bool = False,
) -> str:
    if not dashscope.api_key:
        print("DASHSCOPE_API_KEY 未配置，跳过万相封面生成")
        return ""

    safe_style = re.sub(r"[^a-zA-Z0-9_-]+", "_", cover_style)
    preview_suffix = f"_{int(time.time())}" if preview else ""
    file_name = f"preview_{safe_style}_{article_id}{preview_suffix}.png" if preview else f"article_{article_id}.png"
    local_path = IMAGE_SAVE_DIR / file_name
    if local_path.exists() and not force:
        return LOCAL_IMG_BASE_URL + file_name

    try:
        task_rsp = dashscope.ImageSynthesis.async_call(
            model=WANX_IMAGE_MODEL,
            prompt=build_wanx_cover_prompt(
                title,
                category,
                summary,
                content=content,
                tags=tags,
                related_entities=related_entities,
                cover_style=cover_style,
                variation_index=article_id,
            ),
            n=1,
            size=WANX_IMAGE_SIZE,
        )
        rsp = _wait_for_wanx_result(task_rsp)
        if rsp.status_code != HTTPStatus.OK:
            print(f"万相封面生成失败: {getattr(rsp, 'message', rsp)}")
            return ""

        img_url = _extract_wanx_image_url(rsp)
        if not img_url:
            print(f"万相封面生成失败: 未返回图片 URL | {_wanx_error_detail(rsp)}")
            return ""

        img_res = requests.get(img_url, timeout=30)
        img_res.raise_for_status()
        img_data = img_res.content
        with local_path.open("wb") as f:
            f.write(img_data)
        return LOCAL_IMG_BASE_URL + file_name
    except Exception as err:
        print(f"万相封面生成异常: {err}")
        return ""


def backup_article_covers() -> Path:
    backup_dir = BASE_DIR / "covers_backup" / time.strftime("%Y%m%d_%H%M%S")
    backup_dir.mkdir(parents=True, exist_ok=False)
    copied = 0
    for src in IMAGE_SAVE_DIR.glob("article_*.png"):
        if src.is_file():
            shutil.copy2(src, backup_dir / src.name)
            copied += 1
    print(f"封面备份完成: {copied} 张 -> {backup_dir}")
    return backup_dir


def regenerate_existing_covers(
    limit: int | None = None,
    force: bool = True,
    missing_only: bool = False,
    cover_style: str = "flat_medical",
    preview: bool = False,
    published_only: bool = False,
    backup_covers: bool = False,
):
    db = SessionLocal()
    succeeded = 0
    failed = 0
    try:
        if backup_covers and not preview:
            backup_article_covers()
        query = db.query(Article).order_by(Article.id.asc())
        if published_only:
            query = query.filter(Article.status == "published")
        if missing_only:
            query = query.filter((Article.cover_image.is_(None)) | (Article.cover_image == ""))
        if limit:
            query = query.limit(limit)
        for article in query.all():
            print(f"生成封面: [{article.id}] {article.title}")
            cover = generate_and_save_cover(
                title=article.title,
                article_id=article.id,
                category=article.category,
                summary=article.summary or "",
                content=article.content or "",
                tags=article.tags,
                related_entities=article.related_entities,
                force=force,
                cover_style=cover_style,
                preview=preview,
            )
            if cover:
                if not preview:
                    article.cover_image = cover
                    db.commit()
                succeeded += 1
                print(f"  状态: {'预览封面生成成功' if preview else '补封面成功'} ID={article.id} style={cover_style} cover={cover}")
            else:
                failed += 1
                print(f"  状态: 补封面失败 ID={article.id}")
            time.sleep(1)
    finally:
        db.close()
    print(f"{'预览封面' if preview else '补封面'}完成: 成功 {succeeded} 张，失败 {failed} 张，风格 {cover_style}")


def _existing_article(db, topic: dict[str, Any]) -> Article | None:
    return db.query(Article).filter(
        Article.title == topic["title"],
        Article.category == topic["category"],
    ).first()


def run_aigc_pipeline(
    topics_path: Path = DEFAULT_TOPICS_PATH,
    category: str | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    no_cover: bool = False,
    force_cover: bool = False,
    sleep_seconds: float = 1.0,
    cover_style: str = "flat_medical",
):
    topics = load_topics(topics_path)
    if category:
        if category not in VALID_CATEGORIES:
            raise ValueError(f"非法分类: {category}")
        topics = [topic for topic in topics if topic["category"] == category]
    if limit:
        topics = topics[:limit]

    db = SessionLocal()
    created = 0
    skipped = 0
    covers = 0
    content_failed = 0
    cover_failed = 0
    empty_content = 0
    try:
        print(f"题库选题数: {len(topics)}")
        for idx, topic in enumerate(topics, 1):
            print(f"[{idx}/{len(topics)}] {topic['category']} | {topic['title']}")
            existing = _existing_article(db, topic)
            if existing:
                skipped += 1
                if not no_cover and (force_cover or not existing.cover_image):
                    if dry_run:
                        print(f"  DRY-RUN: 将为已存在文章补封面 ID={existing.id}")
                    else:
                        print(f"  状态: 已存在但缺封面，开始补封面 ID={existing.id}")
                        cover = generate_and_save_cover(
                            existing.title,
                            existing.id,
                            existing.category,
                            existing.summary or "",
                            existing.content or "",
                            tags=existing.tags,
                            related_entities=existing.related_entities,
                            force=force_cover,
                            cover_style=cover_style,
                        )
                        if cover:
                            existing.cover_image = cover
                            db.commit()
                            covers += 1
                            print(f"  状态: 补封面成功 ID={existing.id} cover={cover}")
                        else:
                            cover_failed += 1
                            print(f"  状态: 补封面失败 ID={existing.id}，文章保留为空封面")
                else:
                    print(f"  状态: 已存在，跳过 ID={existing.id}")
                continue

            if dry_run:
                print("  状态: DRY-RUN，将生成正文、入库并调用万相生成封面")
                continue

            data = generate_medical_content(topic)
            if not data:
                content_failed += 1
                print("  状态: 正文生成失败，跳过入库")
                continue
            payload = normalize_article_payload(data, topic)
            if not payload["content"]:
                empty_content += 1
                print("  正文为空，跳过")
                continue

            article = Article(
                **payload,
                cover_image="",
                view_count=random.randint(100, 800),
                likes=random.randint(5, 80),
                status="published",
                is_hot=False,
            )
            db.add(article)
            db.commit()
            db.refresh(article)
            created += 1
            print(f"  状态: 正文入库成功 ID={article.id}")

            if not no_cover:
                print(f"  状态: 开始生成封面 ID={article.id}")
                cover = generate_and_save_cover(
                    article.title,
                    article.id,
                    article.category,
                    article.summary or "",
                    article.content or "",
                    tags=article.tags,
                    related_entities=article.related_entities,
                    force=force_cover,
                    cover_style=cover_style,
                )
                if cover:
                    article.cover_image = cover
                    db.commit()
                    covers += 1
                    print(f"  状态: 封面生成成功 ID={article.id} cover={cover}")
                else:
                    cover_failed += 1
                    print(f"  状态: 封面生成失败 ID={article.id}，文章已入库但封面为空")
            else:
                print(f"  状态: 已按 --no-cover 跳过封面生成 ID={article.id}")
            time.sleep(sleep_seconds)
    finally:
        db.close()

    print(
        "完成: "
        f"新增 {created} 篇，跳过 {skipped} 篇，写入封面 {covers} 张，"
        f"正文失败 {content_failed} 篇，正文为空 {empty_content} 篇，封面失败 {cover_failed} 张"
    )


def main():
    parser = argparse.ArgumentParser(description="Generate health knowledge articles from a structured topic pool.")
    parser.add_argument("--topics", type=Path, default=DEFAULT_TOPICS_PATH, help="Topic pool JSON path.")
    parser.add_argument("--category", choices=sorted(VALID_CATEGORIES), help="Only generate one category.")
    parser.add_argument("--limit", type=int, default=None, help="Limit selected topic count.")
    parser.add_argument("--dry-run", action="store_true", help="Validate topic selection without calling LLM, Wanxiang or DB writes.")
    parser.add_argument("--no-cover", action="store_true", help="Skip Wanxiang cover generation.")
    parser.add_argument("--force-cover", action="store_true", help="Regenerate cover even when article_{id}.png exists.")
    parser.add_argument("--cover-style", choices=sorted(COVER_STYLE_PRESETS), default="flat_medical", help="Wanxiang cover prompt preset.")
    parser.add_argument("--preview-covers", action="store_true", help="Generate preview_{style}_{id}.png without updating article cover_image.")
    parser.add_argument("--sleep", type=float, default=1.0, help="Seconds to sleep between created articles.")
    parser.add_argument("--regen-covers", action="store_true", help="Regenerate covers for existing articles.")
    parser.add_argument("--missing-covers-only", action="store_true", help="Only fill missing covers when used with --regen-covers.")
    parser.add_argument("--published-only", action="store_true", help="Only operate on published articles when used with --regen-covers.")
    parser.add_argument("--backup-covers", action="store_true", help="Backup article_*.png before non-preview regeneration.")
    args = parser.parse_args()

    if args.regen_covers:
        is_healing_full_regen = args.cover_style == "healing_lifestyle" and args.force_cover and not args.preview_covers
        regenerate_existing_covers(
            limit=args.limit,
            force=args.force_cover,
            missing_only=args.missing_covers_only,
            cover_style=args.cover_style,
            preview=args.preview_covers,
            published_only=args.published_only or args.cover_style == "healing_lifestyle",
            backup_covers=args.backup_covers or is_healing_full_regen,
        )
        return

    run_aigc_pipeline(
        topics_path=args.topics,
        category=args.category,
        limit=args.limit,
        dry_run=args.dry_run,
        no_cover=args.no_cover,
        force_cover=args.force_cover,
        sleep_seconds=args.sleep,
        cover_style=args.cover_style,
    )


if __name__ == "__main__":
    main()
