from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional


SECTION_PATTERNS = [
    (re.compile(r"^(摘要|Abstract)\b", re.I), "摘要"),
    (re.compile(r"^(关键词|Key words?)\b", re.I), "关键词"),
    (re.compile(r"(推荐意见|推荐|建议|Recommendation)", re.I), "推荐意见"),
    (re.compile(r"(诊断|筛查|评估|Diagnosis|Screening)", re.I), "诊断与评估"),
    (re.compile(r"(治疗|管理|处理|用药|Treatment|Management)", re.I), "治疗与管理"),
    (re.compile(r"(禁忌|不良反应|相互作用|Contraindication|Adverse)", re.I), "用药安全"),
    (re.compile(r"(随访|康复|预后|Follow)", re.I), "随访与预后"),
    (re.compile(r"^(参考文献|References?)\b", re.I), "参考文献"),
]

REFERENCE_RE = re.compile(r"^\s*(参考文献|References?)\s*$", re.I)
PAGE_HEADER_RE = re.compile(
    r"(中华.+杂志\s+\d{4}.+第\s*\d+\s*卷|Chin J .+ Vol\.|^\s*[·\.\-]?\s*\d+\s*[·\.\-]?\s*$)",
    re.I,
)
MOJIBAKE_RE = re.compile(r"[åæçèéð�]{2,}|\\x[0-9a-fA-F]{2}|�")
HEADING_RE = re.compile(r"^((第[一二三四五六七八九十\d]+[章节])|([一二三四五六七八九十]+[、.])|(\d+(\.\d+){0,3}[、.\s]))")
RECOMMENDATION_RE = re.compile(r"(推荐意见|推荐强度|证据等级|推荐等级|建议|Recommendation)", re.I)
DIAGNOSIS_RE = re.compile(r"(诊断标准|诊断依据|筛查|评估|分型|分级|Diagnosis|Screening)", re.I)
TREATMENT_RE = re.compile(r"(治疗|管理|处理|干预|用药|Treatment|Management)", re.I)
SAFETY_RE = re.compile(r"(禁忌|慎用|不良反应|相互作用|注意事项|Contraindication|Adverse|Interaction)", re.I)
FOLLOWUP_RE = re.compile(r"(随访|康复|预后|复查|Follow)", re.I)
CJK_RE = re.compile(r"[\u4e00-\u9fff]")
PADDLE_OCR = None


@dataclass
class GuidelineChunk:
    doc_id: str
    chunk_id: str
    title: str
    department: str
    section_title: str
    page_start: int
    page_end: int
    text: str
    text_hash: str
    quality: List[str]
    source_tier: str = "T1"
    source_type: str = "guideline"
    year: Optional[int] = None
    organization: str = ""
    license: str = "local_review_required"
    evidence_level: str = ""
    parent_id: str = ""
    section_path: List[str] = field(default_factory=list)
    block_type: str = "paragraph"
    embedding_text: str = ""
    extraction_method: str = "pymupdf"
    ocr_confidence: float = 0.0
    layout_type: str = "paragraph"
    sibling_prev: str = ""
    sibling_next: str = ""


def stable_hash(text: str, length: int = 16) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:length]


def infer_year(text: str, filename: str = "") -> Optional[int]:
    haystack = f"{filename} {text[:1200]}"
    matches = [int(m) for m in re.findall(r"(20[0-3]\d|19[8-9]\d)", haystack)]
    return max(matches) if matches else None


def infer_section(text: str, current: str = "正文") -> str:
    head = text.strip().splitlines()[0][:80] if text.strip() else ""
    for pattern, name in SECTION_PATTERNS:
        if pattern.search(head) or pattern.search(text[:120]):
            return name
    return current


def infer_section_path(text: str, current: List[str]) -> List[str]:
    head = text.strip().splitlines()[0][:80] if text.strip() else ""
    section = infer_section(text, current[-1] if current else "正文")
    if head and HEADING_RE.search(head) and len(head) <= 60:
        return [section, head]
    return [section]


