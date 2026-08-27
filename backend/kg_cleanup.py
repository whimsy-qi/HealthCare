"""KG cleanup: remove low-value Disease nodes and mark stub nodes.

This script intentionally reads Neo4j credentials from the environment.
Required environment variables:
  NEO4J_PASSWORD

Optional:
  NEO4J_URI=bolt://localhost:7687
  NEO4J_USER=neo4j
"""
import os

from neo4j import GraphDatabase, Query

try:
    from dotenv import find_dotenv, load_dotenv

    load_dotenv(find_dotenv(usecwd=True))
except Exception:
    pass


NEO4J_URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
NEO4J_USER = os.getenv("NEO4J_USER", "neo4j")
NEO4J_PASSWORD = os.getenv("NEO4J_PASSWORD")
QUERY_TIMEOUT_SEC = float(os.getenv("NEO4J_QUERY_TIMEOUT_SEC", "10"))

if not NEO4J_PASSWORD:
    raise RuntimeError("NEO4J_PASSWORD is required; refusing to use a hard-coded password.")


driver = GraphDatabase.driver(NEO4J_URI, auth=(NEO4J_USER, NEO4J_PASSWORD))

try:
    with driver.session() as s:
        trash = ["-", "无", "未知", "尚不明确", "参考说明书", "同上", "不详"]
        r = s.run(
            Query(
                """
                MATCH (n:Disease)
                WHERE n.name IN $trash
                   OR size(trim(n.name)) <= 1
                   OR n.name CONTAINS '禁用'
                   OR n.name CONTAINS '过敏者'
                   OR n.name CONTAINS '孕妇'
                   OR n.name CONTAINS '哺乳'
                   OR n.name CONTAINS '说明书'
                   OR n.name CONTAINS '参考'
                DETACH DELETE n
                RETURN count(n) AS c
                """,
                timeout=QUERY_TIMEOUT_SEC,
            ),
            trash=trash,
        )
        deleted = r.single()["c"]
        print(f"1. 删除垃圾节点: {deleted}")

        r = s.run(
            Query(
                "MATCH (n:Disease) WHERE n.desc IS NULL SET n:Stub RETURN count(n) AS c",
                timeout=QUERY_TIMEOUT_SEC,
            )
        )
        stub = r.single()["c"]
        print(f"2. 标记 Stub: {stub}")

        r = s.run(
            Query("MATCH (n:Disease) WHERE NOT n:Stub RETURN count(n) AS c", timeout=QUERY_TIMEOUT_SEC)
        )
        full = r.single()["c"]
        r = s.run(Query("MATCH (n) RETURN count(n) AS c", timeout=QUERY_TIMEOUT_SEC))
        total_nodes = r.single()["c"]
        r = s.run(Query("MATCH ()-[r]->() RETURN count(r) AS c", timeout=QUERY_TIMEOUT_SEC))
        total_rels = r.single()["c"]
        print(f"3. 最终: 完整Disease={full}, 总节点={total_nodes}, 总关系={total_rels}")

        print("4. Top 8 热门 (仅完整节点):")
        for row in s.run(
            Query(
                """
                MATCH (d:Disease) WHERE NOT d:Stub
                MATCH (d)-[r]-()
                WITH d, count(r) AS deg
                RETURN d.name AS name, deg ORDER BY deg DESC LIMIT 8
                """,
                timeout=QUERY_TIMEOUT_SEC,
            )
        ):
            print(f"   {row['name']}: deg={row['deg']}")
finally:
    driver.close()

print("Done")
