"""SQLite storage for the city simulation dataset."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


ROOT_PATH = Path(__file__).resolve().parent.parent
CITY_JSON_PATH = ROOT_PATH / "data" / "city.json"
DATABASE_PATH = ROOT_PATH / "data" / "city.sqlite"


DROP_SCHEMA_SQL = """
DROP TABLE IF EXISTS incompatibility_measures;
DROP TABLE IF EXISTS incompatibilities;
DROP TABLE IF EXISTS synergy_effects;
DROP TABLE IF EXISTS synergy_measures;
DROP TABLE IF EXISTS synergies;
DROP TABLE IF EXISTS measure_effects;
DROP TABLE IF EXISTS measures;
DROP TABLE IF EXISTS district_indicators;
DROP TABLE IF EXISTS districts;
DROP TABLE IF EXISTS weights;
DROP TABLE IF EXISTS indicators;
DROP TABLE IF EXISTS reference_scenarios;
DROP TABLE IF EXISTS scoring;
DROP TABLE IF EXISTS rules;
DROP TABLE IF EXISTS metadata;
"""


SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS rules (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS scoring (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS reference_scenarios (
    name TEXT PRIMARY KEY,
    data_json TEXT NOT NULL,
    order_index INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS indicators (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT NOT NULL,
    order_index INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS weights (
    indicator_id TEXT PRIMARY KEY REFERENCES indicators(id) ON DELETE CASCADE,
    weight NUMERIC NOT NULL,
    order_index INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS districts (
    name TEXT PRIMARY KEY,
    population_share NUMERIC NOT NULL,
    reference_d NUMERIC,
    profile TEXT NOT NULL,
    order_index INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS district_indicators (
    district_name TEXT NOT NULL REFERENCES districts(name) ON DELETE CASCADE,
    indicator_id TEXT NOT NULL REFERENCES indicators(id) ON DELETE CASCADE,
    value NUMERIC NOT NULL,
    PRIMARY KEY (district_name, indicator_id)
);

CREATE TABLE IF NOT EXISTS measures (
    id TEXT PRIMARY KEY,
    direction TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('city', 'district')),
    cost INTEGER NOT NULL,
    lag INTEGER NOT NULL,
    order_index INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS measure_effects (
    measure_id TEXT NOT NULL REFERENCES measures(id) ON DELETE CASCADE,
    indicator_id TEXT NOT NULL REFERENCES indicators(id) ON DELETE CASCADE,
    effect NUMERIC NOT NULL,
    PRIMARY KEY (measure_id, indicator_id)
);

CREATE TABLE IF NOT EXISTS synergies (
    id INTEGER PRIMARY KEY,
    target_measure TEXT NOT NULL REFERENCES measures(id) ON DELETE CASCADE,
    order_index INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS synergy_measures (
    synergy_id INTEGER NOT NULL REFERENCES synergies(id) ON DELETE CASCADE,
    measure_id TEXT NOT NULL REFERENCES measures(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (synergy_id, position)
);

CREATE TABLE IF NOT EXISTS synergy_effects (
    synergy_id INTEGER NOT NULL REFERENCES synergies(id) ON DELETE CASCADE,
    indicator_id TEXT NOT NULL REFERENCES indicators(id) ON DELETE CASCADE,
    effect NUMERIC NOT NULL,
    PRIMARY KEY (synergy_id, indicator_id)
);

CREATE TABLE IF NOT EXISTS incompatibilities (
    id INTEGER PRIMARY KEY,
    scope TEXT NOT NULL CHECK (scope IN ('global', 'same_district')),
    reason TEXT NOT NULL,
    order_index INTEGER NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS incompatibility_measures (
    incompatibility_id INTEGER NOT NULL REFERENCES incompatibilities(id) ON DELETE CASCADE,
    measure_id TEXT NOT NULL REFERENCES measures(id) ON DELETE CASCADE,
    position INTEGER NOT NULL,
    PRIMARY KEY (incompatibility_id, position)
);
"""


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _json_load(value: str) -> Any:
    return json.loads(value)


def _connect(db_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize_database(
    json_path: Path = CITY_JSON_PATH,
    db_path: Path = DATABASE_PATH,
    *,
    reset: bool = True,
) -> Path:
    """Create the SQLite schema and seed it from the JSON dataset."""
    with json_path.open(encoding="utf-8") as source:
        city = json.load(source)

    db_path.parent.mkdir(parents=True, exist_ok=True)
    with _connect(db_path) as connection:
        if reset:
            connection.executescript(DROP_SCHEMA_SQL)
        connection.executescript(SCHEMA_SQL)
        _clear_database(connection)
        _seed_database(connection, city)
        violations = connection.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"SQLite foreign key violations: {violations!r}")
    return db_path


