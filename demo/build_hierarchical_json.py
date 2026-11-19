# Copyright (c) Opendatalab. All rights reserved.
"""Chuyển đổi structured_content.json và PDF gốc sang JSON phân cấp Chương -> Bài -> Mục -> Khối."""

from __future__ import annotations

import json
import re
import unicodedata
from pathlib import Path
from typing import Any, Literal

import numpy as np
import pypdfium2 as pdfium
from PIL import Image

BgKind = Literal["WHITE", "CREAM", "BLUE_SUMMARY", "PINK_APPLY", "CYAN_EXTEND"]
IconKind = Literal["MO_DAU", "HOAT_DONG", "CAU_HOI_BAI_TAP", "EM_CO_BIET"]

_WATERMARK_SUBSTRINGS = (
    "VOI CUOC SONG",
    "KET NOI TRI THUC",
    "OI TRI THUC",
)

_EXTRACTION_TIER_MAP: dict[str, str] = {
    "LY_THUYET": "TIER_1_CORE",
    "EM_DA_HOC": "TIER_1_CORE",
    "EM_CO_BIET": "TIER_1_CORE",
    "HOAT_DONG": "TIER_2_EMPIRICAL",
    "MO_DAU": "TIER_2_EMPIRICAL",
    "CAU_HOI_BAI_TAP": "TIER_3_ASSESSMENT",
    "MUC_TIEU": "METADATA_ONLY",
    "EM_CO_THE": "METADATA_ONLY",
    "GIOI_THIEU": "METADATA_ONLY",
}


def _normalize_ascii(text: str) -> str:
    """Chuẩn hóa chuỗi tiếng Việt về chữ in hoa không dấu để đối chiếu tiêu đề bền vững."""
    text = text.replace("Đ", "D").replace("đ", "d")
    decomposed = unicodedata.normalize("NFD", text)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", stripped).strip().upper()


def _is_left_icon_candidate(block: dict[str, Any]) -> bool:
    """Kiểm tra block image có phải là icon chỉ dẫn nằm ở lề trái hay không."""
    if block.get("type") != "image":
        return False
    bbox = block.get("bbox")
    if not bbox or len(bbox) != 4:
        return False
    x0, y0, x1, y1 = bbox
    return x0 < 0.22 and (x1 - x0) < 0.10 and (y1 - y0) < 0.06


def _classify_icon_image(img_path: Path) -> IconKind | None:
    """Phân loại 4 icon chỉ dẫn của bộ sách Kết nối tri thức bằng tỷ lệ màu."""
    if not img_path.is_file():
        return None
    with Image.open(img_path) as pil_im:
        im = np.array(pil_im.convert("RGB"))
    h, w, _ = im.shape
    r, g, b = im[:, :, 0].astype(float), im[:, :, 1].astype(float), im[:, :, 2].astype(float)

    orange = float(np.mean((r > 200) & (g > 100) & (g < 210) & (b < 100)))
    center = im[int(h * 0.25) : int(h * 0.75), int(w * 0.25) : int(w * 0.75)]
    white_center = float(np.mean((center > 235).all(axis=-1))) if center.size > 0 else 0.0
    blue_cyan = float(np.mean((b > r + 25) & (g > r + 10)))
    dark_blue = float(np.mean((b > 110) & (r < 90)))

    if orange > 0.08:
        return "HOAT_DONG" if white_center >= 0.18 else "MO_DAU"
    if blue_cyan > 0.20:
        return "EM_CO_BIET" if dark_blue >= 0.10 else "CAU_HOI_BAI_TAP"
    return None


def _classify_block_bg(page_arr: np.ndarray, bbox: list[float]) -> BgKind:
    """Đo màu nền chủ đạo (phân vị 85) của một block trên ảnh trang PDF."""
    h, w, _ = page_arr.shape
    x0 = max(0, int(bbox[0] * w))
    y0 = max(0, int(bbox[1] * h))
    x1 = min(w, int(bbox[2] * w))
    y1 = min(h, int(bbox[3] * h))
    if x1 <= x0 or y1 <= y0:
        return "WHITE"

    crop = page_arr[y0:y1, x0:x1]
    r, g, b = np.percentile(crop.reshape(-1, 3), 85, axis=0)

    if 238 <= r <= 252 and 230 <= g <= 246 and 218 <= b <= 236 and (r - b) >= 10:
        return "CREAM"
    if b > r + 12 and 185 <= r <= 215 and 220 <= b <= 245:
        return "BLUE_SUMMARY"
    if r > g + 15 and 225 <= r <= 248 and g <= 222:
        return "PINK_APPLY"
    if b > r + 10 and g > r + 10 and 210 <= r <= 232:
        return "CYAN_EXTEND"
    return "WHITE"


