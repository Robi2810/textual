import os
import json

def read_file(path):
    """
    Читает содержимое текстового файла и возвращает его как строку.
    
    :param path: Путь к файлу
    :return: Содержимое файла как строка
    """
    try:
        with open(path, 'r', encoding='utf-8') as file:
            content = file.read()
        print(f"File read successfully: {path}")
        return content
    except Exception as e:
        print(f"Error reading file {path}: {e}")
        return None

def write_file(path, content):
    """
    Записывает строку в текстовый файл.
    
    :param path: Путь к файлу
    :param content: Содержимое для записи в файл
    """
    try:
        with open(path, 'w', encoding='utf-8') as file:
            file.write(content)
        print(f"File written successfully: {path}")
    except Exception as e:
        print(f"Error writing file {path}: {e}")

def read_json(path):
    """
    Читает содержимое JSON файла и возвращает его как словарь.
    
    :param path: Путь к файлу JSON
    :return: Содержимое файла как словарь
    """
    try:
        with open(path, 'r', encoding='utf-8') as file:
            data = json.load(file)
        print(f"JSON file read successfully: {path}")
        return data
    except Exception as e:
        print(f"Error reading JSON file {path}: {e}")
        return None

def write_json(path, data):
    """
    Записывает словарь в JSON файл.
    
    :param path: Путь к файлу JSON
    :param data: Данные для записи в файл
    """
    try:
        with open(path, 'w', encoding='utf-8') as file:
            json.dump(data, file, ensure_ascii=False, indent=4)
        print(f"JSON file written successfully: {path}")
    except Exception as e:
        print(f"Error writing JSON file {path}: {e}")

def create_directory(path):
    """
    Создает директорию по указанному пути, если она не существует.
    
    :param path: Путь к директории
    """
    try:
        os.makedirs(path, exist_ok=True)
        print(f"Directory created successfully: {path}")
    except Exception as e:
        print(f"Error creating directory {path}: {e}")

def list_files(directory, extension=None):
    """
    Возвращает список файлов в указанной директории с опциональной фильтрацией по расширению.
    
    :param directory: Путь к директории
    :param extension: Расширение файлов для фильтрации (например, '.txt')
    :return: Список файлов
    """
    try:
        if extension:
            files = [f for f in os.listdir(directory) if f.endswith(extension)]
        else:
            files = os.listdir(directory)
        print(f"Files listed successfully in directory: {directory}")
        return files
    except Exception as e:
        print(f"Error listing files in directory {directory}: {e}")
        return []
