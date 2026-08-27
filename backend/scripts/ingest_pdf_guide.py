import os
import re
import time
import json
import hashlib
import sqlite3
import random
import threading
import fitz  # PyMuPDF
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
from openai import OpenAI
import dashscope
from dashvector import Client, Doc
from langchain_text_splitters import RecursiveCharacterTextSplitter

# ==========================================
# 🌟 1. 全局配置与环境初始化
# ==========================================
load_dotenv()
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

dv_client = Client(
    api_key=os.getenv("DASHVECTOR_API_KEY"),
    endpoint=os.getenv("DASHVECTOR_ENDPOINT")
)
COLLECTION_NAME = "multimodal_medical_db"
collection = dv_client.get(COLLECTION_NAME)

# 大模型客户端（用于提取元数据）
llm_client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_API_BASE")
)

# 线程锁，防止 SQLite 并发写入冲突
db_lock = threading.Lock()
DB_FILE = "ingest_cache.db"


# ==========================================
# 🌟 2. 增量更新与幂等性保障 (SQLite)
# ==========================================
def init_db():
    """初始化本地缓存数据库，记录已成功处理的文件 Hash"""
    with sqlite3.connect(DB_FILE) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS processed_files (
                file_hash TEXT PRIMARY KEY,
                file_path TEXT,
                processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')


def get_file_hash(filepath):
    """计算文件的 MD5 哈希值，用于感知文件是否被修改"""
    hasher = hashlib.md5()
    with open(filepath, 'rb') as f:
        buf = f.read()
        hasher.update(buf)
    return hasher.hexdigest()


def is_file_processed(file_hash):
    with sqlite3.connect(DB_FILE) as conn:
        cursor = conn.execute('SELECT 1 FROM processed_files WHERE file_hash = ?', (file_hash,))
        return cursor.fetchone() is not None


def mark_file_processed(file_hash, filepath):
    with db_lock:
        with sqlite3.connect(DB_FILE) as conn:
            conn.execute('INSERT OR REPLACE INTO processed_files (file_hash, file_path) VALUES (?, ?)',
                         (file_hash, filepath))


# ==========================================
# 🌟 3. 智能清洗与文档截断 (核心垃圾过滤)
# ==========================================
def extract_clean_text(pdf_path):
    """
    带“智能截断”的 PDF 解析：
    一旦检测到“参考文献”、“致谢”等无用章节，立即停止读取后续页面！
    """
    try:
        doc = fitz.open(pdf_path)
        full_text = ""

        # 常见指南末尾垃圾信息的触发词
        stop_keywords = [r"^参\s*考\s*文\s*献", r"^主\s*要\s*参\s*考\s*文\s*献", r"^致\s*谢", r"^编\s*委\s*会"]

        for page in doc:
            blocks = page.get_text("blocks")
            text_blocks = [b[4] for b in blocks if b[6] == 0]

            for text in text_blocks:
                text_clean = text.replace('\n', '').strip()
                text_clean = re.sub(r' +', ' ', text_clean)  # 修复字母间距乱码

                # 🛡️ 触发智能截断：如果读到了参考文献章节，直接丢弃后面的所有内容！
                if any(re.search(kw, text_clean) for kw in stop_keywords):
                    doc.close()
                    return re.sub(r'\n{3,}', '\n\n', full_text)  # 立即返回已读取的纯净正文

                if text_clean:
                    full_text += text_clean + "\n\n"

        doc.close()
        return re.sub(r'\n{3,}', '\n\n', full_text)
    except Exception as e:
        print(f"❌ 读取 PDF 失败: {pdf_path}, Error: {e}")
        return ""


# ==========================================
# 🌟 4. LLM 自动元数据提取 (MetaData ETL)
# ==========================================
def extract_metadata_with_llm(text_head, file_name):
    """让大模型阅读 PDF 的前 1000 个字，提取年份和权威度"""
    prompt = f"""
    你是一个医学文献管理员。请阅读以下医学指南的文件名和开头部分，提取出【年份】和【来源权威度】。
    文件名：{file_name}
    文本开头：{text_head[:1000]}

    规则：
    1. 年份：如 "2023"、"2024"，如果找不到则填 "未知"。
    2. 来源权威度：只能从以下三个词中选择一个："国家级指南"、"专家共识"、"基础科普"。

    请只输出 JSON 格式（不要任何其他废话）：
    {{"year": "xxxx", "authority": "xxxx"}}
    """
    try:
        resp = llm_client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.0
        )
        return json.loads(resp.choices[0].message.content)
    except Exception as e:
        return {"year": "未知", "authority": "专家共识"}  # 失败兜底


