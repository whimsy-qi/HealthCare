import os
import time
from dotenv import load_dotenv

# 1. 环境配置：解决网络与 Windows 兼容性
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"

import dashscope
from datasets import load_dataset
from dashvector import Client, Doc

# 2. 加载 API 配置
load_dotenv()
dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

dv_client = Client(
    api_key=os.getenv("DASHVECTOR_API_KEY"),
    endpoint=os.getenv("DASHVECTOR_ENDPOINT")
)
collection = dv_client.get("multimodal_medical_db")


def stream_ingest_2560(dataset_path, data_type, limit=10):
    print(f"📡 正在从镜像站流式读取数据集: {dataset_path}...")
    try:
        dataset = load_dataset(dataset_path, split='train', streaming=True)
    except Exception as e:
        print(f"❌ 连接数据集失败: {e}")
        return

    count = 0
    for entry in dataset:
        if count >= limit: break

        full_text = f"【医学问答】\n问：{entry.get('questions', '')}\n答：{entry.get('answers', '')}"

        # 3. 调用 Qwen3-VL 多模态模型
        # 注意：此处不再传 dimensions 参数，默认输出 2560 维
        resp = dashscope.MultiModalEmbedding.call(
            model="qwen3-vl-embedding",
            input=[{'text': full_text}]
        )

        if resp.status_code == 200:
            try:
                # 提取原生 2560 维向量
                embedding_vector = resp.output['embeddings'][0]['embedding']

                # 4. 构造带唯一 ID 的 Doc 入库
                doc_id = f"{data_type}_{int(time.time() * 1000)}_{count}"
                doc = Doc(id=doc_id, vector=embedding_vector, fields={"source": data_type, "content": full_text})

                ret = collection.insert(doc)
                if ret.code == 0:
                    count += 1
                    print(f"✅ [{count}/{limit}] 入库成功! (维度: {len(embedding_vector)})")
                    time.sleep(0.5)
                else:
                    print(f"❌ DashVector 拒绝写入: {ret.message}")
            except Exception as e:
                print(f"❌ 数据解析异常: {e}")
        else:
            print(f"❌ 向量化请求失败: {resp.message}")


# 修改最后这一行即可
if __name__ == "__main__":
    stream_ingest_2560(
        'FreedomIntelligence/huatuo_knowledge_graph_qa', # 👈 换成知识图谱子集
        'knowledge_graph',
        limit=20
    )