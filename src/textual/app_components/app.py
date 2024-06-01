from __future__ import annotations

import asyncio
import importlib
import inspect
import os
import threading
from concurrent.futures import Future
from functools import partial
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    Generic,
    Type,
    TypeVar,
    overload
)

import rich
import rich.repr
from rich.console import Console


from . import (
    Logger,
    LogGroup,
    LogVerbosity,
    constants,
    events,
)
from .._animator import DEFAULT_EASING, Animatable, Animator, EasingFunction
from .._callback import invoke
from .._path import CSSPathType
from .._types import AnimationLevel
from .._worker_manager import WorkerManager
from ..dom import DOMNode
from ..driver import Driver
from ..features import FeatureFlag, parse_features
from ..geometry import Region, Size
from ..messages import CallbackType
from ..notifications import Notification, Notify, SeverityLevel
from ..reactive import Reactive
from ..screen import Screen
from ..widget import Widget

from .app_compositor import AppCompositor
from .app_event_handler import AppEventHandler
from .app_renderer import AppRenderer
from .app_screen_controller import AppScreenController
from .lifespan_controller import AppLifespanController
from .error_handler import ErrorHandler
from .app_utils import AppUtils

if TYPE_CHECKING:
    from textual_dev.client import DevtoolsClient

ReturnType = TypeVar("ReturnType")
CallThreadReturnType = TypeVar("CallThreadReturnType")


