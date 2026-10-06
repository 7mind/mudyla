"""Symbol definitions with emoji/ASCII fallbacks.

Provides a clean API for accessing symbols that automatically fall back
to ASCII when emoji support is not available or colors are disabled.

Usage:
    symbols = SymbolsFormatter()
    print(symbols.Globe)  # Returns "🌍" or "*" depending on support
    print(symbols.Check)  # Returns "✅" or "+"
"""

import platform
import sys
from dataclasses import dataclass
from functools import cached_property


@dataclass(frozen=True)
class Symbol:
    """A symbol with emoji and ASCII fallback."""

    emoji: str
    ascii: str


class Symbols:
    """Symbol definitions as class attributes."""

    # Status indicators
    Check = Symbol("✅", "+")
    Cross = Symbol("❌", "x")
    Warning = Symbol("⚠️", "!")
    Info = Symbol("ℹ️", "i")
    Play = Symbol("▶️", ">")

    # Objects
    Globe = Symbol("🌍", "*")
    Folder = Symbol("📂", ">")
    File = Symbol("📄", ">")
    Book = Symbol("📚", ">")
    Target = Symbol("🎯", ">")
    Link = Symbol("🔗", ">")
    Gear = Symbol("⚙️", ">")
    Chart = Symbol("📊", ">")
    Clipboard = Symbol("📋", ">")
    Save = Symbol("💾", ">")
    Id = Symbol("🆔", ">")

    # Arrows and flow
    Recycle = Symbol("♻️", "+")
    Refresh = Symbol("🔄", ">")
    Arrow = Symbol("→", "->")

    # Context symbols (colored circles/shapes)
    CircleRed = Symbol("🔴", "A")
    CircleOrange = Symbol("🟠", "B")
    CircleYellow = Symbol("🟡", "C")
    CircleGreen = Symbol("🟢", "D")
    CircleBlue = Symbol("🔵", "E")
    CirclePurple = Symbol("🟣", "F")
    CircleBrown = Symbol("🟤", "G")
    CircleBlack = Symbol("⚫", "H")

    SquareRed = Symbol("🟥", "J")
    SquareOrange = Symbol("🟧", "K")
    SquareYellow = Symbol("🟨", "L")
    SquareGreen = Symbol("🟩", "M")
    SquareBlue = Symbol("🟦", "N")
    SquarePurple = Symbol("🟪", "P")
    SquareBrown = Symbol("🟫", "Q")
    SquareBlack = Symbol("⬛", "R")

    Star = Symbol("⭐", "S")
    StarGlow = Symbol("🌟", "T")
    Sparkle = Symbol("💫", "U")
    Sparkles = Symbol("✨", "V")

    DiamondOrange = Symbol("🔶", "W")
    DiamondBlue = Symbol("🔷", "X")
    DiamondSmallOrange = Symbol("🔸", "Y")
    DiamondSmallBlue = Symbol("🔹", "Z")

    HeartRed = Symbol("❤️", "1")
    HeartOrange = Symbol("🧡", "2")
    HeartYellow = Symbol("💛", "3")
    HeartGreen = Symbol("💚", "4")
    HeartBlue = Symbol("💙", "5")
    HeartPurple = Symbol("💜", "6")
    HeartBlack = Symbol("🖤", "7")
    HeartWhite = Symbol("🤍", "8")