def _probe_pdf_corner_icon(page_arr: np.ndarray, y0_norm: float) -> Literal["HOAT_DONG", "CAU_HOI_BAI_TAP"]:
    """Quét góc trên-trái của hộp màu kem trên ảnh PDF khi MinerU không cắt riêng block icon."""
    h, w, _ = page_arr.shape
    py0 = max(0, int((y0_norm - 0.045) * h))
    py1 = min(h, int((y0_norm + 0.015) * h))
    px0 = max(0, int(0.11 * w))
    px1 = min(w, int(0.18 * w))
    if py1 <= py0 or px1 <= px0:
        return "HOAT_DONG"

    patch = page_arr[py0:py1, px0:px1]
    r, g, b = patch[:, :, 0].astype(float), patch[:, :, 1].astype(float), patch[:, :, 2].astype(float)
    orange = float(np.mean((r > 200) & (g > 100) & (g < 210) & (b < 100)))
    blue_cyan = float(np.mean((b > r + 25) & (g > r + 10)))

    if blue_cyan > orange and blue_cyan > 0.02:
        return "CAU_HOI_BAI_TAP"
    return "HOAT_DONG"


def _is_lesson_header_block(block: dict[str, Any]) -> bool:
    """Kiểm tra block có phải là tiêu đề 'Bài X' (hoặc 'Bài' khi OCR sót số) ở đầu trang không."""
    btype = str(block.get("type", ""))
    if btype not in ("paragraph_title", "doc_title", "header"):
        return False
    bbox = block.get("bbox") or []
    if len(bbox) != 4 or bbox[0] >= 0.25 or bbox[1] >= 0.20:
        return False
    norm = _normalize_ascii(str(block.get("content", "")))
    return bool(re.match(r"^BAI(?:\s+\d+)?$", norm))


def _reorder_page_blocks(blocks: list[dict[str, Any]], output_dir: Path) -> list[dict[str, Any]]:
    """Sửa thứ tự đọc: đưa block bị đảo trước tiêu đề Bài mới về đúng vị trí và đặt dòng ■ trước icon Mở đầu."""
    reordered = list(blocks)

    # Nếu có block nằm trước tiêu đề Bài nhưng tọa độ y0 thực tế nằm bên dưới tiêu đề Bài, chuyển về đúng vị trí y0.
    lesson_idx = next((idx for idx, blk in enumerate(reordered) if _is_lesson_header_block(blk)), None)
    if lesson_idx is not None and lesson_idx > 0:
        lesson_y1 = float(reordered[lesson_idx]["bbox"][3])
        kept_prefix: list[dict[str, Any]] = []
        misplaced: list[dict[str, Any]] = []
        for blk in reordered[:lesson_idx]:
            bbox = blk.get("bbox") or []
            if len(bbox) == 4 and float(bbox[1]) > lesson_y1 + 0.02:
                misplaced.append(blk)
            else:
                kept_prefix.append(blk)
        if misplaced:
            reordered = kept_prefix + reordered[lesson_idx:]
            for blk in misplaced:
                by0 = float(blk["bbox"][1])
                insert_pos = len(reordered)
                for idx in range(len(reordered)):
                    cand_bbox = reordered[idx].get("bbox") or []
                    if len(cand_bbox) == 4 and float(cand_bbox[1]) > by0 + 0.02:
                        insert_pos = idx
                        break
                reordered.insert(insert_pos, blk)

    i = 0
    while i < len(reordered) - 1:
        curr = reordered[i]
        nxt = reordered[i + 1]
        if _is_left_icon_candidate(curr) and nxt.get("type") == "text":
            icon_kind = _classify_icon_image(output_dir / curr["image_source"])
            nxt_text = str(nxt.get("content", "")).strip()
            if icon_kind == "MO_DAU" and nxt_text.startswith("■") and nxt["bbox"][1] < curr["bbox"][3]:
                reordered[i], reordered[i + 1] = nxt, curr
                i += 2
                continue
        i += 1
    return reordered


def _simplify_item(block: dict[str, Any], page_num: int) -> dict[str, Any]:
    """Đóng gói block thành item chuẩn giữ lại page, bbox và nội dung/ảnh."""
    item: dict[str, Any] = {
        "type": block.get("type", "text"),
        "page": page_num,
        "bbox": block.get("bbox", []),
    }
    if block.get("content"):
        item["content"] = block["content"]
    if block.get("image_source"):
        item["image_source"] = block["image_source"]
    if block.get("captions"):
        item["captions"] = [c.get("content", "") for c in block["captions"] if c.get("content")]
    if block.get("footnotes"):
        item["footnotes"] = [f.get("content", "") for f in block["footnotes"] if f.get("content")]
    return item


