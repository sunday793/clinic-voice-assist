from datetime import datetime


def fix_year_century(raw_year: str) -> int:
    """
    Дописывает век к году, опираясь на текущий год.
    Если названный год <= последних двух цифр текущего года, то это 2000-е.
    Иначе — 1900-е.
    """
    year_num = int(raw_year)

    # Если год уже 4-значный, не трогаем его
    if year_num > 100:
        return year_num

    # Получаем последние две цифры текущего года (для 2026 это 26)
    current_year_suffix = datetime.now().year % 100

    if year_num <= current_year_suffix:
        return 2000 + year_num
    else:
        return 1900 + year_num


def get_age(birth_year: int, birth_month: int = 1, birth_day: int = 1) -> int:
    """Вычисляет точный возраст по году, месяцу и дню."""
    today = datetime.now()
    try:
        birth_date = datetime(birth_year, birth_month, birth_day)
    except ValueError:
        return -1  # Неверная дата

    return today.year - birth_date.year - ((today.month, today.day) < (birth_date.month, birth_date.day))


def is_valid_age(birth_date_str: str) -> tuple[bool, str]:
    """Проверяет дату рождения формата ДД.ММ.ГГГГ."""
    try:
        dt = datetime.strptime(birth_date_str, "%d.%m.%Y")
    except ValueError:
        return False, "Неверный формат даты рождения."

    age = get_age(dt.year, dt.month, dt.day)

    if age < 0:
        return False, "Дата рождения в будущем."
    if age > 99:
        return False, "Слишком большой возраст. Проверьте дату рождения."

    return True, ""


def is_child(birth_date_str: str) -> bool:
    """Проверяет, меньше ли пациенту 15 лет (по регламенту)."""
    try:
        dt = datetime.strptime(birth_date_str, "%d.%m.%Y")
        age = get_age(dt.year, dt.month, dt.day)
        return age < 15
    except Exception:
        return False