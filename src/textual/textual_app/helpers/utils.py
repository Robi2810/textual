import logging
from datetime import datetime

def log(message, level='info'):
    """
    Логирует сообщение с указанным уровнем.
    
    :param message: Сообщение для логирования
    :param level: Уровень логирования ('info', 'warning', 'error', 'debug')
    """
    logger = logging.getLogger('textual_app')
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)
    
    log_func = getattr(logger, level.lower(), logger.info)
    log_func(message)

def format_text(text, style='bold'):
    """
    Форматирует текст с использованием указанного стиля.
    
    :param text: Текст для форматирования
    :param style: Стиль форматирования ('bold', 'italic', 'underline')
    :return: Форматированный текст
    """
    styles = {
        'bold': '\033[1m{}\033[0m',
        'italic': '\033[3m{}\033[0m',
        'underline': '\033[4m{}\033[0m'
    }
    return styles.get(style, '{}').format(text)

def get_current_time_str(format="%Y-%m-%d %H:%M:%S"):
    """
    Возвращает текущее время в виде строки в заданном формате.
    
    :param format: Формат даты и времени
    :return: Текущее время в виде строки
    """
    return datetime.now().strftime(format)

def validate_email(email):
    """
    Проверяет корректность email адреса.
    
    :param email: Email адрес для проверки
    :return: True если email корректен, иначе False
    """
    import re
    pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
    return re.match(pattern, email) is not None

def calculate_checksum(data):
    """
    Вычисляет контрольную сумму для данных.
    
    :param data: Данные для вычисления контрольной суммы
    :return: Контрольная сумма
    """
    import hashlib
    checksum = hashlib.md5(data.encode()).hexdigest()
    return checksum