def build_hierarchical_content(structured_data: dict[str, Any], pdf_path: Path, output_dir: Path) -> dict[str, Any]:
    """Chuyển đổi dữ liệu theo trang sang cây phân cấp Chương -> Bài -> Mục -> Khối chức năng."""
    pdf_doc = pdfium.PdfDocument(str(pdf_path))
    chapter_name = ""
    chapter_info: dict[str, Any] | None = None
    chapters_info: list[dict[str, Any]] = []
    current_chapter = ""
    last_lesson_num: int | None = None
    lessons: list[dict[str, Any]] = []

    current_lesson: dict[str, Any] | None = None
    current_section: dict[str, Any] | None = None
    current_unit: dict[str, Any] | None = None

    def _ensure_lesson(fallback_id: str = "Bài học") -> dict[str, Any]:
        nonlocal current_lesson
        if current_lesson is None:
            current_lesson = {
                "chapter": current_chapter,
                "lesson_id": fallback_id,
                "lesson_title": "",
                "pages": [],
                "sections": [],
            }
            lessons.append(current_lesson)
        return current_lesson

    def _open_section(
        section_type: str,
        title: str,
        *,
        page_num: int | None = None,
        bbox: list[float] | None = None,
        section_id: str | None = None,
        marker_icon: str | None = None,
        marker_bbox: list[float] | None = None,
    ) -> dict[str, Any]:
        nonlocal current_section, current_unit
        lesson = _ensure_lesson()
        sec: dict[str, Any] = {
            "section_type": section_type,
            "title": title,
        }
        if section_id:
            sec["section_id"] = section_id
        if page_num is not None:
            sec["page"] = page_num
        if bbox:
            sec["bbox"] = bbox
        if marker_icon:
            sec["marker_icon"] = marker_icon
        if marker_bbox:
            sec["marker_bbox"] = marker_bbox
        if section_type == "NOI_DUNG_CHINH":
            sec["units"] = []
        else:
            sec["items"] = []
        lesson["sections"].append(sec)
        current_section = sec
        current_unit = None
        return sec

    def _open_unit(
        unit_type: str,
        *,
        page_num: int | None = None,
        marker_icon: str | None = None,
        marker_text: str | None = None,
        marker_bbox: list[float] | None = None,
    ) -> dict[str, Any]:
        nonlocal current_unit
        if current_section is None or current_section.get("section_type") != "NOI_DUNG_CHINH":
            _open_section("NOI_DUNG_CHINH", "Nội dung chính")
        assert current_section is not None
        unit: dict[str, Any] = {"unit_type": unit_type}
        if page_num is not None:
            unit["page"] = page_num
        if marker_icon:
            unit["marker_icon"] = marker_icon
        if marker_text:
            unit["marker_text"] = marker_text
        if marker_bbox:
            unit["marker_bbox"] = marker_bbox
        unit["items"] = []
        current_section["units"].append(unit)
        current_unit = unit
        return unit

    def _append_item(block: dict[str, Any], page_num: int) -> None:
        item = _simplify_item(block, page_num)
        if current_section is None:
            _open_section("GIOI_THIEU", "Giới thiệu")
        assert current_section is not None
        if current_section.get("section_type") == "NOI_DUNG_CHINH":
            if current_unit is None:
                _open_unit("LY_THUYET")
            assert current_unit is not None
            current_unit["items"].append(item)
        else:
            current_section["items"].append(item)

    try:
        for page in structured_data.get("pages", []):
            page_idx = int(page["page_idx"])
            page_num = page_idx + 1
            pil_page = pdf_doc[page_idx].render(scale=2.0).to_pil().convert("RGB")
            page_arr = np.array(pil_page)

            blocks = _reorder_page_blocks(page.get("blocks", []), output_dir)
            prev_bg_on_page: BgKind | None = None
            prev_cream_y1: float | None = None
            expect_lesson_title = False
            just_had_icon_on_page = False

            for block in blocks:
                btype = str(block.get("type", "text"))
                if btype == "page_number":
                    continue

                content = str(block.get("content", "")).strip()
                norm = _normalize_ascii(content)

                # Lọc chữ chìm Watermark và ký tự rác
                if btype in ("paragraph_title", "header", "doc_title"):
                    if content in ("#", "-") or any(w in norm for w in _WATERMARK_SUBSTRINGS):
                        continue

                # Nhận diện Chương
                if btype in ("header", "paragraph_title", "doc_title") and norm.startswith("CHUONG "):
                    chap_title = content.replace("\n", ": ")
                    current_chapter = chap_title
                    info_entry = {
                        "title": chap_title,
                        "page": page_num,
                        "bbox": block.get("bbox", []),
                    }
                    chapters_info.append(info_entry)
                    if not chapter_name:
                        chapter_name = chap_title
                        chapter_info = info_entry
                    continue

                # Nhận diện tiêu đề Bài mới ("Bài 2", "Bài 3", ..., hoặc "Bài" khi OCR sót số)
                if _is_lesson_header_block(block):
                    m_bai = re.match(r"^BAI(?:\s+(\d+))?$", norm)
                    assert m_bai is not None
                    if m_bai.group(1):
                        last_lesson_num = int(m_bai.group(1))
                        lesson_id = f"Bài {last_lesson_num}"
                    elif last_lesson_num is not None:
                        last_lesson_num += 1
                        lesson_id = f"Bài {last_lesson_num}"
                    else:
                        lesson_id = content

                    current_lesson = {
                        "chapter": current_chapter,
                        "lesson_id": lesson_id,
                        "lesson_title": "",
                        "page": page_num,
                        "id_bbox": block.get("bbox", []),
                        "pages": [page_num],
                        "sections": [],
                    }
                    lessons.append(current_lesson)
                    current_section = None
                    current_unit = None
                    expect_lesson_title = True
                    continue

                if expect_lesson_title and btype in ("doc_title", "header", "paragraph_title") and block["bbox"][1] < 0.25:
                    assert current_lesson is not None
                    current_lesson["lesson_title"] = content
                    current_lesson["title_bbox"] = block.get("bbox", [])
                    expect_lesson_title = False
                    continue
                expect_lesson_title = False

                if current_lesson is not None and page_num not in current_lesson["pages"]:
                    current_lesson["pages"].append(page_num)

                # Xử lý các tiêu đề Mục bằng chữ
                if btype in ("paragraph_title", "doc_title"):
                    if norm == "MUC TIEU":
                        _open_section("MUC_TIEU", "MỤC TIÊU", page_num=page_num, bbox=block.get("bbox", []))
                        just_had_icon_on_page = False
                        continue
                    if norm == "EM DA HOC":
                        _open_section("EM_DA_HOC", "EM ĐÃ HỌC", page_num=page_num, bbox=block.get("bbox", []))
                        just_had_icon_on_page = False
                        continue
                    if norm == "EM CO THE":
                        _open_section("EM_CO_THE", "EM CÓ THỂ", page_num=page_num, bbox=block.get("bbox", []))
                        just_had_icon_on_page = False
                        continue
                    roman_match = re.match(r"^([IVX]+)\s*[-–—]\s*(.+)$", content)
                    if roman_match:
                        _open_section(
                            "NOI_DUNG_CHINH",
                            content,
                            page_num=page_num,
                            bbox=block.get("bbox", []),
                            section_id=roman_match.group(1),
                        )
                        prev_bg_on_page = None
                        prev_cream_y1 = None
                        just_had_icon_on_page = False
                        continue

                # Xử lý Icon chỉ dẫn được MinerU cắt thành block image nhỏ ở lề trái
                if _is_left_icon_candidate(block):
                    icon_src = str(block.get("image_source", ""))
                    icon_kind = _classify_icon_image(output_dir / icon_src)
                    icon_bbox = block.get("bbox", [])
                    if icon_kind == "MO_DAU":
                        _open_section(
                            "MO_DAU",
                            "MỞ ĐẦU",
                            page_num=page_num,
                            marker_icon=icon_src,
                            marker_bbox=icon_bbox,
                        )
                        just_had_icon_on_page = True
                        continue
                    if icon_kind == "HOAT_DONG":
                        _open_unit("HOAT_DONG", page_num=page_num, marker_icon=icon_src, marker_bbox=icon_bbox)
                        just_had_icon_on_page = True
                        continue
                    if icon_kind == "CAU_HOI_BAI_TAP":
                        _open_unit("CAU_HOI_BAI_TAP", page_num=page_num, marker_icon=icon_src, marker_bbox=icon_bbox)
                        just_had_icon_on_page = True
                        continue
                    if icon_kind == "EM_CO_BIET":
                        if current_section and current_section.get("section_type") == "NOI_DUNG_CHINH":
                            _open_unit("EM_CO_BIET", page_num=page_num, marker_icon=icon_src, marker_bbox=icon_bbox)
                        else:
                            _open_section(
                                "EM_CO_BIET",
                                "EM CÓ BIẾT",
                                page_num=page_num,
                                marker_icon=icon_src,
                                marker_bbox=icon_bbox,
                            )
                        just_had_icon_on_page = True
                        continue

                # Xử lý ký tự "?" đứng riêng ở lề trái (có thể được gán text hoặc paragraph_title)
                if btype in ("text", "paragraph_title") and content == "?":
                    _open_unit("CAU_HOI_BAI_TAP", page_num=page_num, marker_text="?", marker_bbox=block.get("bbox", []))
                    just_had_icon_on_page = True
                    continue

                # Kiểm tra màu nền của các khối văn bản / công thức / bảng / tiêu đề con để đóng/mở khung
                if btype in ("text", "equation", "table", "paragraph_title"):
                    bg = _classify_block_bg(page_arr, block["bbox"])
                    if current_section and current_section.get("section_type") == "NOI_DUNG_CHINH":
                        if bg == "WHITE":
                            if current_unit is None or current_unit.get("unit_type") != "LY_THUYET":
                                _open_unit("LY_THUYET", page_num=page_num)
                        elif bg == "CYAN_EXTEND":
                            if current_unit is None or current_unit.get("unit_type") != "EM_CO_BIET":
                                _open_unit("EM_CO_BIET", page_num=page_num)
                        elif bg == "CREAM":
                            need_new_cream_box = False
                            if not just_had_icon_on_page:
                                if prev_bg_on_page != "CREAM":
                                    need_new_cream_box = True
                                elif prev_cream_y1 is not None and (block["bbox"][1] - prev_cream_y1) > 0.035:
                                    need_new_cream_box = True

                            if need_new_cream_box:
                                detected_type = _probe_pdf_corner_icon(page_arr, block["bbox"][1])
                                _open_unit(detected_type, page_num=page_num, marker_text=f"[auto:{detected_type}]")
                            elif current_unit is None or current_unit.get("unit_type") not in ("HOAT_DONG", "CAU_HOI_BAI_TAP"):
                                detected_type = _probe_pdf_corner_icon(page_arr, block["bbox"][1])
                                _open_unit(detected_type, page_num=page_num, marker_text=f"[auto:{detected_type}]")

                        if bg == "CREAM":
                            prev_cream_y1 = block["bbox"][3]
                        prev_bg_on_page = bg

                just_had_icon_on_page = False
                _append_item(block, page_num)
    finally:
        pdf_doc.close()

    hierarchical = {
        "document_title": structured_data.get("metadata", {}).get("document", {}).get("title", pdf_path.name),
        "chapter": " | ".join(dict.fromkeys(c["title"] for c in chapters_info)) if chapters_info else chapter_name,
        "chapter_info": chapter_info,
        "chapters": chapters_info,
        "lessons": lessons,
    }
    return enrich_hierarchical_content(hierarchical)


