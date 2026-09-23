"""Полный перебор допустимых сценариев штатными validate() и simulate()."""

import argparse
from bisect import bisect_left, bisect_right
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from heapq import heappush, heapreplace
from itertools import combinations, product
import json
from pathlib import Path
import sys
from time import monotonic


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.data import load_city
from engine.scenario_stats import dataset_fingerprint
from engine.simulator import simulate
from engine.validator import validate


def measure_groups(city):
    """Отсечь только запреты, не зависящие от назначения районов."""
    for group in combinations(city["measures"], city["rules"]["decision_count"]):
        if sum(item["cost"] for item in group) > city["budget"]:
            continue
        if max(Counter(item["direction"] for item in group).values()) > city["rules"]["max_per_direction"]:
            continue
        ids = {item["id"] for item in group}
        if any(rule["scope"] == "global" and set(rule["measures"]) <= ids
               for rule in city["incompatibilities"]):
            continue
        yield group


def district_assignments(group, districts):
    """Каждая уникальная комбинация мер и районов ровно один раз."""
    choices = [districts if item["type"] == "district" else (None,) for item in group]
    for assignment in product(*choices):
        yield [
            {"measure_id": item["id"], **({"district": district} if district is not None else {})}
            for item, district in zip(group, assignment)
        ]


def keep_best(heap, entry, index):
    # При одинаковом Score сохраняем первые варианты в порядке полного перебора.
    item = (entry["Score"], -index, entry)
    if len(heap) < 5:
        heappush(heap, item)
    elif item[:2] > heap[0][:2]:
        heapreplace(heap, item)


def evaluate_group(task):
    group, districts = task
    scores, best = [], []
    for decisions in district_assignments(group, districts):
        if validate(decisions):
            continue
        result = simulate(decisions)
        scores.append(result["Score"])
        keep_best(best, {"decisions": decisions, "Score": result["Score"], "cost": result["cost"]}, len(scores))
    return scores, [item[2] for item in sorted(best, reverse=True)]


def distribution_quantiles(scores):
    """1001 эмпирический квантиль (lower); равные Score не считаются худшими."""
    scores = sorted(scores)
    result = []
    for step in range(1001):
        numerator = (len(scores) - 1) * step
        lower = numerator // 1000
        value = scores[lower]
        result.append({
            "percentile": step / 10,
            "score": value,
            "count_less": bisect_left(scores, value),
            "count_less_or_equal": bisect_right(scores, value),
        })
    return result


def precompute(workers=1):
    city = load_city()
    groups = list(measure_groups(city))
    tasks = [(group, tuple(city["districts"])) for group in groups]
    started = monotonic()
    scores, best = [], []
    context = ProcessPoolExecutor(max_workers=workers) if workers > 1 else nullcontext()
    with context as executor:
        results = executor.map(evaluate_group, tasks) if executor else map(evaluate_group, tasks)
        for index, (group_scores, group_best) in enumerate(results, start=1):
            scores.extend(group_scores)
            for rank, entry in enumerate(group_best):
                keep_best(best, entry, index * 5 + rank)
            if index % 100 == 0 or index == len(groups):
                print(f"Комбинации мер: {index}/{len(groups)}; допустимых сценариев: {len(scores)}; "
                      f"{monotonic() - started:.0f} с", flush=True)
    if not scores:
        raise ValueError("В датасете нет допустимых сценариев.")
    top_five = [item[2] for item in sorted(best, reverse=True)]
    return {
        "dataset_sha256": dataset_fingerprint(city),
        "valid_scenario_count": len(scores),
        "baseline_score": simulate([])["Score"],
        "best_scenario": top_five[0],
        "top_5": top_five,
        "quantile_step_percent": 0.1,
        "quantiles": distribution_quantiles(scores),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workers", type=int, default=4, help="Число процессов (по умолчанию 4)")
    parser.add_argument("--output", type=Path, default=ROOT / "data" / "scenario_stats.json")
    args = parser.parse_args()
    if args.workers < 1:
        parser.error("--workers должен быть положительным")
    stats = precompute(args.workers)
    payload = json.dumps(stats, ensure_ascii=False, separators=(",", ":")) + "\n"
    if len(payload.encode("utf-8")) > 1_000_000:
        raise ValueError("Статистика превышает ограничение 1 МБ.")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(".json.tmp")
    temporary.write_text(payload, encoding="utf-8")
    temporary.replace(args.output)
    print(f"Сохранено {args.output}: {len(stats['quantiles'])} квантилей; "
          f"лучший Score {stats['best_scenario']['Score']}; "
          f"{len(payload.encode('utf-8'))} байт", flush=True)


if __name__ == "__main__":
    main()
