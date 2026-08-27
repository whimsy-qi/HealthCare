# RAG Data Expansion Runbook

This runbook keeps data expansion resumable and source-policy aware. Do not clear DashVector for these steps.

## Current Expansion Targets

- `medical_literature_v2`: PubMed abstracts for high-risk research and rumor-check questions.
- `clinical_trial_v2`: ClinicalTrials.gov records for interventions and trial status.
- `drug_label_v2`: openFDA/DailyMed official labels for common and high-risk drugs.
- `medical_guideline_v2`: curated NHC, WHO, USPSTF, CDC, ADA, GOLD, GINA, KDIGO, and IDSA seed pages.
- `patient_education_v2`: MedlinePlus topic XML for low-risk explanations only.
- `cancer_evidence_v2`: NCI PDQ professional summaries for oncology evidence.

## Safe Order While Local Drug Ingestion Is Running

Run offline checks first. These do not call external APIs or write vectors:

```powershell
cd D:\Health_system\backend
.\.venv\Scripts\python.exe -m rag.ingest.pubmed_resume_cli --offline --dry-run
.\.venv\Scripts\python.exe -m rag.ingest.clinical_trials_resume_cli --offline --dry-run
.\.venv\Scripts\python.exe -m rag.ingest.openfda_label_resume_cli --offline --dry-run
.\.venv\Scripts\python.exe -m rag.ingest.external_source_cli --dry-run
```

When the local drug ingestion is no longer saturating embedding/write quota, run small batches:

```powershell
.\.venv\Scripts\python.exe -m rag.ingest.pubmed_resume_cli --limit 5 --top-k 20 --batch-size 16 --dry-run
.\.venv\Scripts\python.exe -m rag.ingest.clinical_trials_resume_cli --limit 5 --top-k 20 --batch-size 16 --dry-run
.\.venv\Scripts\python.exe -m rag.ingest.openfda_label_resume_cli --limit 10 --top-k 2 --batch-size 16 --dry-run
.\.venv\Scripts\python.exe -m rag.ingest.external_source_cli --limit 10 --fetch --dry-run
```

After the dry-run reports look clean, remove `--dry-run` one source at a time. Keep `--limit` until retrieval evaluation improves without degrading drug safety.

## Retrieval Policy

- `latest_research`: prefer PubMed, ClinicalTrials, PMC OA, AHRQ. Do not let `cancer_evidence_v2` satisfy non-oncology research claims.
- `medication_safety`: prefer T1 official labels from openFDA/DailyMed, then local T3 drug labels, then guidelines.
- `guideline_qa`: prefer NHC/gov.cn for Chinese primary-care questions; use WHO, USPSTF, CDC, ADA, GOLD, GINA, KDIGO, and IDSA as English authority supplements.
- `general`: MedlinePlus can explain terms but must not override guideline, drug label, literature, or trial evidence.
- `rumor_check`: therapy claims need research/trial evidence; safety claims need drug labels or safety-source evidence.

## Sources Not Treated As Evidence

Chinese medical dialogue datasets, fine-tuning datasets, SEO medical pages, hospital Q&A pages, and social media content can be used for query phrasing and evaluation generation only. They must not be stored as high-confidence medical evidence.