def load_city_from_db(db_path: Path = DATABASE_PATH) -> dict:
    """Load the city dataset from SQLite in the same shape as city.json."""
    with _connect(db_path) as connection:
        return {
            **_load_metadata(connection),
            "rules": _load_key_value_table(connection, "rules"),
            "scoring": _load_key_value_table(connection, "scoring"),
            "indicators": _load_indicators(connection),
            "weights": _load_weights(connection),
            "districts": _load_districts(connection),
            "measures": _load_measures(connection),
            "synergies": _load_synergies(connection),
            "incompatibilities": _load_incompatibilities(connection),
            "reference_scenarios": _load_reference_scenarios(connection),
        }


def _clear_database(connection: sqlite3.Connection) -> None:
    for table in (
        "incompatibility_measures",
        "incompatibilities",
        "synergy_effects",
        "synergy_measures",
        "synergies",
        "measure_effects",
        "measures",
        "district_indicators",
        "districts",
        "weights",
        "indicators",
        "reference_scenarios",
        "scoring",
        "rules",
        "metadata",
    ):
        connection.execute(f"DELETE FROM {table}")


def _seed_database(connection: sqlite3.Connection, city: dict) -> None:
    _insert_key_values(
        connection,
        "metadata",
        {
            "budget": city["budget"],
            "horizon": city["horizon"],
            "horizon_unit": city["horizon_unit"],
        },
    )
    _insert_key_values(connection, "rules", city["rules"])
    _insert_key_values(connection, "scoring", city["scoring"])

    for order, (indicator_id, indicator) in enumerate(city["indicators"].items()):
        connection.execute(
            """
            INSERT INTO indicators (id, direction, name, description, order_index)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                indicator_id,
                indicator["direction"],
                indicator["name"],
                indicator["description"],
                order,
            ),
        )

    for order, (indicator_id, weight) in enumerate(city["weights"].items()):
        connection.execute(
            "INSERT INTO weights (indicator_id, weight, order_index) VALUES (?, ?, ?)",
            (indicator_id, weight, order),
        )

    for order, (district_name, district) in enumerate(city["districts"].items()):
        connection.execute(
            """
            INSERT INTO districts
                (name, population_share, reference_d, profile, order_index)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                district_name,
                district["population_share"],
                district.get("reference_D"),
                district["profile"],
                order,
            ),
        )
        for indicator_id, value in district["indicators"].items():
            connection.execute(
                """
                INSERT INTO district_indicators
                    (district_name, indicator_id, value)
                VALUES (?, ?, ?)
                """,
                (district_name, indicator_id, value),
            )

    for order, measure in enumerate(city["measures"]):
        connection.execute(
            """
            INSERT INTO measures
                (id, direction, name, type, cost, lag, order_index)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                measure["id"],
                measure["direction"],
                measure["name"],
                measure["type"],
                measure["cost"],
                measure["lag"],
                order,
            ),
        )
        for indicator_id, effect in measure["effects"].items():
            connection.execute(
                """
                INSERT INTO measure_effects (measure_id, indicator_id, effect)
                VALUES (?, ?, ?)
                """,
                (measure["id"], indicator_id, effect),
            )

    for order, synergy in enumerate(city["synergies"]):
        cursor = connection.execute(
            """
            INSERT INTO synergies (target_measure, order_index)
            VALUES (?, ?)
            """,
            (synergy["target_measure"], order),
        )
        synergy_id = cursor.lastrowid
        for position, measure_id in enumerate(synergy["measures"]):
            connection.execute(
                """
                INSERT INTO synergy_measures (synergy_id, measure_id, position)
                VALUES (?, ?, ?)
                """,
                (synergy_id, measure_id, position),
            )
        for indicator_id, effect in synergy["effects"].items():
            connection.execute(
                """
                INSERT INTO synergy_effects (synergy_id, indicator_id, effect)
                VALUES (?, ?, ?)
                """,
                (synergy_id, indicator_id, effect),
            )

    for order, conflict in enumerate(city["incompatibilities"]):
        cursor = connection.execute(
            """
            INSERT INTO incompatibilities (scope, reason, order_index)
            VALUES (?, ?, ?)
            """,
            (conflict["scope"], conflict["reason"], order),
        )
        conflict_id = cursor.lastrowid
        for position, measure_id in enumerate(conflict["measures"]):
            connection.execute(
                """
                INSERT INTO incompatibility_measures
                    (incompatibility_id, measure_id, position)
                VALUES (?, ?, ?)
                """,
                (conflict_id, measure_id, position),
            )

    for order, (name, data) in enumerate(city["reference_scenarios"].items()):
        connection.execute(
            """
            INSERT INTO reference_scenarios (name, data_json, order_index)
            VALUES (?, ?, ?)
            """,
            (name, _json_dump(data), order),
        )


def _insert_key_values(
    connection: sqlite3.Connection,
    table: str,
    values: dict[str, Any],
) -> None:
    for key, value in values.items():
        connection.execute(
            f"INSERT INTO {table} (key, value_json) VALUES (?, ?)",
            (key, _json_dump(value)),
        )


def _load_metadata(connection: sqlite3.Connection) -> dict:
    values = _load_key_value_table(connection, "metadata")
    return {
        "budget": values["budget"],
        "horizon": values["horizon"],
        "horizon_unit": values["horizon_unit"],
    }


def _load_key_value_table(connection: sqlite3.Connection, table: str) -> dict:
    rows = connection.execute(f"SELECT key, value_json FROM {table}").fetchall()
    return {row["key"]: _json_load(row["value_json"]) for row in rows}


def _load_indicators(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        """
        SELECT id, direction, name, description
        FROM indicators
        ORDER BY order_index
        """
    ).fetchall()
    return {
        row["id"]: {
            "direction": row["direction"],
            "name": row["name"],
            "description": row["description"],
        }
        for row in rows
    }


def _load_weights(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        """
        SELECT indicator_id, weight
        FROM weights
        ORDER BY order_index
        """
    ).fetchall()
    return {row["indicator_id"]: row["weight"] for row in rows}


def _load_districts(connection: sqlite3.Connection) -> dict:
    districts = {}
    rows = connection.execute(
        """
        SELECT name, population_share, reference_d, profile
        FROM districts
        ORDER BY order_index
        """
    ).fetchall()
    for row in rows:
        districts[row["name"]] = {
            "population_share": row["population_share"],
            "indicators": _load_district_indicators(connection, row["name"]),
            "reference_D": row["reference_d"],
            "profile": row["profile"],
        }
    return districts


def _load_district_indicators(
    connection: sqlite3.Connection,
    district_name: str,
) -> dict:
    rows = connection.execute(
        """
        SELECT di.indicator_id, di.value
        FROM district_indicators AS di
        JOIN indicators AS i ON i.id = di.indicator_id
        WHERE di.district_name = ?
        ORDER BY i.order_index
        """,
        (district_name,),
    ).fetchall()
    return {row["indicator_id"]: row["value"] for row in rows}


def _load_measures(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, direction, name, type, cost, lag
        FROM measures
        ORDER BY order_index
        """
    ).fetchall()
    return [
        {
            "id": row["id"],
            "direction": row["direction"],
            "name": row["name"],
            "type": row["type"],
            "cost": row["cost"],
            "lag": row["lag"],
            "effects": _load_measure_effects(connection, row["id"]),
        }
        for row in rows
    ]


