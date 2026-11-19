# Copyright (c) Opendatalab. All rights reserved.
"""Sinh giao diện HTML trực quan đối chiếu Cây phân cấp (hierarchical_content.json) với BBox trên PDF."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
from PIL import Image, ImageDraw, ImageFont

ROLE_STYLES: dict[str, dict[str, Any]] = {
    "LESSON_HEADER": {"label": "Chương / Tên Bài", "icon": "📘", "hex": "#15803D", "rgb": (21, 128, 61)},
    "SECTION_TITLE": {"label": "Tiêu đề Mục (I, II...)", "icon": "📑", "hex": "#0F766E", "rgb": (15, 118, 110)},
    "MARKER": {"label": "Icon chỉ dẫn", "icon": "🏷️", "hex": "#F59E0B", "rgb": (245, 158, 11)},
    "MUC_TIEU": {"label": "Mục tiêu", "icon": "🎯", "hex": "#0D9488", "rgb": (13, 148, 136)},
    "MO_DAU": {"label": "Mở đầu", "icon": "👆", "hex": "#EA580C", "rgb": (234, 88, 12)},
    "LY_THUYET": {"label": "Lý thuyết", "icon": "📖", "hex": "#2563EB", "rgb": (37, 99, 235)},
    "HOAT_DONG": {"label": "Hoạt động", "icon": "✋", "hex": "#D97706", "rgb": (217, 119, 6)},
    "CAU_HOI_BAI_TAP": {"label": "Câu hỏi & Bài tập", "icon": "❓", "hex": "#9333EA", "rgb": (147, 51, 234)},
    "EM_DA_HOC": {"label": "Em đã học", "icon": "📌", "hex": "#0284C7", "rgb": (2, 132, 199)},
    "EM_CO_THE": {"label": "Em có thể", "icon": "💡", "hex": "#DB2777", "rgb": (219, 39, 119)},
    "EM_CO_BIET": {"label": "Em có biết", "icon": "🔭", "hex": "#059669", "rgb": (5, 150, 105)},
}
DEFAULT_STYLE = {"label": "Khác", "icon": "📄", "hex": "#64748B", "rgb": (100, 116, 139)}


def _load_font(size: int = 16) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Tải font hệ thống Windows hỗ trợ tiếng Việt, fallback sang font mặc định."""
    for font_name in ("segoeui.ttf", "arial.ttf", "tahoma.ttf"):
        font_path = Path("C:/Windows/Fonts") / font_name
        if font_path.is_file():
            try:
                return ImageFont.truetype(str(font_path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _union_bboxes(bboxes: list[list[float]]) -> list[float] | None:
    """Tính hộp bao hợp nhất của danh sách bbox."""
    valid = [b for b in bboxes if b and len(b) == 4]
    if not valid:
        return None
    return [
        min(b[0] for b in valid),
        min(b[1] for b in valid),
        max(b[2] for b in valid),
        max(b[3] for b in valid),
    ]


def _collect_boxes_by_page(
    hierarchical_data: dict[str, Any],
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, list[dict[str, Any]]]]:
    """Gom toàn bộ Tiêu đề, Icon, Item và Khung bao vùng (Section/Unit) theo từng trang."""
    boxes_by_page: dict[int, list[dict[str, Any]]] = {}
    regions_by_page: dict[int, list[dict[str, Any]]] = {}

    chapters = hierarchical_data.get("chapters") or (
        [hierarchical_data["chapter_info"]] if hierarchical_data.get("chapter_info") else []
    )
    for chap in chapters:
        if chap and chap.get("bbox"):
            pg = int(chap.get("page", 1))
            boxes_by_page.setdefault(pg, []).append(
                {
                    "role": "LESSON_HEADER",
                    "label": "CHUONG",
                    "bbox": chap["bbox"],
                    "thick": True,
                }
            )

    for lesson in hierarchical_data.get("lessons", []):
        l_pg = int(lesson.get("page", (lesson.get("pages") or [1])[0]))
        if lesson.get("id_bbox"):
            boxes_by_page.setdefault(l_pg, []).append(
                {"role": "LESSON_HEADER", "label": lesson.get("lesson_id", "BAI"), "bbox": lesson["id_bbox"], "thick": True}
            )
        if lesson.get("title_bbox"):
            boxes_by_page.setdefault(l_pg, []).append(
                {"role": "LESSON_HEADER", "label": "TEN_BAI", "bbox": lesson["title_bbox"], "thick": True}
            )

        for sec in lesson.get("sections", []):
            sec_type = str(sec.get("section_type", ""))
            sec_title = str(sec.get("title", sec_type))
            sec_bboxes_by_pg: dict[int, list[list[float]]] = {}

            if sec.get("bbox"):
                s_pg = int(sec.get("page", l_pg))
                tag = f"MUC {sec['section_id']}" if sec.get("section_id") else sec_type
                boxes_by_page.setdefault(s_pg, []).append(
                    {"role": "SECTION_TITLE", "label": tag, "bbox": sec["bbox"], "thick": True}
                )
                sec_bboxes_by_pg.setdefault(s_pg, []).append(sec["bbox"])

            if sec.get("marker_bbox"):
                m_pg = int(sec.get("page", l_pg))
                boxes_by_page.setdefault(m_pg, []).append(
                    {"role": "MARKER", "label": f"ICON_{sec_type}", "bbox": sec["marker_bbox"], "thick": False}
                )
                sec_bboxes_by_pg.setdefault(m_pg, []).append(sec["marker_bbox"])

            if sec_type == "NOI_DUNG_CHINH":
                for unit in sec.get("units", []):
                    unit_type = str(unit.get("unit_type", "LY_THUYET"))
                    unit_bboxes_by_pg: dict[int, list[list[float]]] = {}

                    if unit.get("marker_bbox"):
                        u_pg = int(unit.get("page", l_pg))
                        boxes_by_page.setdefault(u_pg, []).append(
                            {"role": "MARKER", "label": f"ICON_{unit_type}", "bbox": unit["marker_bbox"], "thick": False}
                        )
                        unit_bboxes_by_pg.setdefault(u_pg, []).append(unit["marker_bbox"])
                        sec_bboxes_by_pg.setdefault(u_pg, []).append(unit["marker_bbox"])

                    for item in unit.get("items", []):
                        page_num = int(item.get("page", 1))
                        bbox = item.get("bbox", [])
                        boxes_by_page.setdefault(page_num, []).append(
                            {"role": unit_type, "label": unit_type, "bbox": bbox, "thick": False}
                        )
                        unit_bboxes_by_pg.setdefault(page_num, []).append(bbox)
                        sec_bboxes_by_pg.setdefault(page_num, []).append(bbox)

                    for pg, b_list in unit_bboxes_by_pg.items():
                        ub = _union_bboxes(b_list)
                        if ub:
                            regions_by_page.setdefault(pg, []).append(
                                {"role": unit_type, "label": unit_type, "bbox": ub, "is_section": False}
                            )
            else:
                for item in sec.get("items", []):
                    page_num = int(item.get("page", 1))
                    bbox = item.get("bbox", [])
                    boxes_by_page.setdefault(page_num, []).append(
                        {"role": sec_type, "label": sec_type, "bbox": bbox, "thick": False}
                    )
                    sec_bboxes_by_pg.setdefault(page_num, []).append(bbox)

            # Khung bao toàn bộ Section trên từng trang
            for pg, b_list in sec_bboxes_by_pg.items():
                sb = _union_bboxes(b_list)
                if sb:
                    regions_by_page.setdefault(pg, []).append(
                        {
                            "role": "SECTION_TITLE" if sec_type == "NOI_DUNG_CHINH" else sec_type,
                            "label": sec_title,
                            "bbox": sb,
                            "is_section": True,
                        }
                    )

    return boxes_by_page, regions_by_page


def _draw_dashed_rect(
    draw: ImageDraw.ImageDraw,
    rect: tuple[float, float, float, float],
    color: tuple[int, int, int, int],
    width: int = 3,
    dash: int = 12,
    gap: int = 7,
) -> None:
    """Vẽ khung chữ nhật nét đứt để phân định ranh giới vùng Mục/Khối."""
    x0, y0, x1, y1 = rect
    for x in range(int(x0), int(x1), dash + gap):
        draw.line([(x, y0), (min(x + dash, x1), y0)], fill=color, width=width)
        draw.line([(x, y1), (min(x + dash, x1), y1)], fill=color, width=width)
    for y in range(int(y0), int(y1), dash + gap):
        draw.line([(x0, y), (x0, min(y + dash, y1))], fill=color, width=width)
        draw.line([(x1, y), (x1, min(y + dash, y1))], fill=color, width=width)


def _draw_role_overlay(
    page_img: Image.Image,
    page_boxes: list[dict[str, Any]],
    page_regions: list[dict[str, Any]],
    font: ImageFont.FreeTypeFont | ImageFont.ImageFont,
) -> Image.Image:
    """Vẽ khung bao vùng Mục (nét đứt) và khung BBox từng phần tử lên ảnh trang PDF."""
    base = page_img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    width, height = base.size

    # 1. Vẽ khung bao vùng Section (nét đứt đậm, đệm rộng ra ngoài một chút)
    for reg in page_regions:
        if not reg.get("is_section"):
            continue
        bbox = reg.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        role = str(reg.get("role", ""))
        style = ROLE_STYLES.get(role, DEFAULT_STYLE)
        rgb = style["rgb"]
        pad = 8
        rx0 = max(2.0, bbox[0] * width - pad)
        ry0 = max(2.0, bbox[1] * height - pad)
        rx1 = min(width - 2.0, bbox[2] * width + pad)
        ry1 = min(height - 2.0, bbox[3] * height + pad)
        _draw_dashed_rect(draw, (rx0, ry0, rx1, ry1), (*rgb, 210), width=3)

        reg_label = f"[VÙNG: {reg.get('label', role)}]"
        tb = draw.textbbox((0, 0), reg_label, font=font)
        tw, th = tb[2] - tb[0], tb[3] - tb[1]
        lx0 = max(2.0, rx1 - tw - 10)
        ly0 = max(2.0, ry0 - th - 4)
        draw.rectangle([lx0, ly0, lx0 + tw + 10, ly0 + th + 4], fill=(*rgb, 235))
        draw.text((lx0 + 5, ly0 + 2), reg_label, fill=(255, 255, 255, 255), font=font)

    # 2. Vẽ từng BBox (Tiêu đề, Icon, Văn bản, Công thức, Ảnh)
    for idx, entry in enumerate(page_boxes):
        bbox = entry.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        role = str(entry.get("role", ""))
        style = ROLE_STYLES.get(role, DEFAULT_STYLE)
        rgb = style["rgb"]
        is_thick = bool(entry.get("thick"))
        x0, y0, x1, y1 = (
            bbox[0] * width,
            bbox[1] * height,
            bbox[2] * width,
            bbox[3] * height,
        )
        draw.rectangle(
            [x0, y0, x1, y1],
            fill=(*rgb, 48 if is_thick else 28),
            outline=(*rgb, 245 if is_thick else 220),
            width=4 if is_thick else 2,
        )

        label = f"#{idx} {entry.get('label', role)}"
        text_bbox = draw.textbbox((0, 0), label, font=font)
        tw = text_bbox[2] - text_bbox[0]
        th = text_bbox[3] - text_bbox[1]
        pad_x, pad_y = 6, 2
        lx0 = max(0.0, min(x0, width - tw - pad_x * 2))
        ly0 = max(0.0, y0 - th - pad_y * 2)
        draw.rectangle(
            [lx0, ly0, lx0 + tw + pad_x * 2, ly0 + th + pad_y * 2],
            fill=(*rgb, 230),
        )
        draw.text((lx0 + pad_x, ly0 + pad_y), label, fill=(255, 255, 255, 255), font=font)

    return Image.alpha_composite(base, overlay).convert("RGB")


def _extract_annotation_text(blk: dict[str, Any]) -> str:
    """Ghép captions/footnotes hoặc nội dung block nếu có."""
    parts: list[str] = []
    for key in ("captions", "footnotes"):
        for item in blk.get(key, []) or []:
            txt = str(item.get("content", "")).strip()
            if txt:
                parts.append(txt)
    if not parts and blk.get("content"):
        parts.append(str(blk["content"]).strip())
    return " | ".join(parts)


def _copy_all_assets(src_images_dir: Path, dest_images_dir: Path) -> None:
    """Copy toàn bộ ảnh cắt từ output/images sang crop_preview/images."""
    dest_images_dir.mkdir(parents=True, exist_ok=True)
    if not src_images_dir.is_dir():
        return
    for img_file in src_images_dir.iterdir():
        if img_file.is_file():
            shutil.copy2(img_file, dest_images_dir / img_file.name)


def _build_pages_info(
    structured_data: dict[str, Any],
    boxes_by_page: dict[int, list[dict[str, Any]]],
    regions_by_page: dict[int, list[dict[str, Any]]],
    pdf_path: Path,
    preview_dir: Path,
    output_root: Path,
) -> list[dict[str, Any]]:
    """Render overlay cho tất cả các trang PDF và thu thập danh sách ảnh cắt theo trang."""
    font = _load_font(size=15)
    pdf_doc = pdfium.PdfDocument(str(pdf_path))
    pages_info: list[dict[str, Any]] = []

    try:
        for page in structured_data.get("pages", []):
            page_idx = int(page["page_idx"])
            page_num = page_idx + 1
            page_boxes = boxes_by_page.get(page_num, [])
            page_regions = regions_by_page.get(page_num, [])

            pdf_page = pdf_doc[page_idx]
            page_pil = pdf_page.render(scale=2.0).to_pil()
            overlay_img = _draw_role_overlay(page_pil, page_boxes, page_regions, font)
            overlay_filename = f"overlay_p{page_num}.png"
            overlay_img.save(preview_dir / overlay_filename)

            cropped_images: list[dict[str, Any]] = []
            for idx, blk in enumerate(page.get("blocks", [])):
                image_source = blk.get("image_source")
                if not image_source:
                    continue
                img_path = output_root / image_source
                if not img_path.is_file():
                    continue
                with Image.open(img_path) as crop_im:
                    cw, ch = crop_im.size
                bbox = blk.get("bbox", [0.0, 0.0, 0.0, 0.0])
                blk_type = str(blk.get("type", "image"))
                w_ratio = float(bbox[2] - bbox[0])
                h_ratio = float(bbox[3] - bbox[1])
                is_icon = blk_type == "image" and w_ratio < 0.10 and h_ratio < 0.06
                cropped_images.append(
                    {
                        "idx": idx,
                        "block_type": blk_type,
                        "filename": img_path.name,
                        "bbox": bbox,
                        "pixel_size": f"{cw} x {ch} px",
                        "is_icon": is_icon,
                        "content": _extract_annotation_text(blk),
                    }
                )

            pages_info.append(
                {
                    "page": page_num,
                    "overlay_file": overlay_filename,
                    "total_items": len(page_boxes),
                    "total_blocks": len(page.get("blocks", [])),
                    "images": cropped_images,
                }
            )
    finally:
        pdf_doc.close()

    return pages_info


def _build_html(
    hierarchical_data: dict[str, Any],
    pages_info: list[dict[str, Any]],
    skeleton_data: dict[str, Any] | None = None,
) -> str:
    """Sinh giao diện HTML 2 cột tương tác giữa Cây phân cấp, Khung xương MKG và Bản đồ BBox PDF."""
    hier_json = json.dumps(hierarchical_data, ensure_ascii=False)
    pages_json = json.dumps(pages_info, ensure_ascii=False)
    skeleton_json = json.dumps(skeleton_data or {}, ensure_ascii=False)
    styles_json = json.dumps(
        {k: {"label": v["label"], "icon": v["icon"], "hex": v["hex"]} for k, v in ROLE_STYLES.items()},
        ensure_ascii=False,
    )

    legend_badges = "".join(
        f'<span class="badge"><span class="dot" style="background:{v["hex"]};"></span> {v["icon"]} {v["label"]}</span>'
        for v in ROLE_STYLES.values()
    )

    return f"""<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <title>Visual Kiểm tra Cây phân cấp & BBox MinerU</title>
    <style>
        * {{ box-sizing: border-box; }}
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif; margin: 0; background: #f1f5f9; color: #1e293b; }}
        header {{ background: linear-gradient(135deg, #1e3a8a, #1d4ed8); color: white; padding: 12px 24px; display: flex; align-items: center; justify-content: space-between; gap: 16px; position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 6px rgba(0,0,0,0.15); }}
        .header-title h1 {{ margin: 0; font-size: 1.15rem; font-weight: 600; }}
        .header-sub {{ font-size: 0.82rem; opacity: 0.88; margin-top: 2px; }}
        .lesson-tabs {{ display: flex; gap: 8px; flex-wrap: wrap; }}
        .tab-btn {{ background: rgba(255,255,255,0.18); border: 1px solid rgba(255,255,255,0.25); color: white; padding: 7px 14px; border-radius: 6px; cursor: pointer; font-weight: 500; font-size: 13px; transition: all 0.15s; }}
        .tab-btn:hover {{ background: rgba(255,255,255,0.28); }}
        .tab-btn.active {{ background: white; color: #1e3a8a; font-weight: 700; border-color: white; }}
        .legend {{ display: flex; flex-wrap: wrap; gap: 14px; font-size: 12.5px; background: white; padding: 8px 24px; border-bottom: 1px solid #e2e8f0; align-items: center; }}
        .badge {{ display: inline-flex; align-items: center; gap: 5px; font-weight: 500; }}
        .dot {{ width: 11px; height: 11px; border-radius: 3px; display: inline-block; }}
        .workspace {{ display: flex; padding: 14px; gap: 14px; height: calc(100vh - 102px); overflow: hidden; }}
        .pane-left {{ flex: 1.08; background: white; border-radius: 10px; padding: 14px 16px; overflow-y: auto; box-shadow: 0 1px 4px rgba(0,0,0,0.08); }}
        .pane-right {{ flex: 1; background: white; border-radius: 10px; padding: 14px 16px; overflow-y: auto; box-shadow: 0 1px 4px rgba(0,0,0,0.08); display: flex; flex-direction: column; }}
        .lesson-banner {{ background: #eff6ff; border-left: 4px solid #2563eb; padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; cursor: pointer; transition: background 0.15s; }}
        .lesson-banner:hover {{ background: #dbeafe; }}
        .lesson-banner h2 {{ margin: 0 0 4px 0; font-size: 1.05rem; color: #1e3a8a; }}
        .lesson-banner .meta {{ font-size: 12.5px; color: #475569; }}
        .section-card {{ border: 1px solid #e2e8f0; border-radius: 8px; margin-bottom: 12px; overflow: hidden; }}
        .section-header {{ padding: 9px 12px; font-weight: 600; font-size: 14px; display: flex; align-items: center; justify-content: space-between; cursor: pointer; background: #f8fafc; border-bottom: 1px solid #e2e8f0; transition: background 0.15s; }}
        .section-header:hover {{ background: #eff6ff; }}
        .section-header.selected {{ background: #dbeafe; }}
        .toggle-btn {{ border: 1px solid #cbd5e1; background: white; color: #334155; border-radius: 4px; width: 24px; height: 22px; font-size: 12px; cursor: pointer; line-height: 1; }}
        .section-header .left {{ display: flex; align-items: center; gap: 8px; }}
        .role-pill {{ color: white; padding: 2px 8px; border-radius: 4px; font-size: 11.5px; font-weight: 600; letter-spacing: 0.2px; }}
        .section-body {{ padding: 10px 12px; background: white; }}
        .unit-box {{ border: 1px solid #e2e8f0; border-left-width: 4px; border-radius: 6px; padding: 8px 10px; margin-bottom: 10px; background: #fcfcfd; }}
        .unit-box:last-child {{ margin-bottom: 0; }}
        .unit-header {{ display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; font-size: 12.5px; font-weight: 600; cursor: pointer; padding: 3px 6px; border-radius: 4px; }}
        .unit-header:hover {{ background: #f1f5f9; }}
        .item-row {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; margin-bottom: 7px; background: white; cursor: pointer; transition: all 0.15s; position: relative; }}
        .item-row:last-child {{ margin-bottom: 0; }}
        .item-row:hover {{ border-color: #3b82f6; background: #f8fafc; transform: translateX(2px); }}
        .item-row.selected {{ border-color: #2563eb; background: #eff6ff; box-shadow: 0 0 0 2px rgba(37,99,235,0.2); }}
        .item-meta {{ display: flex; align-items: center; justify-content: space-between; font-size: 11.5px; color: #64748b; margin-bottom: 4px; }}
        .item-type {{ font-weight: 600; text-transform: uppercase; background: #f1f5f9; padding: 1px 6px; border-radius: 3px; color: #334155; }}
        .page-pill {{ background: #e0e7ff; color: #3730a3; padding: 1px 7px; border-radius: 10px; font-weight: 600; }}
        .item-text {{ font-size: 13.5px; line-height: 1.5; white-space: pre-wrap; color: #1e293b; }}
        .item-eq {{ font-family: "Consolas", "Courier New", monospace; background: #f0fdfa; border: 1px solid #99f6e4; padding: 6px 8px; border-radius: 4px; font-size: 12.5px; color: #0f766e; margin-bottom: 6px; overflow-x: auto; }}
        .item-img {{ max-width: 240px; max-height: 150px; object-fit: contain; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; background: white; display: block; margin-top: 4px; }}
        .item-caption {{ font-size: 12px; color: #475569; font-style: italic; margin-top: 4px; }}
        .right-toolbar {{ display: flex; flex-wrap: wrap; justify-content: space-between; align-items: center; gap: 8px; border-bottom: 2px solid #e2e8f0; padding-bottom: 10px; margin-bottom: 10px; }}
        .page-pills {{ display: flex; flex-wrap: wrap; gap: 5px; }}
        .pg-btn {{ border: 1px solid #cbd5e1; background: #f8fafc; color: #334155; padding: 4px 10px; border-radius: 5px; cursor: pointer; font-size: 12.5px; font-weight: 500; }}
        .pg-btn.in-lesson {{ border-color: #93c5fd; background: #eff6ff; color: #1d4ed8; }}
        .pg-btn.active {{ background: #2563eb; color: white; border-color: #2563eb; font-weight: 700; }}
        .view-switch {{ display: flex; gap: 6px; }}
        .mode-btn {{ border: 1px solid #cbd5e1; background: white; padding: 5px 10px; border-radius: 5px; cursor: pointer; font-size: 12.5px; font-weight: 600; color: #475569; }}
        .mode-btn.active {{ background: #0f172a; color: white; border-color: #0f172a; }}
        .right-scroll {{ flex: 1; overflow-y: auto; text-align: center; }}
        .overlay-wrapper {{ position: relative; display: inline-block; max-width: 100%; }}
        .overlay-wrapper img {{ max-width: 100%; height: auto; border: 1px solid #cbd5e1; border-radius: 6px; display: block; }}
        .bbox-highlight {{ position: absolute; border: 3px solid #ef4444; background: rgba(239, 68, 68, 0.22); box-shadow: 0 0 0 3px rgba(255,255,255,0.85), 0 0 18px rgba(239,68,68,0.7); border-radius: 3px; pointer-events: none; display: none; z-index: 20; animation: pulseBox 1.2s ease-in-out infinite; }}
        @keyframes pulseBox {{ 0%, 100% {{ transform: scale(1); }} 50% {{ transform: scale(1.012); }} }}
        .crop-list {{ text-align: left; }}
        .image-card {{ border: 1px solid #e2e8f0; border-radius: 6px; padding: 10px; margin-bottom: 10px; background: #f8fafc; display: flex; gap: 12px; align-items: flex-start; }}
        .image-card.icon {{ background: #fffbeb; border-color: #fde68a; }}
        .image-card.equation {{ background: #f0fdfa; border-color: #99f6e4; }}
        .image-card.table {{ background: #fef2f2; border-color: #fecaca; }}
        .image-preview {{ max-width: 160px; max-height: 120px; object-fit: contain; background: white; border: 1px solid #cbd5e1; border-radius: 4px; padding: 2px; }}
        .image-meta {{ font-size: 12.5px; line-height: 1.5; flex: 1; word-break: break-word; }}
        .tag {{ color: white; padding: 2px 6px; border-radius: 3px; font-size: 11px; font-weight: 700; }}
        .mini-icon {{ width: 20px; height: 20px; object-fit: contain; vertical-align: middle; border-radius: 3px; border: 1px solid #cbd5e1; background: white; }}
        .step2-badges {{ display: flex; flex-wrap: wrap; gap: 5px; margin-top: 5px; }}
        .id-badge {{ font-family: "Consolas", monospace; font-size: 11px; font-weight: 700; padding: 1px 6px; border-radius: 4px; background: #e0f2fe; color: #0369a1; border: 1px solid #bae6fd; }}
        .id-badge.eq {{ background: #ccfbf1; color: #0f766e; border-color: #99f6e4; }}
        .id-badge.fig {{ background: #dbeafe; color: #1d4ed8; border-color: #bfdbfe; }}
        .id-badge.tbl {{ background: #fee2e2; color: #b91c1c; border-color: #fecaca; }}
        .id-badge.ref {{ background: #fef3c7; color: #b45309; border-color: #fde68a; }}
        .tier-pill {{ font-size: 10.5px; font-weight: 700; padding: 1px 6px; border-radius: 4px; background: #ede9fe; color: #6d28d9; }}
        .prev-ctx-box {{ font-size: 11.5px; color: #475569; background: #f8fafc; border-left: 3px solid #94a3b8; padding: 4px 8px; margin-bottom: 8px; border-radius: 3px; }}
        .mkg-panel {{ text-align: left; font-size: 13px; }}
        .mkg-stat-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr)); gap: 8px; margin-bottom: 12px; }}
        .mkg-stat-card {{ background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 6px; padding: 8px 10px; }}
        .mkg-stat-num {{ font-size: 1.25rem; font-weight: 700; color: #1e3a8a; }}
        .mkg-edge-row {{ border-bottom: 1px solid #f1f5f9; padding: 5px 4px; font-family: "Consolas", monospace; font-size: 12px; }}
    </style>
</head>
<body>
    <header>
        <div class="header-title">
            <h1>Visual Cây phân cấp JSON &amp; Đối chiếu BBox PDF</h1>
            <div class="header-sub" id="chapterTitle"></div>
        </div>
        <div class="lesson-tabs" id="lessonTabs"></div>
    </header>
    <div class="legend">
        <span style="font-weight:700; color:#334155;">Vai trò sư phạm:</span>
        {legend_badges}
        <span style="margin-left:auto; font-size:12px; color:#64748b;">💡 Mẹo: Click vào bất kỳ item nào bên trái để nhảy tới đúng vị trí trên trang PDF bên phải</span>
    </div>
    <div class="workspace">
        <div class="pane-left" id="treePane"></div>
        <div class="pane-right">
            <div class="right-toolbar">
                <div class="page-pills" id="pagePills"></div>
                <div class="view-switch">
                    <button class="mode-btn active" id="btnModeOverlay" onclick="setRightMode('overlay')">Bản đồ BBox PDF</button>
                    <button class="mode-btn" id="btnModeCrops" onclick="setRightMode('crops')">Ảnh cắt (<span id="cropCount">0</span>)</button>
                    <button class="mode-btn" id="btnModeMkg" onclick="setRightMode('mkg')">🕸️ Khung xương MKG</button>
                </div>
            </div>
            <div class="right-scroll" id="rightContent"></div>
        </div>
    </div>

    <script>
        const hierData = {hier_json};
        const pagesInfo = {pages_json};
        const skeletonData = {skeleton_json};
        const roleStyles = {styles_json};

        let activeLessonIdx = 0;
        let activePageNum = 1;
        let rightMode = 'overlay';
        let highlightedBox = null;

        document.getElementById('chapterTitle').innerText = `${{hierData.document_title}} • ${{hierData.chapter}}`;

        function escapeHtml(text) {{
            const div = document.createElement('div');
            div.textContent = text == null ? '' : String(text);
            return div.innerHTML;
        }}

        function getStyle(role) {{
            return roleStyles[role] || {{ label: role, icon: '📄', hex: '#64748B' }};
        }}

        function renderLessonTabs() {{
            const tabsEl = document.getElementById('lessonTabs');
            tabsEl.innerHTML = '';
            hierData.lessons.forEach((lesson, idx) => {{
                const btn = document.createElement('button');
                btn.className = 'tab-btn' + (idx === activeLessonIdx ? ' active' : '');
                btn.innerText = `${{lesson.lesson_id}} (Tr. ${{lesson.pages.join(',')}})`;
                btn.onclick = () => selectLesson(idx);
                tabsEl.appendChild(btn);
            }});
        }}

        function selectLesson(idx) {{
            activeLessonIdx = idx;
            const lesson = hierData.lessons[idx];
            if (lesson && lesson.pages && lesson.pages.length > 0) {{
                activePageNum = lesson.pages[0];
            }}
            highlightedBox = null;
            renderLessonTabs();
            renderTreePane();
            renderRightPane();
        }}

        function renderItemHtml(item, itemId) {{
            const content = item.content ? `<div class="item-text">${{escapeHtml(item.content)}}</div>` : '';
            const eqBlock = (item.type === 'equation' && item.content)
                ? `<div class="item-eq">LaTeX: ${{escapeHtml(item.content)}}</div>` : '';
            const textBlock = (item.type === 'equation') ? '' : content;
            const imgBlock = item.image_source
                ? `<img class="item-img" src="${{item.image_source.startsWith('images/') ? item.image_source : 'images/' + item.image_source}}" alt="item">`
                : '';
            const caps = (item.captions || []).map(c => `<div class="item-caption">📌 ${{escapeHtml(c)}}</div>`).join('');
            const foots = (item.footnotes || []).map(f => `<div class="item-caption">📝 ${{escapeHtml(f)}}</div>`).join('');
            const bboxStr = (item.bbox || []).map(v => Number(v).toFixed(3)).join(', ');

            const badges = [];
            if (item.formula_id) badges.push(`<span class="id-badge eq">🧮 ${{escapeHtml(item.formula_id)}}</span>`);
            if (item.figure_id) badges.push(`<span class="id-badge fig">🖼️ ${{escapeHtml(item.figure_id)}}</span>`);
            if (item.table_id) badges.push(`<span class="id-badge tbl">📊 ${{escapeHtml(item.table_id)}}</span>`);
            (item.refers_to_figures || []).forEach(r => badges.push(`<span class="id-badge ref">🔗 Nối: ${{escapeHtml(r)}}</span>`));
            (item.refers_to_tables || []).forEach(r => badges.push(`<span class="id-badge ref">🔗 Nối: ${{escapeHtml(r)}}</span>`));
            (item.refers_to_formulas || []).forEach(r => badges.push(`<span class="id-badge ref">🔗 Công thức: ${{escapeHtml(r)}}</span>`));
            const badgeHtml = badges.length ? `<div class="step2-badges">${{badges.join('')}}</div>` : '';

            return `
                <div class="item-row" id="${{itemId}}" onclick='focusItem(${{item.page}}, ${{JSON.stringify(item.bbox || [])}}, "${{itemId}}")'>
                    <div class="item-meta">
                        <span><span class="item-type">${{escapeHtml(item.type)}}</span> &bull; BBox: [${{bboxStr}}]</span>
                        <span class="page-pill">Trang ${{item.page}}</span>
                    </div>
                    ${{eqBlock}}
                    ${{textBlock}}
                    ${{imgBlock}}
                    ${{caps}}
                    ${{foots}}
                    ${{badgeHtml}}
                </div>
            `;
        }}

        function unionBoxes(boxes) {{
            const valid = boxes.filter(b => Array.isArray(b) && b.length === 4);
            if (!valid.length) return null;
            return [
                Math.min(...valid.map(b => b[0])),
                Math.min(...valid.map(b => b[1])),
                Math.max(...valid.map(b => b[2])),
                Math.max(...valid.map(b => b[3])),
            ];
        }}

        function getSectionFocusTarget(sec, lesson) {{
            const pg = sec.page || (lesson.pages && lesson.pages[0]) || 1;
            const boxes = [];
            if (sec.bbox) boxes.push(sec.bbox);
            if (sec.marker_bbox) boxes.push(sec.marker_bbox);
            if (sec.units) {{
                sec.units.forEach(u => {{
                    if (u.page === pg && u.marker_bbox) boxes.push(u.marker_bbox);
                    (u.items || []).forEach(it => {{ if (it.page === pg && it.bbox) boxes.push(it.bbox); }});
                }});
            }} else {{
                (sec.items || []).forEach(it => {{ if (it.page === pg && it.bbox) boxes.push(it.bbox); }});
            }}
            return {{ page: pg, bbox: unionBoxes(boxes) }};
        }}

        function getUnitFocusTarget(unit, fallbackPage) {{
            const items = unit.items || [];
            const pg = unit.page || (items[0] && items[0].page) || fallbackPage || 1;
            const boxes = [];
            if (unit.marker_bbox) boxes.push(unit.marker_bbox);
            items.forEach(it => {{ if (it.page === pg && it.bbox) boxes.push(it.bbox); }});
            return {{ page: pg, bbox: unionBoxes(boxes) }};
        }}

        function toggleSectionBody(btn) {{
            const card = btn.closest('.section-card');
            const body = card ? card.querySelector('.section-body') : null;
            if (!body) return;
            const hidden = body.style.display === 'none';
            body.style.display = hidden ? 'block' : 'none';
            btn.innerText = hidden ? '▾' : '▸';
        }}

        function renderTreePane() {{
            const lesson = hierData.lessons[activeLessonIdx];
            const pane = document.getElementById('treePane');
            if (!lesson) {{
                pane.innerHTML = '<p>Không có dữ liệu bài học.</p>';
                return;
            }}

            const lessonBoxes = [lesson.id_bbox, lesson.title_bbox].filter(Boolean);
            const lessonUnion = unionBoxes(lessonBoxes);
            const lessonPage = lesson.page || (lesson.pages && lesson.pages[0]) || 1;

            let html = `
                <div class="lesson-banner" onclick='focusItem(${{lessonPage}}, ${{JSON.stringify(lessonUnion || [])}}, "lesson-banner")' id="lesson-banner">
                    <h2>📘 ${{escapeHtml(lesson.lesson_id)}}: ${{escapeHtml(lesson.lesson_title)}}</h2>
                    <div class="meta">Phạm vi: Trang ${{lesson.pages.join(', ')}} &bull; Tổng số mục: ${{lesson.sections.length}} &bull; <em>Click để xem tiêu đề Bài trên PDF</em></div>
                </div>
            `;

            lesson.sections.forEach((sec, sIdx) => {{
                const st = getStyle(sec.section_type);
                const markerImg = sec.marker_icon
                    ? `<img class="mini-icon" src="${{sec.marker_icon}}" alt="icon">` : '';
                const secTarget = getSectionFocusTarget(sec, lesson);
                const secHeaderId = `sec-hd-${{sIdx}}`;

                let bodyHtml = '';
                if (sec.units) {{
                    sec.units.forEach((unit, uIdx) => {{
                        const ust = getStyle(unit.unit_type);
                        const uIcon = unit.marker_icon ? `<img class="mini-icon" src="${{unit.marker_icon}}" alt="icon">` : '';
                        const uPages = [...new Set((unit.items || []).map(it => it.page))].join(', ');
                        const uTarget = getUnitFocusTarget(unit, secTarget.page);
                        const uHeaderId = `unit-hd-${{sIdx}}-${{uIdx}}`;
                        const itemsHtml = (unit.items || []).map((it, iIdx) => renderItemHtml(it, `it-${{sIdx}}-${{uIdx}}-${{iIdx}}`)).join('');
                        const tierBadge = unit.extraction_tier ? `<span class="tier-pill">${{escapeHtml(unit.extraction_tier)}}</span>` : '';
                        const uidBadge = unit.unit_id ? `<span class="id-badge">${{escapeHtml(unit.unit_id)}}</span>` : '';
                        const prevBox = unit.prev_context ? `<div class="prev-ctx-box"><strong>Ngữ cảnh đoạn trước:</strong> ${{escapeHtml(unit.prev_context)}}</div>` : '';
                        bodyHtml += `
                            <div class="unit-box" style="border-left-color:${{ust.hex}};">
                                <div class="unit-header" id="${{uHeaderId}}" onclick='focusItem(${{uTarget.page}}, ${{JSON.stringify(uTarget.bbox || [])}}, "${{uHeaderId}}")'>
                                    <span>${{uIcon}} ${{ust.icon}} <span style="color:${{ust.hex}};">${{ust.label}}</span> ${{uidBadge}} ${{tierBadge}}</span>
                                    <span style="color:#64748b; font-weight:500;">${{(unit.items || []).length}} items • Tr. ${{uPages}}</span>
                                </div>
                                ${{prevBox}}
                                ${{itemsHtml}}
                            </div>
                        `;
                    }});
                }} else {{
                    const prevBox = sec.prev_context ? `<div class="prev-ctx-box"><strong>Ngữ cảnh đoạn trước:</strong> ${{escapeHtml(sec.prev_context)}}</div>` : '';
                    const itemsHtml = (sec.items || []).map((it, iIdx) => renderItemHtml(it, `it-${{sIdx}}-${{iIdx}}`)).join('');
                    bodyHtml = prevBox + itemsHtml;
                }}

                const countText = sec.units ? `${{sec.units.length}} khối` : `${{(sec.items || []).length}} items`;
                const secPgLabel = sec.page ? `Tr. ${{sec.page}} • ` : '';
                html += `
                    <div class="section-card">
                        <div class="section-header" id="${{secHeaderId}}" onclick='focusItem(${{secTarget.page}}, ${{JSON.stringify(secTarget.bbox || [])}}, "${{secHeaderId}}")'>
                            <span class="left">
                                ${{markerImg}}
                                <span>${{st.icon}} ${{escapeHtml(sec.title)}}</span>
                            </span>
                            <span style="display:flex; align-items:center; gap:8px;">
                                <span style="font-size:12px; color:#64748b; font-weight:500;">${{secPgLabel}}${{countText}}</span>
                                <span class="role-pill" style="background:${{st.hex}};">${{sec.section_type}}</span>
                                <button class="toggle-btn" title="Thu gọn / Mở rộng" onclick="event.stopPropagation(); toggleSectionBody(this)">▾</button>
                            </span>
                        </div>
                        <div class="section-body">${{bodyHtml}}</div>
                    </div>
                `;
            }});

            pane.innerHTML = html;
        }}

        function focusItem(pageNum, bbox, itemId) {{
            document.querySelectorAll('.selected').forEach(el => el.classList.remove('selected'));
            const el = document.getElementById(itemId);
            if (el) el.classList.add('selected');

            activePageNum = pageNum;
            rightMode = 'overlay';
            highlightedBox = (bbox && bbox.length === 4) ? bbox : null;
            document.getElementById('btnModeOverlay').classList.add('active');
            document.getElementById('btnModeCrops').classList.remove('active');
            document.getElementById('btnModeMkg').classList.remove('active');
            renderRightPane();
        }}

        function setPage(pageNum) {{
            activePageNum = pageNum;
            highlightedBox = null;
            renderRightPane();
        }}

        function setRightMode(mode) {{
            rightMode = mode;
            document.getElementById('btnModeOverlay').classList.toggle('active', mode === 'overlay');
            document.getElementById('btnModeCrops').classList.toggle('active', mode === 'crops');
            document.getElementById('btnModeMkg').classList.toggle('active', mode === 'mkg');
            renderRightPane();
        }}

        function renderRightPane() {{
            const lesson = hierData.lessons[activeLessonIdx];
            const lessonPages = new Set(lesson ? lesson.pages : []);
            const pillsEl = document.getElementById('pagePills');
            pillsEl.innerHTML = '';

            pagesInfo.forEach(pg => {{
                const btn = document.createElement('button');
                btn.className = 'pg-btn'
                    + (lessonPages.has(pg.page) ? ' in-lesson' : '')
                    + (pg.page === activePageNum ? ' active' : '');
                btn.innerText = `Tr. ${{pg.page}}`;
                btn.onclick = () => setPage(pg.page);
                pillsEl.appendChild(btn);
            }});

            const pgInfo = pagesInfo.find(p => p.page === activePageNum) || pagesInfo[0];
            if (!pgInfo) return;
            document.getElementById('cropCount').innerText = pgInfo.images.length;

            const rightEl = document.getElementById('rightContent');
            if (rightMode === 'overlay') {{
                let hlStyle = 'display:none;';
                if (highlightedBox) {{
                    const [x0, y0, x1, y1] = highlightedBox;
                    hlStyle = `display:block; left:${{(x0*100).toFixed(2)}}%; top:${{(y0*100).toFixed(2)}}%; width:${{((x1-x0)*100).toFixed(2)}}%; height:${{((y1-y0)*100).toFixed(2)}}%;`;
                }}
                rightEl.innerHTML = `
                    <div style="font-size:12.5px; color:#475569; margin-bottom:8px; text-align:left;">
                        <strong>Trang ${{pgInfo.page}}</strong> &bull; ${{pgInfo.total_items}} items trong cây phân cấp (${{pgInfo.total_blocks}} raw blocks)
                    </div>
                    <div class="overlay-wrapper">
                        <img src="${{pgInfo.overlay_file}}" alt="Overlay Trang ${{pgInfo.page}}" id="overlayImg">
                        <div class="bbox-highlight" id="bboxHighlight" style="${{hlStyle}}"></div>
                    </div>
                `;
                if (highlightedBox) {{
                    setTimeout(() => {{
                        const hl = document.getElementById('bboxHighlight');
                        if (hl) hl.scrollIntoView({{ behavior: 'smooth', block: 'center' }});
                    }}, 40);
                }}
            }} else if (rightMode === 'mkg') {{
                const stats = (skeletonData && skeletonData.stats) ? skeletonData.stats : {{}};
                const nodes = (skeletonData && skeletonData.nodes) ? skeletonData.nodes : [];
                const edges = (skeletonData && skeletonData.edges) ? skeletonData.edges : [];
                const lessonPrefix = 'BAI' + ((lesson && lesson.lesson_id.match(/\\d+/)) ? lesson.lesson_id.match(/\\d+/)[0] : '');
                const lessonEdges = edges.filter(e =>
                    String(e.source).startsWith(lessonPrefix + '_') || String(e.source) === lessonPrefix
                );
                const crossEdges = lessonEdges.filter(e => String(e.relation).startsWith('REFERS_TO_') || e.relation === 'HAS_FORMULA' || e.relation === 'HAS_FIGURE' || e.relation === 'HAS_TABLE');
                const byLabel = stats.nodes_by_label || {{}};
                const statCards = Object.entries(byLabel).map(([k, v]) =>
                    `<div class="mkg-stat-card"><div class="mkg-stat-num">${{v}}</div><div style="font-size:12px;color:#475569;">${{escapeHtml(k)}}</div></div>`
                ).join('');
                const edgeRows = crossEdges.map(e =>
                    `<div class="mkg-edge-row"><strong>${{escapeHtml(e.source)}}</strong> &rarr; <span style="color:#0f766e;font-weight:700;">[${{escapeHtml(e.relation)}}]</span> &rarr; <strong>${{escapeHtml(e.target)}}</strong></div>`
                ).join('') || '<p style="color:#64748b;">Chưa có cạnh liên kết đa phương thức ở bài này.</p>';
                rightEl.innerHTML = `
                    <div class="mkg-panel">
                        <h3 style="margin:0 0 8px 0;">🕸️ Khung xương Đồ thị Đa phương thức (Toàn tài liệu: ${{stats.total_nodes || nodes.length}} nút, ${{stats.total_edges || edges.length}} cạnh)</h3>
                        <div class="mkg-stat-grid">${{statCards}}</div>
                        <h4 style="margin:10px 0 6px 0;">Liên kết Đa phương thức &amp; Tham chiếu chéo của ${{escapeHtml(lesson ? lesson.lesson_id : '')}} (${{crossEdges.length}} cạnh)</h4>
                        ${{edgeRows}}
                    </div>
                `;
            }} else {{
                let cropsHtml = `<div class="crop-list">`;
                if (pgInfo.images.length === 0) {{
                    cropsHtml += '<p style="color:#64748b;">Trang này không có ảnh/công thức cắt ra.</p>';
                }} else {{
                    pgInfo.images.forEach(img => {{
                        let cls = '';
                        let tag = '<span class="tag" style="background:#1e88e5;">Ảnh bài học</span>';
                        if (img.block_type === 'equation') {{
                            cls = 'equation';
                            tag = '<span class="tag" style="background:#008b8b;">Công thức</span>';
                        }} else if (img.block_type === 'table') {{
                            cls = 'table';
                            tag = '<span class="tag" style="background:#dc143c;">Bảng biểu</span>';
                        }} else if (img.is_icon) {{
                            cls = 'icon';
                            tag = '<span class="tag" style="background:#f59e0b;">Icon chỉ dẫn</span>';
                        }}
                        const extra = img.content ? `<div><strong>Nội dung/Chú thích:</strong> ${{escapeHtml(img.content)}}</div>` : '';
                        cropsHtml += `
                            <div class="image-card ${{cls}}">
                                <img class="image-preview" src="images/${{img.filename}}" alt="${{img.filename}}">
                                <div class="image-meta">
                                    <div><strong>Block #${{img.idx}}</strong> (${{img.block_type}}) &bull; ${{tag}}</div>
                                    <div><strong>File:</strong> <code>${{img.filename}}</code> (${{img.pixel_size}})</div>
                                    <div><strong>BBox:</strong> [${{img.bbox.join(', ')}}]</div>
                                    ${{extra}}
                                </div>
                            </div>
                        `;
                    }});
                }}
                cropsHtml += '</div>';
                rightEl.innerHTML = cropsHtml;
            }}
        }}

        renderLessonTabs();
        renderTreePane();
        renderRightPane();
    </script>
</body>
</html>
"""


def main() -> None:
    """Sinh visual crop_preview kết hợp Cây phân cấp & BBox PDF cho toàn bộ tài liệu."""
    import sys

    project_root = Path(__file__).resolve().parent.parent
    if len(sys.argv) >= 3:
        pdf_path = Path(sys.argv[1]).resolve()
        output_root = Path(sys.argv[2]).resolve()
    elif len(sys.argv) == 2:
        pdf_path = Path(sys.argv[1]).resolve()
        output_root = project_root / "output" / pdf_path.stem
    else:
        pdf_path = project_root / "data" / "KHTN_9_Chuong2,3.pdf"
        output_root = project_root / "output" / "KHTN_9_Chuong2,3"
    structured_path = output_root / "structured_content.json"
    hierarchical_path = output_root / "hierarchical_content.json"
    skeleton_path = output_root / "skeleton_mkg.json"

    preview_dir = output_root / "crop_preview"
    preview_images_dir = preview_dir / "images"
    preview_dir.mkdir(parents=True, exist_ok=True)

    structured_data = json.loads(structured_path.read_text(encoding="utf-8"))
    hierarchical_data = json.loads(hierarchical_path.read_text(encoding="utf-8"))
    skeleton_data = json.loads(skeleton_path.read_text(encoding="utf-8")) if skeleton_path.is_file() else {}

    _copy_all_assets(output_root / "images", preview_images_dir)
    boxes_by_page, regions_by_page = _collect_boxes_by_page(hierarchical_data)
    pages_info = _build_pages_info(structured_data, boxes_by_page, regions_by_page, pdf_path, preview_dir, output_root)

    html_content = _build_html(hierarchical_data, pages_info, skeleton_data)
    index_path = preview_dir / "index.html"
    index_path.write_text(html_content, encoding="utf-8")
    print(f"Đã sinh Visual thành công cho {len(pages_info)} trang ({len(hierarchical_data.get('lessons', []))} bài) tại: {index_path}", flush=True)


if __name__ == "__main__":
    main()
