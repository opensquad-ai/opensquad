"""RichLog subclass with Textual click-drag text selection (OpenCode-like).

Upstream RichLog (Textual ≤8.x) never calls ``Strip.apply_offsets`` and does not
implement ``get_selection``, so the compositor cannot map mouse cells to content
coordinates and Ctrl+C has nothing to copy. See Textual discussion #6249.
"""

from __future__ import annotations

from rich.segment import Segment
from rich.style import Style
from textual.selection import Selection
from textual.strip import Strip
from textual.widgets import RichLog


class SelectableRichLog(RichLog):
    """RichLog that supports mouse text selection and clipboard extract."""

    @property
    def allow_select(self) -> bool:
        return True

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        text = "\n".join(line.text for line in self.lines)
        return selection.extract(text), "\n"

    def selection_updated(self, selection: Selection | None) -> None:
        self._line_cache.clear()
        self.refresh()

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        line = self._render_line(scroll_y + y, scroll_x, self.scrollable_content_region.width)
        return line.apply_style(self.rich_style)

    def _render_line(self, y: int, scroll_x: int, width: int) -> Strip:
        if y >= len(self.lines):
            return Strip.blank(width, self.rich_style)

        selection = self.text_selection
        key = (y + self._start_line, scroll_x, width, self._widest_line_width)
        if selection is None and key in self._line_cache:
            line = self._line_cache[key]
        else:
            line = self.lines[y].crop_extend(scroll_x, scroll_x + width, self.rich_style)
            if selection is None:
                self._line_cache[key] = line

        if selection is not None:
            span = selection.get_span(y)
            if span is not None:
                start, end = span
                if end == -1:
                    end = len(self.lines[y].text)
                start_rel = max(0, start - scroll_x)
                end_rel = max(0, end - scroll_x)
                if end_rel > start_rel:
                    # Force readable contrast: theme selection fg can be invisible on purple
                    base = self.screen.get_component_rich_style("screen--selection")
                    sel_style = Style(
                        color="#ffffff",
                        bgcolor=getattr(base, "bgcolor", None) or "#1f6feb",
                        bold=True,
                    )
                    line = _stylize_chars(line, start_rel, end_rel, sel_style)

        # Required so Screen.get_widget_and_offset_at can resolve content (x, y)
        return line.apply_offsets(scroll_x, y)


def _stylize_chars(strip: Strip, start: int, end: int, style: Style) -> Strip:
    """Apply ``style`` to character indices ``[start, end)`` within a strip.

    Selection style wins over prior color/bg so highlighted text stays readable.
    """
    if start >= end or not strip._segments:
        return strip
    out: list[Segment] = []
    pos = 0
    for segment in strip._segments:
        text = segment.text
        seg_end = pos + len(text)
        if seg_end <= start or pos >= end:
            out.append(segment)
        else:
            local_start = max(0, start - pos)
            local_end = min(len(text), end - pos)
            if local_start > 0:
                out.append(Segment(text[:local_start], segment.style, segment.control))
            mid = text[local_start:local_end]
            # Put selection style last so its color/bg override italic/dim body styles
            mid_style = (segment.style + style) if segment.style else style
            if style.color is not None:
                mid_style = mid_style + Style(color=style.color)
            if style.bgcolor is not None:
                mid_style = mid_style + Style(bgcolor=style.bgcolor)
            out.append(Segment(mid, mid_style, segment.control))
            if local_end < len(text):
                out.append(Segment(text[local_end:], segment.style, segment.control))
        pos = seg_end
    return Strip(out, strip.cell_length)
