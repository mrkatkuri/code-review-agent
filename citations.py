"""Verify code citations within a claimed range, tolerating presentation wrappers."""

import re
import textwrap


def source_evidence(
    evidence: str, source_lines: list[str], first_line: int
) -> str | None:
    """Return canonical source for an exact quote, never a fuzzy or whole-file match.

    Models sometimes return explanatory prose containing inline code, fenced code,
    dedented snippets, or the line-number prefixes used in the prompt. A matching
    quote must still occur inside the claimed line range. Prose alone is rejected.
    """
    text = evidence.replace("\r\n", "\n").strip()
    if not text:
        return None
    candidates = [(text, None)]
    fence = re.fullmatch(r"```[^\n]*\n(.*?)\n```", text, flags=re.DOTALL)
    if fence:
        candidates.append((fence[1], None))
    # Inline code in a narrative is a citation; surrounding explanation is not code.
    candidates.extend(
        (match, None) for match in re.findall(r"(?<!`)`([^`\n]+)`(?!`)", text)
    )
    for candidate, _ in list(candidates):
        rows = candidate.splitlines()
        numbered = [re.fullmatch(r"\s*(\d+): ?(.*)", row) for row in rows]
        if numbered and all(numbered):
            candidates.append(
                (
                    "\n".join(match[2] for match in numbered),
                    [int(match[1]) for match in numbered],
                )
            )
    original = "\n".join(source_lines)
    for candidate, numbers in candidates:
        for source, snippet in (
            (original, candidate.strip()),
            (textwrap.dedent(original), textwrap.dedent(candidate).strip()),
        ):
            if not snippet:
                continue
            offset = source.find(snippet)
            while offset >= 0:
                start = source[:offset].count("\n")
                count = snippet.count("\n") + 1
                if numbers is None or numbers == list(
                    range(first_line + start, first_line + start + count)
                ):
                    return "\n".join(source_lines[start : start + count])
                offset = source.find(snippet, offset + 1)
    return None
