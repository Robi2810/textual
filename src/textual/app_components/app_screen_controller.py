import asyncio

from asyncio import create_task
from typing import (
    Any,
    Callable,
    Iterable,
    Sequence
)

from . import events

from .app import App

from .._context import active_message_pump
from ..await_remove import AwaitRemove
from ..dom import DOMNode, NoScreen
from ..screen import (
    Screen,
    ScreenResultCallbackType,
    ScreenResultType
)
from ..widget import AwaitMount, Widget
from ..worker import NoActiveWorker, get_current_worker

class ScreenError(Exception):
    """Base class for exceptions that relate to screens."""

class ScreenStackError(ScreenError):
    """Raised when trying to manipulate the screen stack incorrectly."""


class ModeError(Exception):
    """Base class for exceptions related to modes."""

class UnknownModeError(ModeError):
    """Raised when attempting to use a mode that is not known."""

class InvalidModeError(ModeError):
    """Raised if there is an issue with a mode name."""

class ActiveModeError(ModeError):
    """Raised when attempting to remove the currently active mode."""



class AppScreenController():

    MODES: ClassVar[dict[str, str | Screen | Callable[[], Screen]]] = {}
    """Modes associated with the app and their base screens."""

    SCREENS: ClassVar[dict[str, Screen[Any] | Callable[[], Screen[Any]]]] = {}

    def _init_(
        self,
        app: App
    ):
        self._app = app
        self._screen_stacks: dict[str, list[Screen[Any]]] = {"_default": []}
        """A stack of screens per mode."""
        self._current_mode: str = "_default"
        """The current mode the app is in."""
        self._installed_screens: dict[str, Screen | Callable[[], Screen]] = {}
        self._installed_screens.update(**self.SCREENS)


    @property
    def screen_stack(self) -> Sequence[Screen[Any]]:
        """A snapshot of the current screen stack.

        Returns:
            A snapshot of the current state of the screen stack.
        """
        return self._screen_stacks[self._current_mode].copy()
    
    @property
    def _screen_stack(self) -> list[Screen[Any]]:
        """A reference to the current screen stack.

        Note:
            Consider using [`screen_stack`][textual.app.App.screen_stack] instead.

        Returns:
            A reference to the current screen stack.
        """
        return self._screen_stacks[self._current_mode]
    
    @property
    def current_mode(self) -> str:
        """The name of the currently active mode."""
        return self._current_mode
    
    @property
    def focused(self) -> Widget | None:
        """The widget that is focused on the currently active screen, or `None`.

        Focused widgets receive keyboard input.

        Returns:
            The currently focused widget, or `None` if nothing is focused.
        """
        focused = self.screen.focused
        if focused is not None and focused.loading:
            return None
        return focused


    @property
    def screen(self) -> Screen[object]:
        """The current active screen.

        Returns:
            The currently active (visible) screen.

        Raises:
            ScreenStackError: If there are no screens on the stack.
        """
        try:
            return self._screen_stack[-1]
        except KeyError:
            raise UnknownModeError(f"No known mode {self._current_mode!r}") from None
        except IndexError:
            raise ScreenStackError("No screens on stack") from None
        
    def get_default_screen(self) -> Screen:
        """Get the default screen.

        This is called when the App is first composed. The returned screen instance
        will be the first screen on the stack.

        Implement this method if you would like to use a custom Screen as the default screen.

        Returns:
            A screen instance.
        """
        return Screen(id="_default")

    @property
    def _background_screens(self) -> list[Screen]:
        """A list of screens that may be visible due to background opacity (top-most first, not including current screen)."""
        screens: list[Screen] = []
        for screen in reversed(self._screen_stack[:-1]):
            screens.append(screen)
            if screen.styles.background.a == 1:
                break
        background_screens = screens[::-1]
        return background_screens
    

    def _init_mode(self, mode: str) -> AwaitMount:
        """Do internal initialisation of a new screen stack mode.

        Args:
            mode: Name of the mode.

        Returns:
            An optionally awaitable object which can be awaited until the screen
            associated with the mode has been mounted.
        """

        stack = self._screen_stacks.get(mode, [])
        if stack:
            await_mount = AwaitMount(stack[0], [])
        else:
            _screen = self.MODES[mode]
            new_screen: Screen | str = _screen() if callable(_screen) else _screen
            screen, await_mount = self._get_screen(new_screen)
            stack.append(screen)
            self._load_screen_css(screen)

        self._screen_stacks[mode] = stack
        return await_mount

    def switch_mode(self, mode: str) -> AwaitMount:
        """Switch to a given mode.

        Args:
            mode: The mode to switch to.

        Returns:
            An optionally awaitable object which waits for the screen associated
                with the mode to be mounted.

        Raises:
            UnknownModeError: If trying to switch to an unknown mode.
        """
        if mode not in self.MODES:
            raise UnknownModeError(f"No known mode {mode!r}")

        self.screen.post_message(events.ScreenSuspend())
        self.screen.refresh()

        if mode not in self._screen_stacks:
            await_mount = self._init_mode(mode)
        else:
            await_mount = AwaitMount(self.screen, [])

        self._current_mode = mode
        self.screen._screen_resized(self.size)
        self.screen.post_message(events.ScreenResume())

        self.log.system(f"{self._current_mode!r} is the current mode")
        self.log.system(f"{self.screen} is active")

        return await_mount

    def add_mode(
        self, mode: str, base_screen: str | Screen | Callable[[], Screen]
    ) -> None:
        """Adds a mode and its corresponding base screen to the app.

        Args:
            mode: The new mode.
            base_screen: The base screen associated with the given mode.

        Raises:
            InvalidModeError: If the name of the mode is not valid/duplicated.
        """
        if mode == "_default":
            raise InvalidModeError("Cannot use '_default' as a custom mode.")
        elif mode in self.MODES:
            raise InvalidModeError(f"Duplicated mode name {mode!r}.")

        self.MODES[mode] = base_screen

    def remove_mode(self, mode: str) -> None:
        """Removes a mode from the app.

        Screens that are running in the stack of that mode are scheduled for pruning.

        Args:
            mode: The mode to remove. It can't be the active mode.

        Raises:
            ActiveModeError: If trying to remove the active mode.
            UnknownModeError: If trying to remove an unknown mode.
        """
        if mode == self._current_mode:
            raise ActiveModeError(f"Can't remove active mode {mode!r}")
        elif mode not in self.MODES:
            raise UnknownModeError(f"Unknown mode {mode!r}")
        else:
            del self.MODES[mode]

        if mode not in self._screen_stacks:
            return

        stack = self._screen_stacks[mode]
        del self._screen_stacks[mode]
        for screen in reversed(stack):
            self._replace_screen(screen)

    def is_screen_installed(self, screen: Screen | str) -> bool:
        """Check if a given screen has been installed.

        Args:
            screen: Either a Screen object or screen name (the `name` argument when installed).

        Returns:
            True if the screen is currently installed,
        """
        if isinstance(screen, str):
            return screen in self._installed_screens
        else:
            return screen in self._installed_screens.values()

    def get_screen(self, screen: Screen | str) -> Screen:
        """Get an installed screen.

        Args:
            screen: Either a Screen object or screen name (the `name` argument when installed).

        Raises:
            KeyError: If the named screen doesn't exist.

        Returns:
            A screen instance.
        """
        if isinstance(screen, str):
            try:
                next_screen = self._installed_screens[screen]
            except KeyError:
                raise KeyError(f"No screen called {screen!r} installed") from None
            if callable(next_screen):
                next_screen = next_screen()
                self._installed_screens[screen] = next_screen
        else:
            next_screen = screen
        return next_screen

    def _get_screen(self, screen: Screen | str) -> tuple[Screen, AwaitMount]:
        """Get an installed screen and an AwaitMount object.

        If the screen isn't running, it will be registered before it is run.

        Args:
            screen: Either a Screen object or screen name (the `name` argument when installed).

        Raises:
            KeyError: If the named screen doesn't exist.

        Returns:
            A screen instance and an awaitable that awaits the children mounting.
        """
        _screen = self.get_screen(screen)
        if not _screen.is_running:
            widgets = self._register(self._app, _screen) #FIXME
            await_mount = AwaitMount(_screen, widgets)
            self.call_next(await_mount)
            return (_screen, await_mount)
        else:
            await_mount = AwaitMount(_screen, [])
            self.call_next(await_mount)
            return (_screen, await_mount)
        

    def _replace_screen(self, screen: Screen) -> Screen:
        """Handle the replaced screen.

        Args:
            screen: A screen object.

        Returns:
            The screen that was replaced.
        """
        if self._screen_stack:
            self.screen.refresh()
        screen.post_message(events.ScreenSuspend())
        self.log.system(f"{screen} SUSPENDED")
        if not self.is_screen_installed(screen) and all(
            screen not in stack for stack in self._screen_stacks.values()
        ):
            screen.remove()
            self.log.system(f"{screen} REMOVED")
        return screen
    
