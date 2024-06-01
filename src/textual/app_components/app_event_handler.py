import asyncio
import inspect
import os
from contextlib import (
    redirect_stderr,
    redirect_stdout,
)

from time import perf_counter
from typing import (
    Any,
    Callable,
    Iterable
)

from rich.console import Console
from rich.control import Control

from . import (
    actions,
    constants,
    events,
    log,
    messages
)

from .app import App

from .._callback import invoke
from .._context import active_message_pump
from .._event_broker import NoHandler, extract_handler_actions
from .._wait import wait_for_idle
from ..actions import ActionParseResult, SkipAction
from ..binding import _Bindings
from ..css.query import NoMatches
from ..css.stylesheet import Stylesheet
from ..dom import DOMNode
from ..errors import NoWidget
from ..geometry import Offset
from ..keys import (
    REPLACED_KEYS,
    _character_to_key,
    _get_unicode_name_from_key,
)
from ..messages import CallbackType
from ..reactive import Reactive
from ..screen import (
    ActiveBinding,
    Screen
)
from ..widget import Widget

if TYPE_CHECKING:
    from .._types import MessageTarget


class AppEventHandler():

    BINDINGS: ClassVar[list[BindingType]] = [
        Binding("ctrl+c", "quit", "Quit", show=False, priority=True),
        Binding("ctrl+backslash", "command_palette", show=False, priority=True),
    ]

    dark: Reactive[bool] = Reactive(True, compute=False)

    app_focus = Reactive(True, compute=False)

    def __init__(
        self,
        app: App,
        css_path: CSSPathType | None = None
    ):
        self._app = app
        self._bindings = (
            _Bindings()
            if self._merged_bindings is None
            else self._merged_bindings.copy()
        )
        self._driver = app.driver
        self.mouse_over: Widget | None = None
        self.mouse_captured: Widget | None = None
        self.css_path = css_path or self.CSS_PATH
        self.design = DEFAULT_COLORS

        self._css_has_errors = False
        self.stylesheet = Stylesheet(variables=self.get_css_variables())


    def action_toggle_dark(self) -> None:
        """An [action](/guide/actions) to toggle dark mode."""
        self._app.dark = not self._app.dark

    def action_screenshot(self, filename: str | None = None, path: str = "./") -> None:
        """This [action](/guide/actions) will save an SVG file containing the current contents of the screen.

        Args:
            filename: Filename of screenshot, or None to auto-generate.
            path: Path to directory. Defaults to current working directory.
        """
        self._app._utils.save_screenshot(filename, path) #FIXME


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
        self._bindings.bind(
            keys, action, description, show=show, key_display=key_display
        )

    @property
    def active_bindings(self) -> dict[str, ActiveBinding]:
        """Get currently active bindings.

        If no widget is focused, then app-level bindings are returned.
        If a widget is focused, then any bindings present in the active screen and app are merged and returned.

        This property may be used to inspect current bindings.

        Returns:
            Active binding information
        """
        return self._app.screen.active_bindings
    
    async def _press_keys(self, keys: Iterable[str]) -> None:
        """A task to send key events."""
        import unicodedata

        app = self._app
        driver = app._driver
        assert driver is not None
        for key in keys:
            if key.startswith("wait:"):
                _, wait_ms = key.split(":")
                await asyncio.sleep(float(wait_ms) / 1000)
                await app._animator.wait_until_complete()
            else:
                if len(key) == 1 and not key.isalnum():
                    key = _character_to_key(key)
                original_key = REPLACED_KEYS.get(key, key)
                char: str | None
                try:
                    char = unicodedata.lookup(_get_unicode_name_from_key(original_key))
                except KeyError:
                    char = key if len(key) == 1 else None
                key_event = events.Key(key, char)
                key_event.set_sender(app)
                driver.send_event(key_event)
                await wait_for_idle(0)
                await app._animator.wait_until_complete()
                await wait_for_idle(0)

    def begin_capture_print(
        self, target: MessageTarget, stdout: bool = True, stderr: bool = True
    ) -> None:
        """Capture content that is printed (or written to stdout / stderr).

        If printing is captured, the `target` will be sent an [events.Print][textual.events.Print] message.

        Args:
            target: The widget where print content will be sent.
            stdout: Capture stdout.
            stderr: Capture stderr.
        """
        if not stdout and not stderr:
            self.end_capture_print(target)
        else:
            self._capture_print[target] = (stdout, stderr)

    def end_capture_print(self, target: MessageTarget) -> None:
        """End capturing of prints.

        Args:
            target: The widget that was capturing prints.
        """
        self._capture_print.pop(target)

    def _set_mouse_over(self, widget: Widget | None) -> None:
            """Called when the mouse is over another widget.

            Args:
                widget: Widget under mouse, or None for no widgets.
            """
            if widget is None:
                if self.mouse_over is not None:
                    try:
                        self.mouse_over.post_message(events.Leave())
                    finally:
                        self.mouse_over = None
            else:
                if self.mouse_over is not widget:
                    try:
                        if self.mouse_over is not None:
                            self.mouse_over.post_message(events.Leave())
                        if widget is not None:
                            widget.post_message(events.Enter())
                    finally:
                        self.mouse_over = widget

    def _update_mouse_over(self, screen: Screen) -> None:
        """Updates the mouse over after the next refresh.

        This method is called whenever a widget is added or removed, which may change
        the widget under the mouse.

        """

        if self.mouse_over is None:
            return

        async def check_mouse() -> None:
            """Check if the mouse over widget has changed."""
            try:
                widget, _ = screen.get_widget_at(*self.mouse_position)
            except NoWidget:
                pass
            else:
                if widget is not self.mouse_over:
                    self._set_mouse_over(widget)

        self.call_after_refresh(check_mouse)

    def capture_mouse(self, widget: Widget | None) -> None:
        """Send all mouse events to the given widget or disable mouse capture.

        Args:
            widget: If a widget, capture mouse event, or `None` to end mouse capture.
        """
        if widget == self.mouse_captured:
            return
        if self.mouse_captured is not None:
            self.mouse_captured.post_message(events.MouseRelease(self.mouse_position))
        self.mouse_captured = widget
        if widget is not None:
            widget.post_message(events.MouseCapture(self.mouse_position))

    async def _process_messages(
        self,
        ready_callback: CallbackType | None = None,
        headless: bool = False,
        inline: bool = False,
        inline_no_clear: bool = False,
        mouse: bool = True,
        terminal_size: tuple[int, int] | None = None,
        message_hook: Callable[[Message], None] | None = None,
    ) -> None:
        self._set_active()
        active_message_pump.set(self)

        if self.devtools is not None:
            from textual_dev.client import DevtoolsConnectionError

            try:
                await self.devtools.connect()
                self.log.system(f"Connected to devtools ( {self.devtools.url} )")
            except DevtoolsConnectionError:
                self.log.system(f"Couldn't connect to devtools ( {self.devtools.url} )")

        self.log.system("---")

        self.log.system(loop=asyncio.get_running_loop())
        self.log.system(features=self.features)
        if constants.LOG_FILE is not None:
            _log_path = os.path.abspath(constants.LOG_FILE)
            self.log.system(f"Writing logs to {_log_path!r}")

        try:
            if self.css_path:
                self.stylesheet.read_all(self.css_path)
            for read_from, css, tie_breaker, scope in self._get_default_css():
                self.stylesheet.add_source(
                    css,
                    read_from=read_from,
                    is_default_css=True,
                    tie_breaker=tie_breaker,
                    scope=scope,
                )
            if self.CSS:
                try:
                    app_path = inspect.getfile(self.__class__)
                except (TypeError, OSError):
                    app_path = ""
                read_from = (app_path, f"{self.__class__.__name__}.CSS")
                self.stylesheet.add_source(
                    self.CSS, read_from=read_from, is_default_css=False
                )
        except Exception as error:
            self._handle_exception(error)
            self._print_error_renderables()
            return

        if self.css_monitor:
            self.set_interval(0.25, self.css_monitor, name="css monitor")
            self.log.system("STARTED", self.css_monitor)

        async def run_process_messages():
            """The main message loop, invoke below."""

            async def invoke_ready_callback() -> None:
                if ready_callback is not None:
                    ready_result = ready_callback()
                    if inspect.isawaitable(ready_result):
                        await ready_result

            with self.batch_update():
                try:
                    try:
                        await self._dispatch_message(events.Compose())
                        default_screen = self.screen
                        await self._dispatch_message(events.Mount())
                        self.check_idle()
                    finally:
                        self._mounted_event.set()
                        self._is_mounted = True

                    Reactive._initialize_object(self)

                    self.stylesheet.apply(self)
                    if self.screen is not default_screen:
                        self.stylesheet.apply(default_screen)

                    await self.animator.start()

                except Exception:
                    await self._app.animator.stop()
                    raise

                finally:
                    self._running = True
                    await self._ready()
                    await invoke_ready_callback()

            try:
                await self._process_messages_loop()
            except asyncio.CancelledError:
                pass
            finally:
                self.workers.cancel_all()
                self._running = False
                try:
                    await self.animator.stop()
                finally:
                    for timer in list(self._timers):
                        timer.stop()

        self._running = True
        try:
            load_event = events.Load()
            await self._dispatch_message(load_event)

            driver = self._driver = self._build_driver(
                headless=headless,
                inline=inline,
                mouse=mouse,
                size=terminal_size,
            )
            self.log(driver=driver)

            if not self._exit:
                driver.start_application_mode()
                try:
                    with redirect_stdout(self._capture_stdout):
                        with redirect_stderr(self._capture_stderr):
                            await run_process_messages()

                finally:
                    if self._driver.is_inline:
                        cursor_x, cursor_y = self._previous_cursor_position
                        self._driver.write(
                            Control.move(-cursor_x, -cursor_y + 1).segment.text
                        )
                    if inline_no_clear:
                        console = Console()
                        console.print(self.screen._compositor)
                        console.print()
                    driver.stop_application_mode()
        except Exception as error:
            self._handle_exception(error)

    async def _pre_process(self) -> bool:
        """Special case for the app, which doesn't need the functionality in MessagePump."""
        return True

    async def _ready(self) -> None:
        """Called immediately prior to processing messages.

        May be used as a hook for any operations that should run first.
        """

        ready_time = (perf_counter() - self._start_time) * 1000
        self.log.system(f"ready in {ready_time:0.0f} milliseconds")

        async def take_screenshot() -> None:
            """Take a screenshot and exit."""
            self.save_screenshot(
                path=constants.SCREENSHOT_LOCATION,
                filename=constants.SCREENSHOT_FILENAME,
            )
            self.exit()

        if constants.SCREENSHOT_DELAY >= 0:
            self.set_timer(
                constants.SCREENSHOT_DELAY, take_screenshot, name="screenshot timer"
            )

    @property
    def _binding_chain(self) -> list[tuple[DOMNode, _Bindings]]:
        """Get a chain of nodes and bindings to consider.

        If no widget is focused, returns the bindings from both the screen and the app level bindings.
        Otherwise, combines all the bindings from the currently focused node up the DOM to the root App.
        """
        focused = self.focused
        namespace_bindings: list[tuple[DOMNode, _Bindings]]

        if focused is None:
            namespace_bindings = [
                (self.screen, self.screen._bindings),
                (self, self._bindings),
            ]
        else:
            namespace_bindings = [
                (node, node._bindings) for node in focused.ancestors_with_self
            ]

        return namespace_bindings

    async def check_bindings(self, key: str, priority: bool = False) -> bool:
        """Handle a key press.

        This method is used internally by the bindings system, but may be called directly
        if you wish to *simulate* a key being pressed.

        Args:
            key: A key.
            priority: If `True` check from `App` down, otherwise from focused up.

        Returns:
            True if the key was handled by a binding, otherwise False
        """
        for namespace, bindings in (
            reversed(self.screen._binding_chain)
            if priority
            else self.screen._modal_binding_chain
        ):
            binding = bindings.keys.get(key)
            if binding is not None and binding.priority == priority:
                if await self.run_action(binding.action, namespace):
                    return True
        return False

    async def on_event(self, event: events.Event) -> None:
        # Handle input events that haven't been forwarded
        # If the event has been forwarded it may have bubbled up back to the App
        if isinstance(event, events.Compose):
            screen: Screen[Any] = self.get_default_screen()
            self._register(self, screen)
            self._screen_stack.append(screen)
            screen.post_message(events.ScreenResume())
            await super().on_event(event)

        elif isinstance(event, events.InputEvent) and not event.is_forwarded:
            if not self.app_focus and isinstance(event, (events.Key, events.MouseDown)):
                self.app_focus = True
            if isinstance(event, events.MouseEvent):
                # Record current mouse position on App
                self.mouse_position = Offset(event.x, event.y)

                if isinstance(event, events.MouseDown):
                    try:
                        self._mouse_down_widget, _ = self.get_widget_at(
                            event.x, event.y
                        )
                    except NoWidget:
                        # Shouldn't occur, since at the very least this will find the Screen
                        self._mouse_down_widget = None

                self.screen._forward_event(event)

                if (
                    isinstance(event, events.MouseUp)
                    and self._mouse_down_widget is not None
                ):
                    try:
                        if (
                            self.get_widget_at(event.x, event.y)[0]
                            is self._mouse_down_widget
                        ):
                            click_event = events.Click.from_event(event)
                            self.screen._forward_event(click_event)
                    except NoWidget:
                        pass

            elif isinstance(event, events.Key):
                if not await self.check_bindings(event.key, priority=True):
                    forward_target = self.focused or self.screen
                    forward_target._forward_event(event)
            else:
                self.screen._forward_event(event)

        elif isinstance(event, events.Paste) and not event.is_forwarded:
            if self.focused is not None:
                self.focused._forward_event(event)
            else:
                self.screen._forward_event(event)
        else:
            await super().on_event(event)

    def _parse_action(
        self, action: str | ActionParseResult, default_namespace: DOMNode
    ) -> tuple[DOMNode, str, tuple[object, ...]]:
        """Parse an action.

        Args:
            action: An action string.

        Raises:
            ActionError: If there are any errors parsing the action string.

        Returns:
            A tuple of (node or None, action name, tuple of parameters).
        """
        if isinstance(action, tuple):
            destination, action_name, params = action
        else:
            destination, action_name, params = actions.parse(action)

        action_target: DOMNode | None = None
        if destination:
            if destination not in self._action_targets:
                raise ActionError(f"Action namespace {destination} is not known")
            action_target = getattr(self, destination, None)
            if action_target is None:
                raise ActionError("Action target {destination!r} not available")
        return (
            (default_namespace if action_target is None else action_target),
            action_name,
            params,
        )

    def _check_action_state(
        self, action: str, default_namespace: DOMNode
    ) -> bool | None:
        """Check if an action is enabled.

        Args:
            action: An action string.

        Returns:
            State of an action.
        """
        action_target, action_name, parameters = self._parse_action(
            action, default_namespace
        )
        return action_target.check_action(action_name, parameters)

    async def run_action(
        self,
        action: str | ActionParseResult,
        default_namespace: DOMNode | None = None,
    ) -> bool:
        """Perform an [action](/guide/actions).

        Actions are typically associated with key bindings, where you wouldn't need to call this method manually.

        Args:
            action: Action encoded in a string.
            default_namespace: Namespace to use if not provided in the action,
                or None to use app.

        Returns:
            True if the event has been handled.
        """
        action_target, action_name, params = self._parse_action(
            action, self if default_namespace is None else default_namespace
        )

        if action_target.check_action(action_name, params):
            return await self._dispatch_action(action_target, action_name, params)
        else:
            return False

    async def _dispatch_action(
        self, namespace: object, action_name: str, params: Any
    ) -> bool:
        """Dispatch an action to an action method.

        Args:
            namespace: Namespace (object) of action.
            action_name: Name of the action.
            params: Action parameters.

        Returns:
            True if handled, otherwise False.
        """
        _rich_traceback_guard = True

        log.system(
            "<action>",
            namespace=namespace,
            action_name=action_name,
            params=params,
        )

        try:
            private_method = getattr(namespace, f"_action_{action_name}", None)
            if callable(private_method):
                await invoke(private_method, *params)
                return True
            public_method = getattr(namespace, f"action_{action_name}", None)
            if callable(public_method):
                await invoke(public_method, *params)
                return True
            log.system(
                f"<action> {action_name!r} has no target."
                f" Could not find methods '_action_{action_name}' or 'action_{action_name}'"
            )
        except SkipAction:
            # The action method raised this to explicitly not handle the action
            log.system(f"<action> {action_name!r} skipped.")
        return False

    async def _broker_event(
        self, event_name: str, event: events.Event, default_namespace: DOMNode
    ) -> bool:
        """Allow the app an opportunity to dispatch events to action system.

        Args:
            event_name: _description_
            event: An event object.
            default_namespace: The default namespace, where one isn't supplied.

        Returns:
            True if an action was processed.
        """
        try:
            style = getattr(event, "style")
        except AttributeError:
            return False
        try:
            _modifiers, action = extract_handler_actions(event_name, style.meta)
        except NoHandler:
            return False
        else:
            event.stop()

        if isinstance(action, str):
            await self.run_action(action, default_namespace)
        elif isinstance(action, tuple) and len(action) == 2:
            action_name, action_params = action
            namespace, parsed_action, _ = actions.parse(action_name)
            await self.run_action(
                (namespace, parsed_action, action_params),
                default_namespace,
            )
        elif callable(action):
            await action()
        else:
            if isinstance(action, tuple) and self.debug:
                # It's a tuple and made it this far, which means it'll be a
                # malformed action. This is a no-op, but let's log that
                # anyway.
                log.warning(
                    f"Can't parse @{event_name} action from style meta; check your console markup syntax"
                )
            return False
        return True

    async def _on_update(self, message: messages.Update) -> None:
        message.stop()

    async def _on_layout(self, message: messages.Layout) -> None:
        message.stop()

    async def _on_key(self, event: events.Key) -> None:
        if not (await self.check_bindings(event.key)):
            await self.dispatch_key(event)

    async def _on_shutdown_request(self, event: events.ShutdownRequest) -> None:
        log.system("shutdown request")
        await self._close_messages()

    async def _on_resize(self, event: events.Resize) -> None:
        event.stop()
        self.screen.post_message(event)
        for screen in self._background_screens:
            screen.post_message(event)

    async def _on_app_focus(self, event: events.AppFocus) -> None:
        """App has focus."""
        # Required by textual-web to manage focus in a web page.
        self.app_focus = True
        self.screen.refresh_bindings()

    async def _on_app_blur(self, event: events.AppBlur) -> None:
        """App has lost focus."""
        # Required by textual-web to manage focus in a web page.
        self.app_focus = False
        self.screen.refresh_bindings()

    async def action_check_bindings(self, key: str) -> None:
        """An [action](/guide/actions) to handle a key press using the binding system.

        Args:
            key: The key to process.
        """
        if not await self.check_bindings(key, priority=True):
            await self.check_bindings(key, priority=False)

    async def action_quit(self) -> None:
        """An [action](/guide/actions) to quit the app as soon as possible."""
        self.exit()

    async def action_bell(self) -> None:
        """An [action](/guide/actions) to play the terminal 'bell'."""
        self.bell()

    async def action_focus(self, widget_id: str) -> None:
        """An [action](/guide/actions) to focus the given widget.

        Args:
            widget_id: ID of widget to focus.
        """
        try:
            node = self.query(f"#{widget_id}").first()
        except NoMatches:
            pass
        else:
            if isinstance(node, Widget):
                self.set_focus(node)

    async def action_switch_screen(self, screen: str) -> None:
        """An [action](/guide/actions) to switch screens.

        Args:
            screen: Name of the screen.
        """
        self.switch_screen(screen)

    async def action_push_screen(self, screen: str) -> None:
        """An [action](/guide/actions) to push a new screen on to the stack and make it active.

        Args:
            screen: Name of the screen.
        """
        self.push_screen(screen)

    async def action_pop_screen(self) -> None:
        """An [action](/guide/actions) to remove the topmost screen and makes the new topmost screen active."""
        self.pop_screen()

    async def action_switch_mode(self, mode: str) -> None:
        """An [action](/guide/actions) that switches to the given mode.."""
        self.switch_mode(mode)

    async def action_back(self) -> None:
        """An [action](/guide/actions) to go back to the previous screen (pop the current screen).

        Note:
            If there is no screen to go back to, this is a non-operation (in
            other words it's safe to call even if there are no other screens
            on the stack.)
        """
        try:
            self.pop_screen()
        except ScreenStackError:
            pass

    async def action_add_class(self, selector: str, class_name: str) -> None:
        """An [action](/guide/actions) to add a CSS class to the selected widget.

        Args:
            selector: Selects the widget to add the class to.
            class_name: The class to add to the selected widget.
        """
        self.screen.query(selector).add_class(class_name)

    async def action_remove_class(self, selector: str, class_name: str) -> None:
        """An [action](/guide/actions) to remove a CSS class from the selected widget.

        Args:
            selector: Selects the widget to remove the class from.
            class_name: The class to remove from  the selected widget."""
        self.screen.query(selector).remove_class(class_name)

    async def action_toggle_class(self, selector: str, class_name: str) -> None:
        """An [action](/guide/actions) to toggle a CSS class on the selected widget.

        Args:
            selector: Selects the widget to toggle the class on.
            class_name: The class to toggle on the selected widget.
        """
        self.screen.query(selector).toggle_class(class_name)

    def action_focus_next(self) -> None:
        """An [action](/guide/actions) to focus the next widget."""
        self.screen.focus_next()

    def action_focus_previous(self) -> None:
        """An [action](/guide/actions) to focus the previous widget."""
        self.screen.focus_previous()
