# RAG 与 medical-graphrag 管理端接入审查报告

生成时间：2026-05-09  
范围：`D:\Health_system\backend` 与 `D:\Health_system\medical-graphrag-deepsearch-0413\graphrag-deepsearch`

## 1. 结论

当前项目已经形成“双后端”架构：

- `D:\Health_system\backend`：C 端主后端，继续负责用户侧问答入口、C 端页面接口、健康知识只读代理，以及当前默认 DashVector RAG 主链路。
- `D:\Health_system\medical-graphrag-deepsearch-0413\graphrag-deepsearch\backend`：新的正式管理员端后端，负责管理员登录、RAG 数据源上传、Celery 入库、Milvus 写入、RAG Debug、RAG 策略实验室和后续评测闭环。

目前不能把 Milvus 直接设为 C 端默认 RAG 后端。原因不是链路没通，而是数据迁移和任务稳定性还没达标：

- Milvus 已有 `drug_label_v2=17075`、`medical_guideline_v2=2289`。
- 小样本 DashVector vs Milvus 评测 `16/16` 通过。
- 但最新 drug_excel 分片 `ingest_4bf4078a3cbf4129ab` 卡在 `embedding_batch`，状态仍是 `processing`，且 `allow_next_batch=false`。
- `medical_literature_v2`、`clinical_trial_v2`、`patient_education_v2`、`cancer_evidence_v2`、`medical_kg_v2` 在 medical-graphrag Milvus 侧仍未建库或无数据。

因此当前合理定位是：

> DashVector 仍是 C 端默认主链路；medical-graphrag + Milvus 是正式管理员端和下一代 RAG 主链路候选，进入分批迁移和评测阶段。

## 2. 当前 C 端 RAG 状态

### 2.1 默认策略

当前 C 端线上策略仍是自研的 `Medical Policy RAG with Hybrid Recall`，不是 medical-graphrag 的 Milvus 主链路。

证据：

- `D:\Health_system\backend\rag\service.py:37` 读取 `RAG_BACKEND`，默认值是 `dashvector`。
- `D:\Health_system\backend\rag\service.py:38-39` 只有当 `RAG_BACKEND=medical_graphrag` 时才调用 `search_medical_graphrag()`。
- `D:\Health_system\backend\rag\retrieval\medical_graphrag_client.py:114` 调用的是稳定接口 `/medical-rag/retrieve`。

当前 C 端策略的实际形态：

- DashVector dense recall。
- 本地 PDF BM25 fallback。
- intent 路由。
- source quota。
- rerank。
- evidence policy。
- 高风险问题的 policy flags，例如 `unsafe_to_answer`、`research_source_missing`。

这可以称为 Hybrid Search，但不只是普通 Hybrid Search。普通 Hybrid 只强调 dense+sparse 召回；当前系统额外加了医疗来源分层、意图强制证据源、locator、低质量证据降权和无证据降置信。

### 2.2 C 端接入 medical-graphrag 的边界

主项目已经具备切换能力，但默认不切：

- `D:\Health_system\backend\rag\retrieval\medical_graphrag_client.py:100` 要求配置 `MEDICAL_GRAPHRAG_API_TOKEN`。
- `D:\Health_system\backend\rag\retrieval\medical_graphrag_client.py:114` 只调用 `/medical-rag/retrieve`。
- C 端不应调用 `/rag-admin/*` 或 `/rag-lab/*`。

这条边界是正确的：`/medical-rag/retrieve` 是稳定服务接口，`/rag-admin/*` 是管理写接口，`/rag-lab/*` 是实验接口。C 端如果绕过稳定接口，会把实验策略暴露给用户，风险不可控。

## 3. 主项目旧 Admin 清理状态

旧 Admin demo 已经下线，主项目不再承担后台写能力。

证据：

- `D:\Health_system\backend\api_server.py:166` 对 `/api/admin` 做拦截。
- `D:\Health_system\backend\api_server.py:1289-1290` 明确说明旧 `/api/admin/*` 实现已移除，C 端只使用健康文章只读代理，所有管理员写操作进入 medical-graphrag。
- `D:\Health_system\backend\api_server.py:844` 保留 `GET /api/health-articles`。
- `D:\Health_system\backend\api_server.py:859` 保留 `GET /api/health-articles/{article_id}`。

