import re
from dataclasses import dataclass

_ASCII_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*")
_CJK_SPAN_RE = re.compile(r"[\u4e00-\u9fff]+")
_PAGE_MARKER_RE = re.compile(r"\[?(第\s*\d+\s*页)\]?")
_HEADING_RE = re.compile(r"^(?:#{1,6}\s+|(§\s*\d+(?:\.\d+)*\s*.*))$")


@dataclass(frozen=True)
class ChunkDraft:
    chunk_index: int
    section_title: str
    page_label: str
    content: str
    lexical_text: str


def tokenize_for_lexical(text: str, *, include_unigrams_when_bigram: bool = True) -> str:
    """为 PostgreSQL simple 词典生成中英文混合词法索引串。"""
    tokens: list[str] = []
    seen: set[str] = set()

    def _add(tok: str) -> None:
        normalized = tok.strip().lower()
        if not normalized:
            return
        if normalized not in seen:
            seen.add(normalized)
            tokens.append(normalized)

    for match in _ASCII_TOKEN_RE.finditer(text):
        raw = match.group(0)
        _add(raw)
        if any(sep in raw for sep in ("-", "_", ".")):
            for part in re.split(r"[-_.]+", raw):
                _add(part)

    for match in _CJK_SPAN_RE.finditer(text):
        span = match.group(0)
        if len(span) == 1:
            _add(span)
            continue
        for idx in range(len(span) - 1):
            _add(span[idx : idx + 2])
        if include_unigrams_when_bigram:
            for ch in span:
                _add(ch)

    return " ".join(tokens)


def build_lexical_tsquery_expression(query: str, *, max_terms: int = 24) -> str:
    """将自然语言问题转换为 simple 词典可用的 OR tsquery 表达式。"""
    lexical = tokenize_for_lexical(query, include_unigrams_when_bigram=False)
    if not lexical:
        lexical = tokenize_for_lexical(query, include_unigrams_when_bigram=True)
    terms: list[str] = []
    for raw in lexical.split():
        cleaned = re.sub(r"[^0-9a-z\u4e00-\u9fff._-]", "", raw)
        if cleaned:
            # 用单引号包裹避免点号/连字符触发 tsquery 语法歧义
            terms.append(f"'{cleaned}'")
        if len(terms) >= max_terms:
            break
    return " | ".join(terms)


def _normalize_heading(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("#"):
        return stripped.lstrip("#").strip() or "正文"
    return stripped


def chunk_markdown_document(
    raw_content: str,
    *,
    default_title: str,
    max_chars: int = 420,
) -> list[ChunkDraft]:
    """按 Markdown 标题、条款号与页码标记切片，保留可追溯引用位置。"""
    lines = raw_content.replace("\r\n", "\n").splitlines()
    current_section = default_title.strip() or "正文"
    current_page: str | None = None
    segments: list[tuple[str, str | None, str]] = []
    buffer: list[str] = []

    def _flush() -> None:
        text_block = "\n".join(buffer).strip()
        buffer.clear()
        if text_block:
            segments.append((current_section, current_page, text_block))

    for line in lines:
        stripped = line.strip()
        if not stripped:
            _flush()
            continue

        page_match = _PAGE_MARKER_RE.fullmatch(stripped)
        if page_match:
            _flush()
            current_page = re.sub(r"\s+", "", page_match.group(1)).replace("第", "第 ").replace("页", " 页")
            continue

        inline_page = _PAGE_MARKER_RE.search(stripped)
        if inline_page and stripped.startswith("#"):
            current_page = re.sub(r"\s+", "", inline_page.group(1)).replace("第", "第 ").replace("页", " 页")

        if stripped.startswith("#") or _HEADING_RE.match(stripped):
            _flush()
            cleaned_heading = _PAGE_MARKER_RE.sub("", _normalize_heading(stripped)).strip()
            if cleaned_heading:
                current_section = cleaned_heading
            continue

        cleaned_line = _PAGE_MARKER_RE.sub("", stripped).strip()
        if cleaned_line:
            buffer.append(cleaned_line)

    _flush()

    if not segments:
        segments = [(default_title.strip() or "正文", current_page, raw_content.strip())]

    merged: list[tuple[str, str, str]] = []
    for section_title, page_hint, body in segments:
        paragraphs = [body] if len(body) <= max_chars else _split_long_block(body, max_chars=max_chars)
        for part in paragraphs:
            if (
                merged
                and merged[-1][0] == section_title
                and len(merged[-1][2]) + len(part) + 1 <= max_chars
                and (page_hint is None or merged[-1][1] == page_hint)
            ):
                prev_sec, prev_page, prev_text = merged[-1]
                merged[-1] = (prev_sec, prev_page, f"{prev_text}\n{part}")
            else:
                assigned_page = page_hint or f"第 {len(merged) + 1} 页"
                merged.append((section_title, assigned_page, part))

    drafts: list[ChunkDraft] = []
    for idx, (section_title, page_label, content) in enumerate(merged):
        lexical = tokenize_for_lexical(f"{default_title} {section_title} {page_label} {content}")
        drafts.append(
            ChunkDraft(
                chunk_index=idx,
                section_title=section_title[:200],
                page_label=page_label[:64],
                content=content,
                lexical_text=lexical,
            )
        )
    return drafts


def _split_long_block(text: str, *, max_chars: int) -> list[str]:
    pieces: list[str] = []
    cursor = 0
    step = max(max_chars, 120)
    while cursor < len(text):
        end = min(cursor + step, len(text))
        pieces.append(text[cursor:end].strip())
        cursor = end
    return [piece for piece in pieces if piece]
