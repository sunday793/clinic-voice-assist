"""Пост-обработка текста после ASR (inverse text normalization).

Whisper пишет дату рождения словами и часто имена строчными:
    «иванов пётр семнадцатого марта тысяча девятьсот восемьдесят пятого года»
NLU на регулярках ждёт «Иванов Пётр, 17.03.1985». Здесь делаем это превращение,
поэтому NLU и движок менять не нужно.
"""

from __future__ import annotations

import re
from datetime import datetime

_TOKEN = re.compile(r"[А-Яа-яЁё]+|\d+")


def _n(s: str) -> str:
    return s.lower().replace("ё", "е")


_MONTH_WORDS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11,
    "декабря": 12,
}
_UNIT_ORD = {
    "первого": 1, "первое": 1, "второго": 2, "второе": 2, "третьего": 3,
    "третье": 3, "четвертого": 4, "четвертое": 4, "пятого": 5, "пятое": 5,
    "шестого": 6, "шестое": 6, "седьмого": 7, "седьмое": 7, "восьмого": 8,
    "восьмое": 8, "девятого": 9, "девятое": 9,
}
_TEEN_ORD = {
    "десятого": 10, "десятое": 10, "одиннадцатого": 11, "одиннадцатое": 11,
    "двенадцатого": 12, "двенадцатое": 12, "тринадцатого": 13,
    "тринадцатое": 13, "четырнадцатого": 14, "четырнадцатое": 14,
    "пятнадцатого": 15, "пятнадцатое": 15, "шестнадцатого": 16,
    "шестнадцатое": 16, "семнадцатого": 17, "семнадцатое": 17,
    "восемнадцатого": 18, "восемнадцатое": 18, "девятнадцатого": 19,
    "девятнадцатое": 19,
}
_TENS_ORD = {
    "двадцатого": 20, "двадцатое": 20, "тридцатого": 30, "тридцатое": 30,
    "сорокового": 40, "пятидесятого": 50, "шестидесятого": 60,
    "семидесятого": 70, "восьмидесятого": 80, "девяностого": 90,
}
_TENS_CARD = {
    "двадцать": 20, "тридцать": 30, "сорок": 40, "пятьдесят": 50,
    "шестьдесят": 60, "семьдесят": 70, "восемьдесят": 80, "девяносто": 90,
}
_TEEN_CARD = {
    "десять": 10, "одиннадцать": 11, "двенадцать": 12, "тринадцать": 13,
    "четырнадцать": 14, "пятнадцать": 15, "шестнадцать": 16,
    "семнадцать": 17, "восемнадцать": 18, "девятнадцать": 19,
}
_HUNDREDS = {
    "сто": 100, "двести": 200, "триста": 300, "четыреста": 400,
    "пятьсот": 500, "шестьсот": 600, "семьсот": 700, "восемьсот": 800,
    "девятьсот": 900,
}
_UNIT_CARD = {
    "один": 1, "одного": 1, "два": 2, "две": 2, "три": 3, "четыре": 4,
    "пять": 5, "шесть": 6, "семь": 7, "восемь": 8, "девять": 9,
}
_YEAR_WORD = ("года", "год", "году")


def _day(prev: list[str]) -> tuple[int, int] | None:
    """День по токенам перед месяцем → (день, сколько токенов занято)."""
    if not prev:
        return None
    t1 = prev[-1]
    if t1.isdigit():
        d = int(t1)
        return (d, 1) if 1 <= d <= 31 else None
    if t1 in _UNIT_ORD:
        if len(prev) >= 2 and prev[-2] in ("двадцать", "тридцать"):
            return _TENS_CARD[prev[-2]] + _UNIT_ORD[t1], 2
        return _UNIT_ORD[t1], 1
    for table in (_TEEN_ORD, _TEEN_CARD, _TENS_ORD):
        if t1 in table:
            return table[t1], 1
    return None


def _year(after: list[str]) -> tuple[int, int] | None:
    """Год по токенам после месяца → (год, сколько токенов занято)."""
    total, i, seen = 0, 0, False
    while i < len(after):
        t = after[i]
        if t.isdigit():
            if len(t) == 4 and not seen:
                total, seen, i = int(t), True, i + 1
            break
        if t in _YEAR_WORD:
            break
        if t == "двухтысячного":
            total += 2000
        elif t in ("две", "два") and i + 1 < len(after) and after[i + 1].startswith("тысяч"):
            total += 2000
            i += 1
        elif t.startswith("тысяч"):
            total += 1000
        elif t in _HUNDREDS:
            total += _HUNDREDS[t]
        elif t in _TENS_CARD:
            total += _TENS_CARD[t]
        elif t in _TENS_ORD:
            total += _TENS_ORD[t]
        elif t in _TEEN_CARD:
            total += _TEEN_CARD[t]
        elif t in _TEEN_ORD:
            total += _TEEN_ORD[t]
        elif t in _UNIT_ORD:
            total += _UNIT_ORD[t]
        elif t in _UNIT_CARD:
            total += _UNIT_CARD[t]
        else:
            break
        seen = True
        i += 1
    if not (seen and total):
        return None
    if i < len(after) and after[i] in _YEAR_WORD:
        i += 1
    return total, i


def spoken_dates_to_digits(text: str) -> str:
    """«семнадцатого марта тысяча девятьсот восемьдесят пятого года» → «17.03.1985»."""
    toks = [(m.start(), m.end(), _n(m.group())) for m in _TOKEN.finditer(text)]
    reps: list[tuple[int, int, str]] = []
    for idx, (_, _, tok) in enumerate(toks):
        if tok not in _MONTH_WORDS:
            continue
        d = _day([t[2] for t in toks[max(0, idx - 2):idx]])
        y = _year([t[2] for t in toks[idx + 1:]])
        if not (d and y):
            continue
        (day, day_n), (year, year_n) = d, y
        if not 1900 <= year <= datetime.now().year + 1:
            continue
        try:
            date = datetime(year, _MONTH_WORDS[tok], day)
        except ValueError:
            continue
        reps.append((toks[idx - day_n][0], toks[idx + year_n][1], date.strftime("%d.%m.%Y")))
    for start, end, value in reversed(reps):
        text = text[:start] + value + text[end:]
    return text


_NAME_BEFORE_DATE = re.compile(
    r"\b([а-яё]+(?:-[а-яё]+)?)[\s,]+([а-яё]+(?:-[а-яё]+)?)[\s,]+(?=\d{2}\.\d{2}\.\d{4})"
)
_NOT_NAME = {
    "родился", "родилась", "года", "год", "рождения", "дата", "я", "в", "и",
    "меня", "зовут", "мне", "это", "моя", "мой",
}


def _title(w: str) -> str:
    return "-".join(p.capitalize() for p in w.split("-"))


def capitalize_names(text: str) -> str:
    """Два слова непосредственно перед датой рождения → «Фамилия Имя»."""

    def rep(m: re.Match) -> str:
        a, b = m.group(1), m.group(2)
        if a in _NOT_NAME or b in _NOT_NAME:
            return m.group(0)
        return f"{_title(a)} {_title(b)}, "

    return _NAME_BEFORE_DATE.sub(rep, text)


def normalize(text: str) -> str:
    """Полная пост-обработка реплики после ASR."""
    return capitalize_names(spoken_dates_to_digits(text))
