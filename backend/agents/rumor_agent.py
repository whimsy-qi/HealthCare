# agents/rumor_agent.py
import os
import json
import logging
import asyncio
from typing import List, Dict, Tuple, Optional
from dotenv import load_dotenv, find_dotenv
from core.llm_client import shared_client as client, DEFAULT_MODEL

from scripts.tools_search import search_dynamic_medical_info
from scripts.main_agent import get_multimodal_context
from scripts.kg_pruner import KnowledgeGraphPruner

load_dotenv(find_dotenv(usecwd=True))
logger = logging.getLogger("RumorAgent")

kg_pruner = KnowledgeGraphPruner()


class RumorAgent:
    """
    🔥 工业级多智能体辩论辟谣框架 (带有增量去重、并发清洗与熔断机制)
    """

    def __init__(self):
        self.max_turns = int(os.getenv("RUMOR_MAX_TURNS", 2))
        self.timeout_sec = float(os.getenv("RUMOR_TIMEOUT", 25.0))
        self.judge_temp = float(os.getenv("RUMOR_JUDGE_TEMP", 0.1))

        self.state = {
            "query": "",
            "raw_scout_evidence": [],
            "raw_medical_evidence": [],
            "seen_urls": set(),
            "seen_med_titles": set(),
            "verdict": None,
            "internal_scratchpad": [],
            "audit_logs": []
        }

    # ==========================================
    # 🌟 架构新增：高并发数据脱水清洗机 (Data Washer)
    # ==========================================
    async def _wash_sources(self, sources: list, source_type: str) -> list:
        """
        利用大模型对抓取到的脏数据（如网页导航栏、PDF页码）进行高并发清洗与提纯。
        """
        if not sources:
            return []

        async def wash(src):
            original_content = src.get('content', '')
            if len(original_content) < 50:  # 太短的无需清洗
                return src

            prompt = f"""
            你是一个严谨的医疗数据清洗专家。请将以下从【{source_type}】提取的原始杂乱文本，剔除所有的网页导航菜单（如"新闻、娱乐"等）、PDF乱码、页码和无意义字符。
            请将核心事实或医学结论提炼成一段通顺、密集的纯文本摘要（约 80-100 字）。
            绝对禁止捏造原始文本中没有的信息。直接输出结果，不要废话。
            【原始文本】：
            {original_content[:1500]}
            """
            try:
                resp = await client.chat.completions.create(
                    model=DEFAULT_MODEL,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=200
                )
                # 将清洗后的纯净文本覆写回 content
                src["content"] = resp.choices[0].message.content.strip()
            except Exception as e:
                logger.warning(f"数据清洗单点波动: {e}")
            return src

        # 高并发清洗所有源卡片，极大地压缩了时间损耗
        washed_sources = await asyncio.gather(*(wash(s) for s in sources))
        return list(washed_sources)

    # ==========================================
    # 🌟 行动智能体：医学病理专家 (Equipment: GraphRAG)
    # ==========================================
    async def run_medical_researcher(self, query: str, entities: List[str]):
        logger.info("[Medical Researcher] 启动：正在并发执行 VectorRAG 与 GraphRAG...")
        try:
            vector_task = get_multimodal_context(query)
            graph_task = asyncio.to_thread(kg_pruner.execute_pruning, entities, top_k=2)

            vector_ctx, sources, imgs = await asyncio.wait_for(vector_task, timeout=self.timeout_sec)
            graph_ctx = await asyncio.wait_for(graph_task, timeout=self.timeout_sec)

            new_sources = []
            for src in (sources or []):
                if src.get("title", "") not in self.state["seen_med_titles"]:
                    self.state["seen_med_titles"].add(src.get("title", ""))
                    new_sources.append(src)

            # 🌟 触发核心脱水动作
            washed_new_sources = await self._wash_sources(new_sources, "权威医学指南与期刊")

            raw_evidence = {
                "vector_based_guidelines": washed_new_sources,
                "graph_based_reasoning_paths": graph_ctx if graph_ctx else "知识图谱无明显推演路径"
            }

            self.state["raw_medical_evidence"].append(raw_evidence)
            self.state["audit_logs"].append(f"[Medical] 证据收集完毕。召回并清洗了{len(washed_new_sources)}条权威指南。")

        except asyncio.TimeoutError:
            logger.error("❌ [Medical Researcher] 检索超时，已触发物理熔断！")
            self.state["audit_logs"].append("⚠️ [Medical] 检索超时，触发熔断保护。")
        except Exception as e:
            logger.error(f"❌ [Medical Researcher] 异常: {e}")

    # ==========================================
    # 🌟 行动智能体：情报侦察员 (Equipment: Web Scout)
    # ==========================================
    async def run_scout_analyst(self, query: str):
        logger.info("[Scout Analyst] 启动：正在执行公网动态扫描兜底...")
        try:
            search_task = search_dynamic_medical_info(query, raw_fetch_count=8, final_top_k=3)
            ctx, sources = await asyncio.wait_for(search_task, timeout=self.timeout_sec)

            new_sources = []
            for src in (sources or []):
                url = src.get("url", "#")
                if url not in self.state["seen_urls"]:
                    self.state["seen_urls"].add(url)
                    new_sources.append(src)

            # 🌟 触发核心脱水动作
            washed_new_sources = await self._wash_sources(new_sources, "公网论坛与新闻报道")

            raw_evidence = {"scout_based_news": washed_new_sources}
            self.state["raw_scout_evidence"].append(raw_evidence)
            self.state["audit_logs"].append(
                f"[Scout] 全球情报收集完毕。严选并清洗了{len(washed_new_sources)}条公网舆情。")

        except asyncio.TimeoutError:
            logger.error("❌ [Scout Analyst] 公网检索超时，已触发物理熔断！")
            self.state["audit_logs"].append("⚠️ [Scout] 公网检索超时，触发熔断保护。")
        except Exception as e:
            logger.error(f"❌ [Scout Analyst] 异常: {e}")

    # ==========================================
    # 🌟 中枢智能体：首席终审判官 (Role: Judge)
    # ==========================================
    async def run_judge_agent(self, history: list) -> Tuple[bool, str]:
        logger.info(f"👨‍⚕️ [Chief Judge] 证据链已就位，启动证据评级与终审程序...")

        scout_evidence = json.dumps(self.state["raw_scout_evidence"], ensure_ascii=False)
        medical_evidence = json.dumps(self.state["raw_medical_evidence"], ensure_ascii=False)

        # 🌟 核心排版修复：通过强力 Markdown 指令约束输出规范
        system_prompt = f"""
        你是一位严谨的三甲医院辟谣中心首席判官。你现在拥有 Medical Researcher 和 Web Scout 搜集来的去重且经过清洗提纯的纯净证据。

        【首席判官的工作流】
        1. 证据格斗 (Critique)：对比 Medical 的【图谱/权威指南】与 Scout 的【公网舆情】。
        2. 证据评级：指南/图谱权重 >>> 外网新闻。
        3. 最终判决：判定证据充足且一致（或虽有冲突但医学依据明确），生成最终 Markdown 报告。
        4. 打回重审：医疗谣言本身就是网民与常识的冲突！遇到冲突直接宣判。🚨只有当【双端均无任何相关证据】时才允许打回重审！

        【医疗谣言】：{self.state["query"]}

        【Medical 纯净物证库】：{medical_evidence}
        【Scout 纯净情报库】：{scout_evidence}

        【Judge Output 规范 (严格 JSON)】
        {{
            "debate_process": "详细的交叉辩驳与证据评级过程（不少于 150 字）。【⚠️ 排版极度严格要求】：必须使用 Markdown 格式分段、分点展示！必须使用 ###、-、**加粗** 等符号。绝不允许输出毫无格式的一整段文字！建议按【Scout证据解析】、【Medical证据解析】、【法官终审比对】三个维度分点列出。",
            "needs_revision": "True (双端均无证据打回重审) | False (已有证据足以宣判)",
            "revision_instructions": "如果 needs_revision 为 True，输出指令；否则为空",
            "final_markdown_report": "如果 needs_revision 为 False，生成最终报告。🚨 绝对禁止自称'三甲医院'。必须严格按照以下 Markdown 模板输出（直接填空，严禁修改结构，🚨绝对不要使用 --- 分割线）：\n\n### 🛡️ AI 智能健康核查报告\n\n#### 📌 核查结论\n\n> **【核查定性】** (选择其一填入：✅ 经核实属实 | ❌ 纯属谣言 | ⚠️ 存在误导/片面 | ❓ 尚无定论)\n>\n> (在此填入1句最精简的结论简述，例如：两者生理机制独立，无法相互抵消。)\n\n#### 🔬 医学原理解析\n\n(在此处务必使用无序列表 `-` 分点提炼生化原理或临床证据，条理清晰，每点不超过50字。)\n\n> *⚠️ 系统免责声明：本核查报告由 AI 多智能体交叉验证生成，仅供健康科普参考，绝不构成任何临床医疗建议。如有不适请立即前往正规医院就诊。*"
        }}
        """
        try:
            response = await client.chat.completions.create(
                model=DEFAULT_MODEL,
                messages=[{"role": "system", "content": system_prompt}],
                response_format={"type": "json_object"},
                temperature=self.judge_temp
            )

            judge_res = json.loads(response.choices[0].message.content)
            self.state["verdict"] = judge_res.get("final_markdown_report", "")

            debate_text = judge_res.get("debate_process", "")
            self.state["internal_scratchpad"].append(debate_text)

            self.state["audit_logs"].append(
                f"[Judge] 庭审辩驳结束。判定 needs_revision: {judge_res.get('needs_revision')}")

            return judge_res.get("needs_revision", False), judge_res.get("revision_instructions", "")

        except Exception as e:
            logger.error(f"❌ 首席判官推理异常: {e}")
            return False, "中枢决策系统发生网络异常，请稍后再试。"


