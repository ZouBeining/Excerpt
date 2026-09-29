r"""从 Markdown 文件中抽取所有加粗内容。

设计要点
--------
* 支持 ``**bold**`` 与 ``__bold__`` 两种 Markdown 加粗语法。
* 屏蔽代码围栏（````` ``` `````）、行内代码（`` `code` ``）、已转义字符
  （``\*``），避免把代码里的星号误判为加粗标记。
* 对每个加粗内容，回溯定位它所在的**完整句子**。Markdown 常对段落做硬换行，
  因此句子识别在**段落级**进行（把段落内各行先用空格拼起来再切句），避免
  把一句话按物理行截断。
* ``code`` 字段携带状态码，供下游管线判定是否需要调用词典 API。本地先判定
  「是不是一个单词」，可以避免为短语白花词典配额。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterator

from .config import *


# ---------------------------------------------------------------------------
# 义项筛选
# ---------------------------------------------------------------------------
# 词典会把「（英）杭，（罗）汉格……（人名）」这类专名信息也作为义项返回，
# 对词汇学习没有价值，因此在生成讲义（Markdown 与 LaTeX）时剔除。
# 注意：按需求，原始数据仍会原样写进 words.json 与本地缓存，便于回溯。
NAME_MARKER = "人名"


def is_name_sense(meaning: str) -> bool:
    """中文释义里出现「人名」二字的，视为人名义项。"""
    return NAME_MARKER in meaning


def drop_name_senses(definitions: list[str]) -> list[str]:
    """剔除人名义项；若全部都是人名则原样返回，避免把词条变成空壳。"""
    kept = [d for d in definitions if not is_name_sense(d)]
    return kept if kept else definitions

# ---------------------------------------------------------------------------
# 正则
# ---------------------------------------------------------------------------
# 代码围栏：``` 或 ~~~，可带语言标注
_FENCE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")

# 行内代码：`...`（允许 `` 包裹）
_INLINE_CODE_RE = re.compile(r"(`+)(?:.+?)\1")

# 加粗：**...** 或 __...__，内容不含换行，且两侧不能是同类标记本身
_BOLD_RE = re.compile(r"(\*\*|__)(?=\S)(.+?)(?<=\S)\1", re.DOTALL)

# 转义字符：\x -> 先把反斜杠吞掉，避免 \* 被判成标记
_ESCAPE_RE = re.compile(r"\\(.)")

# 句子分隔符（中英文）
_SENTENCE_END = ".!?;\u3002\uff01\uff1f\uff1b"

# 一个「单词」：纯字母，可含连字符或撇号；用于判定加粗内容是否是单词
_WORD_RE = re.compile(r"^[A-Za-z]+(?:['\u2019-][A-Za-z]+)*$")

# 占位符字符：表示「被抹掉但保留宽度」的内容
_PLACEHOLDER = "\x00"


def _strip_noise(line: str) -> str:
    """抹掉行内代码与转义字符，保留字符位置以便定位。"""
    line = _INLINE_CODE_RE.sub(lambda m: _PLACEHOLDER * len(m.group(0)), line)
    line = _ESCAPE_RE.sub(lambda m: _PLACEHOLDER + m.group(1), line)
    return line


def _is_blank(line: str) -> bool:
    return not line.strip()


def _iter_paragraphs(text: str) -> Iterator[tuple[str, int]]:
    """把正文切成段落，跳过代码围栏。

    Yields
    ------
    (段落内各物理行用空格连接后的文本, 段落起始行号)
    """
    in_fence = False
    fence_token = ""
    buffer: list[str] = []
    start_line = 0

    def flush() -> Iterator[tuple[str, int]]:
        nonlocal buffer
        if buffer:
            joined = " ".join(part.strip() for part in buffer)
            yield joined, start_line
            buffer = []

    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        fence = _FENCE_RE.match(raw_line)
        if fence:
            token = fence.group(1)[0]
            if not in_fence:
                yield from flush()
                in_fence, fence_token = True, token
            elif token == fence_token:
                in_fence = False
            continue

        if in_fence:
            continue

        if _is_blank(raw_line):
            yield from flush()
        else:
            if not buffer:
                start_line = lineno
            buffer.append(_strip_noise(raw_line))

    yield from flush()


def find_sentence(paragraph: str, needle: str) -> str:
    """在段落文本中定位 ``needle``，返回其所在的完整句子。"""
    pos = paragraph.find(needle)
    if pos < 0:
        return paragraph.replace(_PLACEHOLDER, "").strip()

    # 向前找句首
    start = 0
    for i in range(pos - 1, -1, -1):
        if paragraph[i] in _SENTENCE_END:
            start = i + 1
            break

    # 向后找句尾（包含句末标点）
    end = len(paragraph)
    for i in range(pos + len(needle), len(paragraph)):
        if paragraph[i] in _SENTENCE_END:
            end = i + 1
            break

    sentence = paragraph[start:end].strip()
    sentence = sentence.replace(_PLACEHOLDER, "")
    return re.sub(r"\s+", " ", sentence).strip()


def extract_bold_entries(text: str) -> list[BoldEntry]:
    """Extract all the bolded records, and generate the original JSON structure."""
    results: list[BoldEntry] = []
    seen: set[tuple[str, int]] = set()

    for paragraph, start_line in _iter_paragraphs(text):
        for m in _BOLD_RE.finditer(paragraph):
            inner: str = m.group(2).strip()
            if not inner:
                continue
            plain: str = inner.replace(_PLACEHOLDER, "").strip()
            sentence: str = find_sentence(paragraph, inner)
            key: tuple[str, int] = (plain.lower(), hash(sentence))
            if key in seen:
                continue
            seen.add(key)

            entry = BoldEntry(word=plain, sentence=sentence, line=start_line)
            if not _WORD_RE.match(plain):
                entry.code = CODE_NOT_A_WORD
                entry.detail = "not a single word"
            results.append(entry)

    return results
