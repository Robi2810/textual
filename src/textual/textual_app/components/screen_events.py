from typing import Any

class ScreenEvent:
    def __init__(self, event_type: str, data: Any = None):
        self.event_type = event_type
        self.data = data

    def handle_event(self, event: ScreenEvent) -> None:
        """Handle the given event."""
        pass

