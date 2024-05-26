import json
import os

class Settings:
    def __init__(self, config_file="config.json"):
        self.config_file = config_file
        self.settings = {
            "window_size": (800, 600),
            "theme": "light",
            "language": "en",
            "show_fps": False
        }
        self.load()

    def load(self):
        """Загружает настройки из файла"""
        if os.path.exists(self.config_file):
            try:
                with open(self.config_file, "r") as f:
                    self.settings.update(json.load(f))
                print(f"Settings loaded from {self.config_file}")
            except Exception as e:
                print(f"Failed to load settings: {e}")
        else:
            print(f"No settings file found, using default settings")

    def save(self):
        """Сохраняет текущие настройки в файл"""
        try:
            with open(self.config_file, "w") as f:
                json.dump(self.settings, f, indent=4)
            print(f"Settings saved to {self.config_file}")
        except Exception as e:
            print(f"Failed to save settings: {e}")

    def get(self, key, default=None):
        """Получает значение настройки по ключу"""
        return self.settings.get(key, default)

    def set(self, key, value):
        """Устанавливает значение настройки по ключу"""
        self.settings[key] = value
        self.save()

    def reset_to_defaults(self):
        """Сбрасывает настройки к значениям по умолчанию"""
        self.settings = {
            "window_size": (800, 600),
            "theme": "light",
            "language": "en",
            "show_fps": False
        }
        self.save()
