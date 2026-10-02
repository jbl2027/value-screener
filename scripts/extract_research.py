"""Extract the research PDF's text to docs/research.md."""
from pathlib import Path

from pypdf import PdfReader

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "Value Investing Screener Research.pdf"
OUT = ROOT / "docs" / "research.md"


def main() -> None:
    reader = PdfReader(PDF)
    parts = ["# Value Investing Screener Research\n",
             f"_Text extracted from `{PDF.name}` with pypdf. Formatting and tables may be imperfect._\n"]
    for i, page in enumerate(reader.pages, 1):
        parts.append(f"\n<!-- page {i} -->\n")
        # Default mode emits one word per line for this PDF; layout mode keeps lines intact.
        parts.append((page.extract_text(extraction_mode="layout") or "").strip() + "\n")
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text("\n".join(parts), encoding="utf-8")
    print(f"Wrote {len(reader.pages)} pages to {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
