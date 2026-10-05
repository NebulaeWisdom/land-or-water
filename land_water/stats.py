"""Summary statistics and result exports for the Land-or-Water benchmark.

``summarize`` aggregates stored result rows, ``make_map`` renders the world map
(plain longitude/latitude axes, no map tiles) and ``export_results`` writes the
CSV/JSON/HTML bundle with an offline interactive figure.
"""

import csv
import html as html_module
import json
from pathlib import Path

import numpy as np
import plotly.graph_objects as go

RESULT_COLUMNS = [
    "test_name",
    "model",
    "longitude",
    "latitude",
    "truth",
    "answer",
    "status",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "usage_json",
    "response_json",
    "request_json",
    "latency_seconds",
    "attempts",
    "error",
    "created_at",
]

CATEGORY_ORDER = ("land", "water", "invalid", "failed")
CATEGORY_COLORS = {
    "land": "#2ca02c",
    "water": "#1f77b4",
    "invalid": "#ff7f0e",
    "failed": "#d62728",
}

# Raw usage subitems (chat and responses shapes); shown alongside, never added
# into the request totals.
REASONING_PATHS = (
    ("completion_tokens_details", "reasoning_tokens"),
    ("output_tokens_details", "reasoning_tokens"),
)
CACHED_PATHS = (
    ("prompt_tokens_details", "cached_tokens"),
    ("input_tokens_details", "cached_tokens"),
)

_HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
</head>
<body>
<h1>{title}</h1>
<h2>Summary</h2>
<pre>{summary}</pre>
{map}
</body>
</html>
"""


def _normalized(text: str) -> str:
    return text.strip().lower()


def _category(row: dict) -> str:
    if row["status"] == "ok":
        return _normalized(row["answer"])
    return row["status"]


def _usage(row: dict) -> dict | None:
    raw = row["usage_json"]
    return None if raw is None else json.loads(raw)


def _subitem(usage: dict, paths) -> int | None:
    for outer, inner in paths:
        details = usage.get(outer)
        if details is not None and details.get(inner) is not None:
            return details[inner]
    return None


def _known_sum(values) -> int | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def summarize(rows: list[dict]) -> dict:
    """Aggregate result rows into the displayed statistics."""
    total = len(rows)
    valid = sum(1 for row in rows if row["status"] == "ok")
    invalid = sum(1 for row in rows if row["status"] == "invalid")
    failed = sum(1 for row in rows if row["status"] == "failed")
    correct = sum(
        1
        for row in rows
        if row["status"] == "ok"
        and _normalized(row["answer"]) == _normalized(row["truth"])
    )
    usages = [usage for usage in (_usage(row) for row in rows) if usage is not None]
    latencies = [
        row["latency_seconds"] for row in rows if row["latency_seconds"] is not None
    ]
    output_latency = sum(
        row["latency_seconds"]
        for row in rows
        if row["output_tokens"] is not None and row["latency_seconds"] is not None
    )
    known_output = sum(
        row["output_tokens"] for row in rows if row["output_tokens"] is not None
    )
    return {
        "total": total,
        "valid": valid,
        "invalid": invalid,
        "failed": failed,
        "correct": correct,
        "accuracy": correct / total if total else 0.0,
        "input_tokens": _known_sum(row["input_tokens"] for row in rows),
        "output_tokens": _known_sum(row["output_tokens"] for row in rows),
        "total_tokens": _known_sum(row["total_tokens"] for row in rows),
        "usage_count": len(usages),
        "reasoning_tokens": _known_sum(_subitem(usage, REASONING_PATHS) for usage in usages),
        "cached_tokens": _known_sum(_subitem(usage, CACHED_PATHS) for usage in usages),
        "tps": known_output / output_latency if output_latency else None,
        "mean_latency_seconds": float(np.mean(latencies)) if latencies else None,
        "p50_latency_seconds": float(np.percentile(latencies, 50)) if latencies else None,
        "p95_latency_seconds": float(np.percentile(latencies, 95)) if latencies else None,
    }


def make_map(rows: list[dict], title: str) -> go.Figure:
    """World map of the stored points, coloured by land/water/invalid/failed."""
    figure = go.Figure()
    for category in CATEGORY_ORDER:
        selected = [row for row in rows if _category(row) == category]
        figure.add_trace(
            go.Scattergl(
                x=[row["longitude"] for row in selected],
                y=[row["latitude"] for row in selected],
                mode="markers",
                name=category,
                marker={"size": 4, "color": CATEGORY_COLORS[category]},
                text=[
                    f"lon={row['longitude']}, lat={row['latitude']}, "
                    f"truth={row['truth']}, answer={row['answer']}, status={row['status']}"
                    for row in selected
                ],
                hoverinfo="text",
            )
        )
    figure.update_layout(
        title=title,
        xaxis={"title": "Longitude", "range": [-180, 180]},
        yaxis={"title": "Latitude", "range": [-90, 90]},
        width=1000,
        height=540,
    )
    return figure


def export_results(rows: list[dict], directory: Path, name: str) -> dict[str, Path]:
    """Write ``name``.csv/.json/.html under ``directory`` and return their paths."""
    summary = summarize(rows)
    model = rows[0]["model"] if rows else ""
    title = f"{name} ({model})" if model else name
    directory.mkdir(parents=True, exist_ok=True)
    csv_path = directory / f"{name}.csv"
    json_path = directory / f"{name}.json"
    html_path = directory / f"{name}.html"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    json_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    page = _HTML_PAGE.format(
        title=html_module.escape(title),
        summary=html_module.escape(json.dumps(summary, ensure_ascii=False, indent=2)),
        map=make_map(rows, title).to_html(full_html=False, include_plotlyjs=True),
    )
    html_path.write_text(page, encoding="utf-8")
    return {"csv": csv_path, "json": json_path, "html": html_path}