# ==========================================
# 🌟 Rumor Agent 控制器
# ==========================================
async def run_rumor_controller(query: str, entities: List[str], history: list) -> Tuple[str, dict, list]:
    agent = RumorAgent()
    agent.state["query"] = query
    agent.state["audit_logs"].append(f"👨‍⚕️ 首席终审判官接管 Rumor 轨道...")

    for turn in range(agent.max_turns):
        logger.info(f"🔄 [Rumor Agent] 启动第 {turn + 1} 轮交互反思...")

        tasks = [
            agent.run_medical_researcher(query, entities),
            agent.run_scout_analyst(query)
        ]
        await asyncio.gather(*tasks)

        needs_revision, thinking = await agent.run_judge_agent(history)

        if not needs_revision:
            logger.info("✅ [Rumor Agent] 终审判决已出，证据格斗结束。")
            agent.state["audit_logs"].append("[Judge] 证据充足，无需重审，正在生成终审报告。")
            break
        else:
            logger.warning(f"⚠️ [Rumor Agent] 判官认定证据不足，启动 Revision 重审流程！")
            agent.state["audit_logs"].append(f"[Judge] 打回重审！理由: {thinking}")

    if turn == agent.max_turns - 1 and not agent.state["verdict"]:
        logger.warning("⚠️ [Rumor Agent] 达到最大转数，首席判官强制熔断报告...")
        agent.state[
            "verdict"] = f"### ⚖️ 首席终审判官免责熔断公告\n\n### ❓ 尚无权威定论\n\n由于系统检测到关于【{query}】的相关证据极度匮乏，暂时无法给出高置信度的定性。建议遵循专业医生指导。"

    scout_data = agent.state["raw_scout_evidence"][-1].get("scout_based_news", []) if agent.state[
        "raw_scout_evidence"] else []
    medical_data = agent.state["raw_medical_evidence"][-1].get("vector_based_guidelines", []) if agent.state[
        "raw_medical_evidence"] else []

    trace_dict = {
        "scout_data": scout_data,
        "medical_data": medical_data,
        "medical_truth_text": "已通过 VectorRAG 和 GraphRAG 并发召回并【深度清洗提纯】底层医学依据。",
        "critic_reasoning": "\n\n".join(agent.state["internal_scratchpad"])
    }

    return agent.state["verdict"], trace_dict, agent.state["audit_logs"]