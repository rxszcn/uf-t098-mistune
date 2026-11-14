"""Indexes of closing delimiters for one inline source.

Each delimiter family scans its source once. These are lexical positions, not
memoized parse failures, and are never shared with copied or sliced sources.

Formatting markers are the one family whose closers must respect code span
scope: a ``~~`` written between backticks is literal text, so it cannot end the
formatting that surrounds the code span. A backtick only opens a span where the
inline parser would actually reach the ``codespan`` rule, so backticks owned by
an earlier construct (escape, link destination, autolink, inline HTML, spoiler,
math) are excluded by replaying the parser's own rule order.
"""

import re
from bisect import bisect_left, bisect_right
from typing import TYPE_CHECKING, Dict, List, Match, Optional, Tuple

if TYPE_CHECKING:
    from ..core import InlineState
    from ..inline_parser import InlineParser

_BACKTICKS = re.compile(r"`+")


class DelimiterScanner:
    def __init__(self, src: str) -> None:
        self.src = src
        self._code: Optional[Dict[int, List[int]]] = None
        self._ends: Dict[Tuple[str, bool], List[int]] = {}
        self._has_backtick = "`" in src
        self._ticks: Optional[List[int]] = None
        self._span_starts: List[int] = []
        self._span_ends: List[int] = []
        self._span_cursor = 0
        self._span_exhausted = False
        self._span_owner: Optional["InlineParser"] = None

    def code_end(self, marker: str, pos: int) -> Optional[int]:
        size = len(marker)
        if self._code is None:
            end = self.src.find(marker, pos)
            if end >= 0 and (end == 0 or self.src[end - 1] != "`"):
                after = end + size
                if after == len(self.src) or self.src[after] != "`":
                    return end
            self._code = {}
            for match in _BACKTICKS.finditer(self.src):
                self._code.setdefault(match.end() - match.start(), []).append(match.start())
        positions = self._code.get(size, [])
        index = bisect_left(positions, pos)
        return positions[index] if index < len(positions) else None

    def find(self, marker: str, pos: int, *, formatting: bool = False) -> Optional[int]:
        key = (marker, formatting)
        positions = self._ends.get(key)
        if positions is None:
            positions = []
            start = self.src.find(marker)
            while start >= 0:
                if not formatting or _formatting_closer(self.src, start, marker):
                    positions.append(start)
                start = self.src.find(marker, start + 1)
            self._ends[key] = positions
        index = bisect_right(positions, pos) if formatting else bisect_left(positions, pos)
        return positions[index] if index < len(positions) else None

    def formatting_end(self, inline: "InlineParser", state: "InlineState", marker: str, pos: int) -> Optional[int]:
        """First closer after ``pos`` that is not written inside a code span.

        ``inline`` supplies the rule set because which backticks open a code
        span depends on the constructs parsed before them. A closer inside a
        span is literal text and is skipped, so the next eligible closer wins.
        Sources without a backtick cannot hold a code span, which keeps the
        common case on the plain lexical path.
        """
        positions = self._formatting_positions(marker)
        index = bisect_right(positions, pos)
        if self._has_backtick is False:
            return positions[index] if index < len(positions) else None
        while index < len(positions):
            close = positions[index]
            if not self._tick_between(pos, close):
                # No code span can begin at or after ``pos`` before ``close``,
                # so the lexical answer already holds.
                return close
            self._advance_spans(inline, state, close)
            span = bisect_right(self._span_starts, close) - 1
            if span >= 0 and close < self._span_ends[span]:
                index += 1
                continue
            return close
        return None

    def _tick_between(self, start: int, end: int) -> bool:
        # A span covering ``end`` must open at or after ``start`` (the opener's
        # end), so only ticks in ``[start, end)`` can matter.
        ticks = self._tick_positions()
        index = bisect_left(ticks, start)
        return index < len(ticks) and ticks[index] < end

    def _tick_positions(self) -> List[int]:
        ticks = self._ticks
        if ticks is None:
            ticks = [match.start() for match in _BACKTICKS.finditer(self.src)]
            self._ticks = ticks
        return ticks

    def _formatting_positions(self, marker: str) -> List[int]:
        key = (marker, True)
        positions = self._ends.get(key)
        if positions is None:
            positions = []
            start = self.src.find(marker)
            while start >= 0:
                if _formatting_closer(self.src, start, marker):
                    positions.append(start)
                start = self.src.find(marker, start + 1)
            self._ends[key] = positions
        return positions

    def _advance_spans(self, inline: "InlineParser", state: "InlineState", target: int) -> None:
        """Resolve code spans up to ``target``, resuming where the last call stopped.

        Openers are visited in source order, so the cursor only moves forward
        and the whole source is replayed at most once per scanner.
        """
        if self._span_owner is not inline:
            self._span_starts.clear()
            self._span_ends.clear()
            self._span_cursor = 0
            self._span_exhausted = False
            self._span_owner = inline
        if self._span_exhausted or "codespan" not in inline.rules:
            return

        src = self.src
        sc = inline.compile_sc()
        scratch = inline.state_cls(state.env)
        scratch.src = src
        scratch.depth = state.depth
        while not self._span_exhausted and self._span_cursor <= target:
            m = sc.search(src, self._span_cursor)
            if m is None:
                self._span_exhausted = True
                break
            if m.lastgroup == "codespan":
                marker = m.group(0)
                end = self.code_end(marker, m.end())
                if end is None:
                    self._span_cursor = max(m.end(), m.start() + 1)
                else:
                    self._span_starts.append(m.start())
                    self._span_ends.append(end + len(marker))
                    self._span_cursor = end + len(marker)
                continue
            cursor = _construct_end(self, inline, scratch, m)
            self._span_cursor = cursor if cursor > m.start() else m.start() + 1


