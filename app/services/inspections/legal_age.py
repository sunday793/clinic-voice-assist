"""Проверка точного возраста и правового статуса пациента."""

from datetime import datetime, date


class LegalAge:
    def __init__(self, birth_date_str: str):
        """
        Принимает строку даты рождения в формате ДД.ММ.ГГГГ.
        Если формат неверный, выбрасывает ValueError.
        """
        self.birth_date = datetime.strptime(birth_date_str, "%d.%m.%Y").date()
        self.today = date.today()

    @property
    def age(self) -> int:
        """
        Возвращает точный возраст в годах (учитывая, прошел ли уже день рождения).
        Например: 11.11.2011 в 18.10.2026 -> вернет 14 лет.
        """
        # Считаем разницу в годах
        years = self.today.year - self.birth_date.year

        # Если день рождения в этом году еще не наступил (или сегодня),
        # вычитаем 1 год.
        if (self.today.month, self.today.day) < (self.birth_date.month, self.birth_date.day):
            years -= 1

        return years

    @property
    def is_child(self) -> bool:
        """Ребенок ли это (до 15 лет по регламенту, или до 18 по закону)."""
        # В регламенте сказано: запись с 15 лет. Значит, до 15 лет - ребенок.
        return self.age < 15

    @property
    def is_valid(self) -> bool:
        """Валидный ли возраст (не из будущего и не больше 99 лет)."""
        return 0 <= self.age <= 99

    def get_status(self) -> str:
        """Возвращает статус для диалога."""
        if not self.is_valid:
            return "Ошибка в дате рождения."
        if self.is_child:
            return "Ребёнок"
        return "Взрослый"
