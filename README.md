[English](README.md) · [中文](README.zh.md)

# Land or Water?

An LLM benchmark: for each point of a configurable lat/lon grid, ask a model
whether that point is on land or in water, then score it against the GSHHG
coastline database. Ships a NiceGUI workbench, a CLI runner, and a SQLite
result store with token/latency statistics and CSV/JSON/HTML export.
Inspired by Twitter.

## Requirements

- Python >= 3.11.
- A reachable OpenAI-compatible gateway (the benchmark calls it over HTTP).

## Setup

```bat
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy project.yaml.sample project.yaml
```

POSIX shells: `python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && cp project.yaml.sample project.yaml`.

Edit `project.yaml` and set the gateway `base_url`, `api_key` and `model`.
`project.yaml` is git-ignored (it carries your API key); only
`project.yaml.sample` is committed.

Run every command from the repository root (the directory holding
`project.yaml`): the CLI and the panel resolve `project.yaml` and `assets/`
from the current working directory. Nothing is installed as a package —
`land_water/` is imported straight from the repository root.

## Ground-truth data

The five GSHHG layers used as ground truth are committed as a trimmed archive
at `assets/gshhg-land-water-2.3.7.zip` (about 71 MB), so a normal clone needs
no extra download; `paths.gshhg_archive` points at it. The archive keeps the
complete `.shp`/`.shx`/`.dbf`/`.prj` files plus the upstream notices — no
simplification or raster approximation — so ground-truth semantics are
unchanged.

Regenerating the archive from the original GSHHG 2.3.7 distribution is
optional: download <https://github.com/GenericMappingTools/gshhg-gmt/releases/download/2.3.7/gshhg-shp-2.3.7.zip>
to the repository root as `paths.gshhg_source_archive` (`gshhg-shp-2.3.7.zip`),
then run

```bat
.venv\Scripts\python.exe -m tools.build_truth_archive
```

GSHHG is licensed LGPL-3.0-or-later; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md) and `licenses/gshhg/`.

Fidelity, verified with the real `classify_points` (original archive vs the
committed trimmed archive):

| point set | points | divergent labels |
| --- | ---: | ---: |
| stored 2° grid | 16,200 | 0 |
| full 1° grid | 64,800 | 0 |
| polygon-boundary vertices | 1,491 | 0 |

All 25 copied upstream members are byte-identical (archive 149,157,845 B →
74,398,375 B, 49.9%), so with the classifier unchanged the labels agree for
any grid, not just the tested point sets.

## Configuration (`project.yaml`)

- `paths.database` — SQLite result file (relative to the repo root).
- `paths.gshhg_archive` — the committed trimmed GSHHG archive used at runtime
  (`assets/gshhg-land-water-2.3.7.zip`).
- `paths.gshhg_source_archive` — the original GSHHG 2.3.7 zip, used only by
  `tools/build_truth_archive.py` to rebuild `paths.gshhg_archive`.
- `paths.exports` — directory the panel/CLI write exports to.
- `ui.language` / `ui.languages` — interface language (`zh` or `en`).
- `ui.host` / `ui.port` / `ui.refresh_seconds` — panel address and
  live-refresh interval.
- `ui.map.history_step_degrees` — grid size for drawing a result set whose
  test config has been deleted; `ui.map.colors` — legend colours;
  `ui.map.export_width` — raster width of exported map figures.
- `ui.new_test` — template the panel copies for "新增配置" (new config).
- `tests` — one entry per benchmark, each with:
  - `name` — unique test name (keys the stored results).
  - `base_url`, `api_key`, `model` — gateway endpoint and model id.
  - `protocol` — `chat` (`/chat/completions`) or `responses` (`/responses`,
    always streamed).
  - `response_envelope` — `chat` only: `success_data` reads
    `choices[0].message.content`, `openai` uses the raw response text.
  - `request_parameters` — extra request parameters sent verbatim; a list of
    `{key, type, value}` rows with `type` in `string`/`number`/`boolean`/`json`
    and `value` in the matching YAML scalar type. Dotted keys expand to nested
    objects (e.g. `reasoning.effort` for `responses`, `reasoning_effort` for
    `chat`). An empty list sends nothing. Schema-managed fields (`model`,
    `messages`, `input`, `stream`, `max_tokens`, `max_output_tokens`,
    `temperature`, …) cannot be overridden here.
  - `proxy` — HTTP(S) proxy for the gateway, or `null` for none.
  - `limits.rpm` / `limits.max_in_flight` / `limits.tpm` — rolling 60 s request
    and token limits, plus max concurrent in-flight requests (`tpm: null`
    disables the token limit).
  - `retries`, `retry_delay_seconds`, `timeout_seconds` — failure handling;
    the server's `Retry-After` raises the delay when present.
  - `max_output_tokens`, `temperature` — sampling; `null` omits them.
  - `prompt` — prompt template, formatted with `{latitude}` and `{longitude}`.
  - `step_degrees` — grid cell size; points are cell centres.
  - `point_count` — number of grid points to sample (`null` = the whole grid).
  - `seed` — reproducible point selection and execution order.

## Running

Panel (recommended; also via the `start.cmd` shortcut):

```bat
.venv\Scripts\python.exe -m land_water serve
```

Open <http://127.0.0.1:8501/>. Tick the configs to run, start, and watch live
progress; the panel also lists stored results with maps and exports.

CLI:

```bat
.venv\Scripts\python.exe -m land_water run --tests example
.venv\Scripts\python.exe -m land_water report --tests example
```

`run` executes the named tests (omit `--tests` for all); `report` prints a
summary and writes exports for the named tests.

## Reading the results

Each row in `paths.database` is one grid point: `truth` (GSHHG label),
`answer` (model text), `status`, token counts, latency, attempts, and the raw
`request_json`/`response_json`.

- `status` — `ok` when the trimmed, lower-cased answer is exactly `land` or
  `water`; `invalid` when the call succeeded but the answer is something else;
  `failed` when the call errored after all retries.
- `truth` — the deepest GSHHG layer covering the point: L1 land, L2 lake
  water, L3 island-in-lake land, L4 pond-in-island-in-lake water, L5
  (Antarctic ice sheet) land.
- Reruns resume: points already stored for a test name are skipped, so a run
  can be stopped and continued.
- Summary counts ok/invalid/failed, accuracy over `ok` rows, token totals
  (including reasoning/cached token details when the gateway reports them)
  and average latency. The panel draws a world map of the stored points and
  can export CSV, JSON and HTML.

## Licenses

- Project source code: MIT, see [`LICENSE`](LICENSE).
- GSHHG 2.3.7 data: LGPL-3.0-or-later; obligations for redistributed or
  derived data are documented in
  [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md), with the license texts
  under `licenses/gshhg/`.
