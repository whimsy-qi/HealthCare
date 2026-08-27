# agents/triage_agent.py
import os
import json
import logging
from dotenv import load_dotenv, find_dotenv
from core.llm_client import shared_client as client, DEFAULT_MODEL as TRIAGE_MODEL
from core.intent_ontology import (
    ACT_LABELS, ATTR_LABELS, ACT_KEYS, ATTR_KEYS,
    normalize_act, normalize_attr, render_fewshot_block,
)

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("TriageAgent")


async def triage_query(query: str, history: list = None, has_image: bool = False) -> dict:
    logger.info(f"🏥 [Task Planner] 正在进行全科意图拆解与任务规划: '{query}'")

    context_str = "无"
    if history and len(history) > 0:
        context_lines = [f"{'User' if m.get('role') == 'user' else 'AI'}: {m.get('content')}" for m in history]
        context_str = "\n".join(context_lines)

    # 🌟 核心重构：引入双层意图字典、Urgency评估、严格的角色界限与防幻觉 Few-Shot
    system_prompt = f"""
    你是一个三甲医院的高级 AI 智能分诊大脑（Task Planner）。
    请结合【近期对话上下文】、【用户最新提问】以及【是否包含图片】，将用户的需求精准映射到预定义的“层级意图本体字典”中。

    【近期对话上下文】
    {context_str}

    【当前请求状态】
    用户是否附带了医学图片/检验单：{"是" if has_image else "否"}

    【🧠 双层意图本体字典 (Dual-Level Intent Ontology)】

    1. SYMPTOM_ANALYSIS (症状分析) —— 【默认档】只要用户在描述身体症状且没有明确在问药，就走这里
       - DIAGNOSIS: 描述身体不适 / 求排查 / 求确诊。**即使是一句裸主诉（如"我头疼"、"胸痛"、"肚子不舒服"、"最近老咳嗽"），只要没有明确说"吃什么药/怎么处理"，一律走这个子意图**。也包括显式问"我这是啥病 / 是不是得 XX 了"。
       - DURATION: 问病程/严重度（如"这病多久能好"、"严不严重"、"会不会留后遗症"）

    2. MEDICATION_REVIEW (用药审查)
       - CONTRAINDICATION: 查禁忌（如“高血压能吃这个吗”、“这药有什么绝对不能吃的”）
       - DOSAGE: 查用法用量（如“一天吃几次”、“饭前还是饭后”）
       - SIDE_EFFECT: 查不良反应（如“吃完恶心正常吗”）
       - INTERACTION: 查药物冲突（如“这两个药能一起吃吗”）
       - GENERAL_MED: 综合/模糊用药咨询

    3. RUMOR_VERIFICATION (辟谣与求证)
       - FACT_CHECK: 事实核查（如“听说骨头汤补钙，真的吗？”）

    4. REPORT_INTERPRETATION (报告解读)
       - LAB_RESULT: 化验单/检查单/医学影像解读

    5. CHITCHAT_OR_REJECT (闲聊与拒绝)
       - GREETING: 日常问候或非医疗话题

    6. GENERAL_CONSULTATION (综合问答兜底) —— 只在【明确求治疗建议】或【非症状类宽泛医学问答】时才进
       - TREATMENT: 用户**明确**要求"开药/推荐药/怎么处理/外伤急救"，且通常不是在同时描述新的未确诊症状。触发关键词：吃什么药、推荐、怎么处理、怎么办（仅在伴随明确求药/求处理语气时）、急救。
         · 例："摔破了怎么处理"、"蚊子咬了涂什么好"、"落枕了推荐个膏药"
         · 反例："我头疼" → 走 SYMPTOM_ANALYSIS；"头疼该吃什么药" → 才走 GENERAL/TREATMENT
       - GENERAL: 不属于以上任何类别的宽泛医疗知识问答（如"维生素 D 有什么作用"、"怎么预防感冒"）

    【💡 核心拆解逻辑（至关重要，严禁违背！）】
    1. **SYMPTOM vs GENERAL 的分界（最常出错处，必须严格执行）**：
       判定步骤（按顺序匹配，命中即停）：
       a. 用户描述了身体症状 **且** 明确要求"吃什么药/怎么处理/开药/推荐"之类的治疗性诉求 → **GENERAL_CONSULTATION / TREATMENT**（不允许走 SYMPTOM，这属于医生开处方范畴）
       b. 用户描述了身体症状但**没有**明确求药/求处理（包括裸主诉"我头疼"、"胸口闷"） → **SYMPTOM_ANALYSIS / DIAGNOSIS**（默认档，哪怕只有一个症状词也要走）
       c. 用户完全没描述症状，只是问医学常识/预防/保健 → **GENERAL_CONSULTATION / GENERAL**
       判分表速查：
         "我头疼"                   → SYMPTOM_ANALYSIS / DIAGNOSIS
         "我头疼该吃什么药"          → GENERAL_CONSULTATION / TREATMENT
         "胸痛"                     → SYMPTOM_ANALYSIS / DIAGNOSIS（urgency 可能抬到 HIGH）
         "维生素 D 有什么作用"       → GENERAL_CONSULTATION / GENERAL
         "我能吃布洛芬吗"            → MEDICATION_REVIEW / CONTRAINDICATION
       走 GENERAL/TREATMENT 时 extracted_entities 保持空数组 []（因为不提取药名做审查）。
    2. **实体提取的”当下性”（防幻觉红线）**：
       - 提取的核心药名必须是用户【本次最新提问】中确实想要查询的药！绝不允许因为上下文提到了某个药，就在用户仅仅问”那我该怎么办/吃什么”时，强行把历史药物抓取出来凑数。
    3. **⚠️ 药物名 + 求证句式 → 辟谣而非用药审查（高频出错点！）**：
       即使用了药物名，只要语气是”求证某说法的真假”（标志词：会不会/真的吗/听说…是真的吗/有人说…靠谱吗），
       主线必须是 RUMOR_VERIFICATION/FACT_CHECK，**不是** MEDICATION_REVIEW。
       - “吃头孢后喝酒会不会死人” → RUMOR_VERIFICATION（求证”头孢+酒致死”这一说法）
       - “布洛芬和阿司匹林能一起吃吗” → MEDICATION_REVIEW/INTERACTION（询问用药操作）
       - “阿司匹林真的能防癌吗” → RUMOR_VERIFICATION（求证疗效传言）
       判定口诀：**在问”能不能一起吃/怎么吃” → 用药审查；在问”XX 说法是真的吗” → 辟谣**。
    4. **强制视觉触发**：只要用户附带了图片，必须将 “VISION_ANALYSIS” 加入 parallel_intents。
    5. **复合意图预查**：如果用户描述了症状并询问了某种特定药物（如”我肚子痛，还能吃二甲双胍吗”），主意图为 SYMPTOM_ANALYSIS，同时必须将 “MEDICATION_PRECHECK” 加入 parallel_intents。
    6. **指代消解**：遇到代词（如”这个药”），必须结合上下文还原为真实的医学实体。
    7. **⚠️ 多药对比 → 用药审查（高频出错点！）**：
       提到两个及以上具体药物（或药物+疗法），且在问区别/对比/选择/哪个好，
       主线必须是 MEDICATION_REVIEW，子意图 GENERAL_MED。**不是** GENERAL_CONSULTATION。
       - “速效救心丸和硝酸甘油有什么区别” → MEDICATION_REVIEW/GENERAL_MED
       - “布洛芬和对乙酰氨基酚哪个副作用小” → MEDICATION_REVIEW/SIDE_EFFECT
       - “降压药选缬沙坦还是氨氯地平” → MEDICATION_REVIEW/GENERAL_MED
       判定口诀：**单个药名 + 常识性问题（如”维生素D有什么作用”）→ 全科；多药对比/选择 → 用药审查**。
    
    【🆕 二维内容轴标注（与 primary_intent 正交，必须额外输出）】
    在【domain × sub_intent】之外，额外给出【行为 act_intent × 属性 attr_intent】二元组：

    ◉ act_intent ∈ {{ASK, CONFIRM, SEEK_HELP, DEBUNK, ANALYZE}}
       - ASK       询问：用户主动想了解某医学知识
       - CONFIRM   确认：用户已有判断、希望求证
       - SEEK_HELP 求助：用户处于不适、需要处理方案
       - DEBUNK    辟谣：用户对某说法表怀疑、想求真假
       - ANALYZE   分析：用户提供数据/报告、想要解读

    ◉ attr_intent ∈ {{CAUSE, SYMPTOM, BASIC, CHECKUP, VISIT, PREVENT, DIAGNOSE, CAUTION}}
       - CAUSE    病因（致病机制、风险因素）
       - SYMPTOM  临床表现（症状、体征）
       - BASIC    基础信息（定义、术语）
       - CHECKUP  检查建议（化验/影像/筛查）
       - VISIT    就医建议（挂哪科、是否急诊）
       - PREVENT  预防方式（生活习惯、疫苗）
       - DIAGNOSE 诊断推理（鉴别诊断、可能疾病）
       - CAUTION  注意事项（禁忌、警示、用药须知）

    🎯 act 与 attr 的判定不影响路由（仍由 primary_intent 决定），仅用于让下游 agent
       做 prompt 偏重。即使是同一 domain，"我血压 160 怎么办"(SEEK_HELP/VISIT)
       与"高血压病因"(ASK/CAUSE) 的回答应内容侧重完全不同。

    【🚀 多意图解耦（v2 新增）】
    当用户 query 包含多个可独立处理的子需求时（如"我头疼，布洛芬现在多少钱"），
    请将复合 query 分解为独立的意图列表（intents 字段），每个子意图独立标注 domain/sub/confidence。
    - 如果只有一个主需求，intents 数组只含 1 条
    - 如果有 2+ 个独立需求，列出所有子意图，按优先级排序
    - 每条子意图的 confidence 为 0-1 之间，∑不要求=1（子意图可以同时成立）
    - primary_intent 仍填最高优的那个（向后兼容）

    {render_fewshot_block(max_examples=12)}

    【强制输出规范 (严格 JSON)】
    {{
        "urgency": "EMERGENCY (如吐血、剧烈胸痛、误服大量毒药、极度绝望/自杀倾向) | HIGH (如持续高烧) | NORMAL (常规咨询)",
        "primary_intent": "必须是上述6大主意图之一（最高优先级的那个）",
        "sub_intent": "主意图下属的子意图",
        "act_intent": "上述 5 类 act 之一",
        "attr_intent": "上述 8 类 attr 之一",
        "parallel_intents": ["VISION_ANALYSIS", "MEDICATION_PRECHECK", ... 无则为空数组 []],
        "extracted_entities": ["提取出的核心药名、病名。必须遵循'当下性'原则！无则为空 []"],
        "thinking": "意图与紧急度判定逻辑（20字以内）",
        "intents": [
            {{
                "domain": "SYMPTOM_ANALYSIS",
                "sub": "DIAGNOSIS",
                "confidence": 0.92,
                "act": "SEEK_HELP",
                "attr": "DIAGNOSE"
            }}
        ]
    }}

    【复合意图示例】
    用户：我头疼，布洛芬现在多少钱
    输出：{{..., "primary_intent": "SYMPTOM_ANALYSIS", "sub_intent": "DIAGNOSIS",
      "intents": [
        {{"domain":"SYMPTOM_ANALYSIS","sub":"DIAGNOSIS","confidence":0.92,"act":"SEEK_HELP","attr":"DIAGNOSE"}},
        {{"domain":"MEDICATION_REVIEW","sub":"DOSAGE","confidence":0.85,"act":"ASK","attr":"CAUTION"}}
      ]
    }}

    用户：高血压怎么预防，平时注意什么
    输出：{{..., "primary_intent": "GENERAL_CONSULTATION", "sub_intent": "GENERAL",
      "intents": [
        {{"domain":"GENERAL_CONSULTATION","sub":"GENERAL","confidence":0.90,"act":"ASK","attr":"PREVENT"}}
      ]
    }}

   【Few-Shot 经典防错示例（每条都补齐 act/attr 二元组）】
    用户：我头疼
    输出：{{"urgency": "NORMAL", "primary_intent": "SYMPTOM_ANALYSIS", "sub_intent": "DIAGNOSIS", "act_intent": "SEEK_HELP", "attr_intent": "DIAGNOSE", "parallel_intents": [], "extracted_entities": [], "thinking": "裸主诉默认走诊断分析"}}

    用户：胸痛
    输出：{{"urgency": "HIGH", "primary_intent": "SYMPTOM_ANALYSIS", "sub_intent": "DIAGNOSIS", "act_intent": "SEEK_HELP", "attr_intent": "DIAGNOSE", "parallel_intents": [], "extracted_entities": [], "thinking": "胸痛为高危症状，默认走诊断分析"}}

    用户：最近老咳嗽，已经两周了
    输出：{{"urgency": "NORMAL", "primary_intent": "SYMPTOM_ANALYSIS", "sub_intent": "DIAGNOSIS", "act_intent": "SEEK_HELP", "attr_intent": "DIAGNOSE", "parallel_intents": [], "extracted_entities": [], "thinking": "描述持续症状未求药，走诊断分析"}}

    用户：我刚才误服了一整瓶降压药，现在头很晕！
    输出：{{"urgency": "EMERGENCY", "primary_intent": "SYMPTOM_ANALYSIS", "sub_intent": "DIAGNOSIS", "act_intent": "SEEK_HELP", "attr_intent": "VISIT", "parallel_intents": [], "extracted_entities": ["降压药"], "thinking": "误服药物伴头晕，极高危急症"}}

    用户：我头疼该吃什么药
    输出：{{"urgency": "NORMAL", "primary_intent": "GENERAL_CONSULTATION", "sub_intent": "TREATMENT", "act_intent": "SEEK_HELP", "attr_intent": "CAUTION", "parallel_intents": [], "extracted_entities": [], "thinking": "明确求药属于开处方范畴，转交全科"}}

    用户：我高血压，吃这个阿司匹林肠溶片会伤胃吗？
    输出：{{"urgency": "NORMAL", "primary_intent": "MEDICATION_REVIEW", "sub_intent": "SIDE_EFFECT", "act_intent": "CONFIRM", "attr_intent": "CAUTION", "parallel_intents": [], "extracted_entities": ["高血压", "阿司匹林肠溶片"], "thinking": "询问特定药物在基础病下的副作用"}}

    用户：(上文讨论过布洛芬不能吃) 那我可以吃什么药缓解？
    输出：{{"urgency": "NORMAL", "primary_intent": "GENERAL_CONSULTATION", "sub_intent": "TREATMENT", "act_intent": "SEEK_HELP", "attr_intent": "CAUTION", "parallel_intents": [], "extracted_entities": [], "thinking": "泛泛求推荐药物，转交全科"}}

    用户：[用户带了图片] 医生你看我胳膊长了红疙瘩，该去药店买什么药膏对付一下？
    输出：{{"urgency": "NORMAL", "primary_intent": "GENERAL_CONSULTATION", "sub_intent": "TREATMENT", "act_intent": "SEEK_HELP", "attr_intent": "VISIT", "parallel_intents": ["VISION_ANALYSIS"], "extracted_entities": [], "thinking": "有图需视觉提取，泛泛求药转全科"}}

    用户：维生素 D 有什么作用
    输出：{{"urgency": "NORMAL", "primary_intent": "GENERAL_CONSULTATION", "sub_intent": "GENERAL", "act_intent": "ASK", "attr_intent": "BASIC", "parallel_intents": [], "extracted_entities": [], "thinking": "纯医学常识问答"}}

    用户：吃头孢后喝酒会不会死人
    输出：{{"urgency": "NORMAL", "primary_intent": "RUMOR_VERIFICATION", "sub_intent": "FACT_CHECK", "act_intent": "CONFIRM", "attr_intent": "CAUTION", "parallel_intents": [], "extracted_entities": ["头孢"], "thinking": "求证药物+酒精致死传言，属于辟谣而非用药操作"}}

    用户：阿司匹林真的能防癌吗
    输出：{{"urgency": "NORMAL", "primary_intent": "RUMOR_VERIFICATION", "sub_intent": "FACT_CHECK", "act_intent": "CONFIRM", "attr_intent": "CAUSE", "parallel_intents": [], "extracted_entities": ["阿司匹林"], "thinking": "求证药物疗效传言，辟谣意图"}}

    用户：布洛芬和阿司匹林能一起吃吗
    输出：{{"urgency": "NORMAL", "primary_intent": "MEDICATION_REVIEW", "sub_intent": "INTERACTION", "act_intent": "CONFIRM", "attr_intent": "CAUTION", "parallel_intents": [], "extracted_entities": ["布洛芬", "阿司匹林"], "thinking": "询问两种药物能否联用，用药审查"}}

    用户：速效救心丸和硝酸甘油有什么区别
    输出：{{"urgency": "NORMAL", "primary_intent": "MEDICATION_REVIEW", "sub_intent": "GENERAL_MED", "act_intent": "ASK", "attr_intent": "BASIC", "parallel_intents": [], "extracted_entities": ["速效救心丸", "硝酸甘油"], "thinking": "多药对比属用药审查，不走全科"}}

    用户：布洛芬和对乙酰氨基酚哪个副作用小
    输出：{{"urgency": "NORMAL", "primary_intent": "MEDICATION_REVIEW", "sub_intent": "SIDE_EFFECT", "act_intent": "ASK", "attr_intent": "CAUTION", "parallel_intents": [], "extracted_entities": ["布洛芬", "对乙酰氨基酚"], "thinking": "多药副作用对比，用药审查"}}
    """

    raw_content = ""
    try:
        # 🌟 核心修复：在真实提问前，动态注入视觉标签，强迫大模型触发模式匹配
        image_tag = "[用户带了图片] " if has_image else ""

        response = await client.chat.completions.create(
            model=TRIAGE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"【用户最新提问】：{image_tag}{query}"}
            ],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        raw_content = (response.choices[0].message.content or "").strip()
        if not raw_content:
            logger.warning("[Triage] 首次响应为空，1 秒后重试...")
            import asyncio
            await asyncio.sleep(1)
            response = await client.chat.completions.create(
                model=TRIAGE_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"【用户最新提问】：{image_tag}{query}"}
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )
            raw_content = (response.choices[0].message.content or "").strip()
        if not raw_content:
            raise ValueError("两次尝试后响应仍为空")
        result = json.loads(raw_content)

        # 🆕 二维内容轴标准化（容错：大小写 / 中文别名 / 非法值 → 空串）
        result["act_intent"]  = normalize_act(result.get("act_intent", ""))
        result["attr_intent"] = normalize_attr(result.get("attr_intent", ""))

        # 🚀 多意图列表标准化
        raw_intents = result.get("intents", [])
        if not raw_intents or not isinstance(raw_intents, list):
            # 向后兼容：旧格式→构造单条 intents
            result["intents"] = [{
                "domain": result.get("primary_intent", "GENERAL_CONSULTATION"),
                "sub": result.get("sub_intent", "GENERAL"),
                "confidence": 1.0,
                "act": result["act_intent"],
                "attr": result["attr_intent"],
            }]
        else:
            normalized = []
            for item in raw_intents:
                if not isinstance(item, dict):
                    continue
                item["act"] = normalize_act(item.get("act", ""))
                item["attr"] = normalize_attr(item.get("attr", ""))
                item.setdefault("domain", result.get("primary_intent", "GENERAL_CONSULTATION"))
                item.setdefault("sub", result.get("sub_intent", "GENERAL"))
                item.setdefault("confidence", 0.8)
                normalized.append(item)
            result["intents"] = normalized if normalized else result["intents"]
        # 多意图数量反映不确定性
        if len(result["intents"]) > 1:
            result["uncertainty"] = min(0.5, len(result["intents"]) * 0.2)

        logger.info(
            f"🔀 [Task Planner] 优先级: {result.get('urgency')} | 主线: {result.get('primary_intent')} "
            f"| 子意图: {result.get('sub_intent')} | 二维轴: act={result.get('act_intent') or '-'}/"
            f"attr={result.get('attr_intent') or '-'} | 并行: {result.get('parallel_intents')} "
            f"| 实体: {result.get('extracted_entities')}"
        )
        return result

    except Exception as e:
        logger.error(f"❌ [Task Planner] 任务拆解异常: {e} | raw={raw_content[:200]}")
        # 严格的安全降级兜底
        return {
            "urgency": "NORMAL",
            "primary_intent": "GENERAL_CONSULTATION",
            "sub_intent": "GENERAL",
            "act_intent": "ASK",
            "attr_intent": "BASIC",
            "parallel_intents": [],
            "extracted_entities": [],
            "thinking": "解析失败，执行综合安全降级"
        }