def _lesson_code_prefix(lesson_id: str, fallback_idx: int) -> str:
    """Chuyển 'Bài 2' thành mã tiền tố chuẩn 'BAI2'."""
    m = re.search(r"\d+", lesson_id)
    return f"BAI{m.group(0)}" if m else f"BAI_{fallback_idx}"


def _normalize_latex_expr(expr: str) -> str:
    """Chuẩn hóa biểu thức LaTeX (bỏ khoảng trắng và \\mathrm/\\mathcal) để gom chung mã công thức."""
    s = re.sub(r"\\(?:mathrm|mathcal|text|textbf|mathit)\s*", "", expr)
    s = re.sub(r"\s+", "", s)
    s = re.sub(r"\{([a-zA-Z0-9])\}", r"\1", s)
    return s.lower()


def _extract_tail_sentences(items: list[dict[str, Any]], max_sentences: int = 2) -> str:
    """Lấy 1-2 câu văn cuối cùng của một khối làm ngữ cảnh trượt (prev_context)."""
    texts = [
        re.sub(r"\s+", " ", str(it.get("content", ""))).strip()
        for it in items
        if it.get("type") in ("text", "paragraph_title") and str(it.get("content", "")).strip()
    ]
    if not texts:
        return ""
    joined = " ".join(texts)
    parts = [p.strip() for p in re.split(r"(?<=[.?!:;])\s+", joined) if p.strip()]
    tail = " ".join(parts[-max_sentences:]) if parts else joined
    return tail[-350:] if len(tail) > 350 else tail


