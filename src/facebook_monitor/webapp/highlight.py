"""Web UI keyword highlight compatibility wrapper。"""

from __future__ import annotations

from facebook_monitor.core.keyword_highlight import HighlightSegment
from facebook_monitor.core.keyword_highlight import build_highlight_segment_dicts
from facebook_monitor.core.keyword_highlight import build_highlight_segments


__all__ = [
    "HighlightSegment",
    "build_highlight_segment_dicts",
    "build_highlight_segments",
]
