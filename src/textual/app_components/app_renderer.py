
import inspect
from contextlib import contextmanager
from time import perf_counter
from typing import Generator

from .app import App

from rich.console import RenderableType
from rich.control import Control

from rich.terminal_theme import TerminalTheme

from .._compositor import CompositorUpdate
from ..css.errors import StylesheetError
from ..css.stylesheet import Stylesheet
from ..design import ColorSystem
from ..dom import DOMNode
from ..file_monitor import FileMonitor
from ..filter import ANSIToTruecolor
from ..keys import _get_key_display
from ..reactive import Reactive
from ..renderables.blank import Blank
from ..screen import Screen

if TYPE_CHECKING:
    from typing_extensions import Self

DEFAULT_COLORS = {
    "dark": ColorSystem(
        primary="#004578",
        secondary="#ffa62b",
        warning="#ffa62b",
        error="#ba3c5b",
        success="#4EBF71",
        accent="#0178D4",
        dark=True,
    ),
    "light": ColorSystem(
        primary="#004578",
        secondary="#ffa62b",
        warning="#ffa62b",
        error="#ba3c5b",
        success="#4EBF71",
        accent="#0178D4",
        dark=False,
    ),
}

class AppRenderer():

    dark: Reactive[bool] = Reactive(True, compute=False)

    def __init__(
        self,
        app: App,
        watch_css: bool = False
    ):
        self._app = app
        self._batch_count = 0
        self.design = DEFAULT_COLORS
        self.css_monitor = (
            FileMonitor(self.css_path, self._on_css_change)
            if watch_css or self.debug
            else None
        )
        self._css_has_errors = False
        self.stylesheet = Stylesheet(variables=self.get_css_variables())


    @contextmanager
    def batch_update(self) -> Generator[None, None, None]:
        """A context manager to suspend all repaints until the end of the batch."""
        self._begin_batch()
        try:
            yield
        finally:
            self._end_batch()

    def _begin_batch(self) -> None:
        """Begin a batch update."""
        self._batch_count += 1

    def _end_batch(self) -> None:
        """End a batch update."""
        self._batch_count -= 1
        assert self._batch_count >= 0, "This won't happen if you use `batch_update`"
        if not self._batch_count:
            self.check_idle()

    def get_css_variables(self) -> dict[str, str]:
        """Get a mapping of variables used to pre-populate CSS.

        May be implemented in a subclass to add new CSS variables.

        Returns:
            A mapping of variable name to value.
        """
        variables = self.design["dark" if self.dark else "light"].generate()
        return variables
    
    def watch_dark(self, dark: bool) -> None:
        """Watches the dark bool.

        This method handles the transition between light and dark mode when you
        change the [dark][textual.app.App.dark] attribute.
        """
        self.set_class(dark, "-dark-mode", update=False)
        self.set_class(not dark, "-light-mode", update=False)
        self._refresh_truecolor_filter(self.ansi_theme)
        self.call_later(self.refresh_css)

    def watch_ansi_theme_dark(self, theme: TerminalTheme) -> None:
        if self.dark:
            self._refresh_truecolor_filter(theme)
            self.call_later(self.refresh_css)

    def watch_ansi_theme_light(self, theme: TerminalTheme) -> None:
        if not self.dark:
            self._refresh_truecolor_filter(theme)
            self.call_later(self.refresh_css)

    @property
    def ansi_theme(self) -> TerminalTheme:
        """The ANSI TerminalTheme currently being used.

        Defines how colors defined as ANSI (e.g. `magenta`) inside Rich renderables
        are mapped to hex codes.
        """
        return self.ansi_theme_dark if self.dark else self.ansi_theme_light

    def _refresh_truecolor_filter(self, theme: TerminalTheme) -> None:
        """Update the ANSI to Truecolor filter, if available, with a new theme mapping.

        Args:
            theme: The new terminal theme to use for mapping ANSI to truecolor.
        """
        filters = self._filters
        for index, filter in enumerate(filters):
            if isinstance(filter, ANSIToTruecolor):
                filters[index] = ANSIToTruecolor(theme)
                return
            
    def get_key_display(self, key: str) -> str:
        """For a given key, return how it should be displayed in an app
        (e.g. in the Footer widget).
        By key, we refer to the string used in the "key" argument for
        a Binding instance. By overriding this method, you can ensure that
        keys are displayed consistently throughout your app, without
        needing to add a key_display to every binding.

        Args:
            key: The binding key string.

        Returns:
            The display string for the input key.
        """
        return _get_key_display(key)
    

    async def _on_css_change(self) -> None:
        """Callback for the file monitor, called when CSS files change."""
        css_paths = (
            self.css_monitor._paths if self.css_monitor is not None else self.css_path
        )
        if css_paths:
            try:
                time = perf_counter()
                stylesheet = self.stylesheet.copy()
                try:
                    stylesheet.read_all(css_paths)
                except StylesheetError as error:
                    # If one of the CSS paths is no longer available (or perhaps temporarily unavailable),
                    #  we'll end up with partial CSS, which is probably confusing more than anything. We opt to do
                    #  nothing here, knowing that we'll retry again very soon, on the next file monitor invocation.
                    #  Related issue: https://github.com/Textualize/textual/issues/3996
                    self.log.warning(str(error))
                    return
                stylesheet.parse()
                elapsed = (perf_counter() - time) * 1000
                if self._css_has_errors:
                    from rich.panel import Panel

                    self.log.system(
                        Panel(
                            "CSS files successfully loaded after previous error:\n\n- "
                            + "\n- ".join(str(path) for path in css_paths),
                            style="green",
                            border_style="green",
                        )
                    )
                self.log.system(
                    f"<stylesheet> loaded {len(css_paths)} CSS files in {elapsed:.0f} ms"
                )
            except Exception as error:
                # TODO: Catch specific exceptions
                self._css_has_errors = True
                self.log.error(error)
                self.bell()
            else:
                self._css_has_errors = False
                self.stylesheet = stylesheet
                self.stylesheet.update(self)
                for screen in self.screen_stack:
                    self.stylesheet.update(screen)


    def render(self) -> RenderResult:
        """Render method inherited from widget, to render the screen's background.

        May be override to customize background visuals.

        """
        return Blank(self.styles.background)
    

    def update_styles(self, node: DOMNode) -> None:
        """Immediately update the styles of this node and all descendant nodes.

        Should be called whenever CSS classes / pseudo classes change.
        For example, when you hover over a button, the :hover pseudo class
        will be added, and this method is called to apply the corresponding
        :hover styles.
        """
        descendants = node.walk_children(with_self=True) #FIXME
        self.stylesheet.update_nodes(descendants, animate=True)


    def _load_screen_css(self, screen: Screen):
        """Loads the CSS associated with a screen."""

        if self.css_monitor is not None:
            self.css_monitor.add_paths(screen.css_path)

        update = False
        for path in screen.css_path:
            if not self.stylesheet.has_source(str(path), ""):
                self.stylesheet.read(path)
                update = True
        if screen.CSS:
            try:
                screen_path = inspect.getfile(screen.__class__)
            except (TypeError, OSError):
                screen_path = ""
            screen_class_var = f"{screen.__class__.__name__}.CSS"
            read_from = (screen_path, screen_class_var)
            if not self.stylesheet.has_source(screen_path, screen_class_var):
                self.stylesheet.add_source(
                    screen.CSS,
                    read_from=read_from,
                    is_default_css=False,
                    scope=screen._css_type_name if screen.SCOPED_CSS else "",
                )
                update = True
        if update:
            self.stylesheet.reparse()
            self.stylesheet.update(self)

    def refresh(
        self,
        *,
        repaint: bool = True,
        layout: bool = False,
        recompose: bool = False,
    ) -> Self:
        """Refresh the entire screen.

        Args:
            repaint: Repaint the widget (will call render() again).
            layout: Also layout widgets in the view.
            recompose: Re-compose the widget (will remove and re-mount children).

        Returns:
            The `App` instance.
        """
        if recompose:
            self._recompose_required = recompose
            self.call_next(self._check_recompose)
            return self

        if self._screen_stack:
            self.screen.refresh(repaint=repaint, layout=layout)
        self.check_idle()
        return self

    def refresh_css(self, animate: bool = True) -> None:
        """Refresh CSS.

        Args:
            animate: Also execute CSS animations.
        """
        stylesheet = self.app.stylesheet
        stylesheet.set_variables(self.get_css_variables())
        stylesheet.reparse()
        stylesheet.update(self.app, animate=animate)
        self.screen._refresh_layout(self.size)
        # The other screens in the stack will need to know about some style
        # changes, as a final pass let's check in on every screen that isn't
        # the current one and update them too.
        for screen in self.screen_stack:
            if screen != self.screen:
                stylesheet.update(screen, animate=animate)

    def _display(self, screen: Screen, renderable: RenderableType | None) -> None:
        """Display a renderable within a sync.

        Args:
            screen: Screen instance
            renderable: A Rich renderable.
        """

        try:
            if renderable is None:
                return

            if (
                self._running
                and not self._closed
                and not self.is_headless
                and self._driver is not None
            ):
                console = self.console
                self._begin_update()
                try:
                    try:
                        if isinstance(renderable, CompositorUpdate):
                            cursor_position = self.screen.outer_size.clamp_offset(
                                self.cursor_position
                            )
                            if self._driver.is_inline:
                                terminal_sequence = Control.move(
                                    *(-self._previous_cursor_position)
                                ).segment.text
                                terminal_sequence += renderable.render_segments(console)
                                terminal_sequence += Control.move(
                                    *cursor_position
                                ).segment.text
                            else:
                                terminal_sequence = renderable.render_segments(console)
                                terminal_sequence += Control.move_to(
                                    *cursor_position
                                ).segment.text
                            self._previous_cursor_position = cursor_position
                        else:
                            segments = console.render(renderable)
                            terminal_sequence = console._render_buffer(segments)
                    except Exception as error:
                        self._handle_exception(error)
                    else:
                        if WINDOWS:
                            # Combat a problem with Python on Windows.
                            #
                            # https://github.com/Textualize/textual/issues/2548
                            # https://github.com/python/cpython/issues/82052
                            CHUNK_SIZE = 8192
                            write = self._driver.write
                            for chunk in (
                                terminal_sequence[offset : offset + CHUNK_SIZE]
                                for offset in range(
                                    0, len(terminal_sequence), CHUNK_SIZE
                                )
                            ):
                                write(chunk)
                        else:
                            self._driver.write(terminal_sequence)
                finally:
                    self._end_update()

                self._driver.flush()

        finally:
            self.post_display_hook()

    def post_display_hook(self) -> None:
        """Called immediately after a display is done. Used in tests."""
