"""Renderer for the Claude Code (CC) source tab.

Layout at 240x135:
  y=0..17    source header line ("ClaudeCC_xxxx ● CC", right-aligned dot)
  y=18..50   big percentage number, size 4, color thresholded
  y=52..62   subtitle: "N'NNN / N'NNN tok"
  y=66..96   cumul graph (cyan line, dashed budget cap)
  y=98..110  cache split line ("cache r:NM c:NK u:NK")
  y=120..134 hint strip: "Tab=CD"

set_heartbeat(hb) stashes the latest heartbeat dict; paint() renders.
The dispatcher in buddy_ui_cp.py decides which renderer is active.
"""

from M5 import Lcd as _LCD

_W = 240
_H = 135

BLACK = 0x000000
WHITE = 0xFFFFFF
CYAN = 0x00FFFF
YELLOW = 0xFFFF00
RED = 0xFF0000
GRAY_MID = 0x808080
DIM = 0x404040
GREEN = 0x00C800
CREAM = 0xEEEED5


class StatsRenderer:
    def __init__(self, state):
        self._state = state
        self._hb = {}
        self._link_up = False

    def set_link(self, up: bool) -> None:
        self._link_up = up

    def set_heartbeat(self, hb: dict) -> None:
        self._hb = hb

    # Alias for BuddyUI compatibility: dispatcher routes update_heartbeat
    # to both renderers via the same call site.
    update_heartbeat = set_heartbeat

    def paint(self) -> None:
        _LCD.fillScreen(BLACK)
        self._draw_header()
        self._draw_percentage()
        self._draw_subtitle()
        self._draw_graph()
        self._draw_cache_line()
        self._draw_hint_strip()

    def _draw_header(self) -> None:
        _LCD.setTextSize(1)
        _LCD.setTextColor(CYAN, BLACK)
        # advertised name comes from the BLE layer; for now show the tab
        # tag as a placeholder — the app passes the real name in via
        # set_heartbeat or a dedicated setter (added in a later task).
        _LCD.drawString("CC source", 6, 4)
        dot_color = GREEN if self._link_up else GRAY_MID
        _LCD.fillCircle(_W - 14, 8, 4, dot_color)
        _LCD.drawString("CC", _W - 28, 4)
        _LCD.drawFastHLine(0, 17, _W, DIM)

    def _draw_percentage(self) -> None:
        daily_total = self._hb.get("daily_total", self._hb.get("tokens_today", 0))
        pct = self._state.budget_percentage(daily_total) if self._state else None
        if pct is None:
            text = "--%"
            color = GRAY_MID
        else:
            text = "{}%".format(pct)
            if pct >= 90:
                color = RED
            elif pct >= 70:
                color = YELLOW
            else:
                color = CYAN
        _LCD.setTextSize(4)
        _LCD.setTextColor(color, BLACK)
        _LCD.drawString(text, 6, 18)
        _LCD.setTextSize(1)  # restore for subsequent draws

    def _draw_subtitle(self) -> None:
        daily_total = self._hb.get("daily_total", self._hb.get("tokens_today", 0))
        budget = self._state.daily_budget_tokens if self._state else 0
        cur = "{:,}".format(daily_total).replace(",", "'")
        cap = "{:,}".format(budget).replace(",", "'") if budget else "--"
        line = "{} / {} tok".format(cur, cap)
        while _LCD.textWidth(line) > _W - 12 and len(line) > 1:
            line = line[:-1]
        _LCD.setTextSize(1)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString(line, 6, 52)

    def _draw_graph(self) -> None:
        # Plot region: x=6..234 (228 wide), y=66..96 (30 tall).
        x0, y0, w, h = 6, 66, _W - 12, 30
        # Frame: a faint floor line + dashed budget cap.
        _LCD.drawFastHLine(x0, y0 + h, w, DIM)
        budget = self._state.daily_budget_tokens if self._state else 0
        if budget <= 0 or not self._state or len(self._state.graph_samples) < 2:
            # Not enough data to draw the line or no budget to scale by.
            return
        # Y scale: 0 at bottom (y0+h), budget at top (y0). Anything over
        # 100% is clamped to top (we already render '999%' in the big
        # number for that case).
        def yv(cum):
            ratio = cum / budget
            if ratio > 1.0:
                ratio = 1.0
            return int(y0 + h - ratio * h)
        # X scale: oldest sample at x0, newest at x0+w.
        samples = list(self._state.graph_samples)
        n = len(samples)
        # Cap line: every 8 px draw a 4-px dash at y = y0 (top).
        for x in range(x0, x0 + w, 8):
            _LCD.drawFastHLine(x, y0, 4, GRAY_MID)
        # Line: connect successive samples.
        prev_x = x0
        prev_y = yv(samples[0][1])
        for i in range(1, n):
            cx = x0 + int(i * w / (n - 1))
            cy = yv(samples[i][1])
            _LCD.drawLine(prev_x, prev_y, cx, cy, CYAN)
            prev_x, prev_y = cx, cy

    def _draw_cache_line(self) -> None:
        cr = self._hb.get("cache_read")
        cc = self._hb.get("cache_create")
        cu = self._hb.get("cache_uncached")
        if cr is None and cc is None and cu is None:
            # Source didn't provide cache info — render a dim placeholder.
            _LCD.setTextSize(1)
            _LCD.setTextColor(GRAY_MID, BLACK)
            _LCD.drawString("cache: --", 6, 98)
            return
        _LCD.setTextSize(1)
        x = 6
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("cache", x, 98)
        x += _LCD.textWidth("cache ")
        for label, val, color in (
            ("r:", cr, GREEN),
            ("c:", cc, CYAN),
            ("u:", cu, GRAY_MID),
        ):
            if val is None:
                continue
            _LCD.setTextColor(color, BLACK)
            txt = label + _fmt_short(val)
            if x + _LCD.textWidth(txt) > _W - 6:
                break  # ran out of horizontal room
            _LCD.drawString(txt, x, 98)
            x += _LCD.textWidth(txt) + 4

    def _draw_hint_strip(self) -> None:
        _LCD.drawFastHLine(0, 116, _W, DIM)
        _LCD.setTextSize(1)
        _LCD.setTextColor(GRAY_MID, BLACK)
        _LCD.drawString("Tab=CD", 6, 120)


def _fmt_short(n: int) -> str:
    """Compact integer: 1234 -> '1.2K', 1234567 -> '1.2M'."""
    if n is None:
        return "?"
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return "{:.1f}K".format(n / 1000.0)
    return "{:.1f}M".format(n / 1_000_000.0)
