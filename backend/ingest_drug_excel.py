import os
import re
import time
import sqlite3
import pandas as pd
import dashscope
from dashvector import Client, Doc
from dotenv import load_dotenv

# 1. 环境初始化
load_dotenv()
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

dv_client = Client(
    api_key=os.getenv("DASHVECTOR_API_KEY"),
    endpoint=os.getenv("DASHVECTOR_ENDPOINT")
)
collection = dv_client.get("multimodal_medical_db")

# ==========================================
# 🌟 核心机制 1：本地 SQLite 断点续传数据库
# ==========================================
DB_FILE = "drug_ingest_cache.db"


def init_db():
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS processed_drugs (
                drug_name TEXT PRIMARY KEY,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')


def is_drug_processed(drug_name):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.execute('SELECT 1 FROM processed_drugs WHERE drug_name = ?', (drug_name,))
        return cursor.fetchone() is not None


def mark_drugs_processed(drug_names):
    with sqlite3.connect(DB_FILE) as conn:
        conn.executemany('INSERT OR IGNORE INTO processed_drugs (drug_name) VALUES (?)',
                         [(name,) for name in drug_names])


# ==========================================
# 数据清洗与组装 (保持不变)
# ==========================================
def clean_text(text):
    if pd.isna(text) or text is None: return "暂无数据"
    text = str(text).strip()
    text = re.sub(r'(?<![a-zA-Z0-9\u4e00-\u9fa5])\?(?![a-zA-Z0-9\u4e00-\u9fa5])', '', text)
    text = re.sub(r'\s+', ' ', text)
    return "暂无数据" if len(text.replace("?", "")) < 2 else text


def construct_drug_markdown(row):
    generic_name = clean_text(row.get('通用名称', ''))
    trade_name = clean_text(row.get('商品名称', ''))
    name_display = generic_name if not trade_name or trade_name == "暂无数据" else f"{generic_name}（{trade_name}）"

    md_card = f"""【药品名称】：{name_display}
【药品分类】：{clean_text(row.get('药品分类', ''))}
【适应症】：{clean_text(row.get('适应症', ''))}
【用法用量】：{clean_text(row.get('用法用量', ''))}
【禁忌症】：{clean_text(row.get('禁忌', ''))}
【不良反应】：{clean_text(row.get('不良反应', ''))}
【注意事项】：{clean_text(row.get('注意事项', ''))}
【特殊人群】：孕产妇（{clean_text(row.get('孕妇及哺乳期妇女用药', ''))}）；儿童（{clean_text(row.get('儿童用药', ''))}）；老人（{clean_text(row.get('老人用药', ''))}）
【药物相互作用】：{clean_text(row.get('药物相互作用', ''))}
"""
    return generic_name, md_card


# ==========================================
# 🌟 核心机制 2：批量并行处理流水线
# ==========================================
def process_batch(batch_data):
    """
    一次性处理 20 条数据，极大降低 API 调用耗时
    """
    if not batch_data: return 0

    # 组装给大模型的批量 input
    inputs = [{'text': item['embed_text']} for item in batch_data]

    for retry in range(3):
        try:
            # 批量请求 Embedding API
            resp = dashscope.MultiModalEmbedding.call(model="qwen3-vl-embedding", input=inputs)

            if resp.status_code == 200:
                docs_to_insert = []
                success_drug_names = []

                # 遍历返回的 20 个向量
                for i, emb_data in enumerate(resp.output['embeddings']):
                    vec = emb_data['embedding']
                    item = batch_data[i]

                    docs_to_insert.append(Doc(
                        id=f"drug_2025_{item['drug_name']}_{int(time.time() * 1000)}_{i}",
                        vector=vec,
                        fields={
                            "source": "drug_manual",
                            "drug_name": item['drug_name'],
                            "content": item['content']
                        }
                    ))
                    success_drug_names.append(item['drug_name'])

                # 批量存入 DashVector
                collection.insert(docs_to_insert)
                # 批量记录到 SQLite 断点续传库
                mark_drugs_processed(success_drug_names)

                return len(success_drug_names)
            else:
                print(f"⚠️ API 批量请求报错: {resp.message}，正在重试...")
                time.sleep(2 ** retry)  # 指数退避，防限流
        except Exception as e:
            print(f"⚠️ 发生异常: {e}，正在重试...")
            time.sleep(2 ** retry)

    return 0


def ingest_drug_data(folder_path):
    init_db()
    print(f"🚀 启动极速药典入库引擎 (已开启断点续传保护)...\n")

    files = [f for f in os.listdir(folder_path) if f.endswith('.xlsx')]
    total_inserted = 0

    for file in files:
        file_path = os.path.join(folder_path, file)
        print(f"=====================================")
        print(f"📄 正在解析 Excel: {file}")

        try:
            df = pd.read_excel(file_path, dtype=str)
        except Exception as e:
            print(f"❌ 读取 {file} 失败: {e}")
            continue

        batch_data = []
        batch_size = 20  # 🌟 阿里 API 单次推荐批处理上限

        for index, row in df.iterrows():
            drug_name, drug_content = construct_drug_markdown(row)

            if not drug_name or drug_name == "暂无数据" or str(drug_name) == "nan":
                continue

            # 🌟 断点续传拦截：如果已经处理过，瞬间跳过，0耗时
            if is_drug_processed(drug_name):
                continue

            embed_text = f"药品名：{drug_name}。适应症：{clean_text(row.get('适应症', ''))}"

            batch_data.append({
                "drug_name": drug_name,
                "embed_text": embed_text,
                "content": drug_content
            })

            # 凑满 20 条，发射一次批量请求
            if len(batch_data) >= batch_size:
                success_count = process_batch(batch_data)
                total_inserted += success_count
                print(f"✅ 成功入库 1 批，已累计入库 {total_inserted} 种新药...")
                batch_data = []
                time.sleep(0.5)  # 平滑限流

        # 扫尾该表剩余的数据
        if batch_data:
            success_count = process_batch(batch_data)
            total_inserted += success_count

        print(f"🎉 表格 {file} 清点完毕！\n")

    print(f"🎊 所有药品数据处理完毕！本次新增入库: {total_inserted} 条。")


if __name__ == "__main__":
    TARGET_FOLDER = r"D:\Health_system\backend\drug_data"
    ingest_drug_data(TARGET_FOLDER)