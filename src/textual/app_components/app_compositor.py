
from typing import (
    Iterable,
    Sequence,
    TypeVar
)
from weakref import WeakKeyDictionary, WeakSet

from .app import App
from .app_screen_controller import AppScreenController

from .._compose import compose
from ..css.stylesheet import RulesMap
from ..dom import DOMNode
from ..screen import (
    Screen,
    _SystemModalScreen,
)
from ..widget import Widget

class AppError(Exception):
    """Base class for general App related exceptions."""

ComposeResult = Iterable[Widget]

class AppCompositor():

    def __init__(
        self,
        app: App,
        app_screen_controller: AppScreenController
    ):
        self._app = app
        self._screen_stack = app_screen_controller._screen_stack
        self._recompose_required = False
        self._registry: WeakSet[DOMNode] = WeakSet()


    @property
    def children(self) -> Sequence["Widget"]:
        """A view onto the app's immediate children.

        This attribute exists on all widgets.
        In the case of the App, it will only ever contain a single child, which will
        be the currently active screen.

        Returns:
            A sequence of widgets.
        """
        try:
            return (
                next(
                    screen
                    for screen in reversed(self._screen_stack)
                    if not isinstance(screen, _SystemModalScreen)
                ),
            )
        except StopIteration:
            return ()
        
    ExpectType = TypeVar("ExpectType", bound=Widget)
        
    def compose(self) -> ComposeResult:
        """Yield child widgets for a container.

        This method should be implemented in a subclass.
        """
        yield from ()

    def get_loading_widget(self) -> Widget:
        """Get a widget to be used as a loading indicator.

        Extend this method if you want to display the loading state a little differently.

        Returns:
            A widget to display a loading state.
        """
        from ..widgets import LoadingIndicator

        return LoadingIndicator()
    

    def get_child_by_id(
        self, id: str, expect_type: type[ExpectType] | None = None
    ) -> ExpectType | Widget:
        """Get the first child (immediate descendent) of this DOMNode with the given ID.

        Args:
            id: The ID of the node to search for.
            expect_type: Require the object be of the supplied type,
                or use `None` to apply no type restriction.

        Returns:
            The first child of this node with the specified ID.

        Raises:
            NoMatches: If no children could be found for this ID.
            WrongType: If the wrong type was found.
        """
        return (
            self._app.screen.get_child_by_id(id)
            if expect_type is None
            else self._app.screen.get_child_by_id(id, expect_type)
        )
    
    def get_widget_by_id(
        self, id: str, expect_type: type[ExpectType] | None = None
    ) -> ExpectType | Widget:
        """Get the first descendant widget with the given ID.

        Performs a breadth-first search rooted at the current screen.
        It will not return the Screen if that matches the ID.
        To get the screen, use `self.screen`.

        Args:
            id: The ID to search for in the subtree
            expect_type: Require the object be of the supplied type, or None for any type.
                Defaults to None.

        Returns:
            The first descendant encountered with this ID.

        Raises:
            NoMatches: if no children could be found for this ID
            WrongType: if the wrong type was found.
        """
        return (
            self._app.screen.get_widget_by_id(id)
            if expect_type is None
            else self._app.screen.get_widget_by_id(id, expect_type)
        )

    def get_child_by_type(self, expect_type: type[ExpectType]) -> ExpectType:
        """Get a child of a give type.

        Args:
            expect_type: The type of the expected child.

        Raises:
            NoMatches: If no valid child is found.

        Returns:
            A widget.
        """
        return self._app.screen.get_child_by_type(expect_type)
    
    async def _on_compose(self) -> None:
        _rich_traceback_omit = True
        try:
            widgets = [*self.screen._nodes, *compose(self)]
        except TypeError as error:
            raise TypeError(
                f"{self!r} compose() method returned an invalid result; {error}"
            ) from error

        await self.lifespan_controller.mount_all(widgets) #FIXME

    async def _check_recompose(self) -> None:
        """Check if a recompose is required."""
        if self._recompose_required:
            self._recompose_required = False
            await self.recompose()

    async def recompose(self) -> None:
        """Recompose the widget.

        Recomposing will remove children and call `self.compose` again to remount.
        """
        async with self._app.screen.batch():
            await self._app.screen.query("*").exclude(".-textual-system").remove()
            await self._app.screen.mount_all(compose(self))

    def _register_child(
        self, parent: DOMNode, child: Widget, before: int | None, after: int | None
    ) -> None:
        """Register a widget as a child of another.

        Args:
            parent: Parent node.
            child: The child widget to register.
            widgets: The widget to register.
            before: A location to mount before.
            after: A location to mount after.
        """

        # Let's be 100% sure that we've not been asked to do a before and an
        # after at the same time. It's possible that we can remove this
        # check later on, but for the purposes of development right now,
        # it's likely a good idea to keep it here to check assumptions in
        # the rest of the code.
        if before is not None and after is not None:
            raise AppError("Only one of 'before' and 'after' may be specified.")

        # If we don't already know about this widget...
        if child not in self._registry:
            # Now to figure out where to place it. If we've got a `before`...
            if before is not None:
                # ...it's safe to NodeList._insert before that location.
                parent._nodes._insert(before, child)
            elif after is not None and after != -1:
                # In this case we've got an after. -1 holds the special
                # position (for now) of meaning "okay really what I mean is
                # do an append, like if I'd asked to add with no before or
                # after". So... we insert before the next item in the node
                # list, iff after isn't -1.
                parent._nodes._insert(after + 1, child)
            else:
                # At this point we appear to not be adding before or after,
                # or we've got a before/after value that really means
                # "please append". So...
                parent._nodes._append(child)

            # Now that the widget is in the NodeList of its parent, sort out
            # the rest of the admin.
            self._registry.add(child)
            child._attach(parent)
            child._post_register(self)
            child._start_messages()

    def _register(
        self,
        parent: DOMNode,
        *widgets: Widget,
        before: int | None = None,
        after: int | None = None,
        cache: dict[tuple, RulesMap] | None = None,
    ) -> list[Widget]:
        """Register widget(s) so they may receive events.

        Args:
            parent: Parent node.
            *widgets: The widget(s) to register.
            before: A location to mount before.
            after: A location to mount after.
            cache: Optional rules map cache.

        Returns:
            List of modified widgets.
        """

        if not widgets:
            return []

        if cache is None:
            cache = {}
        widget_list: Iterable[Widget]
        if before is not None or after is not None:
            # There's a before or after, which means there's going to be an
            # insertion, so make it easier to get the new things in the
            # correct order.
            widget_list = reversed(widgets)
        else:
            widget_list = widgets

        apply_stylesheet = self.stylesheet.apply
        for widget in widget_list:
            if not isinstance(widget, Widget):
                raise AppError(f"Can't register {widget!r}; expected a Widget instance")
            if widget not in self._registry:
                self._register_child(parent, widget, before, after)
                if widget._nodes:
                    self._register(widget, *widget._nodes, cache=cache)
                apply_stylesheet(widget, cache=cache)

        if not self._running:
            # If the app is not running, prevent awaiting of the widget tasks
            return []

        return list(widgets)

    def _unregister(self, widget: Widget) -> None:
        """Unregister a widget.

        Args:
            widget: A Widget to unregister
        """
        widget.blur()
        if isinstance(widget._parent, Widget):
            widget._parent._nodes._remove(widget)
            widget._detach()
        self._registry.discard(widget)

    async def _disconnect_devtools(self):
        if self.devtools is not None:
            await self.devtools.disconnect()

    def _start_widget(self, parent: Widget, widget: Widget) -> None:
        """Start a widget (run it's task) so that it can receive messages.

        Args:
            parent: The parent of the Widget.
            widget: The Widget to start.
        """

        widget._attach(parent)
        widget._start_messages()
        self.app._registry.add(widget)

    async def _close_all(self) -> None:
        """Close all message pumps."""

        # Close all screens on all stacks:
        for stack in self._screen_stacks.values():
            for stack_screen in reversed(stack):
                if stack_screen._running:
                    await self._prune_node(stack_screen)
            stack.clear()

        # Close pre-defined screens.
        for screen in self.SCREENS.values():
            if isinstance(screen, Screen) and screen._running:
                await self._prune_node(screen)

        # Close any remaining nodes
        # Should be empty by now
        remaining_nodes = list(self._registry)
        for child in remaining_nodes:
            await child._close_messages()