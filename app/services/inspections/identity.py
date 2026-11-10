"""Проверка идентичности (звонит ли сам пациент или за него)."""
from app.services.inspections.gender import guess_gender_by_middle_name
from app.services.inspections.age import is_valid_age, is_child


def validate_patient_data(birth_date: str, middle_name: str | None) -> tuple[bool, str]:
    """Комплексная проверка данных пациента перед записью.
    Возвращает (True, "") если ок, иначе (False, "Сообщение об ошибке").
    """
    # 1. Проверка возраста
    is_age_ok, age_err = is_valid_age(birth_date)
    if not is_age_ok:
        return False, age_err

    # 2. Определение пола по отчеству (для информации)
    gender = guess_gender_by_middle_name(middle_name)

    # 3. Если это ребёнок, а отчество не указано — это нормально
    if is_child(birth_date) and not middle_name:
        return True, ""

    # 4. Здесь можно добавить проверку: если отчество мужское (Иванович),
    # но пациент зарегистрирован в МИС как женщина — это конфликт.

    return True, ""