def push_screen(
        self,
        screen: Screen[ScreenResultType] | str,
        callback: ScreenResultCallbackType[ScreenResultType] | None = None,
        wait_for_dismiss: bool = False,
    ) -> AwaitMount | asyncio.Future[ScreenResultType]:
        """Push a new [screen](/guide/screens) on the screen stack, making it the current screen.

        Args:
            screen: A Screen instance or the name of an installed screen.
            callback: An optional callback function that will be called if the screen is [dismissed][textual.screen.Screen.dismiss] with a result.
            wait_for_dismiss: If `True`, awaiting this method will return the dismiss value from the screen. When set to `False`, awaiting
                this method will wait for the screen to be mounted. Note that `wait_for_dismiss` should only be set to `True` when running in a worker.

        Raises:
            NoActiveWorker: If using `wait_for_dismiss` outside of a worker.

        Returns:
            An optional awaitable that awaits the mounting of the screen and its children, or an asyncio Future
                to await the result of the screen.
        """
        if not isinstance(screen, (Screen, str)):
            raise TypeError(
                f"push_screen requires a Screen instance or str; not {screen!r}"
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # Mainly for testing, when push_screen isn't called in an async context
            future: asyncio.Future[ScreenResultType] = asyncio.Future()
        else:
            future = loop.create_future()

        if self._screen_stack:
            self.screen.post_message(events.ScreenSuspend())
            self.screen.refresh()
        next_screen, await_mount = self._get_screen(screen)
        try:
            message_pump = active_message_pump.get()
        except LookupError:
            message_pump = self.app

        next_screen._push_result_callback(message_pump, callback, future)
        self._load_screen_css(next_screen)
        self._screen_stack.append(next_screen)
        self.stylesheet.update(next_screen)
        next_screen.post_message(events.ScreenResume())
        self.log.system(f"{self.screen} is current (PUSHED)")
        if wait_for_dismiss:
            try:
                get_current_worker()
            except NoActiveWorker:
                raise NoActiveWorker(
                    "push_screen must be run from a worker when `wait_for_dismiss` is True"
                ) from None
            return future
        else:
            return await_mount
        

async def push_screen_wait(
    self, screen: Screen[ScreenResultType] | str
) -> ScreenResultType | Any:
    """Push a screen and wait for the result (received from [`Screen.dismiss`][textual.screen.Screen.dismiss]).

    Note that this method may only be called when running in a worker.

    Args:
        screen: A screen or the name of an installed screen.

    Returns:
        The screen's result.
    """
    return await self.push_screen(screen, wait_for_dismiss=True)

def switch_screen(self, screen: Screen | str) -> AwaitMount:
    """Switch to another [screen](/guide/screens) by replacing the top of the screen stack with a new screen.

    Args:
        screen: Either a Screen object or screen name (the `name` argument when installed).
    """
    if not isinstance(screen, (Screen, str)):
        raise TypeError(
            f"switch_screen requires a Screen instance or str; not {screen!r}"
        )

    next_screen, await_mount = self._get_screen(screen)
    if screen is self.screen or next_screen is self.screen:
        self.log.system(f"Screen {screen} is already current.")
        return AwaitMount(self.screen, [])

    previous_screen = self._replace_screen(self._screen_stack.pop())
    previous_screen._pop_result_callback()
    self._load_screen_css(next_screen)
    self._screen_stack.append(next_screen)
    self.screen.post_message(events.ScreenResume())
    self.screen._push_result_callback(self.screen, None)
    self.log.system(f"{self.screen} is current (SWITCHED)")
    return await_mount

def install_screen(self, screen: Screen, name: str) -> None:
    """Install a screen.

    Installing a screen prevents Textual from destroying it when it is no longer on the screen stack.
    Note that you don't need to install a screen to use it. See [push_screen][textual.app.App.push_screen]
    or [switch_screen][textual.app.App.switch_screen] to make a new screen current.

    Args:
        screen: Screen to install.
        name: Unique name to identify the screen.

    Raises:
        ScreenError: If the screen can't be installed.

    Returns:
        An awaitable that awaits the mounting of the screen and its children.
    """
    if name in self._installed_screens:
        raise ScreenError(f"Can't install screen; {name!r} is already installed")
    if screen in self._installed_screens.values():
        raise ScreenError(
            f"Can't install screen; {screen!r} has already been installed"
        )
    self._installed_screens[name] = screen
    self.log.system(f"{screen} INSTALLED name={name!r}")

def uninstall_screen(self, screen: Screen | str) -> str | None:
    """Uninstall a screen.

    If the screen was not previously installed then this method is a null-op.
    Uninstalling a screen allows Textual to delete it when it is popped or switched.
    Note that uninstalling a screen is only required if you have previously installed it
    with [install_screen][textual.app.App.install_screen].
    Textual will also uninstall screens automatically on exit.

    Args:
        screen: The screen to uninstall or the name of a installed screen.

    Returns:
        The name of the screen that was uninstalled, or None if no screen was uninstalled.
    """
    if isinstance(screen, str):
        if screen not in self._installed_screens:
            return None
        uninstall_screen = self._installed_screens[screen]
        if any(uninstall_screen in stack for stack in self._screen_stacks.values()):
            raise ScreenStackError("Can't uninstall screen in screen stack")
        del self._installed_screens[screen]
        self.log.system(f"{uninstall_screen} UNINSTALLED name={screen!r}")
        return screen
    else:
        if any(screen in stack for stack in self._screen_stacks.values()):
            raise ScreenStackError("Can't uninstall screen in screen stack")
        for name, installed_screen in self._installed_screens.items():
            if installed_screen is screen:
                self._installed_screens.pop(name)
                self.log.system(f"{screen} UNINSTALLED name={name!r}")
                return name
    return None

def pop_screen(self) -> Screen[object]:
    """Pop the current [screen](/guide/screens) from the stack, and switch to the previous screen.

    Returns:
        The screen that was replaced.
    """
    screen_stack = self._screen_stack
    if len(screen_stack) <= 1:
        raise ScreenStackError(
            "Can't pop screen; there must be at least one screen on the stack"
        )
    previous_screen = self._replace_screen(screen_stack.pop())
    previous_screen._pop_result_callback()
    self.screen.post_message(events.ScreenResume())
    self.log.system(f"{self.screen} is active")
    return previous_screen

def set_focus(self, widget: Widget | None, scroll_visible: bool = True) -> None:
    """Focus (or unfocus) a widget. A focused widget will receive key events first.

    Args:
        widget: Widget to focus.
        scroll_visible: Scroll widget in to view.
    """
    self.screen.set_focus(widget, scroll_visible)

def _detach_from_dom(self, widgets: list[Widget]) -> list[Widget]:
    """Detach a list of widgets from the DOM.

    Args:
        widgets: The list of widgets to detach from the DOM.

    Returns:
        The list of widgets that should be pruned.

    Note:
        A side-effect of calling this function is that each parent of
        each affected widget will be made to forget about the affected
        child.
    """

    # We've been given a list of widgets to remove, but removing those
    # will also result in other (descendent) widgets being removed. So
    # to start with let's get a list of everything that's not going to
    # be in the DOM by the time we've finished. Note that, at this
    # point, it's entirely possible that there will be duplicates.
    everything_to_remove: list[Widget] = []
    for widget in widgets:
        everything_to_remove.extend(
            widget.walk_children(
                Widget, with_self=True, method="depth", reverse=True
            )
        )

    # Next up, let's quickly create a deduped collection of things to
    # remove and ensure that, if one of them is the focused widget,
    # focus gets moved to somewhere else.
    dedupe_to_remove = set(everything_to_remove)
    if self.screen.focused in dedupe_to_remove:
        self.screen._reset_focus(
            self.screen.focused,
            [to_remove for to_remove in dedupe_to_remove if to_remove.can_focus],
        )

    # Next, we go through the set of widgets we've been asked to remove
    # and try and find the minimal collection of widgets that will
    # result in everything else that should be removed, being removed.
    # In other words: find the smallest set of ancestors in the DOM that
    # will remove the widgets requested for removal, and also ensure
    # that all knock-on effects happen too.
    request_remove = set(widgets)
    pruned_remove = [
        widget for widget in widgets if request_remove.isdisjoint(widget.ancestors)
    ]

    # Now that we know that minimal set of widgets, we go through them
    # and get their parents to forget about them. This has the effect of
    # snipping each affected branch from the DOM.
    for widget in pruned_remove:
        if widget.parent is not None:
            widget.parent._nodes._remove(widget)

    for node in pruned_remove:
        node._detach()

    # Return the list of widgets that should end up being sent off in a
    # prune event.
    return pruned_remove

def _walk_children(self, root: Widget) -> Iterable[list[Widget]]:
    """Walk children depth first, generating widgets and a list of their siblings.

    Returns:
        The child widgets of root.
    """
    stack: list[Widget] = [root]
    pop = stack.pop
    push = stack.append

    while stack:
        widget = pop()
        children = [*widget._nodes, *widget._get_virtual_dom()]
        if children:
            yield children
        for child in widget._nodes:
            push(child)

def _remove_nodes(
    self, widgets: list[Widget], parent: DOMNode | None
) -> AwaitRemove:
    """Remove nodes from DOM, and return an awaitable that awaits cleanup.

    Args:
        widgets: List of nodes to remove.
        parent: Parent node of widgets, or None for no parent.

    Returns:
        Awaitable that returns when the nodes have been fully removed.
    """

    async def prune_widgets_task(
        widgets: list[Widget], finished_event: asyncio.Event
    ) -> None:
        """Prune widgets as a background task.

        Args:
            widgets: Widgets to prune.
            finished_event: Event to set when complete.
        """
        try:
            await self._prune_nodes(widgets)
        finally:
            finished_event.set()
            self._update_mouse_over(self.screen)
            if parent is not None:
                parent.refresh(layout=True)

    removed_widgets = self._detach_from_dom(widgets)

    finished_event = asyncio.Event()
    remove_task = create_task(
        prune_widgets_task(removed_widgets, finished_event), name="prune nodes"
    )

    await_remove = AwaitRemove(finished_event, remove_task)
    self.call_next(await_remove)
    return await_remove

async def _prune_nodes(self, widgets: list[Widget]) -> None:
    """Remove nodes and children.

    Args:
        widgets: Widgets to remove.
    """
    async with self._dom_lock:
        for widget in widgets:
            await self._prune_node(widget)

async def _prune_node(self, root: Widget) -> None:
    """Remove a node and its children. Children are removed before parents.

    Args:
        root: Node to remove.
    """
    # Pruning a node that has been removed is a no-op
    if root not in self._registry:
        return

    node_children = list(self._walk_children(root))

    for children in reversed(node_children):
        # Closing children can be done asynchronously.
        close_messages = [
            child._close_messages(wait=True) for child in children if child._running
        ]
        # TODO: What if a message pump refuses to exit?
        if close_messages:
            await asyncio.gather(*close_messages)
            for child in children:
                self._unregister(child)

    await root._close_messages(wait=True)
    self._unregister(root)

def _watch_app_focus(self, focus: bool) -> None:
    """Respond to changes in app focus."""
    if focus:
        # If we've got a last-focused widget, if it still has a screen,
        # and if the screen is still the current screen and if nothing
        # is focused right now...
        try:
            if (
                self._last_focused_on_app_blur is not None
                and self._last_focused_on_app_blur.screen is self.screen
                and self.screen.focused is None
            ):
                # ...settle focus back on that widget.
                # Don't scroll the newly focused widget, as this can be quite jarring
                self.screen.set_focus(
                    self._last_focused_on_app_blur, scroll_visible=False
                )
        except NoScreen:
            pass
        # Now that we have focus back on the app and we don't need the
        # widget reference any more, don't keep it hanging around here.
        self._last_focused_on_app_blur = None
    else:
        # Remember which widget has focus, when the app gets focus back
        # we'll want to try and focus it again.
        self._last_focused_on_app_blur = self.screen.focused
        # Remove focus for now.
        self.screen.set_focus(None)