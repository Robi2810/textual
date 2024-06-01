from rich.console import Console, RenderableType

class ErrorHandler():

    def __init__(self):
        self.error_console = Console(markup=False, highlight=False, stderr=True)

    def print_error(self, renderable: RenderableType):
        self.error_console.print(renderable)

    def print_error(self, text: str, markup: bool=True):
        self.error_console.print(text, markup)


    def panic(self, *renderables: RenderableType) -> None:
        """Exits the app and display error message(s).

        Used in response to unexpected errors.
        For a more graceful exit, see the [exit][textual.app.App.exit] method.

        Args:
            *renderables: Text or Rich renderable(s) to display on exit.
        """
        assert all(
            is_renderable(renderable) for renderable in renderables
        ), "Can only call panic with strings or Rich renderables"

        def render(renderable: RenderableType) -> list[Segment]:
            """Render a panic renderables."""
            segments = list(self.console.render(renderable, self.console.options))
            return segments

        pre_rendered = [Segments(render(renderable)) for renderable in renderables]
        self._exit_renderables.extend(pre_rendered)

        self._close_messages_no_wait()

    def _handle_exception(self, error: Exception) -> None:
        """Called with an unhandled exception.

        Always results in the app exiting.

        Args:
            error: An exception instance.
        """
        self._return_code = 1
        # If we're running via pilot and this is the first exception encountered,
        # take note of it so that we can re-raise for test frameworks later.
        if self.is_headless and self._exception is None:
            self._exception = error
            self._exception_event.set()

        if hasattr(error, "__rich__"):
            # Exception has a rich method, so we can defer to that for the rendering
            self.panic(error)
        else:
            # Use default exception rendering
            self._fatal_error()

    def _fatal_error(self) -> None:
        """Exits the app after an unhandled exception."""
        from rich.traceback import Traceback

        self.bell()
        traceback = Traceback(
            show_locals=True, width=None, locals_max_length=5, suppress=[rich]
        )
        self._exit_renderables.append(
            Segments(self.console.render(traceback, self.console.options))
        )
        self._close_messages_no_wait()

    def _print_error_renderables(self) -> None:
        """Print and clear exit renderables."""
        error_count = len(self._exit_renderables)
        if "debug" in self.features:
            for renderable in self._exit_renderables:
                self.error_console.print(renderable)
            if error_count > 1:
                self.error_console.print(
                    f"\n[b]NOTE:[/b] {error_count} errors shown above.", markup=True
                )
        elif self._exit_renderables:
            self.error_console.print(self._exit_renderables[0])
            if error_count > 1:
                self.error_console.print(
                    f"\n[b]NOTE:[/b] 1 of {error_count} errors shown. Run with [b]textual run --dev[/] to see all errors.",
                    markup=True,
                )

        self._exit_renderables.clear()