# ==========================================
# 🌟 5. 带重试的 Embedding 与分片
# ==========================================
def get_embedding_with_retry(text: str, max_retries: int = 5):
    for retry in range(max_retries):
        try:
            resp = dashscope.MultiModalEmbedding.call(model="qwen3-vl-embedding", input=[{'text': text}])
            if resp.status_code == 200:
                return resp.output['embeddings'][0]['embedding']

            wait_time = (2 ** retry) + random.random()
            time.sleep(wait_time)
        except Exception:
            time.sleep((2 ** retry) + random.random())
    return None


def semantic_splitter(text: str):
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=600, chunk_overlap=120,
        separators=["\n\n", "\n", "。", "！", "？", ".", ";", " ", ""]
    )
    return splitter.split_text(text)


# ==========================================
# 🌟 6. 核心处理管线 (单个文件事务)
# ==========================================
def process_single_pdf(pdf_path, dept_name, disease_name):
    try:
        file_hash = get_file_hash(pdf_path)

        # 1. 幂等性校验
        if is_file_processed(file_hash):
            print(f"⏩ 跳过 (已入库): {disease_name}")
            return True

        print(f"📄 开始解析: [{dept_name}] {disease_name}")

        # 2. 智能提取（自动切除参考文献）
        raw_text = extract_clean_text(pdf_path)
        if not raw_text.strip(): return False

        # 3. LLM 抽取元数据 (只看开头前1000字)
        meta = extract_metadata_with_llm(raw_text[:1000], disease_name)

        # 4. 语义切片
        chunks = semantic_splitter(raw_text)

        # 5. 向量化与组装 (在内存中完成，不立即写入 DB)
        docs_to_insert = []
        for i, chunk in enumerate(chunks):
            # 将 LLM 提取的权威标签嵌入到向量上下文中，供智能体检索
            content_to_embed = f"【{meta['authority']}：{disease_name} ({meta['year']}版)】\n内容：{chunk}"

            vec = get_embedding_with_retry(content_to_embed)
            if vec:
                docs_to_insert.append(Doc(
                    id=f"{file_hash}_{i}",  # 使用 Hash + 序号作为全球唯一 ID
                    vector=vec,
                    fields={
                        "source": "expert_guide",
                        "content": content_to_embed,
                        "dept": dept_name,
                        "disease": disease_name,
                        "year": meta.get("year", "未知"),
                        "authority": meta.get("authority", "未知")
                    }
                ))

        # 6. 批量事务插入 (Batch Insert)
        # 只有 DashVector 成功接收，才会写入 SQLite，保证事务一致性
        if docs_to_insert:
            # 每 100 条一批次插入
            for i in range(0, len(docs_to_insert), 100):
                batch = docs_to_insert[i:i + 100]
                collection.insert(batch)

            # 标记文件处理成功
            mark_file_processed(file_hash, pdf_path)
            print(
                f"✅ 入库成功: {disease_name} | 年份: {meta['year']} | 权威度: {meta['authority']} | 纯净块数: {len(docs_to_insert)}")
            return True
        return False

    except Exception as e:
        print(f"❌ 处理异常 [{disease_name}]: {e}")
        return False


# ==========================================
# 🌟 7. 并发调度中心
# ==========================================
def start_enterprise_scan(base_folder, max_workers=5):
    init_db()

    tasks = []
    # 扫描收集所有任务
    for root, dirs, files in os.walk(base_folder):
        dept_name = os.path.basename(root)
        if dept_name == "data_PDF": dept_name = "综合指南"

        pdf_files = [f for f in files if f.endswith('.pdf')]
        for file in pdf_files:
            disease_name = os.path.splitext(file)[0]
            tasks.append((os.path.join(root, file), dept_name, disease_name))

    print(f"🚀 扫描到 {len(tasks)} 个 PDF 文件，启动 {max_workers} 个并发线程池...\n")

    # 启动线程池并发处理
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_single_pdf, path, dept, name): name for path, dept, name in tasks}

        for future in as_completed(futures):
            # 这里可以用来捕获线程崩溃异常
            pass


if __name__ == "__main__":
    TARGET_PATH = r"D:\Health_system\backend\data_PDF"
    print("=" * 60)
    print("🏥 启动企业级医疗知识 ETL 流水线")
    print("=" * 60)
    start_enterprise_scan(TARGET_PATH, max_workers=3)  # 为了保护 API 限流，并发设为 3