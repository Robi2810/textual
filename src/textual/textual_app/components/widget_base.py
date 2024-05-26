from __future__ import annotations
from asyncio import Lock, create_task, wait
from collections import Counter
from contextlib import asynccontextmanager
from fractions import Fraction
from itertools import islice
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    AsyncGenerator,
    Awaitable,
    ClassVar,
    Collection,
    Generator,
    Iterable,
    NamedTuple,
    Sequence,
    TypeVar,
    cast,
    overload,
)

import rich.repr
from rich.console import (
    Console,
    ConsoleOptions,
    ConsoleRenderable,
    JustifyMethod,
    RenderableType,
)
from rich.console import RenderResult as RichRenderResult
from rich.console import RichCast
from rich.measure import Measurement
from rich.segment import Segment
from rich.style import Style
from rich.text import Text
from typing_extensions import Self

if TYPE_CHECKING:
    from .app import RenderResult

from . import constants, errors, events, messages
from ._animator import DEFAULT_EASING, Animatable, BoundAnimator, EasingFunction
from ._arrange import DockArrangeResult, arrange
from ._compose import compose
from ._context import NoActiveAppError, active_app
from ._easing import DEFAULT_SCROLL_EASING
from ._layout import Layout
from ._segment_tools import align_lines
from ._styles_cache import StylesCache
from ._types import AnimationLevel
from .actions import SkipAction
from .await_complete import AwaitComplete
from .await_remove import AwaitRemove
from .box_model import BoxModel
from .cache import FIFOCache
from .css.match import match
from .css.parse import parse_selectors
from .css.query import NoMatches, WrongType
from .css.scalar import ScalarOffset
from .dom import DOMNode, NoScreen
from .geometry import (
    NULL_REGION,
    NULL_SIZE,
    NULL_SPACING,
    Offset,
    Region,
    Size,
    Spacing,
    clamp,
)
from .layouts.vertical import VerticalLayout
from .message import Message
from .messages import CallbackType
from .notification import Notification

class Widget(DOMNode):
    def __init__(self, name: str | None = None):
        super().__init__(name)

    def render(self) -> RenderableType:
        """Render the widget."""
        pass