def _load_measure_effects(connection: sqlite3.Connection, measure_id: str) -> dict:
    rows = connection.execute(
        """
        SELECT me.indicator_id, me.effect
        FROM measure_effects AS me
        JOIN indicators AS i ON i.id = me.indicator_id
        WHERE me.measure_id = ?
        ORDER BY i.order_index
        """,
        (measure_id,),
    ).fetchall()
    return {row["indicator_id"]: row["effect"] for row in rows}


def _load_synergies(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, target_measure
        FROM synergies
        ORDER BY order_index
        """
    ).fetchall()
    return [
        {
            "measures": _load_related_measures(
                connection,
                "synergy_measures",
                "synergy_id",
                row["id"],
            ),
            "target_measure": row["target_measure"],
            "effects": _load_synergy_effects(connection, row["id"]),
        }
        for row in rows
    ]


def _load_synergy_effects(connection: sqlite3.Connection, synergy_id: int) -> dict:
    rows = connection.execute(
        """
        SELECT se.indicator_id, se.effect
        FROM synergy_effects AS se
        JOIN indicators AS i ON i.id = se.indicator_id
        WHERE se.synergy_id = ?
        ORDER BY i.order_index
        """,
        (synergy_id,),
    ).fetchall()
    return {row["indicator_id"]: row["effect"] for row in rows}


def _load_incompatibilities(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute(
        """
        SELECT id, scope, reason
        FROM incompatibilities
        ORDER BY order_index
        """
    ).fetchall()
    return [
        {
            "measures": _load_related_measures(
                connection,
                "incompatibility_measures",
                "incompatibility_id",
                row["id"],
            ),
            "scope": row["scope"],
            "reason": row["reason"],
        }
        for row in rows
    ]


def _load_related_measures(
    connection: sqlite3.Connection,
    table: str,
    parent_column: str,
    parent_id: int,
) -> list[str]:
    rows = connection.execute(
        f"""
        SELECT measure_id
        FROM {table}
        WHERE {parent_column} = ?
        ORDER BY position
        """,
        (parent_id,),
    ).fetchall()
    return [row["measure_id"] for row in rows]


def _load_reference_scenarios(connection: sqlite3.Connection) -> dict:
    rows = connection.execute(
        """
        SELECT name, data_json
        FROM reference_scenarios
        ORDER BY order_index
        """
    ).fetchall()
    return {row["name"]: _json_load(row["data_json"]) for row in rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the city SQLite database.")
    parser.add_argument("--json", type=Path, default=CITY_JSON_PATH)
    parser.add_argument("--db", type=Path, default=DATABASE_PATH)
    parser.add_argument("--keep", action="store_true", help="Do not drop tables first.")
    args = parser.parse_args(argv)

    db_path = initialize_database(args.json, args.db, reset=not args.keep)
    print(f"SQLite database created: {db_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
