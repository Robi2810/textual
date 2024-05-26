from typing import Any

class WidgetEvent:
    def __init__(self, event_type: str, data: Any = None):
        self.event_type = event_type
        self.data = data

    def handle_event(self, event: WidgetEvent) -> None:
        """Handle the given event."""
        pass