def _find_adjacent_caption(items: list[dict[str, Any]], idx: int, prefix_regex: str) -> str | None:
    """Tìm chú thích đứng ngay sau hoặc ngay trước một khối ảnh/bảng nếu caption đang rỗng."""
    for neighbor_idx in (idx + 1, idx - 1):
        if 0 <= neighbor_idx < len(items):
            nb = items[neighbor_idx]
            if nb.get("type") in ("text", "paragraph_title"):
                txt = re.sub(r"\s+", " ", str(nb.get("content", ""))).strip()
                if len(txt) < 160 and re.match(prefix_regex, txt, re.IGNORECASE):
                    return txt
    return None


def enrich_hierarchical_content(hierarchical: dict[str, Any]) -> dict[str, Any]:
    """Bước 2: Gắn unit_id, extraction_tier, breadcrumb, prev_context, formula_id, figure_id, table_id và refers_to."""
    global_fig_map: dict[str, list[str]] = {}
    global_tbl_map: dict[str, list[str]] = {}

    for l_idx, lesson in enumerate(hierarchical.get("lessons", []), start=1):
        l_code = _lesson_code_prefix(str(lesson.get("lesson_id", "")), l_idx)
        l_chapter = str(lesson.get("chapter") or hierarchical.get("chapter") or "").strip()
        l_title = str(lesson.get("lesson_title", "")).replace("\n", " ").strip()
        l_id_str = str(lesson.get("lesson_id", "")).strip()

        formula_norm_to_id: dict[str, str] = {}
        eq_counter = 0
        fig_counter = 0
        tbl_counter = 0
        used_fig_ids: set[str] = set()
        used_tbl_ids: set[str] = set()
        lesson_fig_map: dict[str, list[str]] = {}
        lesson_tbl_map: dict[str, list[str]] = {}

        # Lượt 1: Đánh mã unit_id, section_uid, extraction_tier, breadcrumb, prev_context và định danh Formula/Figure/Table
        prev_context_text = ""
        for s_idx, sec in enumerate(lesson.get("sections", []), start=1):
            sec_type = str(sec.get("section_type", ""))
            sec_id = sec.get("section_id")
            sec_uid = f"{l_code}_MUC_{sec_id}" if sec_id else f"{l_code}_SEC_{s_idx}"
            sec["section_uid"] = sec_uid
            sec_title = str(sec.get("title", sec_type)).replace("\n", " ").strip()
            breadcrumb_base = " > ".join(
                part for part in (l_chapter, f"{l_id_str}: {l_title}".strip(": "), sec_title) if part
            )

            if sec_type != "NOI_DUNG_CHINH":
                sec["extraction_tier"] = _EXTRACTION_TIER_MAP.get(sec_type, "METADATA_ONLY")
                sec["breadcrumb"] = breadcrumb_base
                sec["prev_context"] = prev_context_text if sec_type != "MUC_TIEU" else ""

            containers = [(sec, u) for u in sec.get("units", [])] if sec_type == "NOI_DUNG_CHINH" else [(sec, None)]
            for u_idx, (_, unit) in enumerate(containers, start=1):
                target = unit if unit is not None else sec
                if unit is not None:
                    u_type = str(unit.get("unit_type", "LY_THUYET"))
                    unit["unit_id"] = f"{sec_uid}_UNIT_{u_idx}"
                    unit["extraction_tier"] = _EXTRACTION_TIER_MAP.get(u_type, "TIER_1_CORE")
                    unit["breadcrumb"] = f"{breadcrumb_base} > {u_type}"
                    unit["prev_context"] = prev_context_text

                items = target.get("items", [])
                for i_idx, item in enumerate(items):
                    itype = item.get("type")
                    if itype == "equation":
                        raw_latex = str(item.get("content", "")).strip()
                        norm_expr = _normalize_latex_expr(raw_latex)
                        if norm_expr and norm_expr in formula_norm_to_id:
                            fid = formula_norm_to_id[norm_expr]
                        else:
                            eq_counter += 1
                            fid = f"{l_code}_EQ{eq_counter}"
                            if norm_expr:
                                formula_norm_to_id[norm_expr] = fid
                        item["formula_id"] = fid

                    elif itype == "image":
                        caps = list(item.get("captions") or [])
                        foots = list(item.get("footnotes") or [])
                        if not caps and not foots:
                            adj = _find_adjacent_caption(items, i_idx, r"^Hình\s+\d+\.\d+")
                            if adj:
                                caps = [adj]
                                item["captions"] = caps
                        combined_cap = " ".join(caps + foots)
                        m_fig = re.search(r"Hình\s+(\d+\.\d+)", combined_cap, re.IGNORECASE)
                        if m_fig:
                            fig_num = m_fig.group(1)
                            base_id = f"{l_code}_HINH_{fig_num}"
                            fig_id = base_id
                            dup_idx = 2
                            while fig_id in used_fig_ids:
                                fig_id = f"{base_id}_{dup_idx}"
                                dup_idx += 1
                            used_fig_ids.add(fig_id)
                            lesson_fig_map.setdefault(fig_num, []).append(fig_id)
                            global_fig_map.setdefault(fig_num, []).append(fig_id)
                        else:
                            fig_counter += 1
                            fig_id = f"{l_code}_FIG_{fig_counter}"
                            while fig_id in used_fig_ids:
                                fig_counter += 1
                                fig_id = f"{l_code}_FIG_{fig_counter}"
                            used_fig_ids.add(fig_id)
                        item["figure_id"] = fig_id
                        item["visual_description"] = item.get("visual_description")

                    elif itype == "table":
                        caps = list(item.get("captions") or [])
                        foots = list(item.get("footnotes") or [])
                        if not caps and not foots:
                            adj = _find_adjacent_caption(items, i_idx, r"^Bảng\s+\d+\.\d+")
                            if adj:
                                caps = [adj]
                                item["captions"] = caps
                        combined_cap = " ".join(caps + foots)
                        m_tbl = re.search(r"Bảng\s+(\d+\.\d+)", combined_cap, re.IGNORECASE)
                        if m_tbl:
                            tbl_num = m_tbl.group(1)
                            base_id = f"{l_code}_BANG_{tbl_num}"
                            tbl_id = base_id
                            dup_idx = 2
                            while tbl_id in used_tbl_ids:
                                tbl_id = f"{base_id}_{dup_idx}"
                                dup_idx += 1
                            used_tbl_ids.add(tbl_id)
                            lesson_tbl_map.setdefault(tbl_num, []).append(tbl_id)
                            global_tbl_map.setdefault(tbl_num, []).append(tbl_id)
                        else:
                            tbl_counter += 1
                            tbl_id = f"{l_code}_TBL_{tbl_counter}"
                            while tbl_id in used_tbl_ids:
                                tbl_counter += 1
                                tbl_id = f"{l_code}_TBL_{tbl_counter}"
                            used_tbl_ids.add(tbl_id)
                        item["table_id"] = tbl_id

                tail_text = _extract_tail_sentences(items)
                if tail_text:
                    prev_context_text = tail_text

        # Lượt 2: Đối chiếu tham chiếu chéo trong các đoạn văn (Hình X.Y, Bảng X.Y, công thức nội dòng $...= ...$)
        for sec in lesson.get("sections", []):
            targets = sec.get("units", []) if sec.get("section_type") == "NOI_DUNG_CHINH" else [sec]
            for target in targets:
                for item in target.get("items", []):
                    if item.get("type") not in ("text", "paragraph_title"):
                        continue
                    txt = str(item.get("content", ""))
                    if not txt:
                        continue

                    fig_refs: list[str] = []
                    for fnum in re.findall(r"Hình\s+(\d+\.\d+)", txt, re.IGNORECASE):
                        for fid in lesson_fig_map.get(fnum) or global_fig_map.get(fnum, []):
                            if fid not in fig_refs:
                                fig_refs.append(fid)
                    if fig_refs:
                        item["refers_to_figures"] = fig_refs

                    tbl_refs: list[str] = []
                    for tnum in re.findall(r"Bảng\s+(\d+\.\d+)", txt, re.IGNORECASE):
                        for tid in lesson_tbl_map.get(tnum) or global_tbl_map.get(tnum, []):
                            if tid not in tbl_refs:
                                tbl_refs.append(tid)
                    if tbl_refs:
                        item["refers_to_tables"] = tbl_refs

                    eq_refs: list[str] = []
                    for inline_eq in re.findall(r"\$([^$]+?=[^$]+?)\$", txt):
                        norm_in = _normalize_latex_expr(inline_eq)
                        if norm_in in formula_norm_to_id:
                            eq_id = formula_norm_to_id[norm_in]
                            if eq_id not in eq_refs:
                                eq_refs.append(eq_id)
                    if eq_refs:
                        item["refers_to_formulas"] = eq_refs

    return hierarchical


