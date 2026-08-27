"""
build_neo4j_graph_v2.py — 完整化 KG 构建（参考 RAGQnASystem schema）
====================================================================
解决 v1 严重欠抽取问题：
- v1：4 类节点 / 4 种关系 / ~25k 节点 / ~50k 关系（仅用 medical.json 部分字段）
- v2：8 类节点 / 11 种关系 / ~45k 节点 / ~30 万关系（同一份数据完整解析）

新增节点：Food / Check / Producer / Cure
新增关系：DO_EAT / NOT_EAT / NEED_CHECK / CURE_WAY / COMMON_DRUG /
         RECOMMAND_DRUG / RECOMMAND_EAT / ACOMPANY_WITH / PRODUCED_BY
新增疾病属性：desc / cause / prevent / cure_lasttime / cured_prob /
             easy_get / get_prob / get_way / yibao_status / cost_money

用法：
  python build_neo4j_graph_v2.py --dry-run    # 仅解析统计，不写入 Neo4j
  python build_neo4j_graph_v2.py --rebuild    # 清库 + 全量重建
  python build_neo4j_graph_v2.py              # 增量（仅缺失节点）—— 不推荐，schema 改了

旧的 build_neo4j_graph.py 保留以备回退。
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import argparse
import logging
from collections import defaultdict, Counter
from typing import Dict, List, Tuple, Optional

# Windows 终端默认 GBK，强制 UTF-8 防 emoji / 中文乱码
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

import pandas as pd
from neo4j import GraphDatabase
from dotenv import load_dotenv, find_dotenv
import dashscope

_ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
load_dotenv(_ENV_PATH if os.path.exists(_ENV_PATH) else find_dotenv(usecwd=True))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("BuildKG.v2")

# ==========================================
# 全局配置
# ==========================================
URI = os.getenv("NEO4J_URI", "bolt://localhost:7687")
USER = os.getenv("NEO4J_USER", "neo4j")
PASSWORD = os.getenv("NEO4J_PASSWORD")

JSON_FILE_PATH = os.getenv("KG_JSON_PATH", r"D:\Health_system\backend\scripts\medical.json")
DRUG_FOLDER_PATH = os.getenv("KG_DRUG_FOLDER", r"D:\Health_system\backend\drug_data")
KG_SOURCE_NAME = os.getenv("KG_SOURCE_NAME", "local_diseasekg_json")
KG_DRUG_SOURCE_NAME = os.getenv("KG_DRUG_SOURCE_NAME", "local_drug_excel_kg_edges")
KG_SOURCE_TIER = os.getenv("KG_SOURCE_TIER", "T3")
KG_LICENSE = os.getenv("KG_LICENSE", "local_review_required")

# drug_detail 字段格式："<厂家名>+<商品名>(<通用名>)"
# 例："惠普森穿心莲内酯片(穿心莲内酯片)"  → 厂家=惠普森, 商品=穿心莲内酯片, 通用=穿心莲内酯片
#     "西藏甘露仁青芒觉(仁青芒觉)"        → 厂家=西藏甘露, 商品=仁青芒觉, 通用=仁青芒觉
# 厂家名通常 2-6 字，商品名贴在后面无分隔。这里采用启发式：通用名 == 商品名时
# 视为厂家位于通用名之前的前缀；不等时按"商品(通用)"格式解析。
DRUG_DETAIL_RE = re.compile(r"^(.+?)\(([^()]+)\)$")
DRUG_DISEASE_NAME_MAX_LEN = 32
DRUG_DISEASE_SPLIT_RE = re.compile(r"[、,，;；|/\r\n]+")
DRUG_DISEASE_BAD_PATTERNS = (
    "本品",
    "患者",
    "禁用",
    "禁用于",
    "慎用",
    "不推荐",
    "不应",
    "不得",
    "过敏",
    "使用",
    "服用",
    "应用",
    "治疗",
    "诊断",
    "排除",
    "病史",
    "临床试验",
    "资料",
    "尚无",
    "未明确",
    "禁忌",
    "避免",
)


# ==========================================
# 解析层（不依赖 Neo4j，便于 dry-run）
# ==========================================
class KGParser:
    """从 medical.json + drug_data Excel 解析出节点和关系，不写库。"""

    def __init__(self):
        # 节点：用 set 去重
        self.diseases: Dict[str, dict] = {}        # name → 属性 dict
        self.drugs: Dict[str, dict] = {}           # name → {class}
        self.symptoms: set = set()
        self.foods: set = set()
        self.checks: set = set()
        self.cures: set = set()
        self.departments: Dict[str, dict] = {}     # name → {level: 1/2}
        self.producers: set = set()

        # 关系：list of tuples
        self.rels: Dict[str, List[Tuple]] = defaultdict(list)

        # 统计
        self.stats = Counter()
        self.skipped_drug_terms = Counter()

    # ----------------- 工具 -----------------
    @staticmethod
    def _norm(s) -> str:
        if s is None:
            return ""
        if isinstance(s, list):
            return ""
        return str(s).strip()

    @staticmethod
    def _list(v) -> List[str]:
        if not v:
            return []
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        return []

    def _parse_drug_detail(self, raw: str) -> Tuple[Optional[str], str]:
        """
        从 "厂家+商品名(通用名)" 解析出 (producer, generic_name)。
        如果格式不对，返回 (None, raw)。
        """
        raw = (raw or "").strip()
        m = DRUG_DETAIL_RE.match(raw)
        if not m:
            return None, raw
        head, generic = m.group(1).strip(), m.group(2).strip()
        # head 形如 "惠普森穿心莲内酯片"，去掉尾部的 generic 部分得到厂家
        if head.endswith(generic):
            producer = head[: -len(generic)].strip()
        else:
            # 通用名包含成分异化（"百咳静糖浆" vs "邦琪药业百咳静糖浆"），尝试反向截取
            # 找通用名最后一次出现位置
            idx = head.rfind(generic[:2]) if len(generic) >= 2 else -1
            producer = head[:idx].strip() if idx > 0 else head
        if not producer or len(producer) > 20:
            producer = None
        return producer, generic

    def _split_drug_disease_terms(self, raw: str) -> List[str]:
        text = self._norm(raw)
        if not text or text.lower() == "nan":
            return []
        return [item for item in (self._norm(x) for x in DRUG_DISEASE_SPLIT_RE.split(text)) if item]

    def _is_valid_drug_disease_term(self, term: str) -> bool:
        name = self._norm(term)
        if not name or name.lower() == "nan":
            return False
        if name in self.diseases:
            return True
        if len(name) > DRUG_DISEASE_NAME_MAX_LEN:
            return False
        if re.search(r"[\s。！？!?：:；;|]", name):
            return False
        if re.search(r"(^[0-9]+[.、)]|[0-9]+[.、)])", name):
            return False
        if any(pattern in name for pattern in DRUG_DISEASE_BAD_PATTERNS):
            return False
        if re.search(r"[A-Za-z0-9]", name):
            return False
        return True

    # ----------------- 主解析 -----------------
    def parse_medical_json(self, path: str):
        logger.info(f"📖 [解析] 开始扫描 {path}")
        line_no = 0
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line_no += 1
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                    self._parse_one_disease(d)
                    self.stats["lines_parsed"] += 1
                except Exception as e:
                    logger.warning(f"  ⚠️ 第 {line_no} 行解析失败：{e}")
                    self.stats["lines_failed"] += 1
        logger.info(f"📖 [解析] medical.json 扫完，共 {line_no} 行")

    def _parse_one_disease(self, d: dict):
        name = self._norm(d.get("name"))
        if not name:
            return

        # 1) Disease 节点（带 11 个属性，超出 v1 的 1 个）
        self.diseases[name] = {
            "desc":          self._norm(d.get("desc"))[:1500],     # 主描述截 1500 字防 prop 超长
            "cause":         self._norm(d.get("cause"))[:1500],
            "prevent":       self._norm(d.get("prevent"))[:1500],
            "cure_lasttime": self._norm(d.get("cure_lasttime"))[:80],
            "cured_prob":    self._norm(d.get("cured_prob"))[:40],
            "easy_get":      self._norm(d.get("easy_get"))[:200],
            "get_prob":      self._norm(d.get("get_prob"))[:40],
            "get_way":       self._norm(d.get("get_way"))[:80],
            "yibao_status":  self._norm(d.get("yibao_status"))[:10],
            "cost_money":    self._norm(d.get("cost_money"))[:200],
        }

        # 2) Department：category 字段携带二级科室层级；cure_department 是同样信息（可能更详）
        category = self._list(d.get("category"))
        # category 形如 ["疾病百科", "内科", "呼吸内科"]：第 0 项忽略；第 1 项 lvl1；第 2 项 lvl2
        lvl1, lvl2 = None, None
        if len(category) >= 2:
            lvl1 = category[1]
            self.departments.setdefault(lvl1, {"level": 1})
        if len(category) >= 3:
            lvl2 = category[2]
            self.departments.setdefault(lvl2, {"level": 2})
            if lvl1:
                self.rels["DEPT_PARENT"].append((lvl2, lvl1))  # 二级 → 一级

        # cure_department 字段（与 category 重合，但有时更全），全部建 BELONGS_TO
        for dept in self._list(d.get("cure_department")):
            self.departments.setdefault(dept, {"level": 0})
            self.rels["BELONGS_TO"].append((name, dept))

        # 3) Symptom
        for sym in self._list(d.get("symptom")):
            self.symptoms.add(sym)
            self.rels["HAS_SYMPTOM"].append((name, sym))

        # 4) ACOMPANY_WITH（疾病 → 疾病并发症）
        for acc in self._list(d.get("acompany")):
            # 并发症不预先创建 Disease 节点（避免误创建二级实体）；写入时若没有该疾病则用 MERGE 创建空节点
            self.rels["ACOMPANY_WITH"].append((name, acc))

        # 5) Cure（治疗方式）
        for cure in self._list(d.get("cure_way")):
            self.cures.add(cure)
            self.rels["CURE_WAY"].append((name, cure))

        # 6) Check（检查项目）
        for chk in self._list(d.get("check")):
            self.checks.add(chk)
            self.rels["NEED_CHECK"].append((name, chk))

        # 7) Food：do_eat / not_eat / recommand_eat 三种关系
        for food in self._list(d.get("do_eat")):
            self.foods.add(food)
            self.rels["DO_EAT"].append((name, food))
        for food in self._list(d.get("not_eat")):
            self.foods.add(food)
            self.rels["NOT_EAT"].append((name, food))
        for recipe in self._list(d.get("recommand_eat")):
            self.foods.add(recipe)   # 菜谱也归到 Food（对标 RAGQnASystem schema）
            self.rels["RECOMMAND_EAT"].append((name, recipe))

        # 8) Drug：common_drug / recommand_drug
        for drug in self._list(d.get("common_drug")):
            self.drugs.setdefault(drug, {"class": ""})
            self.rels["COMMON_DRUG"].append((name, drug))
        for drug in self._list(d.get("recommand_drug")):
            self.drugs.setdefault(drug, {"class": ""})
            self.rels["RECOMMAND_DRUG"].append((name, drug))

        # 9) Producer + drug_detail：解析 "厂家+商品(通用)" 格式
        for raw in self._list(d.get("drug_detail")):
            producer, generic = self._parse_drug_detail(raw)
            if generic:
                self.drugs.setdefault(generic, {"class": ""})
            if producer and generic:
                self.producers.add(producer)
                self.rels["PRODUCED_BY"].append((generic, producer))

    # ----------------- drug_data Excel（保留 v1 逻辑） -----------------
    def parse_drug_excel(self, folder_path: str):
        if not os.path.isdir(folder_path):
            logger.warning(f"  ⚠️ drug_data 文件夹不存在：{folder_path}，跳过")
            return
        logger.info(f"💊 [解析] 开始扫描 {folder_path}")
        files = [f for f in os.listdir(folder_path) if f.endswith(".xlsx")]
        for file in files:
            file_path = os.path.join(folder_path, file)
            logger.info(f"  📄 读取：{file}")
            try:
                df = pd.read_excel(file_path, dtype=str)
            except Exception as e:
                logger.error(f"    ❌ Excel 读取失败：{e}")
                continue

            for _, row in df.iterrows():
                drug_name = self._norm(row.get("通用名称"))
                if not drug_name:
                    continue
                drug_class = self._norm(row.get("药品分类"))
                self.drugs.setdefault(drug_name, {"class": drug_class})
                if drug_class:
                    self.drugs[drug_name]["class"] = drug_class

                # TREATS：Excel 字段可能混入说明句，仅保留疾病实体名
                for d_name in self._split_drug_disease_terms(row.get("相关疾病")):
                    if self._is_valid_drug_disease_term(d_name):
                        self.rels["TREATS"].append((drug_name, d_name))
                    else:
                        self.skipped_drug_terms["TREATS"] += 1

                # CONTRAINDICATED_FOR：禁忌经常是说明书段落，不能整段建成 Disease 节点
                for c_name in self._split_drug_disease_terms(row.get("禁忌")):
                    if self._is_valid_drug_disease_term(c_name):
                        self.rels["CONTRAINDICATED_FOR"].append((drug_name, c_name))
                    else:
                        self.skipped_drug_terms["CONTRAINDICATED_FOR"] += 1

                # 厂家（drug_data Excel 通常有"生产厂家"列）
                producer = self._norm(row.get("生产厂家")) or self._norm(row.get("厂家"))
                if producer and producer.lower() != "nan":
                    self.producers.add(producer)
                    self.rels["PRODUCED_BY"].append((drug_name, producer))

    # ----------------- 报表 -----------------
    def report(self):
        # 边去重（同一对 head/tail/rel 多次出现统一为一条）
        # 这才是 Neo4j MERGE 后的真实关系数
        deduped: Dict[str, set] = {}
        for rel, edges in self.rels.items():
            deduped[rel] = set((h, t) for h, t in edges)

        n_nodes = (
            len(self.diseases) + len(self.drugs) + len(self.symptoms)
            + len(self.foods) + len(self.checks) + len(self.cures)
            + len(self.departments) + len(self.producers)
        )
        n_rels_raw = sum(len(v) for v in self.rels.values())
        n_rels = sum(len(v) for v in deduped.values())
        # 把去重后的边写回（写库时直接用去重版即可，避免无用 MERGE 调用）
        self.rels = {k: list(v) for k, v in deduped.items()}
        print("=" * 60)
        print("📊 KG 解析统计（v2）")
        print("=" * 60)
        print(f"\n【节点：{n_nodes}】")
        print(f"  Disease     : {len(self.diseases):>6}")
        print(f"  Drug        : {len(self.drugs):>6}")
        print(f"  Symptom     : {len(self.symptoms):>6}")
        print(f"  Food        : {len(self.foods):>6}")
        print(f"  Check       : {len(self.checks):>6}")
        print(f"  Cure        : {len(self.cures):>6}")
        print(f"  Department  : {len(self.departments):>6}")
        print(f"  Producer    : {len(self.producers):>6}")
        print(f"\n【关系：{n_rels}（原始 {n_rels_raw}，去重折算 {(1 - n_rels/max(1,n_rels_raw))*100:.1f}%）】")
        for rel, items in sorted(self.rels.items(), key=lambda x: -len(x[1])):
            print(f"  {rel:<22}: {len(items):>6}")
        if self.skipped_drug_terms:
            print("\n【已过滤药品 Excel 非实体片段】")
            for rel, count in self.skipped_drug_terms.items():
                print(f"  {rel:<22}: {count:>6}")
        print("\n【对比 RAGQnASystem 基准】")
        print(f"  RAGQnASystem    : 44,000 节点 / 310,000 关系")
        print(f"  本系统 v1（旧） : ~25,000 节点 / ~50,000 关系")
        print(f"  本系统 v2（新） : {n_nodes:,} 节点 / {n_rels:,} 关系")
        print("=" * 60)


# ==========================================
# 写入层（依赖 Neo4j）
# ==========================================
class KGWriter:
    """把 KGParser 的产物批量写入 Neo4j。"""

    DISEASEKG_LABELS = [
        "Disease",
        "Drug",
        "Symptom",
        "Department",
        "Food",
        "Check",
        "Cure",
        "Producer",
    ]

    DISEASEKG_RELS = [
        "BELONGS_TO",
        "DEPT_PARENT",
        "HAS_SYMPTOM",
        "ACOMPANY_WITH",
        "CURE_WAY",
        "NEED_CHECK",
        "DO_EAT",
        "NOT_EAT",
        "RECOMMAND_EAT",
        "COMMON_DRUG",
        "RECOMMAND_DRUG",
        "TREATS",
        "CONTRAINDICATED_FOR",
        "PRODUCED_BY",
        "RELATED_TO",
        "RELATIONSHIP",
    ]

    def __init__(self):
        if not PASSWORD:
            raise RuntimeError("NEO4J_PASSWORD 未配置（.env）")
        self.driver = GraphDatabase.driver(URI, auth=(USER, PASSWORD))
        self.updated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        logger.info("🟢 已连接 Neo4j")

    def close(self):
        self.driver.close()

    def clean_db(self):
        logger.info("🧹 清空旧图谱…")
        with self.driver.session() as s:
            s.run("MATCH (n) DETACH DELETE n")

    def clean_diseasekg_scope(self):
        """Only remove the DiseaseKG entity subgraph; keep Document/Section graph intact."""
        logger.info("🧹 清理 DiseaseKG 子图（保留 Document/Section 文档图）…")
        for label in self.DISEASEKG_LABELS:
            total_deleted = 0
            while True:
                with self.driver.session() as s:
                    result = s.run(
                        f"""
                        MATCH (n:`{label}`)
                        WITH n LIMIT $batch
                        DETACH DELETE n
                        RETURN count(n) AS deleted
                        """,
                        batch=500,
                    ).single()
                deleted = int(result["deleted"]) if result else 0
                total_deleted += deleted
                if deleted == 0:
                    break
            if total_deleted:
                logger.info(f"  删除 {label}: {total_deleted}")
        logger.info("✅ DiseaseKG 子图清理完成")

    # ----------------- 批写节点 -----------------
    def write_diseases(self, items: Dict[str, dict]):
        logger.info(f"⏳ 写入 Disease：{len(items)}")
        rows = [{"name": k, **v} for k, v in items.items()]
        cypher = """
        UNWIND $rows AS row
        MERGE (d:Disease {name: row.name})
        SET d.desc = row.desc, d.cause = row.cause, d.prevent = row.prevent,
            d.cure_lasttime = row.cure_lasttime, d.cured_prob = row.cured_prob,
            d.easy_get = row.easy_get, d.get_prob = row.get_prob,
            d.get_way = row.get_way, d.yibao_status = row.yibao_status,
            d.cost_money = row.cost_money,
            d.source_name = $source_name, d.source_tier = $source_tier,
            d.license = $license, d.updated_at = $updated_at
        """
        self._batch_run(cypher, rows, batch=500, source_name=KG_SOURCE_NAME)

    def write_drugs(self, items: Dict[str, dict]):
        logger.info(f"⏳ 写入 Drug：{len(items)}")
        rows = [{"name": k, "class": v.get("class", "")} for k, v in items.items()]
        cypher = """
        UNWIND $rows AS row
        MERGE (m:Drug {name: row.name})
        SET m.class = coalesce(row.class, m.class),
            m.source_name = $source_name, m.source_tier = $source_tier,
            m.license = $license, m.updated_at = $updated_at
        """
        self._batch_run(cypher, rows, batch=1000, source_name=KG_DRUG_SOURCE_NAME)

    def write_simple(self, label: str, names: set):
        logger.info(f"⏳ 写入 {label}：{len(names)}")
        rows = [{"name": n} for n in names]
        cypher = f"""
        UNWIND $rows AS row
        MERGE (n:{label} {{name: row.name}})
        SET n.source_name = $source_name, n.source_tier = $source_tier,
            n.license = $license, n.updated_at = $updated_at
        """
        self._batch_run(cypher, rows, batch=2000, source_name=KG_SOURCE_NAME)

    def write_departments(self, items: Dict[str, dict]):
        logger.info(f"⏳ 写入 Department：{len(items)}")
        rows = [{"name": k, "level": v.get("level", 0)} for k, v in items.items()]
        cypher = """
        UNWIND $rows AS row
        MERGE (n:Department {name: row.name})
        SET n.level = row.level,
            n.source_name = $source_name, n.source_tier = $source_tier,
            n.license = $license, n.updated_at = $updated_at
        """
        self._batch_run(cypher, rows, batch=500, source_name=KG_SOURCE_NAME)

    # ----------------- 批写关系 -----------------
    REL_SPECS = {
        # rel_name: (head_label, tail_label)
        "BELONGS_TO":         ("Disease", "Department"),
        "DEPT_PARENT":        ("Department", "Department"),
        "HAS_SYMPTOM":        ("Disease", "Symptom"),
        "ACOMPANY_WITH":      ("Disease", "Disease"),
        "CURE_WAY":           ("Disease", "Cure"),
        "NEED_CHECK":         ("Disease", "Check"),
        "DO_EAT":             ("Disease", "Food"),
        "NOT_EAT":            ("Disease", "Food"),
        "RECOMMAND_EAT":      ("Disease", "Food"),
        "COMMON_DRUG":        ("Disease", "Drug"),
        "RECOMMAND_DRUG":     ("Disease", "Drug"),
        "TREATS":             ("Drug", "Disease"),
        "CONTRAINDICATED_FOR": ("Drug", "Disease"),
        "PRODUCED_BY":        ("Drug", "Producer"),
    }

    def write_rels(self, rels: Dict[str, List[Tuple]]):
        for rel, edges in rels.items():
            spec = self.REL_SPECS.get(rel)
            if not spec:
                logger.warning(f"  ⚠️ 未知关系类型：{rel}（跳过）")
                continue
            head_label, tail_label = spec
            logger.info(f"⏳ 写入关系 {rel} ({head_label}→{tail_label})：{len(edges)}")
            rows = [{"h": h, "t": t} for h, t in edges]
            cypher = f"""
            UNWIND $rows AS row
            MERGE (h:{head_label} {{name: row.h}})
            MERGE (t:{tail_label} {{name: row.t}})
            MERGE (h)-[r:{rel}]->(t)
            SET r.source_name = $source_name, r.source_tier = $source_tier,
                r.license = $license, r.updated_at = $updated_at
            """
            source_name = KG_DRUG_SOURCE_NAME if rel in {"TREATS", "CONTRAINDICATED_FOR", "PRODUCED_BY"} else KG_SOURCE_NAME
            self._batch_run(cypher, rows, batch=2000, source_name=source_name)

    def _batch_run(self, cypher: str, rows: list, batch: int = 1000, *, source_name: str = KG_SOURCE_NAME):
        if not rows:
            return
        with self.driver.session() as s:
            for i in range(0, len(rows), batch):
                s.run(
                    cypher,
                    rows=rows[i:i + batch],
                    source_name=source_name,
                    source_tier=KG_SOURCE_TIER,
                    license=KG_LICENSE,
                    updated_at=self.updated_at,
                )
                if (i // batch) % 5 == 0 and i > 0:
                    logger.debug(f"    进度：{i}/{len(rows)}")

    # ----------------- 向量索引（与 v1 兼容） -----------------
    def build_vector_indices(self):
        logger.info("🛠️ 创建向量索引…")
        labels_to_index = ["Disease", "Symptom", "Department", "Drug",
                           "Food", "Check", "Cure", "Producer"]
        for label in labels_to_index:
            try:
                self.driver.execute_query(f"""
                CREATE VECTOR INDEX {label.lower()}_embedding IF NOT EXISTS
                FOR (n:{label}) ON (n.embedding)
                OPTIONS {{indexConfig: {{
                    `vector.dimensions`: 1024,
                    `vector.similarity_function`: 'cosine'
                }}}}
                """)
            except Exception as e:
                logger.warning(f"  索引 {label} 创建跳过：{e}")
        logger.info("✅ 向量索引就绪（8 类节点）")

    def embed_all_nodes(self, batch_size: int = 10):
        logger.info("🚀 为所有节点生成向量（断点续传）…")
        dashscope.api_key = os.getenv("DASHSCOPE_API_KEY")
        with self.driver.session() as s:
            result = s.run("""
                MATCH (n)
                WHERE (n:Disease OR n:Symptom OR n:Drug OR n:Department
                       OR n:Food OR n:Check OR n:Cure OR n:Producer)
                  AND n.embedding IS NULL
                RETURN elementId(n) AS id, n.name AS name
            """)
            nodes = [{"id": r["id"], "name": r["name"]} for r in result]

            if not nodes:
                logger.info("✨ 所有节点已有向量，跳过")
                return

            logger.info(f"🔍 待向量化节点：{len(nodes)}")
            for i in range(0, len(nodes), batch_size):
                batch = nodes[i:i + batch_size]
                texts = [str(n["name"]).strip() for n in batch if n["name"]]
                if not texts:
                    continue
                try:
                    resp = dashscope.TextEmbedding.call(
                        model=dashscope.TextEmbedding.Models.text_embedding_v3,
                        input=texts
                    )
                    if resp.status_code == 200:
                        embeddings = [emb["embedding"] for emb in resp.output["embeddings"]]
                        for j, node in enumerate(batch):
                            s.run("""
                            MATCH (n) WHERE elementId(n) = $id
                            SET n.embedding = $embedding
                            """, id=node["id"], embedding=embeddings[j])
                        progress = min(i + batch_size, len(nodes))
                        logger.info(f"  ✔️ 进度 {progress}/{len(nodes)}")
                    else:
                        logger.warning(f"  ❌ 批量向量化失败：{resp.message}")
                except Exception as e:
                    logger.warning(f"  ⚠️ 网络/API 异常：{e}")
                time.sleep(0.4)
        logger.info("🎉 全量向量化完成")


# ==========================================
# CLI 入口
# ==========================================
def main():
    ap = argparse.ArgumentParser(description="KG v2 builder")
    ap.add_argument("--dry-run", action="store_true",
                    help="仅解析统计，不写入 Neo4j（推荐先跑一次）")
    ap.add_argument("--rebuild", action="store_true",
                    help="清库 + 全量重建（schema 改了必须用这个）")
    ap.add_argument("--rebuild-diseasekg", action="store_true",
                    help="仅清理并重建 DiseaseKG 子图，保留 Document/Section 文档图")
    ap.add_argument("--skip-embed", action="store_true",
                    help="跳过向量化阶段（用于先建图后单独跑 embedding）")
    ap.add_argument("--json-path", default=JSON_FILE_PATH)
    ap.add_argument("--drug-folder", default=DRUG_FOLDER_PATH)
    args = ap.parse_args()

    parser = KGParser()
    parser.parse_medical_json(args.json_path)
    parser.parse_drug_excel(args.drug_folder)
    parser.report()

    if args.dry_run:
        logger.info("✅ Dry-run 完成，未写入数据库")
        return

    if args.rebuild and args.rebuild_diseasekg:
        raise SystemExit("--rebuild 与 --rebuild-diseasekg 不能同时使用")

    writer = KGWriter()
    try:
        if args.rebuild:
            writer.clean_db()
        elif args.rebuild_diseasekg:
            writer.clean_diseasekg_scope()

        # 节点（按依赖顺序）
        writer.write_diseases(parser.diseases)
        writer.write_drugs(parser.drugs)
        writer.write_simple("Symptom", parser.symptoms)
        writer.write_simple("Food", parser.foods)
        writer.write_simple("Check", parser.checks)
        writer.write_simple("Cure", parser.cures)
        writer.write_departments(parser.departments)
        writer.write_simple("Producer", parser.producers)

        # 关系
        writer.write_rels(parser.rels)

        # 向量
        writer.build_vector_indices()
        if not args.skip_embed:
            writer.embed_all_nodes()
    finally:
        writer.close()

    logger.info("✨ 全流程完成")


if __name__ == "__main__":
    main()
