"""One terminal display lifetime shared by planning and action rendering."""

import os
import threading
from typing import Literal, cast

from rich.console import Console, RenderableType
from rich.control import Control
from rich.live import Live
from rich.segment import Segment, Segments
from rich.text import Text


def effective_console(console: Console | None, *, no_color: bool, force_interactive: bool) -> Console:
    if console is not None and (not force_interactive or console.is_interactive and not console.is_dumb_terminal):
        return console
    terminal_env = dict(os.environ)
    if terminal_env.get("TERM") in {"dumb", "unknown"}:
        terminal_env["TERM"] = "xterm-256color"
    return Console(file=console.file if console is not None else None,
                   width=console._width if console is not None else None,
                   height=console._height if console is not None else None,
                   color_system=cast(Literal["auto", "standard", "256", "truecolor", "windows"] | None,
                                     console.color_system) if console is not None else "auto",
                   force_terminal=True, force_interactive=True, no_color=no_color, _environ=terminal_env)


class InlineDisplay:
    """Keep the mutable frame in one logical terminal line across reflow."""

    def __init__(self, renderable: RenderableType, console: Console):
        self.console = console
        self.renderable = renderable
        self.started = False
        self.transient = True

    def start(self) -> None:
        self.started = True
        self.console.show_cursor(False)

    def update(self, renderable: RenderableType, refresh: bool) -> None:
        self.renderable = renderable
        if not refresh or not self.started:
            return
        width, height = self.console.size
        rows = self.console.render_lines(renderable, pad=False)[:height]
        segments = [segment for row in rows
                    for segment in Segment.adjust_line_length(row, width, pad=True)]
        self.console.file.write("\x1b[J")
        self.console.file.flush()
        self.console.print(Segments(segments), end="", soft_wrap=True)
        self.console.control(Control.move_to_column(0), Control.move(y=1 - len(rows)))

    def stop(self) -> None:
        if not self.started:
            return
        self.started = False
        try:
            self.console.file.write("\x1b[J")
            self.console.file.flush()
        finally:
            self.console.show_cursor(True)

    def clear(self) -> None:
        self.console.file.write("\x1b[J")
        self.console.file.flush()


class DisplaySession:
    def __init__(self, console: Console) -> None:
        self.console = console
        self.live: Live | InlineDisplay | None = None
        self.screen_active = False
        self.lock = threading.RLock()

    def clear(self) -> None:
        with self.lock:
            if isinstance(self.live, InlineDisplay):
                self.live.clear()
            elif self.live is not None:
                self.live.update(Text(""), refresh=True)

    def start(self, frame: RenderableType, *, fullscreen: bool) -> None:
        with self.lock:
            screen = fullscreen and self.console.is_terminal and not self.console.legacy_windows
            if self.live is not None and self.screen_active != screen:
                self.close(discard=False)
            if self.live is None:
                if not screen and self.console.is_terminal and not self.console.legacy_windows and not self.console.is_dumb_terminal:
                    self.live = InlineDisplay(frame, self.console)
                else:
                    self.live = Live(frame, console=self.console, screen=screen,
                                     refresh_per_second=24, transient=False, auto_refresh=False,
                                     vertical_overflow="crop")
                self.screen_active = screen
                self.live.start()

    def update(self, frame: RenderableType, *, fullscreen: bool) -> None:
        with self.lock:
            self.start(frame, fullscreen=fullscreen)
            assert self.live is not None
            self.live.update(frame, refresh=True)

    def close(self, *, discard: bool) -> None:
        with self.lock:
            live, self.live = self.live, None
            try:
                if live is not None:
                    live.transient = True
                    if discard:
                        with self.console.capture():
                            pass
                        live.update(Text(""), refresh=False)
                    try:
                        live.stop()
                    except BaseException as error:
                        # Live may fail before installing the hook that its stop requires.
                        restorations = [lambda: self.console.show_cursor(True)]
                        if self.screen_active:
                            restorations.append(lambda: self.console.set_alt_screen(False))
                        for restore in restorations:
                            try:
                                restore()
                            except BaseException as restore_error:
                                error.add_note(f"Terminal display restoration failed: {restore_error}")
                        raise
            finally:
                self.screen_active = False