def build_skeleton_mkg(hierarchical: dict[str, Any]) -> dict[str, Any]:
    """Xuất đồ thị khung xương Tầng 1 & Tầng 2 (Nodes + Edges) từ hierarchical_content đã làm giàu."""
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_node_ids: set[str] = set()
    seen_edges: set[tuple[str, str, str]] = set()

    def _add_node(node_id: str, label: str, **props: Any) -> None:
        if node_id in seen_node_ids:
            return
        seen_node_ids.add(node_id)
        node = {"id": node_id, "label": label}
        for k, v in props.items():
            if v is not None and v != "":
                node[k] = v
        nodes.append(node)

    def _add_edge(source: str, relation: str, target: str) -> None:
        key = (source, relation, target)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edges.append({"source": source, "relation": relation, "target": target})

    chapter_id_map: dict[str, str] = {}
    chap_counter = 0

    for l_idx, lesson in enumerate(hierarchical.get("lessons", []), start=1):
        l_code = _lesson_code_prefix(str(lesson.get("lesson_id", "")), l_idx)
        chap_title = str(lesson.get("chapter") or hierarchical.get("chapter") or "Chương").strip()
        if chap_title not in chapter_id_map:
            chap_counter += 1
            chap_id = f"CHAPTER_{chap_counter}"
            chapter_id_map[chap_title] = chap_id
            _add_node(chap_id, "Chapter", title=chap_title)
        chap_id = chapter_id_map[chap_title]

        lesson_node_id = l_code
        _add_node(
            lesson_node_id,
            "Lesson",
            lesson_id=lesson.get("lesson_id", ""),
            title=lesson.get("lesson_title", ""),
            pages=lesson.get("pages", []),
        )
        _add_edge(chap_id, "HAS_LESSON", lesson_node_id)

        prev_unit_node_id: str | None = None
        for sec in lesson.get("sections", []):
            sec_uid = str(sec.get("section_uid", f"{l_code}_SEC"))
            sec_type = str(sec.get("section_type", ""))
            _add_node(
                sec_uid,
                "Section",
                section_type=sec_type,
                title=sec.get("title", ""),
                page=sec.get("page"),
                extraction_tier=sec.get("extraction_tier"),
            )
            _add_edge(lesson_node_id, "HAS_SECTION", sec_uid)

            containers = sec.get("units", []) if sec_type == "NOI_DUNG_CHINH" else [sec]
            for container in containers:
                is_unit = "unit_id" in container
                node_id = str(container.get("unit_id") if is_unit else sec_uid)
                if is_unit:
                    _add_node(
                        node_id,
                        "PedagogicalUnit",
                        unit_type=container.get("unit_type", ""),
                        extraction_tier=container.get("extraction_tier", ""),
                        breadcrumb=container.get("breadcrumb", ""),
                        prev_context=container.get("prev_context", ""),
                        page=container.get("page"),
                    )
                    _add_edge(sec_uid, "HAS_UNIT", node_id)
                    if prev_unit_node_id is not None:
                        _add_edge(prev_unit_node_id, "NEXT_UNIT", node_id)
                    prev_unit_node_id = node_id

                for item in container.get("items", []):
                    if item.get("formula_id"):
                        fid = str(item["formula_id"])
                        _add_node(
                            fid,
                            "Formula",
                            latex=item.get("content", ""),
                            image_source=item.get("image_source"),
                            page=item.get("page"),
                            bbox=item.get("bbox"),
                        )
                        _add_edge(node_id, "HAS_FORMULA", fid)
                    if item.get("figure_id"):
                        fig_id = str(item["figure_id"])
                        _add_node(
                            fig_id,
                            "Figure",
                            image_source=item.get("image_source"),
                            captions=item.get("captions", []),
                            footnotes=item.get("footnotes", []),
                            visual_description=item.get("visual_description"),
                            page=item.get("page"),
                            bbox=item.get("bbox"),
                        )
                        _add_edge(node_id, "HAS_FIGURE", fig_id)
                    if item.get("table_id"):
                        tid = str(item["table_id"])
                        _add_node(
                            tid,
                            "Table",
                            markdown=item.get("content", ""),
                            captions=item.get("captions", []),
                            image_source=item.get("image_source"),
                            page=item.get("page"),
                            bbox=item.get("bbox"),
                        )
                        _add_edge(node_id, "HAS_TABLE", tid)

                    for ref_fig in item.get("refers_to_figures", []):
                        _add_edge(node_id, "REFERS_TO_FIGURE", str(ref_fig))
                    for ref_tbl in item.get("refers_to_tables", []):
                        _add_edge(node_id, "REFERS_TO_TABLE", str(ref_tbl))
                    for ref_eq in item.get("refers_to_formulas", []):
                        _add_edge(node_id, "REFERS_TO_FORMULA", str(ref_eq))

    label_counts: dict[str, int] = {}
    for n in nodes:
        lbl = n["label"]
        label_counts[lbl] = label_counts.get(lbl, 0) + 1
    rel_counts: dict[str, int] = {}
    for e in edges:
        rel = e["relation"]
        rel_counts[rel] = rel_counts.get(rel, 0) + 1

    return {
        "document_title": hierarchical.get("document_title", ""),
        "stats": {
            "total_nodes": len(nodes),
            "total_edges": len(edges),
            "nodes_by_label": label_counts,
            "edges_by_relation": rel_counts,
        },
        "nodes": nodes,
        "edges": edges,
    }