def clean_pdf_text(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\u3000", " ")
    text = re.sub(r"-\s*\n\s*", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\n(?=[\u4e00-\u9fff])", "", text)
    lines = []
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if not line:
            continue
        if PAGE_HEADER_RE.search(line):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def page_quality_stats(text: str) -> dict:
    chars = [ch for ch in (text or "") if not ch.isspace()]
    total = len(chars)
    cjk = len(CJK_RE.findall(text or ""))
    mojibake = len(MOJIBAKE_RE.findall(text or ""))
    return {
        "chars": total,
        "cjk_ratio": cjk / max(total, 1),
        "mojibake_ratio": mojibake / max(total, 1),
    }


def should_ocr_page(
    text: str,
    *,
    min_chars: int = 60,
    min_cjk_ratio: float = 0.08,
    max_mojibake_ratio: float = 0.02,
) -> bool:
    stats = page_quality_stats(text)
    if stats["chars"] < min_chars:
        return True
    if stats["mojibake_ratio"] > max_mojibake_ratio:
        return True
    return stats["chars"] >= 120 and stats["cjk_ratio"] < min_cjk_ratio


def _get_paddle_ocr(lang: str = "ch"):
    global PADDLE_OCR
    if PADDLE_OCR is None:
        from paddleocr import PaddleOCR

        PADDLE_OCR = PaddleOCR(use_angle_cls=True, lang=lang)
    return PADDLE_OCR


def _flatten_paddle_result(result) -> tuple[str, float]:
    lines: list[str] = []
    confidences: list[float] = []
    for page_result in result or []:
        rows = page_result if isinstance(page_result, list) else []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 2:
                continue
            payload = row[1]
            if not isinstance(payload, (list, tuple)) or not payload:
                continue
            text = str(payload[0]).strip()
            if not text:
                continue
            lines.append(text)
            if len(payload) > 1:
                try:
                    confidences.append(float(payload[1]))
                except (TypeError, ValueError):
                    pass
    confidence = sum(confidences) / max(len(confidences), 1) if confidences else 0.0
    return clean_pdf_text("\n".join(lines)), confidence


def ocr_pdf_page(page, *, lang: str = "ch", zoom: float = 2.0) -> tuple[str, float]:
    import fitz
    import numpy as np
    from PIL import Image

    pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
    image = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
    result = _get_paddle_ocr(lang=lang).ocr(np.array(image), cls=True)
    return _flatten_paddle_result(result)


def quality_flags(text: str, *, page_start: Optional[int] = None, title: str = "") -> List[str]:
    flags: List[str] = []
    stripped = (text or "").strip()
    if len(stripped) < 80:
        flags.append("too_short")
    if MOJIBAKE_RE.search(stripped):
        flags.append("mojibake")
    if page_start is None:
        flags.append("missing_page")
    if not title:
        flags.append("missing_title")
    if REFERENCE_RE.search(stripped[:80]) or stripped.count("[") > 12:
        flags.append("likely_references")
    if stripped and stripped[-1] not in "。.!?！？；;）)】]":
        flags.append("possible_truncation")
    return flags


def _sentence_units(text: str) -> List[str]:
    pieces = re.split(r"(?<=[。！？!?；;])\s*", text)
    return [p.strip() for p in pieces if p and p.strip()]


def _looks_like_table(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 3:
        return False
    numeric_lines = sum(1 for line in lines if len(re.findall(r"\d+(\.\d+)?", line)) >= 2)
    delimiter_lines = sum(1 for line in lines if line.count("|") >= 2 or len(re.findall(r"\s{2,}", line)) >= 2)
    return numeric_lines >= 2 or delimiter_lines >= 2


def classify_block_type(text: str, section_title: str = "") -> str:
    haystack = f"{section_title}\n{text[:220]}"
    if RECOMMENDATION_RE.search(haystack):
        return "recommendation"
    if SAFETY_RE.search(haystack):
        return "medication_safety"
    if DIAGNOSIS_RE.search(haystack):
        return "diagnostic_criteria"
    if TREATMENT_RE.search(haystack):
        return "treatment"
    if FOLLOWUP_RE.search(haystack):
        return "follow_up"
    if _looks_like_table(text):
        return "table"
    return "paragraph"


def _pack_units(
    units: Iterable[str],
    *,
    target_chars: int,
    min_chars: int,
    max_chars: int,
    overlap_chars: int,
) -> List[str]:
    chunks: List[str] = []
    buf = ""
    for unit in units:
        if len(unit) >= min_chars and RECOMMENDATION_RE.search(unit) and buf:
            chunks.append(buf)
            buf = unit
            continue
        if not buf:
            buf = unit
            continue
        next_len = len(buf) + len(unit) + 1
        if next_len <= target_chars or (len(buf) < min_chars and next_len <= max_chars):
            buf = f"{buf}\n{unit}"
        else:
            chunks.append(buf)
            tail = buf[-overlap_chars:] if overlap_chars > 0 and classify_block_type(buf) == "paragraph" else ""
            buf = f"{tail}\n{unit}".strip() if tail and len(unit) < max_chars else unit
    if buf:
        chunks.append(buf)
    return chunks


def _contextual_embedding_text(
    *,
    title: str,
    department: str,
    section_path: List[str],
    page_no: int,
    block_type: str,
    text: str,
) -> str:
    return "\n".join([
        f"[文档] {title}",
        f"[科室] {department}",
        f"[章节] {' > '.join(section_path) if section_path else '正文'}",
        f"[页码] P{page_no}",
        f"[块类型] {block_type}",
        "[正文]",
        text,
    ])


def read_pdf_pages_with_metadata(
    pdf_path: str | Path,
    *,
    enable_ocr_fallback: bool = False,
    ocr_min_chars: int = 60,
    ocr_min_cjk_ratio: float = 0.08,
    ocr_max_mojibake_ratio: float = 0.02,
    min_ocr_confidence: float = 0.5,
) -> List[dict]:
    import fitz

    path = Path(pdf_path)
    pages: List[dict] = []
    with fitz.open(path) as doc:
        for idx, page in enumerate(doc, start=1):
            text = clean_pdf_text(page.get_text("text"))
            extraction_method = "pymupdf"
            ocr_confidence = 0.0
            if enable_ocr_fallback and should_ocr_page(
                text,
                min_chars=ocr_min_chars,
                min_cjk_ratio=ocr_min_cjk_ratio,
                max_mojibake_ratio=ocr_max_mojibake_ratio,
            ):
                try:
                    ocr_text, confidence = ocr_pdf_page(page)
                    if confidence >= min_ocr_confidence and len(ocr_text) > len(text):
                        text = ocr_text
                        extraction_method = "paddleocr"
                        ocr_confidence = confidence
                except Exception:
                    extraction_method = "pymupdf_ocr_failed"
            if text:
                pages.append({
                    "page": idx,
                    "text": text,
                    "extraction_method": extraction_method,
                    "ocr_confidence": round(float(ocr_confidence), 6),
                })
    return pages


def read_pdf_pages(pdf_path: str | Path) -> List[tuple[int, str]]:
    return [(row["page"], row["text"]) for row in read_pdf_pages_with_metadata(pdf_path)]


def build_guideline_chunks(
    pdf_path: str | Path,
    *,
    department: str = "",
    target_chars: int = 650,
    min_chars: int = 180,
    max_chars: int = 1100,
    overlap_chars: int = 40,
    enable_ocr_fallback: bool = False,
    ocr_min_chars: int = 60,
    ocr_min_cjk_ratio: float = 0.08,
    ocr_max_mojibake_ratio: float = 0.02,
    min_ocr_confidence: float = 0.5,
) -> List[GuidelineChunk]:
    path = Path(pdf_path)
    title = path.stem
    dept = department or path.parent.name
    doc_bytes = path.read_bytes()
    doc_hash = hashlib.sha256(doc_bytes).hexdigest()[:16]
    doc_id = f"guideline:{stable_hash(str(path.resolve()), 10)}:{doc_hash}"

    pages = read_pdf_pages_with_metadata(
        path,
        enable_ocr_fallback=enable_ocr_fallback,
        ocr_min_chars=ocr_min_chars,
        ocr_min_cjk_ratio=ocr_min_cjk_ratio,
        ocr_max_mojibake_ratio=ocr_max_mojibake_ratio,
        min_ocr_confidence=min_ocr_confidence,
    )
    chunks: List[GuidelineChunk] = []
    current_section_path = ["正文"]
    year = infer_year("\n".join(str(row["text"]) for row in pages[:2]), title)

    for page_row in pages:
        page_no = int(page_row["page"])
        page_text = str(page_row["text"])
        extraction_method = str(page_row.get("extraction_method") or "pymupdf")
        ocr_confidence = float(page_row.get("ocr_confidence") or 0.0)
        if REFERENCE_RE.search(page_text[:80]):
            break
        current_section_path = infer_section_path(page_text, current_section_path)
        current_section = current_section_path[-1]
        if current_section_path[0] == "参考文献":
            break
        units = _sentence_units(page_text)
        packed_units = _pack_units(
            units,
            target_chars=target_chars,
            min_chars=min_chars,
            max_chars=max_chars,
            overlap_chars=overlap_chars,
        )
        for local_idx, text in enumerate(packed_units):
            text_hash = stable_hash(text, 20)
            block_type = classify_block_type(text, current_section)
            section_hash = stable_hash(">".join(current_section_path), 8)
            parent_id = f"{doc_hash}_{section_hash}"
            chunk_id = f"{doc_hash}_{page_no}_{local_idx}_{text_hash[:8]}"
            flags = quality_flags(text, page_start=page_no, title=title)
            chunks.append(
                GuidelineChunk(
                    doc_id=doc_id,
                    chunk_id=chunk_id,
                    title=title,
                    department=dept,
                    section_title=current_section,
                    page_start=page_no,
                    page_end=page_no,
                    text=text,
                    text_hash=text_hash,
                    quality=flags,
                    year=year,
                    parent_id=parent_id,
                    section_path=list(current_section_path),
                    block_type=block_type,
                    embedding_text=_contextual_embedding_text(
                        title=title,
                        department=dept,
                        section_path=current_section_path,
                        page_no=page_no,
                        block_type=block_type,
                        text=text,
                    ),
                    extraction_method=extraction_method,
                    ocr_confidence=ocr_confidence,
                    layout_type="table" if block_type == "table" else "paragraph",
                )
            )
    for idx, chunk in enumerate(chunks):
        chunk.sibling_prev = chunks[idx - 1].chunk_id if idx > 0 and chunks[idx - 1].parent_id == chunk.parent_id else ""
        chunk.sibling_next = chunks[idx + 1].chunk_id if idx + 1 < len(chunks) and chunks[idx + 1].parent_id == chunk.parent_id else ""
    return chunks
