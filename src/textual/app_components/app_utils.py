import inspect
import io
import os
from datetime import datetime

from rich.console import Console

from . import events

class AppUtils():

    def __init__(
        self,
        app: App
    ):
        self._app = app
        self._driver = app._driver

    def copy_to_clipboard(self, text: str) -> None:
        """Copy text to the clipboard.

        !!! note

            This does not work on macOS Terminal, but will work on most other terminals.

        Args:
            text: Text you wish to copy to the clipboard.
        """
        if self._driver is None:
            return

        import base64

        base64_text = base64.b64encode(text.encode("utf-8")).decode("utf-8")
        self._driver.write(f"\x1b]52;c;{base64_text}\a")

    def export_screenshot(self, *, title: str | None = None) -> str:
        """Export an SVG screenshot of the current screen.

        See also [save_screenshot][textual.app.App.save_screenshot] which writes the screenshot to a file.

        Args:
            title: The title of the exported screenshot or None
                to use app title.
        """
        assert self._driver is not None, "App must be running"
        width, height = self.size

        console = Console(
            width=width,
            height=height,
            file=io.StringIO(),
            force_terminal=True,
            color_system="truecolor",
            record=True,
            legacy_windows=False,
            safe_box=False,
        )
        screen_render = self.screen._compositor.render_update(
            full=True, screen_stack=self.app._background_screens
        )
        console.print(screen_render)
        return console.export_svg(title=title or self.title)
    
    def save_screenshot(
        self,
        filename: str | None = None,
        path: str | None = None,
        time_format: str | None = None,
    ) -> str:
        """Save an SVG screenshot of the current screen.

        Args:
            filename: Filename of SVG screenshot, or None to auto-generate
                a filename with the date and time.
            path: Path to directory for output. Defaults to current working directory.
            time_format: Date and time format to use if filename is None.
                Defaults to a format like ISO 8601 with some reserved characters replaced with underscores.

        Returns:
            Filename of screenshot.
        """
        path = path or "./"
        if not filename:
            if time_format is None:
                dt = datetime.now().isoformat()
            else:
                dt = datetime.now().strftime(time_format)
            svg_filename_stem = f"{self.title.lower()} {dt}"
            for reserved in ' <>:"/\\|?*.':
                svg_filename_stem = svg_filename_stem.replace(reserved, "_")
            svg_filename = svg_filename_stem + ".svg"
        else:
            svg_filename = filename
        svg_path = os.path.expanduser(os.path.join(path, svg_filename))
        screenshot_svg = self.export_screenshot()
        with open(svg_path, "w", encoding="utf-8") as svg_file:
            svg_file.write(screenshot_svg)
        return svg_path
    
    def _flush(self, stderr: bool = False) -> None:
        """Called when stdout or stderr is flushed.

        Args:
            stderr: True if the print was to stderr, or False for stdout.

        """
        if self._devtools_redirector is not None:
            self._devtools_redirector.flush()

    def _print(self, text: str, stderr: bool = False) -> None:
        """Called with captured print.

        Dispatches printed content to appropriate destinations: devtools,
        widgets currently capturing output, stdout/stderr.

        Args:
            text: Text that has been printed.
            stderr: True if the print was to stderr, or False for stdout.
        """
        if self._devtools_redirector is not None:
            current_frame = inspect.currentframe()
            self._devtools_redirector.write(
                text, current_frame.f_back if current_frame is not None else None
            )

        # If we're in headless mode, we want printed text to still reach stdout/stderr.
        if self.is_headless:
            target_stream = self._original_stderr if stderr else self._original_stdout
            target_stream.write(text)

        # Send Print events to all widgets that are currently capturing output.
        for target, (_stdout, _stderr) in self._capture_print.items():
            if (_stderr and stderr) or (_stdout and not stderr):
                target.post_message(events.Print(text, stderr=stderr))