class SymbolsFormatter:
    """Provides symbols with automatic emoji/ASCII fallback based on terminal support.

    Emoji is disabled when no_color=True or when the terminal doesn't support it.
    """

    def __init__(self, no_color: bool = False):
        """Initialize the symbols formatter.

        Args:
            no_color: If True, always use ASCII symbols instead of emoji
        """
        self._no_color = no_color

    @cached_property
    def supports_emoji(self) -> bool:
        """Detect if terminal supports emoji display."""
        # Disable emoji when no_color is set
        if self._no_color:
            return False

        if platform.system() == "Windows":
            return False

        if not hasattr(sys.stdout, 'encoding') or sys.stdout.encoding is None:
            return False

        encoding = sys.stdout.encoding.lower()
        emoji_encodings = ['utf-8', 'utf8', 'utf-16', 'utf16']

        return any(enc in encoding for enc in emoji_encodings)

    def _resolve(self, symbol: Symbol) -> str:
        """Resolve a symbol to emoji or ASCII based on support."""
        return symbol.emoji if self.supports_emoji else symbol.ascii

    def get(self, symbol: Symbol) -> str:
        """Get the resolved symbol string.

        Args:
            symbol: A Symbol instance to resolve

        Returns:
            Emoji or ASCII string based on terminal support
        """
        return self._resolve(symbol)

    # Status indicators
    @property
    def Check(self) -> str:
        return self._resolve(Symbols.Check)

    @property
    def Cross(self) -> str:
        return self._resolve(Symbols.Cross)

    @property
    def Warning(self) -> str:
        return self._resolve(Symbols.Warning)

    @property
    def Info(self) -> str:
        return self._resolve(Symbols.Info)

    @property
    def Play(self) -> str:
        return self._resolve(Symbols.Play)

    # Objects
    @property
    def Globe(self) -> str:
        return self._resolve(Symbols.Globe)

    @property
    def Folder(self) -> str:
        return self._resolve(Symbols.Folder)

    @property
    def File(self) -> str:
        return self._resolve(Symbols.File)

    @property
    def Book(self) -> str:
        return self._resolve(Symbols.Book)

    @property
    def Target(self) -> str:
        return self._resolve(Symbols.Target)

    @property
    def Link(self) -> str:
        return self._resolve(Symbols.Link)

    @property
    def Gear(self) -> str:
        return self._resolve(Symbols.Gear)

    @property
    def Chart(self) -> str:
        return self._resolve(Symbols.Chart)

    @property
    def Clipboard(self) -> str:
        return self._resolve(Symbols.Clipboard)

    @property
    def Save(self) -> str:
        return self._resolve(Symbols.Save)

    @property
    def Id(self) -> str:
        return self._resolve(Symbols.Id)

    # Arrows and flow
    @property
    def Recycle(self) -> str:
        return self._resolve(Symbols.Recycle)

    @property
    def Refresh(self) -> str:
        return self._resolve(Symbols.Refresh)

    @property
    def Arrow(self) -> str:
        return self._resolve(Symbols.Arrow)

    # Context symbols
    @property
    def CircleRed(self) -> str:
        return self._resolve(Symbols.CircleRed)

    @property
    def CircleOrange(self) -> str:
        return self._resolve(Symbols.CircleOrange)

    @property
    def CircleYellow(self) -> str:
        return self._resolve(Symbols.CircleYellow)

    @property
    def CircleGreen(self) -> str:
        return self._resolve(Symbols.CircleGreen)

    @property
    def CircleBlue(self) -> str:
        return self._resolve(Symbols.CircleBlue)

    @property
    def CirclePurple(self) -> str:
        return self._resolve(Symbols.CirclePurple)

    @property
    def CircleBrown(self) -> str:
        return self._resolve(Symbols.CircleBrown)

    @property
    def CircleBlack(self) -> str:
        return self._resolve(Symbols.CircleBlack)

    @property
    def SquareRed(self) -> str:
        return self._resolve(Symbols.SquareRed)

    @property
    def SquareOrange(self) -> str:
        return self._resolve(Symbols.SquareOrange)

    @property
    def SquareYellow(self) -> str:
        return self._resolve(Symbols.SquareYellow)

    @property
    def SquareGreen(self) -> str:
        return self._resolve(Symbols.SquareGreen)

    @property
    def SquareBlue(self) -> str:
        return self._resolve(Symbols.SquareBlue)

    @property
    def SquarePurple(self) -> str:
        return self._resolve(Symbols.SquarePurple)

    @property
    def SquareBrown(self) -> str:
        return self._resolve(Symbols.SquareBrown)

    @property
    def SquareBlack(self) -> str:
        return self._resolve(Symbols.SquareBlack)

    @property
    def Star(self) -> str:
        return self._resolve(Symbols.Star)

    @property
    def StarGlow(self) -> str:
        return self._resolve(Symbols.StarGlow)

    @property
    def Sparkle(self) -> str:
        return self._resolve(Symbols.Sparkle)

    @property
    def Sparkles(self) -> str:
        return self._resolve(Symbols.Sparkles)

    @property
    def DiamondOrange(self) -> str:
        return self._resolve(Symbols.DiamondOrange)

    @property
    def DiamondBlue(self) -> str:
        return self._resolve(Symbols.DiamondBlue)

    @property
    def DiamondSmallOrange(self) -> str:
        return self._resolve(Symbols.DiamondSmallOrange)

    @property
    def DiamondSmallBlue(self) -> str:
        return self._resolve(Symbols.DiamondSmallBlue)

    @property
    def HeartRed(self) -> str:
        return self._resolve(Symbols.HeartRed)

    @property
    def HeartOrange(self) -> str:
        return self._resolve(Symbols.HeartOrange)

    @property
    def HeartYellow(self) -> str:
        return self._resolve(Symbols.HeartYellow)

    @property
    def HeartGreen(self) -> str:
        return self._resolve(Symbols.HeartGreen)

    @property
    def HeartBlue(self) -> str:
        return self._resolve(Symbols.HeartBlue)

    @property
    def HeartPurple(self) -> str:
        return self._resolve(Symbols.HeartPurple)

    @property
    def HeartBlack(self) -> str:
        return self._resolve(Symbols.HeartBlack)

    @property
    def HeartWhite(self) -> str:
        return self._resolve(Symbols.HeartWhite)
