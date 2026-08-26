from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from .models import SourceChunk, SourceWork, StoryJobConfig

SUPPORTED_SOURCE_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".fountain",
    ".fdx",
    ".docx",
    ".pdf",
}


def discover_source_paths(task: str, config: StoryJobConfig) -> list[str]:
    candidates = list(config.source_paths)
    # The existing workbench inserts `user_temp/<name>` into task text.  Keep
    # spaces and CJK characters; stop only at a newline or explicit quote.
    extensions = "txt|md|markdown|fountain|fdx|docx|pdf"
    pattern = (
        rf"(?:user_temp/|file://)[^\n\r\"'<>]+?\.(?:{extensions})(?=\s|$|[，,。.;；])"
    )
    candidates.extend(
        match.strip().rstrip("，,。.;；") for match in re.findall(pattern, task)
    )
    seen: list[str] = []
    for candidate in candidates:
        if candidate and candidate not in seen:
            seen.append(candidate)
    return seen


def ingest_sources(
    task: str,
    config: StoryJobConfig,
    *,
    workspace: Path,
    user_workspace: Path,
) -> list[SourceWork]:
    sources: list[SourceWork] = []
    for index, raw in enumerate(discover_source_paths(task, config), start=1):
        path = _resolve_source(raw, workspace=workspace, user_workspace=user_workspace)
        if path is None or path.suffix.lower() not in SUPPORTED_SOURCE_SUFFIXES:
            continue
        text = extract_document_text(path)
        if not text.strip():
            continue
        sources.append(
            _source_work(path, text, index=index, language=config.source_language)
        )

    if not sources:
        idea = task.strip()
        if not idea:
            raise ValueError("Story development requires a source file or story idea.")
        digest = hashlib.sha256(idea.encode("utf-8")).hexdigest()
        sources.append(
            SourceWork(
                source_id="source_inline",
                title="User story brief",
                format="brief",
                language=config.source_language,
                sha256=digest,
                character_count=len(idea),
                text=idea,
                chunks=_chunks("source_inline", idea),
            )
        )
    return sources


def extract_document_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".markdown", ".fountain"}:
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".fdx":
        return _extract_fdx(path)

    converted = _markitdown_text(path)
    if converted:
        return converted
    if suffix == ".docx":
        return _extract_docx_xml(path)
    if suffix == ".pdf":
        return _extract_pdf(path)
    raise ValueError(f"Unsupported story source format: {suffix}")


def _resolve_source(raw: str, *, workspace: Path, user_workspace: Path) -> Path | None:
    value = str(raw or "").strip()
    if value.startswith("file://"):
        value = value[7:]
    source = Path(value)
    candidates: list[Path] = []
    if source.is_absolute():
        candidates.append(source)
    else:
        parts = source.parts
        if parts and parts[0] == "user_temp":
            candidates.append(user_workspace / Path(*parts[1:]))
        candidates.extend(
            (workspace / source, user_workspace / source, user_workspace / source.name)
        )

    roots = (workspace.resolve(strict=False), user_workspace.resolve(strict=False))
    for candidate in candidates:
        resolved = candidate.resolve(strict=False)
        if not resolved.is_file():
            continue
        if any(_is_relative_to(resolved, root) for root in roots):
            return resolved
    return None


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _markitdown_text(path: Path) -> str:
    try:
        from markitdown import MarkItDown

        result = MarkItDown().convert(str(path))
        return str(getattr(result, "text_content", "") or "").strip()
    except Exception:
        return ""


def _extract_docx_xml(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as archive:
            document = archive.read("word/document.xml")
    except (OSError, KeyError, zipfile.BadZipFile) as exc:
        raise ValueError(f"Unable to read DOCX source: {path.name}") from exc
    root = ET.fromstring(document)
    namespace = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    paragraphs: list[str] = []
    for paragraph in root.iter(f"{namespace}p"):
        text = "".join(
            node.text or "" for node in paragraph.iter(f"{namespace}t")
        ).strip()
        if text:
            paragraphs.append(text)
    return "\n\n".join(paragraphs)


def _extract_pdf(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise ValueError(
            "PDF ingestion requires MarkItDown or pypdf. Install requirements-story.txt."
        ) from exc
    try:
        return "\n\n".join(
            (page.extract_text() or "").strip() for page in PdfReader(path).pages
        )
    except Exception as exc:
        raise ValueError(
            f"Unable to extract text from PDF source: {path.name}"
        ) from exc


def _extract_fdx(path: Path) -> str:
    try:
        root = ET.fromstring(path.read_bytes())
    except (OSError, ET.ParseError) as exc:
        raise ValueError(f"Unable to read FDX source: {path.name}") from exc
    paragraphs: list[str] = []
    for paragraph in root.iter():
        if paragraph.tag.rsplit("}", 1)[-1] != "Paragraph":
            continue
        content = "".join(
            node.text or ""
            for node in paragraph.iter()
            if node.tag.rsplit("}", 1)[-1] == "Text"
        )
        if content.strip():
            kind = paragraph.attrib.get("Type", "")
            paragraphs.append(
                f"[{kind}] {content.strip()}" if kind else content.strip()
            )
    return "\n\n".join(paragraphs)


def _source_work(path: Path, text: str, *, index: int, language: str) -> SourceWork:
    raw = path.read_bytes()
    source_id = f"source_{index:03d}"
    return SourceWork(
        source_id=source_id,
        title=path.stem,
        path=str(path),
        format=path.suffix.lower().lstrip("."),
        language=language,
        sha256=hashlib.sha256(raw).hexdigest(),
        character_count=len(text),
        text=text,
        chunks=_chunks(source_id, text),
    )


def _chunks(
    source_id: str, text: str, *, target_chars: int = 6000
) -> list[SourceChunk]:
    chunks: list[SourceChunk] = []
    cursor = 0
    while cursor < len(text):
        end = min(len(text), cursor + target_chars)
        if end < len(text):
            boundary = text.rfind("\n", cursor + target_chars // 2, end)
            if boundary > cursor:
                end = boundary
        chunk_text = text[cursor:end].strip()
        if chunk_text:
            chunks.append(
                SourceChunk(
                    chunk_id=f"{source_id}_chunk_{len(chunks) + 1:03d}",
                    start=cursor,
                    end=end,
                    text=chunk_text,
                )
            )
        if end >= len(text):
            break
        cursor = max(cursor + 1, end - 300)
    return chunks
