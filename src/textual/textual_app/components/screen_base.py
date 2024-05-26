from __future__ import annotations
import asyncio
from functools import partial
from operator import attrgetter
from typing import (
    TYPE_CHECKING,
    Any,
    Awaitable,
    Callable,
    ClassVar,
    Generic,
    Iterable,
    Iterator,
    Type,
    TypeVar,
    Union,
    cast,
)

import rich.repr
from rich.console import RenderableType
from rich.style import Style

from . import constants, errors, events, messages
from ._callback import invoke
from ._compositor import Compositor, MapGeometry
from ._context import active_message_pump, visible_screen_stack
from ._path import CSSPathType, _css_path_type_as_list, _make_path_object_relative
from ._types import CallbackType
from .binding import ActiveBinding, Binding, _Bindings
from .css.match import match
from .css.parse import parse_selectors
from .css.query import NoMatches, QueryType
from .dom import DOMNode
from .errors import NoWidget
from .geometry import Offset, Region, Size
from .reactive import Reactive
from .renderables.background_screen import BackgroundScreen
from .renderables.blank import Blank
from .signal import Signal
from .timer import Timer
from .widget import Widget
from .widgets import Tooltip
from .widgets._toast import ToastRack

if TYPE_CHECKING:
    from typing_extensions import Final
    from .command import Provider
    from .message_pump import MessagePump

UPDATE_PERIOD: Final[float] = 1 / constants.MAX_FPS

ScreenResultType = TypeVar("ScreenResultType")
ScreenResultCallbackType = Union[
    Callable[[ScreenResultType], None], Callable[[ScreenResultType], Awaitable[None]]
]

class Screen(DOMNode):
    def __init__(self, name: str | None = None):
        super().__init__(name)

    def render(self) -> RenderableType:
        """Render the screen."""
        pass

