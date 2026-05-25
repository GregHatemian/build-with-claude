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
        pass  # task 11

    def _draw_cache_line(self) -> None:
        pass  # task 12

    def _draw_hint_strip(self) -> None:
        pass  # task 13