def save_hierarchical_json(output_dir: Path, pdf_path: Path) -> Path:
    """Đọc structured_content.json trong output_dir, lưu hierarchical_content.json và skeleton_mkg.json."""
    structured_path = output_dir / "structured_content.json"
    structured_data = json.loads(structured_path.read_text(encoding="utf-8"))
    hierarchical = build_hierarchical_content(structured_data, pdf_path, output_dir)
    out_path = output_dir / "hierarchical_content.json"
    out_path.write_text(json.dumps(hierarchical, ensure_ascii=False, indent=2), encoding="utf-8")

    skeleton = build_skeleton_mkg(hierarchical)
    skeleton_path = output_dir / "skeleton_mkg.json"
    skeleton_path.write_text(json.dumps(skeleton, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path


def main() -> None:
    """Chạy chuyển đổi trực tiếp cho output/<tên> mà không cần chạy lại MinerU."""
    import sys

    project_root = Path(__file__).resolve().parent.parent
    if len(sys.argv) >= 3:
        pdf_path = Path(sys.argv[1]).resolve()
        output_dir = Path(sys.argv[2]).resolve()
    elif len(sys.argv) == 2:
        pdf_path = Path(sys.argv[1]).resolve()
        output_dir = project_root / "output" / pdf_path.stem
    else:
        pdf_path = project_root / "data" / "KHTN_9_Chuong2,3.pdf"
        output_dir = project_root / "output" / "KHTN_9_Chuong2,3"
    out_path = save_hierarchical_json(output_dir, pdf_path)
    print(f"Đã tạo JSON phân cấp và khung xương MKG thành công tại: {out_path}", flush=True)


if __name__ == "__main__":
    main()