当前边界正确：

- 主项目保留 C 端只读内容代理。
- 管理员登录、文章写入、RAG 上传、入库任务都归 medical-graphrag。

需要注意：旧 Admin 表如果仍在数据库中，不应在当前阶段物理删除。先停止路由和写入口即可，后续单独做归档迁移。

## 4. medical-graphrag 管理端接入状态

### 4.1 已完成能力

medical-graphrag 当前已经承担正式管理员端雏形：

- Docker 服务可运行。
- 管理员登录可用。
- RAG 数据源上传可用。
- Celery 入库任务可用。
- Milvus 写入可用。
- RAG Debug / 策略实验室存在。
- 稳定检索接口 `/api/v1/medical-rag/retrieve` 存在。

证据：

- `D:\Health_system\medical-graphrag-deepsearch-0413\graphrag-deepsearch\backend\app\api\endpoints\medical_rag.py:24` 注册 `/medical-rag`。
- `...\medical_rag.py:36-39` 提供 `POST /retrieve`，并依赖 `require_medical_rag_retrieve_access`。
- `D:\Health_system\medical-graphrag-deepsearch-0413\graphrag-deepsearch\backend\app\api\endpoints\rag_lab.py:24` 注册 `/rag-lab`。
- `...\rag_lab.py:174-180` 提供策略列表和单策略运行。
- `...\rag_lab.py:188-210` 提供策略 compare，并声明稳定接口是 `/api/v1/medical-rag/retrieve`。

### 4.2 当前支持的真实入库类型

目前不要误判为所有数据源都已经接入。真实支持的入库类型只有两个：

- `guideline_pdf`
- `drug_excel`

证据：

- `D:\Health_system\medical-graphrag-deepsearch-0413\graphrag-deepsearch\backend\app\services\medical_rag\ingest_service.py:25`：`SUPPORTED_SOURCE_TYPES = {"guideline_pdf", "drug_excel"}`。
- `...\rag_admin.py:542-546`：启动任务时会检查 source type，不支持的类型返回 `source_type_not_implemented`。

因此 PubMed、ClinicalTrials、openFDA、patient education external import、KG source 目前最多只能登记或作为规划项，不能宣称已经通过管理端真实入库。

## 5. Milvus 数据现状

来自恢复报告 `D:\Health_system\backend\rag\reports\medical_graphrag_migration\after_failed_drug_offset_2000_recovered.json`：

| Collection | 当前状态 | row_count |
|---|---:|---:|
| `drug_label_v2` | exists | 17075 |
| `medical_guideline_v2` | exists | 2289 |
| `medical_literature_v2` | not exists | 0 |
| `clinical_trial_v2` | not exists | 0 |
| `patient_education_v2` | not exists | 0 |
| `cancer_evidence_v2` | not exists | 0 |
| `medical_kg_v2` | not exists | 0 |

任务汇总：

- 总 source：45。
- 总 ingest run：45。
- completed：39。
- failed：2。
- quarantined：3。
- processing：1。

按 source type：

- `drug_excel`：13 个 runs，其中 completed 7、failed 2、quarantined 3、processing 1。
- `guideline_pdf`：32 个 runs，全部 completed。

这说明 PDF 试迁移比较稳定；药品迁移规模更大，当前瓶颈在 embedding_batch 阶段。

## 6. 当前阻塞点

### 6.1 药品分片任务卡住

当前卡住 run：

- `ingest_run_id`: `ingest_4bf4078a3cbf4129ab`
- `source_type`: `drug_excel`
- `collection`: `drug_label_v2`
- `status`: `processing`
- `current_phase`: `embedding_batch`
- `processed_rows`: 1000
- `accepted_chunks`: 8320
- `quarantined_chunks`: 2385
- `embedded_chunks`: 1536
- `embedding_total_chunks`: 8320
- `stale_processing`: true
- `seconds_since_update`: 约 1126 秒（报告生成时）

这不是“还有很多行没解析”的问题。解析和 chunk 已完成，卡点在 embedding 批处理。这个阶段继续提交 offset 3000/4000 会放大阻塞，不应继续。

