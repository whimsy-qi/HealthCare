import os
import json
import time
import pandas as pd
from neo4j import GraphDatabase
from dotenv import load_dotenv, find_dotenv
import dashscope

# 加载环境变量 (用于读取大模型 API KEY)
load_dotenv(find_dotenv(usecwd=True))

# ==========================================
# 1. Neo4j 数据库连接配置 (与 Docker 启动时一致)
# ==========================================
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD")
if not PASSWORD:
    raise RuntimeError(
        "NEO4J_PASSWORD 未配置。请在 backend/.env 中设置 NEO4J_PASSWORD。"
    )


# ==========================================
# 2. 核心建图引擎
# ==========================================
class MedicalGraphBuilder:
    def __init__(self):
        try:
            self.driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
            print("🟢 成功连接 Neo4j 数据库！")
        except Exception as e:
            print(f"🔴 连接失败，请检查 Docker 是否运行: {e}")
            exit()

    def close(self):
        self.driver.close()

    def clean_db(self):
        print("🧹 正在清空旧图谱，准备注入全新数据...")
        with self.driver.session() as session:
            session.run("MATCH (n) DETACH DELETE n")

    def build_from_json(self, json_path):
        print(f"\n🧬 [阶段 1] 开始解析疾病 JSON 构建底层网络: {json_path}")
        count = 0
        with self.driver.session() as session:
            with open(json_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip(): continue
                    try:
                        data = json.loads(line)
                        disease_name = data.get("name")
                        if not disease_name: continue

                        # 1. 创建疾病节点 (Disease)
                        session.run("MERGE (d:Disease {name: $name})", name=disease_name)

                        # 2. 创建科室 (Department) 并与疾病连线 [:BELONGS_TO]
                        depts = data.get("cure_department", [])
                        for dept in depts:
                            session.run("""
                                MATCH (d:Disease {name: $d_name})
                                MERGE (dp:Department {name: $dp_name})
                                MERGE (d)-[:BELONGS_TO]->(dp)
                            """, d_name=disease_name, dp_name=dept)

                        # 3. 创建症状 (Symptom) 并与疾病连线 [:HAS_SYMPTOM]
                        symptoms = data.get("symptom", [])
                        for sym in symptoms:
                            session.run("""
                                MATCH (d:Disease {name: $d_name})
                                MERGE (s:Symptom {name: $s_name})
                                MERGE (d)-[:HAS_SYMPTOM]->(s)
                            """, d_name=disease_name, s_name=sym)

                        count += 1
                        if count % 500 == 0:
                            print(f"   ⏳ 已处理 {count} 种疾病...")
                    except Exception as e:
                        print(f"跳过错误行: {e}")
        print(f"✅ 疾病基础图谱构建完成！共录入 {count} 种疾病。")

    def build_from_excel(self, folder_path):
        print(f"\n💊 [阶段 2] 开始解析药物 Excel 并进行跨界连线: {folder_path}")
        files = [f for f in os.listdir(folder_path) if f.endswith('.xlsx')]

        with self.driver.session() as session:
            for file in files:
                file_path = os.path.join(folder_path, file)
                print(f"   📄 正在读取: {file}")

                df = pd.read_excel(file_path, dtype=str)
                count = 0

                for index, row in df.iterrows():
                    drug_name = row.get('通用名称', '')
                    if pd.isna(drug_name) or not str(drug_name).strip(): continue
                    drug_name = str(drug_name).strip()

                    drug_class = str(row.get('药品分类', '')).strip()

                    # 1. 创建药物节点 (Drug)
                    session.run("MERGE (m:Drug {name: $name}) SET m.class = $d_class",
                                name=drug_name, d_class=drug_class)

                    # 2. 建立药物与疾病的治疗关系 [:TREATS]
                    related_diseases = str(row.get('相关疾病', ''))
                    if related_diseases and related_diseases.lower() != 'nan':
                        # 兼容中文顿号和英文逗号
                        related_diseases = related_diseases.replace('、', ',')
                        disease_list = [d.strip() for d in related_diseases.split(',') if d.strip()]
                        for d_name in disease_list:
                            session.run("""
                                MATCH (m:Drug {name: $drug_name})
                                MERGE (d:Disease {name: $d_name})
                                MERGE (m)-[:TREATS]->(d)
                            """, drug_name=drug_name, d_name=d_name)

                    # ==========================================
                    # 🌟 3. 新增：建立药物与禁忌疾病的关联 [:CONTRAINDICATED_FOR]
                    # ==========================================
                    contra_diseases = str(row.get('禁忌', ''))
                    if contra_diseases and contra_diseases.lower() != 'nan':
                        # 兼容中文顿号和英文逗号，防止 Excel 填写不规范
                        contra_diseases = contra_diseases.replace('、', ',')
                        c_list = [c.strip() for c in contra_diseases.split(',') if c.strip()]
                        for c_name in c_list:
                            session.run("""
                                MATCH (m:Drug {name: $drug_name})
                                MERGE (c:Disease {name: $c_name})
                                MERGE (m)-[:CONTRAINDICATED_FOR]->(c)
                            """, drug_name=drug_name, c_name=c_name)

                    count += 1
                print(f"   ✅ 表格 {file} 处理完毕，录入药物 {count} 种。")

    # ==========================================
    # 🌟 核心新增 1：为所有四大核心实体创建高维向量索引
    # ==========================================
    def build_vector_indices(self):
        print("\n🛠️ [阶段 3] 正在创建 Neo4j 向量索引...")
        labels_to_index = ["Disease", "Symptom", "Department", "Drug"]

        try:
            for label in labels_to_index:
                # 针对每一种实体类型单独创建索引
                self.driver.execute_query(f"""
                CREATE VECTOR INDEX {label.lower()}_embedding IF NOT EXISTS
                FOR (n:{label}) ON (n.embedding)
                OPTIONS {{indexConfig: {{
                 `vector.dimensions`: 1024,
                 `vector.similarity_function`: 'cosine'
                }}}}
                """)
            print("  ✅ 成功确认/创建[疾病、症状、科室、药物]这 4 大类节点的向量索引！")
        except Exception as e:
            print(f"  ⚠️ 创建向量索引异常 (若已存在或版本限制可忽略): {e}")

    # ==========================================
    # 🌟 核心新增 2：全量图谱节点向量化 (完美断点续传)
    # ==========================================
    def embed_all_nodes(self):
        print("\n🚀 [阶段 4] 开始为图谱四大核心节点生成语义向量 (Embedding)...")
        dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")

        with self.driver.session() as session:
            # 🌟 涵盖了这 4 类节点，并且只查 embedding 是空的
            result = session.run("""
                MATCH (n) 
                WHERE (n:Symptom OR n:Disease OR n:Drug OR n:Department) AND n.embedding IS NULL 
                RETURN elementId(n) AS id, n.name AS name
            """)
            nodes = [{"id": record["id"], "name": record["name"]} for record in result]

            if not nodes:
                print("  ✨ 完美！图谱中所有核心节点均已拥有向量灵魂，无需重复生成！")
                return

            print(f"  🔍 发现 {len(nodes)} 个新节点需要向量化，开始批量调用大模型...")

            batch_size = 10  # 每批处理 20 个，防止超过 API 限制
            for i in range(0, len(nodes), batch_size):
                batch = nodes[i:i + batch_size]
                texts = [str(n["name"]).strip() for n in batch if n["name"]]

                if not texts:
                    continue

                try:
                    # 调用阿里云降维模型 text-embedding-v3 (输出1024维向量)
                    resp = dashscope.TextEmbedding.call(
                        model=dashscope.TextEmbedding.Models.text_embedding_v3,
                        input=texts
                    )

                    if resp.status_code == 200:
                        embeddings = [emb['embedding'] for emb in resp.output['embeddings']]

                        # 将生成的向量数组，批量写回 Neo4j 对应节点
                        for j, node in enumerate(batch):
                            session.run("""
                            MATCH (n) WHERE elementId(n) = $id
                            SET n.embedding = $embedding
                            """, id=node["id"], embedding=embeddings[j])

                        progress = min(i + batch_size, len(nodes))
                        print(f"    ✔️ 进度: {progress}/{len(nodes)} 节点已完成语义向量化。")
                    else:
                        print(f"    ❌ 批量向量化失败: {resp.message}")

                except Exception as e:
                    print(f"    ⚠️ 网络或 API 异常: {e}。不要慌，按 Ctrl+C 停止后，下次运行会自动重试！")

                # 休眠 0.5 秒防止 QPS 限流触发风控
                time.sleep(0.5)

            print("\n🎉 恭喜！图谱全量节点语义向量化圆满完成！")


# ==========================================
# 3. 智能启动执行
# ==========================================
if __name__ == "__main__":
    # ⚠️ 请确保这里的路径与你的实际数据路径完全一致！
    JSON_FILE_PATH = r"D:\Health_system\backend\scripts\medical.json"
    DRUG_FOLDER_PATH = r"D:\Health_system\backend\drug_data"

    builder = MedicalGraphBuilder()

    # 🌟 智能流控：检查当前数据库是否已经有节点
    with builder.driver.session() as session:
        node_count = session.run("MATCH (n) RETURN count(n) AS count").single()["count"]

    if node_count == 0:
        print("📦 检测到空数据库，开始从头构建基础图谱...")
        builder.clean_db()
        builder.build_from_json(JSON_FILE_PATH)
        builder.build_from_excel(DRUG_FOLDER_PATH)
    else:
        print(f"📦 检测到数据库已有 {node_count} 个节点！")
        print("⏭️ 已自动跳过清库和基础建图步骤，直接进入【断点续传】向量化阶段！")

    # 无论是否新建图谱，都执行向量索引和 embedding 生成
    builder.build_vector_indices()
    builder.embed_all_nodes()

    builder.close()
    print("\n✨ 整个系统底座构建完毕！你的四大核心图谱节点现在全部拥有了向量寻址能力！")