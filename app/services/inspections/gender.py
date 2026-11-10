"""Проверка пола по отчеству."""


def guess_gender_by_middle_name(middle_name: str) -> str | None:
    """Определяет пол по отчеству (Иванович -> male, Ивановна -> female)."""
    if not middle_name:
        return None

    mn = middle_name.lower()
    if mn.endswith(("ич", "ича", "ичу", "ичем", "иче")):
        return "male"
    if mn.endswith(("на", "ны", "не", "ну", "ной")):
        return "female"
    return None