### 6.2 迁移脚本 token 过期问题已修，但未重跑

`medical_graphrag_seed_migrate.py` 之前失败原因是管理员 JWT 过期：长时间轮询第一个分片后，第二次上传时 `/rag-admin/upload` 返回 401。已修正上传和启动任务也走 token refresh。

涉及文件：

- `D:\Health_system\backend\rag\migration\medical_graphrag_seed_migrate.py`

当前状态：

- 代码已修。
- 未继续执行新的分片。
- 已按用户要求停止迁移。

### 6.3 乱码仍是实际问题

评测报告里仍能看到旧 DashVector 或旧 PDF/BM25 结果存在 mojibake，例如 `after_batched_drug_dashvector_vs_milvus.json` 中 query/title/text_preview 出现 `楂樿鍘嬭瘖...` 这类乱码。

这说明：

- medical-graphrag 新入库链路里的中文常量已经局部修复。
- 但历史 DashVector / 本地 BM25 / 旧入库数据仍可能有乱码。
- 不能只看指标 1.0 就认为文本质量已经完成治理。

## 7. 药品质量门禁状态

已完成的修正：

- `D:\Health_system\medical-graphrag-deepsearch-0413\graphrag-deepsearch\backend\app\services\medical_rag\ingest_service.py:30` 定义 safety sections。
- `...\ingest_service.py:39` 增加 `TERSE_SAFETY_MAX_CHARS = 80`。
- `...\ingest_service.py:134-140` 增加 `drug_section_quality_flags()`，对安全章节降低短文本门槛。
- `...\ingest_service.py:396` 使用新的药品章节质量门禁。
- `...\ingest_service.py:440-441` metadata 写入 `safety_critical` 与 `terse_safety_label`。
- `...\ingest_service.py:434`、`449`、`514` 保留 `official_source_assumption=True`。

当前策略符合需求：

- `禁忌 / 不良反应 / 药物相互作用 / 注意事项 / 儿童用药 / 老人用药 / 孕妇及哺乳期妇女用药` 使用更低短文本门槛。
- “严重肝肾功能不全者禁用”“对本品过敏者禁用”等短句不应再因为 `too_short` 被 quarantine。
- “尚不明确”“未进行该项试验且无可靠参考文献”等无信息内容仍会进入 quarantine。

需要补充的验证：

- 还没有跑独立单元测试证明这些短句全部进入主库。
- 还没有重新跑 offset 2000 之后的分片验证新门禁的实际收益。

## 8. 小样本评测状态

已有报告：

- `D:\Health_system\backend\rag\reports\eval_runs\after_batched_drug_dashvector_vs_milvus.json`

结果：

| 指标 | DashVector | medical-graphrag / Milvus |
|---|---:|---:|
| n | 16 | 16 |
| top1_source_accuracy | 1.0 | 1.0 |
| top5_source_accuracy | 1.0 | 1.0 |
| preferred_source_type_hit | 1.0 | 1.0 |
| authority_tier_match | 1.0 | 1.0 |
| citation_locator_valid_rate | 1.0 | 1.0 |
| low_tier_override_error | 0.0 | 0.0 |
| unsafe_no_evidence_answer_rate | 0.0 | 0.0 |

这个结果只能说明“小样本链路可用”，不能说明 Milvus 可以接管 C 端默认链路。原因：

- n=16 太小。
- medical-graphrag Milvus 侧没有研究证据层。
- patient education、cancer evidence、KG 也未迁移。
- 当前还有一个药品 run 卡住。

## 9. RAG 策略实验室的定位

RAG 策略实验室有价值，但它不是线上策略选择开关。

当前合理定位：

- `Medical Policy RAG`：线上候选基线。
- `Dense Vector RAG`：对照组。
- `Hybrid RAG`：检验 dense + sparse/BM25 组合收益；如果管理端 BM25 未接入，不能伪装完整 Hybrid。
- `GraphRAG-assisted`：候选扩展和路径解释，不给最终高置信医学结论。
- `LightRAG`：未实现就返回 `not_implemented`。
- `DeepSearch`：线索发现，不作为高风险医学证据。

关键约束：

- 管理员可以在实验室手动选择策略做对比。
- C 端不能调用实验室策略。
- 实验结果必须经过评测门禁后，才能影响默认策略。

