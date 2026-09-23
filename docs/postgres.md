# PostgreSQL / pgAdmin 4

Готовый SQL-скрипт для локального PostgreSQL лежит в `data/city_postgres.sql`.

## Как загрузить базу через pgAdmin 4

1. Откройте pgAdmin 4 и подключитесь к серверу `localhost`.
2. Создайте базу данных, например `akim_simulator`, если ее еще нет.
3. Откройте `Query Tool` именно для этой базы.
4. Откройте файл `data/city_postgres.sql` и выполните его.

Скрипт создаст схему `city_simulator`, таблицы и заполнит их данными из
`data/city.json`.

## Проверка

После выполнения можно проверить данные:

```sql
SET search_path TO city_simulator;

SELECT COUNT(*) FROM districts;
SELECT COUNT(*) FROM indicators;
SELECT COUNT(*) FROM measures;
SELECT COUNT(*) FROM measure_effects;
SELECT COUNT(*) FROM synergies;
SELECT COUNT(*) FROM incompatibilities;
```

Ожидаемые значения:

- `districts`: 5
- `indicators`: 10
- `measures`: 14
- `measure_effects`: 28
- `synergies`: 3
- `incompatibilities`: 3

## Быстрый просмотр мер

```sql
SET search_path TO city_simulator;

SELECT id, direction, name, type, cost, lag
FROM measures
ORDER BY order_index;
```

## Пересоздание SQL из JSON

Если `data/city.json` изменится, пересоздайте PostgreSQL-скрипт:

```bash
python -m engine.postgres_dump --out data/city_postgres.sql
```
