import pytest

from engine import simulator as simulator_module
from engine import validator as validator_module
from engine.data import load_city, load_city_from_database
from engine.database import initialize_database, load_city_from_db


def test_database_roundtrip_matches_json(tmp_path):
    db_path = tmp_path / "city.sqlite"

    initialize_database(db_path=db_path)

    assert load_city_from_db(db_path) == load_city()
    assert load_city_from_database(db_path) == load_city()


def test_database_data_can_drive_engine(tmp_path, monkeypatch):
    db_path = tmp_path / "city.sqlite"
    initialize_database(db_path=db_path)
    city = load_city_from_db(db_path)
    districts = list(city["districts"])
    nura = districts[4]
    saryarka = districts[2]

    monkeypatch.setattr(
        simulator_module,
        "load_city",
        lambda: load_city_from_db(db_path),
    )
    monkeypatch.setattr(
        validator_module,
        "load_city",
        lambda: load_city_from_db(db_path),
    )

    decisions = [
        {"measure_id": "M7", "district": nura},
        {"measure_id": "M8", "district": nura},
        {"measure_id": "M10", "district": nura},
        {"measure_id": "M12"},
        {"measure_id": "M5", "district": saryarka},
    ]

    assert validator_module.validate(decisions) == []
    assert simulator_module.simulate(decisions)["Score"] == pytest.approx(56.54307)