## 10. 下一步建议

### P0：先处理卡住的 drug_excel run

不要继续跑 offset 3000/4000。先处理 `ingest_4bf4078a3cbf4129ab`：

1. 确认 Celery worker 是否还有该 task active。
2. 如果没有 active task，将 run 标记为 failed，reason 写 `stale_embedding_batch_no_active_worker`。
3. 如果有 active task，但 embedding 长时间无进度，应给 embedding batch 增加 request timeout、单批失败重试和失败落库。

当前不建议物理删除 run 或清 Milvus，因为该 run 的 `inserted_chunks=0`，主要问题是状态清理和可恢复性。

### P1：把药品迁移粒度从 1000 行降到 250-500 行

当前 1000 行会生成 6000-8000+ chunks，embedding 阶段耗时长，token 过期和任务卡住概率高。

建议改为：

- `drug-batch-rows=250` 或 `500`。
- 每个分片独立 source/run/report。
- 每个 run 完成后立即生成恢复报告。
- 不允许存在 `processing/stale_processing` 时继续下一批。

### P2：完善 embedding 批处理容错

当前 `embedding_batch` 卡住说明还缺硬超时和降级失败机制。

建议：

- 每个 embedding request 设置明确 timeout。
- 单批失败记录 batch index。
- 连续失败超过阈值时 run 转 failed。
- run.debug 写入：
  - `embedding_batch_index`
  - `embedding_failed_batches`
  - `last_embedding_error`
  - `last_successful_batch_at`

### P3：补药品质量门禁测试

至少加测试覆盖：

- `严重肝肾功能不全者禁用`：进入主库，`terse_safety_label=true`。
- `儿童患流感或水痘应避免使用`：进入主库。
- `对本品过敏者禁用`：进入主库。
- `尚不明确`：quarantine。
- `未进行该项试验且无可靠参考文献`：quarantine。

### P4：重新跑小批量迁移与评测

只在 P0-P3 完成后继续：

1. offset 2000 重新提交较小分片。
2. offset 3000/4000 按小分片继续。
3. 完整 golden queries 评测。
4. 若通过，再做 C 端测试环境 `RAG_BACKEND=medical_graphrag` 预演。

### P5：暂缓事项

当前不应做：

- 不切 C 端默认 Milvus。
- 不接 PubMed/ClinicalTrials/openFDA 管理端真实入库。
- 不启用 GraphRAG 作为最终证据。
- 不扩大 PDF OCR。
- 不清空 DashVector。

## 11. 当前可执行命令建议

只读恢复报告：

```powershell
cd D:\Health_system\backend
.\.venv\Scripts\python.exe -m rag.migration.medical_graphrag_recover_report `
  --api-base http://localhost:8026/api/v1 `
  --username admin `
  --password admin123 `
  --out rag\reports\medical_graphrag_migration\current_recovered.json
```

如果确认 worker 已无 active task 后，标记卡住 run：

```powershell
POST http://localhost:8026/api/v1/rag-admin/ingest-runs/ingest_4bf4078a3cbf4129ab/mark-failed
reason=stale_embedding_batch_no_active_worker
```

后续较小药品分片建议：

```powershell
cd D:\Health_system\backend
.\.venv\Scripts\python.exe -m rag.migration.medical_graphrag_seed_migrate `
  --api-base http://localhost:8026/api/v1 `
  --username admin `
  --password admin123 `
  --pdf-limit 0 `
  --drug-root drug_data `
  --drug-row-offset 2000 `
  --drug-row-limit 500 `
  --drug-batch-rows 250 `
  --start `
  --poll `
  --out rag\reports\medical_graphrag_migration\drug_offset_2000_2x250.json
```

## 12. 总体判断

medical-graphrag 接入方向是对的，但现在还不是“上线替换 DashVector”的阶段。它已经可以作为正式管理员端和 Milvus RAG 管理服务继续建设；C 端主链路仍应默认 DashVector。

当前最重要的问题不是再扩数据，而是把迁移任务做成真正可恢复、可观测、可失败、可重试。否则数据量一上来，管理员端会出现“看起来在 processing，实际上卡死”的状态，这会比检索质量问题更难排查。

