from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Land or Water batch benchmark")
    parser.add_argument("command", choices=("serve", "run", "report"), nargs="?", default="serve")
    parser.add_argument("--tests", nargs="+", help="Test names from project.yaml")
    args = parser.parse_args()
    root = Path.cwd()
    project = load_config(root / "project.yaml")

    if args.command == "serve":
        from .ui import main as serve_ui

        serve_ui(project)
        return

    from .runner import run_tests
    from .stats import export_results, summarize
    from .store import Store

    names = args.tests if args.tests is not None else [test["name"] for test in project["tests"]]
    if args.command == "run":
        execution = asyncio.run(run_tests(root, project, names))
        print(json.dumps({"execution": execution}, ensure_ascii=False, indent=2))

    store = Store(root / project["paths"]["database"])
    for name in names:
        rows = store.results(name)
        files = export_results(rows, root / project["paths"]["exports"], name)
        print(json.dumps({"test": name, "summary": summarize(rows), "files": {key: str(path) for key, path in files.items()}}, ensure_ascii=False, indent=2))
    store.close()


if __name__ == "__main__":
    main()
