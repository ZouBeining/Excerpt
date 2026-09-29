r"""Use LemmInflect to restore the base form of a word.

Usage
----
The words in the bold records are usually not the base form 
(e.g. scolded, whining, etc.) Therefore, we take a step of base form restoration 
after looking up the original word, if a different word is get, 
then it is **also** included in the output words.json, Markdown an LaTeX

实测结论（`test.md` 的 30 个真实标记词）
----------------------------------------
* 正常屈折形式还原准确：``flattering→flatter``、``whining→whine``、
  ``bummed→bum``、``spotted→spot``…，可用；
* **连字符词还原不出来**：``banged-up``、``heavy-duty`` 返回空，
  这类就得不到原形（不报错，只是没有）；
* 本词自身也在候选里时要剔除（如 ``deformed`` 的候选是
  ``deform``/``deformed``）；
* 大小写不敏感：``Pause`` 的候选还是 ``Pause``，算「没有变化」；
* 性能可忽略：单次查询约 6 µs（首次 import 约 0.5s）。

与 MW 的关系
------------
MW 的检索本身已带词干匹配，**有时直接返回原形词条**（查 ``muttering``
返回的词头就是 ``mutter``）。这种情况下标记词条目里展示的**已经**是原形的
释义，再补一条原形条目只会重复，所以调用方要先比对 MW 词头再决定是否新增。
"""

from __future__ import annotations

from typing import Any

from . import mw_api
from .cache import WordCache
from .config import (CODE_OK, BoldEntry)

try:  # 缺失时优雅降级：不还原，而不是崩掉
    from lemminflect import getAllLemmas
except ImportError:  # pragma: no cover
    getAllLemmas = None  # type: ignore[assignment]


def is_available() -> bool:
    """Check if LemmInflect is avaliable."""
    return getAllLemmas is not None


def get_lemma(word: str) -> list[str]:
    """返回 ``word`` 的原形；无法还原、或还原结果与自身相同，返回 ``None``。

    候选里会剔除与 ``word`` 同形的项（大小写不敏感），多个候选时取最短的
    那个——``better`` 的候选是 ``good``/``well``，取 ``good``。
    """
    if not word or getAllLemmas is None:
        return []

    text = word.strip()
    if not text.isalpha():
        return []

    try:
        found = getAllLemmas(text)
    except Exception:  # pragma: no cover - 库内部异常不应影响主流程
        return []

    if not found:
        return []

    candidates: list[str] = []
    for lemmas in found.values():
        for lemma in lemmas or ():
            low = str(lemma).strip().lower()
            if low and low != text.lower() and low not in candidates:
                candidates.append(low)
    return candidates


# ---------------------------------------------------------------------------
# 给 BoldEntry 附加「这是原形条目」的元信息
# ---------------------------------------------------------------------------
# 这里刻意不新建 dataclass：extract.BoldEntry 是旧版共用的类型，本轮不动它。
# 所需信息量很小（是不是原形 + 来自哪个标记词），直接作为属性挂上去即可。
def mark_as_lemma(boldentry: BoldEntry, marked_word: str) -> BoldEntry:
    """把 ``boldentry`` 标记为「由 ``marked_word`` 还原出的原形条目」。"""
    boldentry.is_lemma = True
    boldentry.lemma_from = marked_word
    return boldentry


def is_lemma_entry(boldentry: BoldEntry) -> bool:
    return bool(getattr(boldentry, "is_lemma", False))


def lemma_from(boldentry: BoldEntry) -> str:
    return str(getattr(boldentry, "lemma_from", ""))

def boldentry_to_dict(boldentry: BoldEntry) -> dict:
    bdict = boldentry.to_dict()
    if bdict:
        return bdict
    return {}

def attach_lemma_entries(
    entries: list[BoldEntry],
    *,
    session,
    api_key: str | None,
    cache: WordCache | None,
    timeout: float,
    delay: float,
) -> list[BoldEntry]:
    """Get the base form of a word. 
    
    This function is only applied in these conditions:

    * LemmInflect 能还原出原形，且原形**与标记词不同**
    * 原形**不等于 MW 返回的词头**。MW 的检索自带词干匹配，查 ``muttering``
      返回的词头已经是 ``mutter``，标记词条目里展示的就是原形的释义，
      再补一条纯属重复；
    * 原形没有和别的标记词或已补的原形撞车。

    补出来的条目沿用来源词条的原句（那是唯一可用的上下文），并带上
    ``lemmaFrom`` 以便在讲义里注明来历。
    """
    if not is_available():
        print("[lemma] [warn] LemmInflect is not installed. Base form restoration is skipped.")
        return entries

    included = {e.word.strip().lower() for e in entries}
    pending: list[tuple[BoldEntry, str]] = []
    for boldentry in entries:
        if boldentry.code != CODE_OK:
            continue
        head = str((boldentry.entry or {}).get("word") or "").strip().lower()
        lemma_list = get_lemma(boldentry.word)
        if not lemma_list:
            continue
        for candidate in lemma_list:
            if candidate == boldentry.word.strip().lower() or candidate == head:
                continue
            if candidate in included:
                continue
            included.add(candidate)
            pending.append((boldentry, candidate))

    if not pending:
        print("[lemma] 没有需要补充的原形条目")
        return entries

    added: dict[int, BoldEntry] = {}
    for index, (source_boldentry, candidate) in enumerate(pending, start=1):
        new_entry = BoldEntry(
            word=candidate, 
            sentence=source_boldentry.sentence, 
            line=source_boldentry.line
        )
        mw_api.lookup(
            new_entry,
            session=session,
            api_key=api_key,
            cache=cache,
            timeout=timeout,
            sleep=delay,
        )
        mark_as_lemma(new_entry, source_boldentry.word)
        added[id(source_boldentry)] = new_entry
        flag = "Succeeded" if new_entry.code == CODE_OK else f"ERR {new_entry.code}"
        print(f"  [lemma {index}/{len(pending)}] {source_boldentry.word!r} -> {candidate!r} {flag}")

    # 原形条目紧跟在来源词条之后，读者先看到屈折形式、再看到原形
    result: list[BoldEntry] = []
    for entry in entries:
        result.append(entry)
        if id(entry) in added:
            result.append(added[id(entry)])
    return result