def _construct_end(scanner: DelimiterScanner, inline: "InlineParser", state: "InlineState", m: Match[str]) -> int:
    """Return the cursor after the construct matched at ``m``.

    A rule hides only the backticks it actually consumes. Almost every handler
    stops at ``m.end()``, so the match is the exact extent. The exceptions scan
    ahead for their own terminator: inline HTML, inline spoiler, and the link
    suffix consumed after ``]``. Their extent is computed here from positions
    alone — calling their handlers would emit tokens and, for footnotes or
    links, write shared ``env`` state.
    """
    name = m.lastgroup
    if name == "link_close":
        return _link_suffix_end(inline, state, m)
    if name == "inline_html":
        return _html_end(inline, state, m)
    if name == "inline_spoiler":
        return _spoiler_end(scanner, m)
    return m.end()


def _link_suffix_end(inline: "InlineParser", state: "InlineState", m: Match[str]) -> int:
    # Imported here: ``core`` imports this module, so ``helpers`` (which
    # imports ``core``) cannot be loaded at module import time.
    from ..helpers import parse_link, parse_link_label
    from ..util import unikey

    src = state.src
    end = m.end()
    if src.startswith("(", end):
        _attrs, next_pos = parse_link(src, end + 1, max_nested_level=inline.max_nested_level)
        if next_pos is not None:
            return next_pos
    elif src.startswith("[", end):
        label, next_pos = parse_link_label(src, end + 1)
        refs = state.env.get("ref_links") or {}
        if next_pos is not None and label and unikey(label) in refs:
            return next_pos
    return end


def _html_end(inline: "InlineParser", state: "InlineState", m: Match[str]) -> int:
    # Inline HTML only appends a token and toggles ``in_link``; the scratch
    # state absorbs both and never touches ``env``.
    end = inline._methods["inline_html"](m, state)
    return end if end and end > m.start() else m.end()


def _spoiler_end(scanner: DelimiterScanner, m: Match[str]) -> int:
    # Mirrors the handler's extent checks without rendering its children.
    pos = m.end()
    close = scanner.find("!<", pos + 1)
    if close is None or scanner.src.find("\n", pos, close) >= 0:
        return pos
    if not scanner.src[pos:close].strip():
        return pos
    return close + 2


def _formatting_closer(src: str, start: int, marker: str) -> bool:
    char = marker[0]
    end = start + len(marker)
    if end < len(src) and src[end] == char and not src.startswith(marker, end):
        return False
    if not start:
        return False
    previous = src[start - 1]
    if previous != char:
        return not previous.isspace()
    # A literal escaped first character can precede a closing pair in a run.
    if start < 2 or src[start - 2] != "\\":
        return False
    backslashes = 0
    pos = start - 2
    while pos >= 0 and src[pos] == "\\":
        backslashes += 1
        pos -= 1
    return backslashes % 2 == 1
