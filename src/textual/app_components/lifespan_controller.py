import asyncio
import threading
import sys

from asyncio import Task, create_task
from contextlib import (
    contextmanager,
    redirect_stderr,
    redirect_stdout
)
from typing import (
    Generic,
    TypeVar,
    Iterable,
    Iterator
)

from rich.console import Console, RenderableType

from . import constants, events, on

from .._context import active_app, active_message_pump
from ..driver import Driver
from ..widget import AwaitMount, Widget
from ..signal import Signal

from .app import App
from .error_handler import ErrorHandler

_ASYNCIO_GET_EVENT_LOOP_IS_DEPRECATED = sys.version_info >= (3, 10, 0)

AutopilotCallbackType: TypeAlias = (
    "Callable[[Pilot[object]], Coroutine[Any, Any, None]]"
)
"""Signature for valid callbacks that can be used to control apps."""


ReturnType = TypeVar("ReturnType")

class SuspendNotSupported(Exception):
    """Raised if suspending the application is not supported.

    This exception is raised if [`App.suspend`][textual.app.App.suspend] is called while
    the application is running in an environment where this isn't supported.
    """


class AppLifespanController(Generic[ReturnType]):

    def __init__(
        self,
        app: App,
        driver: Driver,
        error_handler: ErrorHandler
    ):
        self._app = app
        self._driver = driver
        self.app_suspend_signal: Signal[App] = Signal(self, "app-suspend")
        self.app_resume_signal: Signal[App] = Signal(self, "app-resume")

        self._exit = False
        self._return_value: ReturnType | None = None
        self._return_code: int | None = None
        self._exit_renderables: list[RenderableType] = []

        self._error_handler = error_handler

    def exit(
        self,
        result: ReturnType | None = None,
        return_code: int = 0,
        message: RenderableType | None = None,
    ) -> None:
        """Exit the app, and return the supplied result.

        Args:
            result: Return value.
            return_code: The return code. Use non-zero values for error codes.
            message: Optional message to display on exit.
        """
        self._exit = True
        self._return_value = result
        self._return_code = return_code
        self._app.post_message(messages.ExitApp()) # FIXME!!!
        if message:
            self._exit_renderables.append(message)


    def _print_error_renderables(self) -> None:
        """Print and clear exit renderables."""
        error_count = len(self._exit_renderables)
        if "debug" in self._app.features:
            for renderable in self._exit_renderables:
                self.error_handler.print_error(renderable)
            if error_count > 1:
                self.error_handler.print_error(
                    f"\n[b]NOTE:[/b] {error_count} errors shown above.", markup=True
                )
        elif self._exit_renderables:
            self.error_handler.print_error(self._exit_renderables[0])
            if error_count > 1:
                self.error_handler.print_error(
                    f"\n[b]NOTE:[/b] 1 of {error_count} errors shown. Run with [b]textual run --dev[/] to see all errors.",
                    markup=True,
                )

        self._exit_renderables.clear()


    async def run_async(
        self,
        *,
        headless: bool = False,
        inline: bool = False,
        inline_no_clear: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
        auto_pilot: AutopilotCallbackType | None = None,
    ) -> ReturnType | None:
        """Run the app asynchronously.

        Args:
            headless: Run in headless mode (no output).
            inline: Run the app inline (under the prompt).
            inline_no_clear: Don't clear the app output when exiting an inline app.
            mouse: Enable mouse support.
            size: Force terminal size to `(WIDTH, HEIGHT)`,
                or None to auto-detect.
            auto_pilot: An auto pilot coroutine.

        Returns:
            App return value.
        """
        from ..pilot import Pilot

        app = self._app

        auto_pilot_task: Task | None = None

        if auto_pilot is None and constants.PRESS:
            keys = constants.PRESS.split(",")

            async def press_keys(pilot: Pilot) -> None:
                """Auto press keys."""
                await pilot.press(*keys)

            auto_pilot = press_keys

        async def app_ready() -> None:
            """Called by the message loop when the app is ready."""
            nonlocal auto_pilot_task

            if auto_pilot is not None:

                async def run_auto_pilot(
                    auto_pilot: AutopilotCallbackType, pilot: Pilot
                ) -> None:
                    try:
                        await auto_pilot(pilot)
                    except Exception:
                        self.exit()
                        raise

                pilot = Pilot(app)
                active_message_pump.set(self)
                auto_pilot_task = create_task(
                    run_auto_pilot(auto_pilot, pilot), name=repr(pilot)
                )

        try:
            app._loop = asyncio.get_running_loop()
            app._thread_id = threading.get_ident()

            await app._process_messages(
                ready_callback=None if auto_pilot is None else app_ready,
                headless=headless,
                inline=inline,
                inline_no_clear=inline_no_clear,
                mouse=mouse,
                terminal_size=size,
            )
        finally:
            try:
                if auto_pilot_task is not None:
                    await auto_pilot_task
            finally:
                await self._shutdown()

        return self.return_value

    def run(
        self,
        *,
        headless: bool = False,
        inline: bool = False,
        inline_no_clear: bool = False,
        mouse: bool = True,
        size: tuple[int, int] | None = None,
        auto_pilot: AutopilotCallbackType | None = None,
    ) -> ReturnType | None:
        """Run the app.

        Args:
            headless: Run in headless mode (no output).
            inline: Run the app inline (under the prompt).
            inline_no_clear: Don't clear the app output when exiting an inline app.
            mouse: Enable mouse support.
            size: Force terminal size to `(WIDTH, HEIGHT)`,
                or None to auto-detect.
            auto_pilot: An auto pilot coroutine.

        Returns:
            App return value.
        """

        async def run_app() -> None:
            """Run the app."""
            self._loop = asyncio.get_running_loop()
            self._thread_id = threading.get_ident()
            try:
                await self.run_async(
                    headless=headless,
                    inline=inline,
                    inline_no_clear=inline_no_clear,
                    mouse=mouse,
                    size=size,
                    auto_pilot=auto_pilot,
                )
            finally:
                self._loop = None
                self._thread_id = 0

        if _ASYNCIO_GET_EVENT_LOOP_IS_DEPRECATED:
            # N.B. This doesn't work with Python<3.10, as we end up with 2 event loops:
            asyncio.run(run_app())
        else:
            # However, this works with Python<3.10:
            event_loop = asyncio.get_event_loop()
            event_loop.run_until_complete(run_app())
        return self.return_value
    


    def mount(
        self,
        *widgets: Widget,
        before: int | str | Widget | None = None,
        after: int | str | Widget | None = None,
    ) -> AwaitMount:
        """Mount the given widgets relative to the app's screen.

        Args:
            *widgets: The widget(s) to mount.
            before: Optional location to mount before. An `int` is the index
                of the child to mount before, a `str` is a `query_one` query to
                find the widget to mount before.
            after: Optional location to mount after. An `int` is the index
                of the child to mount after, a `str` is a `query_one` query to
                find the widget to mount after.

        Returns:
            An awaitable object that waits for widgets to be mounted.

        Raises:
            MountError: If there is a problem with the mount request.

        Note:
            Only one of ``before`` or ``after`` can be provided. If both are
            provided a ``MountError`` will be raised.
        """
        return self._app.screen.mount(*widgets, before=before, after=after)
    

    def mount_all(
        self,
        widgets: Iterable[Widget],
        *,
        before: int | str | Widget | None = None,
        after: int | str | Widget | None = None,
    ) -> AwaitMount:
        """Mount widgets from an iterable.

        Args:
            widgets: An iterable of widgets.
            before: Optional location to mount before. An `int` is the index
                of the child to mount before, a `str` is a `query_one` query to
                find the widget to mount before.
            after: Optional location to mount after. An `int` is the index
                of the child to mount after, a `str` is a `query_one` query to
                find the widget to mount after.

        Returns:
            An awaitable object that waits for widgets to be mounted.

        Raises:
            MountError: If there is a problem with the mount request.

        Note:
            Only one of ``before`` or ``after`` can be provided. If both are
            provided a ``MountError`` will be raised.
        """
        return self.mount(*widgets, before=before, after=after)
    

    async def _shutdown(self) -> None:
        self._app._begin_batch()  # Prevents any layout / repaint while shutting down
        driver = self._driver
        self._running = False
        if driver is not None:
            driver.disable_input()
        await self._app._close_all()
        await self._app._close_messages()

        await self._dispatch_message(events.Unmount())

        if self._driver is not None:
            self._driver.close()

        if self.devtools is not None and self.devtools.is_connected:
            await self._disconnect_devtools()

        self._print_error_renderables()

        if constants.SHOW_RETURN:
            from rich.console import Console
            from rich.pretty import Pretty

            console = Console()
            console.print("[b]The app returned:")
            console.print(Pretty(self._return_value))

    async def _on_exit_app(self) -> None:
        self._app._begin_batch()  # Prevent repaint / layout while shutting down
        await self._app._message_queue.put(None)


    def _suspend_signal(self) -> None:
        """Signal that the application is being suspended."""
        self.app_suspend_signal.publish(self)

    @on(Driver.SignalResume)
    def _resume_signal(self) -> None:
        """Signal that the application is being resumed from a suspension."""
        self.app_resume_signal.publish(self)

    @contextmanager
    def suspend(self) -> Iterator[None]:
        """A context manager that temporarily suspends the app.

        While inside the `with` block, the app will stop reading input and
        emitting output. Other applications will have full control of the
        terminal, configured as it was before the app started running. When
        the `with` block ends, the application will start reading input and
        emitting output again.

        Example:
            ```python
            with self.suspend():
                os.system("emacs -nw")
            ```

        Raises:
            SuspendNotSupported: If the environment doesn't support suspending.

        !!! note
            Suspending the application is currently only supported on
            Unix-like operating systems and Microsoft Windows. Suspending is
            not supported in Textual Web.
        """
        if self._driver is None:
            return
        if self._driver.can_suspend:
            # Publish a suspend signal *before* we suspend application mode.
            self._suspend_signal()
            self._driver.suspend_application_mode()
            # We're going to handle the start of the driver again so mark
            # this next part as such; the reason for this is that the code
            # the developer may be running could be in this process, and on
            # Unix-like systems the user may `action_suspend_process` the
            # app, and we don't want to have the driver auto-restart
            # application mode when the application comes back to the
            # foreground, in this context.
            with self._driver.no_automatic_restart(), redirect_stdout(
                sys.__stdout__
            ), redirect_stderr(sys.__stderr__):
                yield
            # We're done with the dev's code so resume application mode.
            self._driver.resume_application_mode()
            # ...and publish a resume signal.
            self._resume_signal()
        else:
            raise SuspendNotSupported(
                "App.suspend is not supported in this environment."
            )