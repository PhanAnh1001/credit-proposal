# Hướng dẫn Test — Credit Proposal Agent

Thư mục này chứa hướng dẫn kiểm thử từng bước nhỏ cho toàn bộ hệ thống.

## Cấu trúc

| File | Nội dung |
|------|----------|
| `01_env_setup.md` | Kiểm tra môi trường, dependencies, API keys |
| `02_tools.md` | Test từng tool riêng lẻ (company_info, pdf_extractor, ratio_calculator, web_search) |
| `03_nodes.md` | Test từng LangGraph node riêng lẻ |
| `04_pipeline.md` | Test pipeline end-to-end |
| `05_output_validation.md` | Kiểm tra chất lượng output tổng thể (số liệu, hallucination, format MD, structure DOCX) |
| `06_docx_output.md` | Kiểm tra từng cell cụ thể trong DOCX output (company info, shareholders, PHỤ LỤC 1, financials) |

## Thứ tự thực hiện

```
01 → 02 → 03 → 04 → 05 → 06
```

Mỗi bước phụ thuộc vào bước trước. **Phải pass 01 trước khi chạy 02.**

## Triết lý test

- **Từng lớp một**: Tool trước, Node sau, Pipeline cuối
- **Fail fast**: Dừng ngay khi một bước fail, không chạy tiếp
- **Kiểm tra log**: Sau mỗi bước, xem file `logs/run_YYYYMMDD.log` để debug
- **Không cần pytest**: Các test được viết dưới dạng script Python chạy tay để dễ debug

## Cách xem log

```bash
# Log mới nhất
tail -50 logs/run_$(date +%Y%m%d).log

# Lọc theo node
grep "\[subgraph3\]" logs/run_$(date +%Y%m%d).log

# Lọc lỗi
grep "\[ERROR\]" logs/run_$(date +%Y%m%d).log
```
