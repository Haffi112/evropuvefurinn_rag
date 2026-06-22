"""Convert an LLM answer (Markdown + structured references) into the
Vísindavefur publish format.

Output uses:
  - <strong>...</strong> for headings (any level of `#`)
  - <b>...</b> for bold
  - <em>...</em> for italic
  - <ul><li>…</li></ul> for unordered lists
  - <ol><li>…</li><li>…</li></ol> for ordered lists
  - {{footnote|text=…}} for inline citations, mapped from [[N]](url) markers
    emitted by the LLM per its system prompt.
  - {{footnote_list|}} marker — the CMS renders the "Tilvísanir" section from
    the inline footnotes above.

We deliberately do NOT emit a "Heimildir" list: the cited articles already
appear in the footnotes/Tilvísanir, and the Vísindavefur CMS shows the
source articles separately as related answers ("tengd svör", under the
heading "Svör fræðafólks sem gervigreindin nýtti"). An inline list here
just duplicated them, so the editor asked us to drop it.

Layout contract (per Vísindavefur editorial feedback): every block —
paragraph, heading, or list — sits on a single line, and blocks are
separated by blank lines; the VV CMS derives paragraph breaks from those
newlines ("kerfið sér um restina"). This holds regardless of whether the
source Markdown had blank lines between blocks, and it is also why list
HTML must contain no internal newlines — a newline between <li> items
would be read as a paragraph break inside the list.

Blank-line spacing is tiered, because the CMS preserves blank lines and the
editor wanted progressively more breathing room: sub-headings get the most,
lists less, plain paragraphs the least (see the *_GAP constants). Sub-headings
need the extra room because we render them as bold <strong> lines rather than
real heading elements, so they carry no heading margin of their own. This is a
workaround for the CMS's newline handling and is expected to be relaxed once
that is fixed (the editor's "Einhvern daginn bætum við úr þessari kvöð").
"""

from __future__ import annotations

import re
from html import escape


# Leading `[ \t]*` consumes any stray horizontal whitespace the LLM may have
# inserted before the citation marker (e.g. "word [[1]](url).") so the
# footnote attaches tightly to the preceding word — Vísindavefur's style
# requires no whitespace between the cited word, the period, and the
# footnote: `word.{{footnote|...}}`. Newlines are preserved.
CITATION_RUN_RE = re.compile(r"[ \t]*((?:\[\[\d+\]\]\([^)]+\))+)(\.?)")
INNER_CITATION_RE = re.compile(r"\[\[(\d+)\]\]\(([^)]+)\)")
HEIMILDIR_HEADER_RE = re.compile(
    r"^\s*##+\s*(Heimildir|References)\s*$", re.IGNORECASE
)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*$")
UL_RE = re.compile(r"^[-*]\s+(.*)$")
OL_RE = re.compile(r"^(\d+)\.\s+(.*)$")

# Block separators, sized by the kind of block on either side of a boundary.
# The CMS preserves blank lines as vertical space, and the editor asked for
# progressively more room around lists and (especially) sub-headings. These
# three constants are the single knobs to turn if the CMS's needs change.
_PARAGRAPH_GAP = "\n\n"  # one blank line — between plain paragraphs
_LIST_GAP = "\n\n\n"  # two blank lines — around <ul>/<ol> lists
_HEADING_GAP = "\n\n\n\n"  # three blank lines — around <strong> sub-headings
_HEADING_PREFIX = "<strong>"
_LIST_PREFIXES = ("<ul>", "<ol>")


def _gap_between(prev: str, curr: str) -> str:
    """Pick the separator for a boundary: the widest gap requested by either
    neighbour wins (heading > list > paragraph)."""
    if prev.startswith(_HEADING_PREFIX) or curr.startswith(_HEADING_PREFIX):
        return _HEADING_GAP
    if prev.startswith(_LIST_PREFIXES) or curr.startswith(_LIST_PREFIXES):
        return _LIST_GAP
    return _PARAGRAPH_GAP


def _join_blocks(blocks: list[str]) -> str:
    """Join rendered blocks with blank-line separators sized to each boundary."""
    out: list[str] = []
    for idx, block in enumerate(blocks):
        if idx:
            out.append(_gap_between(blocks[idx - 1], block))
        out.append(block)
    return "".join(out)


def _format_reference(ref: dict) -> str:
    """Render a single reference as the footnote / Heimildir-item text."""
    title = (ref.get("title") or "Án titils").strip()
    url = (ref.get("source_url") or "").strip()
    if url:
        return f"{title} — {url}"
    return title


def _strip_trailing_heimildir(body: str) -> str:
    """Remove the trailing `## Heimildir` / `## References` block the
    web-search prompt instructs the LLM to add — we reconstruct it from
    structured data instead.
    """
    lines = body.splitlines()
    cutoff: int | None = None
    for i, line in enumerate(lines):
        if HEIMILDIR_HEADER_RE.match(line):
            cutoff = i
            break
    if cutoff is None:
        return body
    return "\n".join(lines[:cutoff]).rstrip()


