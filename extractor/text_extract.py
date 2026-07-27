"""Extrai texto bruto de PDF, Word, Excel e CSV/TXT."""
from __future__ import annotations

import csv
import io
import os


def extract_text(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename)[1].lower()
    if ext == ".pdf":
        return _from_pdf(data)
    if ext in (".docx",):
        return _from_docx(data)
    if ext in (".xlsx",):
        return _from_xlsx(data)
    if ext in (".csv",):
        return _from_csv(data)
    if ext in (".txt", ".md"):
        return data.decode("utf-8", errors="ignore")
    raise ValueError(
        f"Formato não suportado: {ext or '(sem extensão)'}. "
        "Use PDF, DOCX, XLSX, CSV ou TXT."
    )


def _from_pdf(data: bytes) -> str:
    import pdfplumber

    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page in pdf.pages:
            txt = page.extract_text() or ""
            parts.append(txt)
            # Tabelas costumam guardar cronogramas; achatamos em linhas.
            for table in page.extract_tables() or []:
                for row in table:
                    cells = [c for c in row if c]
                    if cells:
                        parts.append(" | ".join(str(c) for c in cells))
    return "\n".join(parts)


def _from_docx(data: bytes) -> str:
    import docx

    doc = docx.Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs if p.text.strip()]
    for table in doc.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells if c.text.strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _from_xlsx(data: bytes) -> str:
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    parts: list[str] = []
    for ws in wb.worksheets:
        parts.append(f"# Planilha: {ws.title}")
        for row in ws.iter_rows(values_only=True):
            cells = [str(c) for c in row if c is not None and str(c).strip()]
            if cells:
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _from_csv(data: bytes) -> str:
    text = data.decode("utf-8", errors="ignore")
    reader = csv.reader(io.StringIO(text))
    return "\n".join(
        " | ".join(c for c in row if c.strip()) for row in reader if any(row)
    )
