# Copyright (c) Opendatalab. All rights reserved.
"""使用 Python SDK 解析示例 PDF；在项目根目录运行 python -m demo.demo。"""

from pathlib import Path

from mineru.parser import parse
from mineru.parser.writer import FileBasedDataWriter

from .build_hierarchical_json import save_hierarchical_json


def main() -> None:
    """Trích xuất PDF bằng Python SDK của MinerU.
    Ưu tiên file trong thư mục data/ (ví dụ KHTN_9_Chuong2,3.pdf), sau đó tới demo/pdfs/.
    """
    project_root = Path(__file__).resolve().parent.parent
    data_dir = project_root / "data"
    demo_dir = Path(__file__).resolve().parent
    pdf_dir = demo_dir / "pdfs"

    # Ưu tiên tìm file trong data/, nếu không có thì tìm trong demo/pdfs/
    target_pdf = data_dir / "KHTN_9_Chuong2,3.pdf"
    pdf_paths: list[Path] = []
    if target_pdf.is_file():
        pdf_paths = [target_pdf]
    elif data_dir.is_dir():
        pdf_paths = sorted(p for p in data_dir.iterdir() if p.is_file() and p.suffix.lower() == ".pdf")

    if not pdf_paths and pdf_dir.is_dir():
        pdf_paths = sorted(path for path in pdf_dir.iterdir() if path.is_file() and path.suffix.lower() == ".pdf")

    if not pdf_paths:
        print(f"Không tìm thấy file PDF nào trong: {data_dir} hoặc {pdf_dir}", flush=True)
        return

    for index, pdf_path in enumerate(pdf_paths, start=1):
        print(f"[{index}/{len(pdf_paths)}] Đang xử lý: {pdf_path.name}", flush=True)
        result = parse(
            pdf_path,
            tier="standard",
            ocr_mode="auto",
            page_range="all",
        )

        # Lưu Markdown, JSON (MiddleJson, StructuredContent) và ảnh ra thư mục output
        output_dir = project_root / "output" / pdf_path.stem
        writer = FileBasedDataWriter(str(output_dir))
        result.save(writer)

        # Lưu thêm bản markdown có chú thích phân trang (page markers) phục vụ dựng đồ thị tri thức
        md_with_markers = result.markdown(add_markers=True)
        writer.write_string("markdown_with_markers.md", md_with_markers)

        # Tạo thêm JSON phân cấp Chương -> Bài -> Mục -> Khối chức năng
        hierarchical_path = save_hierarchical_json(output_dir, pdf_path)

        print(
            f"Đã trích xuất xong {len(result.pages)} trang! Kết quả lưu tại: {output_dir} "
            f"(JSON phân cấp: {hierarchical_path.name})",
            flush=True,
        )


if __name__ == "__main__":
    main()
