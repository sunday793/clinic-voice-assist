"""Тесты для пакета inspections (проверка возраста, пола, идентичности)."""

import pytest
from datetime import datetime, timedelta

from app.services.inspections.age import fix_year_century, is_valid_age, is_child
from app.services.inspections.legal_age import LegalAge
from app.services.inspections.gender import guess_gender_by_middle_name


# ─────────────────────────────────────────────────────────────
# 1. Тесты для fix_year_century (исправление века)
# ─────────────────────────────────────────────────────────────

class TestFixYearCentury:
    def test_two_digit_year_2000s(self):
        """Если год <= последних цифр текущего года, то это 2000-е."""
        current_year_suffix = datetime.now().year % 100

        # Допустим, сейчас 2026. Год "8" (2008) или "26" (2026).
        year_8 = fix_year_century("8")
        year_26 = fix_year_century("26")

        assert year_8 == 2008
        assert year_26 == 2000 + current_year_suffix

    def test_two_digit_year_1900s(self):
        """Если год > последних цифр текущего года, то это 1900-е."""
        current_year_suffix = datetime.now().year % 100

        # Допустим, сейчас 2026. Год "78" (1978) или "94" (1994).
        # Они точно больше 26.
        year_78 = fix_year_century("78")
        year_94 = fix_year_century("94")

        assert year_78 == 1978
        assert year_94 == 1994

    def test_four_digit_year_untouched(self):
        """Четырехзначные годы не меняются."""
        assert fix_year_century("1985") == 1985
        assert fix_year_century("2001") == 2001


# ─────────────────────────────────────────────────────────────
# 2. Тесты для LegalAge (точный возраст и статус)
# ─────────────────────────────────────────────────────────────

class TestLegalAge:
    def test_exact_age_before_birthday(self):
        """Если день рождения еще не наступил в этом году, возраст меньше на 1."""
        today = datetime.now()

        # Берем сегодняшнюю дату и прибавляем 1 день (день рождения завтра)
        tomorrow = today + timedelta(days=1)
        birth_date = tomorrow.replace(year=today.year - 20).strftime("%d.%m.%Y")

        age_info = LegalAge(birth_date)

        # Если сегодня 18 октября, а ДР 19 октября 2006 года, то возраст = 19 (еще не 20)
        assert age_info.age == 19

    def test_exact_age_after_birthday(self):
        """Если день рождения уже прошел, возраст полный."""
        today = datetime.now()

        # Берем вчерашнюю дату (день рождения был вчера)
        yesterday = today - timedelta(days=1)
        birth_date = yesterday.replace(year=today.year - 20).strftime("%d.%m.%Y")

        age_info = LegalAge(birth_date)
        assert age_info.age == 20

    def test_is_child_true(self):
        """Ребенок до 15 лет."""
        birth_date = "12.03.2015"  # ~11 лет
        age_info = LegalAge(birth_date)
        assert age_info.is_child is True

    def test_is_child_false(self):
        """Взрослый (15+ лет)."""
        birth_date = "17.03.1985"  # ~41 год
        age_info = LegalAge(birth_date)
        assert age_info.is_child is False

    def test_is_valid_edge_cases(self):
        """Проверка на невалидный возраст (будущее или >99)."""
        # Будущее (родился в следующем году)
        future_year = datetime.now().year + 1
        future_date = f"01.01.{future_year}"
        age_future = LegalAge(future_date)
        assert age_future.is_valid is False

        # Слишком старый (100+ лет)
        old_date = f"01.01.{datetime.now().year - 100}"
        age_old = LegalAge(old_date)
        assert age_old.is_valid is False

    def test_invalid_date_raises_error(self):
        """Неверный формат даты вызывает ValueError."""
        with pytest.raises(ValueError):
            LegalAge("32.13.2020")


# ─────────────────────────────────────────────────────────────
# 3. Тесты для старых функций валидации (age.py)
# ─────────────────────────────────────────────────────────────

class TestLegacyAgeFunctions:
    def test_is_valid_age(self):
        assert is_valid_age("17.03.1985") == (True, "")
        assert is_valid_age("32.13.2020") == (False, "Неверный формат даты рождения.")

        # 150 лет — невалидно
        old_date = f"01.01.{datetime.now().year - 150}"
        is_ok, err = is_valid_age(old_date)
        assert is_ok is False
        assert "Слишком большой возраст" in err

    def test_is_child_function(self):
        assert is_child("12.03.2015") is True  # ~11 лет
        assert is_child("17.03.1985") is False  # ~41 год


# ─────────────────────────────────────────────────────────────
# 4. Тесты для gender.py (определение пола)
# ─────────────────────────────────────────────────────────────

class TestGenderDetection:
    def test_male_middle_name(self):
        assert guess_gender_by_middle_name("Иванович") == "male"
        assert guess_gender_by_middle_name("Петрович") == "male"
        assert guess_gender_by_middle_name("Сергеевич") == "male"

    def test_female_middle_name(self):
        assert guess_gender_by_middle_name("Ивановна") == "female"
        assert guess_gender_by_middle_name("Петровна") == "female"
        assert guess_gender_by_middle_name("Сергеевна") == "female"

    def test_unknown_middle_name(self):
        assert guess_gender_by_middle_name("Смит") is None
        assert guess_gender_by_middle_name("") is None
        assert guess_gender_by_middle_name(None) is None