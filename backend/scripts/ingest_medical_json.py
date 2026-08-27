import os
import json
import time
import re
from typing import List, Dict
from dotenv import load_dotenv
import dashscope
from dashvector import Client, Doc

# 1. 环境配置
load_dotenv()
os.environ['HTTP_PROXY'] = ''
os.environ['HTTPS_PROXY'] = ''
os.environ['ALL_PROXY'] = ''

dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
dv_client = Client(api_key=os.getenv("DASHVECTOR_API_KEY"), endpoint=os.getenv("DASHVECTOR_ENDPOINT"))
collection = dv_client.get("multimodal_medical_db")

# 🌟 核心科室名单
TARGET_DEPTS = [
    "内科", "外科", "五官科", "儿科", "皮肤性病科", "皮肤科", "骨外科", "肿瘤科",
    "中医科", "中医综合", "眼科", "妇产科", "消化内科", "普外科", "儿科综合",
    "肿瘤外科", "神经内科", "妇科", "血液科", "心内科", "呼吸内科", "小儿内科",
    "传染科", "耳鼻喉科", "内分泌科", "急诊科", "心胸外科", "神经外科",
    "风湿免疫科", "口腔科", "泌尿外科", "肾内科", "肿瘤内科", "产科",
    "精神科", "小儿外科", "男科", "肝病", "肛肠科", "肝胆外科", "心理科", "性病科", "营养科"
]


def show_db_status():
    """实时监控数据库水位 - 增强鲁棒版"""
    try:
        stats = collection.stats()
        if stats.code == 0:
            # 🌟 仅访问确定存在的属性 total_doc_count
            total = getattr(stats.output, 'total_doc_count', 0)
            # status 属性可能不存在，我们改用 getattr 安全获取
            status_str = getattr(stats.output, 'status', 'SERVING')
            print(f"\n📊 [数据库监控] 当前已存向量总数: {total} | 状态: {status_str}")
            return total
        else:
            print(f"⚠️ 无法获取统计信息: {stats.message}")
            return 0
    except Exception as e:
        # 即使统计报错，也不要影响主程序运行
        print(f"⚠️ 统计模块提示: 无法读取实时水位 ({e})")
        return 0


def clean_text(text: str) -> str:
    if not text: return ""
    return re.sub(r'[\n\r\t]', ' ', text).strip()


def build_knowledge_chunks(data: Dict) -> List[str]:
    disease = data.get('name', '未知疾病')
    chunks = []
    # 抽取逻辑：百科、诊断、检查、方案、导诊
    if data.get('desc'): chunks.append(f"【疾病百科】{disease}：{clean_text(data['desc'])}")
    if data.get('symptom'):
        chunks.append(f"【诊断逻辑】若出现 {'、'.join(data['symptom'])}，应怀疑为“{disease}”。")
    if data.get('check'):
        chunks.append(f"【临床检查】针对疑似“{disease}”，建议检查：{'、'.join(data['check'])}。")
    if data.get('common_drug'):
        chunks.append(f"【治疗方案】“{disease}”常用药：{'、'.join(data['common_drug'])}。")
    if data.get('cure_department'):
        chunks.append(f"【导诊建议】“{disease}”请挂号：{'、'.join(data['cure_department'])}。")
    return chunks


def ingest_full_engine(file_path: str, checkpoint_file: str = "ingest_checkpoint.txt"):
    # 启动前状态
    before_count = show_db_status()

    start_line = 0
    if os.path.exists(checkpoint_file):
        with open(checkpoint_file, 'r') as f:
            start_line = int(f.read().strip())
            print(f"🔄 检测到断点，将从第 {start_line} 行重启...")

    print("🚀 启动全量精炼入库脚本 (增强防断网模式)...")
    success_count = 0

    with open(file_path, 'r', encoding='utf-8') as f:
        for i, line in enumerate(f):
            if i < start_line: continue

            try:
                data = json.loads(line)
                depts = data.get('cure_department', [])

                # 过滤策略
                if not any(d in TARGET_DEPTS for d in depts):
                    continue

                disease_name = data.get('name', 'unknown')
                category = data.get('category', ['未知科室'])[-1]
                knowledge_list = build_knowledge_chunks(data)

                for snippet in knowledge_list:
                    snippet_success = False

                    # 🌟 终极防断网重试机制 (6次)
                    for retry in range(6):
                        try:
                            resp = dashscope.MultiModalEmbedding.call(
                                model="qwen3-vl-embedding",
                                input=[{'text': snippet}]
                            )
                            if resp.status_code == 200:
                                vec = resp.output['embeddings'][0]['embedding']
                                # 🌟 使用 v3 标识符，确保与旧数据区分
                                collection.insert(Doc(
                                    id=f"kg_v3_{i}_{int(time.time() * 1000)}",
                                    vector=vec,
                                    fields={"source": "medical_kg", "content": snippet, "disease": disease_name,
                                            "dept": category}
                                ))
                                snippet_success = True
                                break  # 成功，跳出当前重试循环
                            elif resp.status_code == 429:
                                print(f"⚠️ [API限流] 触发 429，休眠 8 秒...")
                                time.sleep(8)
                            else:
                                print(f"⚠️ [API异常] {resp.message}，休眠 3 秒...")
                                time.sleep(3)

                        except Exception as network_e:
                            # 🌟 核心修复：捕获底层 SSL 阻断和 10053 报错，强制休眠恢复网络环境
                            print(
                                f"⚠️ [网络阻断] 第 {retry + 1}/6 次尝试失败，强行休眠 10 秒... ({str(network_e)[:50]})")
                            time.sleep(10)

                    # 如果6次重试都失败了，抛出异常放弃这个疾病
                    if not snippet_success:
                        raise Exception("该片段在 6 次重试后彻底失败。")

                success_count += 1

                # 每 20 条记录存一次档
                if i % 20 == 0:
                    with open(checkpoint_file, 'w') as f_check:
                        f_check.write(str(i))
                    print(f"✅ 已处理 {success_count} 条，进度记录至第 {i} 行...")

                # 加大基础限速，温柔对待服务器
                time.sleep(0.5)

            except Exception as e:
                print(f"❌ 解析第 {i} 行彻底失败: {e}")
                continue

    print("\n" + "=" * 40)
    print(f"🎊 全量入库任务圆满结束！")
    after_count = show_db_status()
    print(f"📈 本次运行新增向量数: {after_count - before_count}")
    print("=" * 40)


if __name__ == "__main__":
    # 🌟 请确保此处路径 100% 正确
    json_path = r'D:\Health_system\backend\scripts\medical.json'
    ingest_full_engine(json_path)