def _replace_citations(body: str, references: list[dict]) -> str:
    """Turn runs of `[[N]](url)` markers into `{{footnote|text=…}}` blocks.

    The system prompt mandates citations like `…sentence[[1]](url).` and
    multi-citations `…sentence[[1]](a)[[2]](b).`. The trailing period (if any)
    is emitted *once* before the whole footnote cluster, regardless of how
    many citations were grouped.
    """
    by_number = {i + 1: ref for i, ref in enumerate(references)}

    def replace_run(match: re.Match[str]) -> str:
        citations = match.group(1)
        trailing_period = match.group(2)
        parts: list[str] = []
        for inner in INNER_CITATION_RE.finditer(citations):
            n = int(inner.group(1))
            url_in_marker = inner.group(2)
            ref = by_number.get(n)
            text = _format_reference(ref) if ref else url_in_marker
            # `|` and `}}` would break the template; replace with safe chars.
            safe = text.replace("|", "/").replace("}}", "} }")
            parts.append(f"{{{{footnote|text={safe}}}}}")
        return f"{trailing_period}{''.join(parts)}"

    return CITATION_RUN_RE.sub(replace_run, body)


def _apply_inline(text: str) -> str:
    """Inline replacements: bold, italic, leftover anchor links."""
    # HTML-escape first so user content can't inject tags. Then re-introduce
    # the tags we explicitly produce.
    text = escape(text, quote=False)

    # Bold: **x** → <b>x</b>. Run before italic to avoid `*italic*` swallowing
    # one of the asterisks. Use a non-greedy match within a single line.
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)

    # Italic: *x* or _x_ → <em>x</em>. Avoid matching `_` inside identifiers
    # by requiring non-word boundaries on either side.
    text = re.sub(r"(?<!\w)\*(?!\s)(.+?)(?<!\s)\*(?!\w)", r"<em>\1</em>", text)
    text = re.sub(r"(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)", r"<em>\1</em>", text)

    # Anchor links: [label](url) → <a href="url" target="_blank">label</a>
    # (the citation regex already consumed [[N]](url) markers, so these are
    # genuine prose links if any).
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{m.group(2)}" target="_blank">{m.group(1)}</a>',
        text,
    )

    return text


def _render_blocks(body: str) -> list[str]:
    """Walk the post-citation body and emit VV-formatted HTML as a list of
    single-line blocks (paragraphs, headings, lists), to be joined with
    blank-line separators by `_join_blocks`.

    LLM output never soft-wraps, so every non-blank line is its own block;
    blank lines in the source carry no extra information and are dropped.
    Normalising separation here — rather than preserving the source's line
    structure — guarantees the CMS sees a paragraph break after every block
    even when the model glued a heading or list directly onto adjacent text.
    """
    lines = body.splitlines()
    blocks: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()

        # Blank line — block separation is normalised on output; skip.
        if not stripped:
            i += 1
            continue

        # Heading: `# …`, `## …`, `### …` → <strong>…</strong>
        m = HEADING_RE.match(stripped)
        if m:
            blocks.append(f"<strong>{_apply_inline(m.group(2))}</strong>")
            i += 1
            continue

        # Ordered list — collect consecutive `N. item` lines.
        if OL_RE.match(stripped):
            items: list[str] = []
            while i < len(lines):
                m2 = OL_RE.match(lines[i].strip())
                if not m2:
                    break
                items.append(_apply_inline(m2.group(2)))
                i += 1
            blocks.append(_render_list("ol", items))
            continue

        # Unordered list — collect consecutive `- item` / `* item` lines.
        if UL_RE.match(stripped):
            items = []
            while i < len(lines):
                m2 = UL_RE.match(lines[i].strip())
                if not m2:
                    break
                items.append(_apply_inline(m2.group(1)))
                i += 1
            blocks.append(_render_list("ul", items))
            continue

        # Plain paragraph line.
        blocks.append(_apply_inline(stripped))
        i += 1

    return blocks


def _render_list(tag: str, items: list[str]) -> str:
    """Render a list on a single line — the CMS reads any newline inside
    the list as a paragraph break, splitting the list apart."""
    li_html = "".join(f"<li>{it}</li>" for it in items)
    return f"<{tag}>{li_html}</{tag}>"


def to_vv_html(answer_md: str, references: list[dict] | None = None) -> str:
    """Convert an LLM answer into the Vísindavefur publish format.

    Parameters
    ----------
    answer_md:
        The LLM answer in Markdown. May include `[[N]](url)` inline citations
        and an optional trailing `## Heimildir` / `## References` block.
    references:
        Ordered list of reference dicts as stored in `query_log."references"`.
        Each dict should have `title` and `source_url` keys; `number` is
        derived from list position (1-indexed) to match the LLM's citations.
    """
    refs = references or []
    body = _strip_trailing_heimildir(answer_md or "")
    body = _replace_citations(body, refs)
    blocks = _render_blocks(body)

    # Emit the {{footnote_list|}} marker (→ the CMS "Tilvísanir" section) when
    # there are citations, but NOT a "Heimildir" list — the CMS shows the
    # source articles separately as "tengd svör" (see module docstring).
    if refs:
        blocks.append("{{footnote_list|}}")
    return _join_blocks(blocks).strip() + "\n"
