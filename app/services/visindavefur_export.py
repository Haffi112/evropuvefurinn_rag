"""Convert an LLM answer (Markdown + structured references) into the
Vísindavefur publish format.

Output uses:
  - <strong>...</strong> for headings (any level of `#`)
  - <b>...</b> for bold
  - <em>...</em> for italic
  - <ul><li>…</li></ul> for unordered lists
  - <ol><li>…</li><li>…</li></ol> for ordered lists, with NO blank lines
    between <li> items (a rendering quirk of the target CMS).
  - {{footnote|text=…}} for inline citations, mapped from [[N]](url) markers
    emitted by the LLM per its system prompt.
  - {{footnote_list|}} marker followed by a <strong>Heimildir:</strong> block.
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


def _render_body(body: str) -> str:
    """Walk the post-citation body line-by-line and emit VV-formatted HTML."""
    lines = body.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Blank line — preserve as paragraph break.
        if not stripped:
            out.append("")
            i += 1
            continue

        # Heading: `# …`, `## …`, `### …` → <strong>…</strong>
        m = HEADING_RE.match(stripped)
        if m:
            out.append(f"<strong>{_apply_inline(m.group(2))}</strong>")
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
            # No newlines between <li> — VV renders blank lines between
            # <li> as extra vertical space.
            li_html = "".join(f"<li>{it}</li>" for it in items)
            out.append(f"<ol>{li_html}</ol>")
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
            li_html = "\n  ".join(f"<li>{it}</li>" for it in items)
            out.append(f"<ul>\n  {li_html}\n</ul>")
            continue

        # Plain paragraph line.
        out.append(_apply_inline(stripped))
        i += 1

    # Collapse runs of blank lines and trim trailing whitespace.
    rendered: list[str] = []
    prev_blank = False
    for line in out:
        if line == "":
            if prev_blank:
                continue
            prev_blank = True
        else:
            prev_blank = False
        rendered.append(line)
    return "\n".join(rendered).strip()


def _render_heimildir(references: list[dict]) -> str:
    """Build the `<strong>Heimildir:</strong><ul>...</ul>` block."""
    if not references:
        return ""
    items: list[str] = []
    for ref in references:
        title = escape((ref.get("title") or "Án titils").strip())
        url = (ref.get("source_url") or "").strip()
        if url:
            safe_url = escape(url, quote=True)
            items.append(
                f'<li>{title} <a href="{safe_url}" target="_blank">{escape(url)}</a></li>'
            )
        else:
            items.append(f"<li>{title}</li>")
    li_html = "\n  ".join(items)
    return f"<strong>Heimildir:</strong><ul>\n  {li_html}\n</ul>"


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
    rendered_body = _render_body(body)

    parts = [rendered_body]
    if refs:
        parts.append("")
        parts.append("{{footnote_list|}}")
        parts.append("")
        parts.append(_render_heimildir(refs))
    return "\n".join(p for p in parts).strip() + "\n"
