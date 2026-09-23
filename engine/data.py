"""Загрузка единственного источника данных и правил симуляции."""

import json
from pathlib import Path


CITY_PATH = Path(__file__).resolve().parent.parent / "data" / "city.json"


def load_city() -> dict:
    """Каждый вызов возвращает независимые данные, независимо от текущего cwd."""
    with CITY_PATH.open(encoding="utf-8") as source:
        return json.load(source)
