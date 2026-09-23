"""Загрузка единственного источника данных и правил симуляции."""

import json
from pathlib import Path


CITY_PATH = Path(__file__).resolve().parent.parent / "data" / "city.json"
DATABASE_PATH = Path(__file__).resolve().parent.parent / "data" / "city.sqlite"


def load_city() -> dict:
    """Каждый вызов возвращает независимые данные, независимо от текущего cwd."""
    with CITY_PATH.open(encoding="utf-8") as source:
        return json.load(source)


def load_city_from_database(db_path: Path = DATABASE_PATH) -> dict:
    """Load the same city data shape from SQLite."""
    from .database import load_city_from_db

    return load_city_from_db(db_path)
