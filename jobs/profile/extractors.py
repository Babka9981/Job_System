import os
import zipfile
from pathlib import Path
from xml.etree import ElementTree


class ResumeExtractionError(Exception):
    pass


def _docx_text(path):
    max_unpacked = int(os.environ.get("JOB_CV_MAX_UNPACKED_BYTES", 25 * 1024 * 1024))
    max_entries = int(os.environ.get("JOB_CV_MAX_ARCHIVE_ENTRIES", 1000))
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
            if len(members) > max_entries or sum(item.file_size for item in members) > max_unpacked:
                raise ResumeExtractionError("DOCX превышает безопасный предел распаковки.")
            root = ElementTree.fromstring(archive.read("word/document.xml"))
    except (KeyError, OSError, RuntimeError, NotImplementedError, zipfile.BadZipFile, ElementTree.ParseError) as exc:
        raise ResumeExtractionError("DOCX повреждён; вставьте текст вручную.") from exc

    namespace = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    blocks = []
    for paragraph in root.findall(".//w:p", namespace):
        text = "".join(node.text or "" for node in paragraph.findall(".//w:t", namespace)).strip()
        if text:
            blocks.append(f"[Блок {len(blocks) + 1}] {text}")
    if not blocks:
        raise ResumeExtractionError("DOCX не содержит извлекаемого текста; вставьте текст вручную.")
    return "\n".join(blocks)


def _pdf_text(path):
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ResumeExtractionError("PDF extractor не установлен.") from exc
    max_pages = int(os.environ.get("JOB_CV_MAX_PAGES", "50"))
    try:
        reader = PdfReader(path, strict=True)
        if len(reader.pages) > max_pages:
            raise ResumeExtractionError("PDF содержит слишком много страниц.")
        pages = []
        for number, page in enumerate(reader.pages, start=1):
            text = (page.extract_text() or "").strip()
            if text:
                pages.append(f"[Страница {number}] {text}")
    except ResumeExtractionError:
        raise
    except Exception as exc:
        raise ResumeExtractionError("PDF повреждён; вставьте текст вручную.") from exc
    if not pages:
        raise ResumeExtractionError("PDF не содержит извлекаемого текста; вставьте текст вручную.")
    return "\n".join(pages)


def extract_resume_text(path):
    suffix = Path(path).suffix.lower()
    if suffix == ".docx":
        return _docx_text(path)
    if suffix == ".pdf":
        return _pdf_text(path)
    raise ResumeExtractionError("Поддерживаются только PDF и DOCX.")
