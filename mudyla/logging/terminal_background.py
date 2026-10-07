"""Bounded OSC 11 replies and a selection tint derived from the reported background."""

import re
from typing import Optional

from rich.color import Color, ColorSystem
from rich.style import Style

RGB = tuple[int, int, int]
PROBE_SECONDS = 0.250
ESCAPE_SECONDS = 0.020
SELECTION_BLEND = 0.03
LIGHT_BACKGROUND_THRESHOLD = 128
MAX_PALETTE_CHANNEL_DELTA = 16
BACKGROUND_QUERY = "\x1b]11;?\x1b\\"
REPLY_PREFIX = "\x1b]11;rgb:"


def luminance(rgb: RGB) -> float:
    return 0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]


class BackgroundProbe:
    def __init__(self, started: float) -> None:
        self.deadline = started + PROBE_SECONDS
        self.background: Optional[RGB] = None
        self.answered = False
        self._candidate = ""
        self._candidate_started = started
        self._discard_terminator = False

    def pending(self, now: float) -> bool:
        return not self.answered and now < self.deadline

    def feed(self, text: str, now: float) -> str:
        result = []
        if self._candidate == "\x1b" and now - self._candidate_started >= ESCAPE_SECONDS:
            result.append(self._candidate)
            self._candidate = ""
        for char in text:
            if not self._candidate:
                if char == "\x1b":
                    self._candidate = char
                    self._candidate_started = now
                elif char == "\x07" and self._discard_terminator:
                    self._discard_terminator = False
                else:
                    result.append(char)
                continue
            if self._candidate == "\x1b" and char == "\\" and self._discard_terminator:
                self._candidate = ""
                self._discard_terminator = False
                continue
            candidate = self._candidate + char
            if REPLY_PREFIX.startswith(candidate):
                self._candidate = candidate
                continue
            if self._candidate.startswith(REPLY_PREFIX):
                payload = candidate[len(REPLY_PREFIX):]
                if char == "\x07" or payload.endswith("\x1b\\"):
                    channels = payload[:-1] if char == "\x07" else payload[:-2]
                    if re.fullmatch(r"[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}", channels):
                        values = [round(int(channel, 16) * 255 / (16 ** len(channel) - 1)) for channel in channels.split("/")]
                        self.background = (values[0], values[1], values[2])
                    self.answered = True
                    self._candidate = ""
                    self._discard_terminator = False
                    continue
                if (re.fullmatch(r"(?:[0-9a-fA-F]{1,4}/){0,2}[0-9a-fA-F]{0,4}", payload)
                        or re.fullmatch(r"[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}/[0-9a-fA-F]{1,4}\x1b", payload)):
                    self._candidate = candidate
                    continue
            if self._candidate.startswith("\x1b]11;"):
                escaped = self._candidate.endswith("\x1b")
                self._discard_terminator = True
                self._candidate = ""
                if char == "\x1b":
                    self._candidate = char
                    self._candidate_started = now
                elif char != "\x07":
                    result.append(("\x1b" if escaped else "") + char)
            elif char == "\x1b":
                result.append(self._candidate)
                self._candidate = char
                self._candidate_started = now
            else:
                result.append(candidate)
                self._candidate = ""
        return "".join(result)

    def selection_style(self, color_system: Optional[str]) -> Optional[Style]:
        if self.background is None or color_system not in {"truecolor", "256"}:
            return None
        background = self.background
        target = 0 if luminance(background) >= LIGHT_BACKGROUND_THRESHOLD else 255
        tint = tuple(round(channel * (1 - SELECTION_BLEND) + target * SELECTION_BLEND) for channel in background)
        color = Color.from_rgb(*tint)
        if color_system == "256":
            color = color.downgrade(ColorSystem.EIGHT_BIT)
            if color.number is None or color.number < 16:
                return None
            quantized = color.get_truecolor()
            change = luminance(quantized) - luminance(background)
            if (change <= 0 if target else change >= 0) or any(
                    abs(a - b) > MAX_PALETTE_CHANNEL_DELTA for a, b in zip(quantized, background)):
                return None
        return Style(bgcolor=color)