class App(Generic[ReturnType], DOMNode):

    TITLE: str | None = None
    """A class variable to set the *default* title for the application.

    To update the title while the app is running, you can set the [title][textual.app.App.title] attribute.
    See also [the `Screen.TITLE` attribute][textual.screen.Screen.TITLE].
    """

    SUB_TITLE: str | None = None
    """A class variable to set the default sub-title for the application.

    To update the sub-title while the app is running, you can set the [sub_title][textual.app.App.sub_title] attribute.
    See also [the `Screen.SUB_TITLE` attribute][textual.screen.Screen.SUB_TITLE].
    """


    title: Reactive[str] = Reactive("", compute=False)
    sub_title: Reactive[str] = Reactive("", compute=False)
    
    def __init__(
        self,
        driver_class: Type[Driver] | None = None,
        css_path: CSSPathType | None = None,
        watch_css: bool = False
    ):
        super().__init__()
        self.features: frozenset[FeatureFlag] = parse_features(os.getenv("TEXTUAL", ""))
        environ = dict(os.environ)
        
        self.console = Console(
            color_system=constants.COLOR_SYSTEM,
            file=_NullFile(),
            markup=True,
            highlight=False,
            emoji=False,
            legacy_windows=False,
            _environ=environ,
            force_terminal=True,
            safe_box=False,
            soft_wrap=False,
        )
        self._workers = WorkerManager(self)
        self.driver_class = driver_class or self.get_driver_class()
        self._driver: Driver | None = None
        self._animator = Animator(self)
        self._animate = self._animator.bind(self)

        self.title = (
            self.TITLE if self.TITLE is not None else f"{self.__class__.__name__}"
        )
        """The title for the application.

        The initial value for `title` will be set to the `TITLE` class variable if it exists, or
        the name of the app if it doesn't.
        """

        self.sub_title = self.SUB_TITLE if self.SUB_TITLE is not None else ""
        """The sub-title for the application.

        The initial value for `sub_title` will be set to the `SUB_TITLE` class variable if it exists, or
        an empty string if it doesn't.
        """

        self._logger = Logger(self._log)
        self.devtools: DevtoolsClient | None = None
        self._devtools_redirector: StdoutRedirector | None = None
        if "devtools" in self.features:
            try:
                from textual_dev.client import DevtoolsClient
                from textual_dev.redirect_output import StdoutRedirector
            except ImportError:
                # Dev dependencies not installed
                pass
            else:
                self.devtools = DevtoolsClient(constants.DEVTOOLS_HOST)
                self._devtools_redirector = StdoutRedirector(self.devtools)


        self._error_handler = ErrorHandler()
        self._app_screen_controller = AppScreenController(app=self)
        self._app_lifespan_controller = AppLifespanController(
            app=self, driver=self._driver, error_handler=self._error_handler
        )
        self._app_compositor = AppCompositor(app=self, app_screen_controller=self._app_screen_controller)
        self._app_renderer = AppRenderer(app=self, watch_css=watch_css)
        self._app_event_handler = AppEventHandler(app=self, css_path=css_path)
        self.app_utils = AppUtils(self)
        


    def validate_title(self, title: Any) -> str:
        """Make sure the title is set to a string."""
        return str(title)

    def validate_sub_title(self, sub_title: Any) -> str:
        """Make sure the sub-title is set to a string."""
        return str(sub_title)
    
    def compose(self) -> ComposeResult:
        """Yield child widgets for a container.

        This method should be implemented in a subclass.
        """
        yield from ()

    
    def get_driver_class(self) -> Type[Driver]:
        """Get a driver class for this platform.

        This method is called by the constructor, and unlikely to be required when
        building a Textual app.

        Returns:
            A Driver class which manages input and display.
        """

        driver_class: Type[Driver]

        driver_import = constants.DRIVER
        if driver_import is not None:
            # The driver class is set from the environment
            # Syntax should be foo.bar.baz:MyDriver
            module_import, _, driver_symbol = driver_import.partition(":")
            driver_module = importlib.import_module(module_import)
            driver_class = getattr(driver_module, driver_symbol)
            if not inspect.isclass(driver_class) or not issubclass(
                driver_class, Driver
            ):
                raise RuntimeError(
                    f"Unable to import {driver_import!r}; {driver_class!r} is not a Driver class "
                )
            return driver_class

        if WINDOWS:
            from .drivers.windows_driver import WindowsDriver

            driver_class = WindowsDriver
        else:
            from .drivers.linux_driver import LinuxDriver

            driver_class = LinuxDriver
        return driver_class
    
    def _build_driver(
        self, headless: bool, inline: bool, mouse: bool, size: tuple[int, int] | None
    ) -> Driver:
        """Construct a driver instance.

        Args:
            headless: Request headless driver.
            inline: Request inline driver.
            mouse: Request mouse support.
            size: Initial size.

        Returns:
            Driver instance.
        """
        driver: Driver
        driver_class: type[Driver]
        if headless:
            from .drivers.headless_driver import HeadlessDriver

            driver_class = HeadlessDriver
        elif inline and not WINDOWS:
            from .drivers.linux_inline_driver import LinuxInlineDriver

            driver_class = LinuxInlineDriver
        else:
            driver_class = self.driver_class

        driver = self._driver = driver_class(
            self,
            debug=constants.DEBUG,
            mouse=mouse,
            size=size,
        )
        return driver

    def panic(self, *renderables: RenderableType) -> None:
        """Exits the app and display error message(s).
        Used in response to unexpected errors.
        For a more graceful exit, see the [exit][textual.app.App.exit] method.
        Args:
            *renderables: Text or Rich renderable(s) to display on exit.
        """
        self._error_handler.panic(*renderables)
    

    @property
    def workers(self) -> WorkerManager:
        """The [worker](/guide/workers/) manager.

        Returns:
            An object to manage workers.
        """
        return self._workers

    def animate(
        self,
        attribute: str,
        value: float | Animatable,
        *,
        final_value: object = ...,
        duration: float | None = None,
        speed: float | None = None,
        delay: float = 0.0,
        easing: EasingFunction | str = DEFAULT_EASING,
        on_complete: CallbackType | None = None,
        level: AnimationLevel = "full",
    ) -> None:
        """Animate an attribute.

        See the guide for how to use the [animation](/guide/animation) system.

        Args:
            attribute: Name of the attribute to animate.
            value: The value to animate to.
            final_value: The final value of the animation.
            duration: The duration (in seconds) of the animation.
            speed: The speed of the animation.
            delay: A delay (in seconds) before the animation starts.
            easing: An easing method.
            on_complete: A callable to invoke when the animation is finished.
            level: Minimum level required for the animation to take place (inclusive).
        """
        self._animate(
            attribute,
            value,
            final_value=final_value,
            duration=duration,
            speed=speed,
            delay=delay,
            easing=easing,
            on_complete=on_complete,
            level=level,
        )

    async def stop_animation(self, attribute: str, complete: bool = True) -> None:
        """Stop an animation on an attribute.

        Args:
            attribute: Name of the attribute whose animation should be stopped.
            complete: Should the animation be set to its final value?

        Note:
            If there is no animation scheduled or running, this is a no-op.
        """
        await self._animator.stop_animation(self, attribute, complete)

    @property
    def is_dom_root(self) -> bool:
        """Is this a root node (i.e. the App)?"""
        return True

    @property
    def debug(self) -> bool:
        """Is debug mode enabled?"""
        return "debug" in self.features

    @property
    def is_headless(self) -> bool:
        """Is the app running in 'headless' mode?

        Headless mode is used when running tests with [run_test][textual.app.App.run_test].
        """
        return False if self._driver is None else self._driver.is_headless

    @property
    def is_inline(self) -> bool:
        """Is the app running in 'inline' mode?"""
        return False if self._driver is None else self._driver.is_inline

    
    def __rich_repr__(self) -> rich.repr.Result:
        yield "title", self.title
        yield "id", self.id, None
        if self.name:
            yield "name", self.name
        if self.classes:
            yield "classes", set(self.classes)
        pseudo_classes = self.pseudo_classes
        if pseudo_classes:
            yield "pseudo_classes", set(pseudo_classes)
    

    @property
    def animator(self) -> Animator:
        """The animator object."""
        return self._animator
    

    @property
    def screen(self) -> Screen[object]:
        """The current active screen.

        Returns:
            The currently active (visible) screen.
        """
        return self._app_screen_controller.screen
    
    @property
    def size(self) -> Size:
        """The size of the terminal.

        Returns:
            Size of the terminal.
        """
        if self._driver is not None and self._driver._size is not None:
            width, height = self._driver._size
        else:
            width, height = self.console.size
        return Size(width, height)
    
    @property
    def log(self) -> Logger:
        """The textual logger.

        Example:
            ```python
            self.log("Hello, World!")
            self.log(self.tree)
            ```

        Returns:
            A Textual logger.
        """
        return self._logger

    def _log(
        self,
        group: LogGroup,
        verbosity: LogVerbosity,
        _textual_calling_frame: inspect.Traceback,
        *objects: Any,
        **kwargs,
    ) -> None:
        """Write to logs or devtools.

        Positional args will logged. Keyword args will be prefixed with the key.

        Example:
            ```python
            data = [1,2,3]
            self.log("Hello, World", state=data)
            self.log(self.tree)
            self.log(locals())
            ```

        Args:
            verbosity: Verbosity level 0-3.
        """

        devtools = self.devtools
        if devtools is None or not devtools.is_connected:
            return

        if verbosity.value > LogVerbosity.NORMAL.value and not devtools.verbose:
            return

        try:
            from textual_dev.client import DevtoolsLog

            if len(objects) == 1 and not kwargs:
                devtools.log(
                    DevtoolsLog(objects, caller=_textual_calling_frame),
                    group,
                    verbosity,
                )
            else:
                output = " ".join(str(arg) for arg in objects)
                if kwargs:
                    key_values = " ".join(
                        f"{key}={value!r}" for key, value in kwargs.items()
                    )
                    output = f"{output} {key_values}" if output else key_values
                devtools.log(
                    DevtoolsLog(output, caller=_textual_calling_frame),
                    group,
                    verbosity,
                )
        except Exception as error:
            self._handle_exception(error)

    def call_from_thread(
        self,
        callback: Callable[..., CallThreadReturnType | Awaitable[CallThreadReturnType]],
        *args: Any,
        **kwargs: Any,
    ) -> CallThreadReturnType:
        """Run a callable from another thread, and return the result.

        Like asyncio apps in general, Textual apps are not thread-safe. If you call methods
        or set attributes on Textual objects from a thread, you may get unpredictable results.

        This method will ensure that your code runs within the correct context.

        !!! tip

            Consider using [post_message][textual.message_pump.MessagePump.post_message] which is also thread-safe.

        Args:
            callback: A callable to run.
            *args: Arguments to the callback.
            **kwargs: Keyword arguments for the callback.

        Raises:
            RuntimeError: If the app isn't running or if this method is called from the same
                thread where the app is running.

        Returns:
            The result of the callback.
        """

        if self._loop is None:
            raise RuntimeError("App is not running")

        if self._thread_id == threading.get_ident():
            raise RuntimeError(
                "The `call_from_thread` method must run in a different thread from the app"
            )

        callback_with_args = partial(callback, *args, **kwargs)

        async def run_callback() -> CallThreadReturnType:
            """Run the callback, set the result or error on the future."""
            self._set_active()
            return await invoke(callback_with_args)

        # Post the message to the main loop
        future: Future[CallThreadReturnType] = asyncio.run_coroutine_threadsafe(
            run_callback(), loop=self._loop
        )
        result = future.result()
        return result
    
    def bind(
        self,
        keys: str,
        action: str,
        *,
        description: str = "",
        show: bool = True,
        key_display: str | None = None,
    ) -> None:
        """Bind a key to an action.

        Args:
            keys: A comma separated list of keys, i.e.
            action: Action to bind to.
            description: Short description of action.
            show: Show key in UI.
            key_display: Replacement text for key, or None to use default.
        """
        self._app_event_handler.bind(keys, action, description, show=show, key_display=key_display)
    

    @overload
    def get_child_by_id(self, id: str) -> Widget: ...

    @overload
    def get_child_by_id(self, id: str, expect_type: type[ExpectType]) -> ExpectType: ...

    def get_child_by_id(
        self, id: str, expect_type: type[ExpectType] | None = None
    ) -> ExpectType | Widget:
        """Get the first child (immediate descendent) of this DOMNode with the given ID.
        Args:
            id: The ID of the node to search for.
            expect_type: Require the object be of the supplied type
        Returns:
            The first child of this node with the specified ID.
        """
        return self._app_compositor.get_child_by_id(id=id, expect_type=expect_type, type=type)


    @overload
    def get_widget_by_id(self, id: str) -> Widget: ...

    @overload
    def get_widget_by_id(
        self, id: str, expect_type: type[ExpectType]
    ) -> ExpectType: ...

    def get_widget_by_id(
        self, id: str, expect_type: type[ExpectType] | None = None
    ) -> ExpectType | Widget:
        """Get the first descendant widget with the given ID.
        Args:
            id: The ID to search for in the subtree
            expect_type: Require the object be of the supplied type, or None for any type.
        Returns:
            The first descendant encountered with this ID.
        """
        return self._app_compositor.get_widget_by_id(id=id, expect_type=expect_type, type=type)
    
    def get_widget_at(self, x: int, y: int) -> tuple[Widget, Region]:
        """Get the widget under the given coordinates.
        Args:
            x: X coordinate.
            y: Y coordinate.
        Returns:
            The widget and the widget's screen region.
        """
        return self.screen.get_widget_at(x, y)
    
    async def on_event(self, event: events.Event) -> None:
        self._app_event_handler.on_event(event)

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
        self._app_lifespan_controller.run(
            headless=headless, inline=inline, inline_no_clear=inline_no_clear, 
            mouse=mouse, size=size, auto_pilot=auto_pilot
        )

    def notify(
        self,
        message: str,
        *,
        title: str = "",
        severity: SeverityLevel = "information",
        timeout: float | None = None,
    ) -> None:
        """Create a notification.

        !!! tip

            This method is thread-safe.


        Args:
            message: The message for the notification.
            title: The title for the notification.
            severity: The severity of the notification.
            timeout: The timeout (in seconds) for the notification, or `None` for default.

        The `notify` method is used to create an application-wide
        notification, shown in a [`Toast`][textual.widgets._toast.Toast],
        normally originating in the bottom right corner of the display.

        Notifications can have the following severity levels:

        - `information`
        - `warning`
        - `error`

        The default is `information`.

        Example:
            ```python
            # Show an information notification.
            self.notify("It's an older code, sir, but it checks out.")

            # Show a warning. Note that Textual's notification system allows
            # for the use of Rich console markup.
            self.notify(
                "Now witness the firepower of this fully "
                "[b]ARMED[/b] and [i][b]OPERATIONAL[/b][/i] battle station!",
                title="Possible trap detected",
                severity="warning",
            )

            # Show an error. Set a longer timeout so it's noticed.
            self.notify("It's a trap!", severity="error", timeout=10)

            # Show an information notification, but without any sort of title.
            self.notify("It's against my programming to impersonate a deity.", title="")
            ```
        """
        if timeout is None:
            timeout = self.NOTIFICATION_TIMEOUT
        notification = Notification(message, title, severity, timeout)
        self.post_message(Notify(notification))
    
    def clear_notifications(self) -> None:
        """Clear all the current notifications."""
        self._notifications.clear()
        self._refresh_notifications()