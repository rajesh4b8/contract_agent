#!/usr/bin/env python3
"""Turn a plain-text contract into a PDF the upload path can read.

    python scripts/make_sample_pdf.py sample-contracts/TinyContract-Fast.txt

Sample contracts are easiest to write and review as text, but the UI only
accepts PDFs. This writes a minimal single-font PDF directly rather than pulling
in a rendering library for test fixtures — the extractors (`pypdf`, then
`pdfplumber`) only need real text objects, not layout.
"""
import pathlib
import sys

PAGE_WIDTH, PAGE_HEIGHT = 612, 792   # US Letter, in points
MARGIN, FONT_SIZE, LEADING = 56, 10.5, 14.5
MAX_LINE = 92                        # characters per line at this size


def _escape(text: str) -> str:
    """Escape the three characters that are special inside a PDF string."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _wrap(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if not raw.strip():
            lines.append("")
            continue
        words, current = raw.split(), ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > MAX_LINE and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        lines.append(current)
    return lines


def _pages(lines: list[str]) -> list[list[str]]:
    per_page = int((PAGE_HEIGHT - 2 * MARGIN) / LEADING)
    return [lines[i:i + per_page] for i in range(0, len(lines), per_page)] or [[]]


def build_pdf(text: str) -> bytes:
    pages = _pages(_wrap(text))

    objects: list[bytes] = []
    page_ids = [4 + 2 * i for i in range(len(pages))]

    objects.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    for index, page_lines in enumerate(pages):
        content = [f"BT /F1 {FONT_SIZE} Tf {MARGIN} {PAGE_HEIGHT - MARGIN} Td {LEADING} TL"]
        for line in page_lines:
            content.append(f"({_escape(line)}) Tj T*" if line else "T*")
        content.append("ET")
        stream = "\n".join(content).encode("latin-1", "replace")

        objects.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_ids[index] + 1} 0 R >>".encode()
        )
        objects.append(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n"
                       + stream + b"\nendstream")

    out = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"

    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n").encode()
    return bytes(out)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 1

    source = pathlib.Path(sys.argv[1])
    if not source.exists():
        print(f"No such file: {source}")
        return 1

    target = source.with_suffix(".pdf")
    target.write_bytes(build_pdf(source.read_text()))
    print(f"{source} -> {target} ({target.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
