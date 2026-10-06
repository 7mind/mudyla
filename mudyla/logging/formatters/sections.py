"""Shared pure section headings and ordered optional chrome."""

from typing import Optional

from rich.console import Group, RenderableType
from rich.text import Text

HEADING_STYLE = "bold cyan"


def heading(title: str | Text) -> Text:
    text = Text(title) if isinstance(title, str) else title.copy()
    text.style = HEADING_STYLE
    return text


def section(title: str | Text, data: RenderableType, toolbar: Optional[RenderableType],
            footer: Optional[RenderableType]) -> Group:
    rows: list[RenderableType] = [heading(title)]
    if toolbar is not None:
        rows.append(toolbar)
    rows.append(data)
    if footer is not None:
        rows.append(footer)
    return Group(*rows)
