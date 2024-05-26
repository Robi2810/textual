class AppWidgets:
    def __init__(self):
        # Инициализация виджетов
        self.widgets = []
        print("AppWidgets initialized")

    def add_widget(self, widget):
        # Добавление виджета
        self.widgets.append(widget)
        print(f"Widget added: {widget}")
