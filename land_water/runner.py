"""Batch runner: points -> truth -> concurrent SDK requests -> per-point SQLite rows."""
from __future__ import annotations

import asyncio
import copy
import json
import random
import threading
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from time import monotonic
from typing import Callable

from openai import APIError, APIStatusError

from . import protocol
from .dataset import generate_points
from .store import Store
from .truth import classify_points

WINDOW_SECONDS = 60.0


class _Limiter:
    """Per-test rolling 60s request/usage windows plus the in-flight semaphore."""

    def __init__(self, limits: dict) -> None:
        self._rpm: int = limits["rpm"]
        self._tpm: int | None = limits["tpm"]
        self._sends: deque[float] = deque()
        self._tokens: deque[tuple[float, int]] = deque()
        self.semaphore = asyncio.Semaphore(limits["max_in_flight"])

    def _trim(self, now: float) -> None:
        while self._sends and now - self._sends[0] >= WINDOW_SECONDS:
            self._sends.popleft()
        while self._tokens and now - self._tokens[0][0] >= WINDOW_SECONDS:
            self._tokens.popleft()

    async def wait_tpm(self) -> None:
        if self._tpm is None:
            return
        while True:
            now = monotonic()
            self._trim(now)
            if sum(tokens for _, tokens in self._tokens) < self._tpm:
                return
            await asyncio.sleep(WINDOW_SECONDS - (now - self._tokens[0][0]))

    async def claim_send(self) -> None:
        while True:
            now = monotonic()
            self._trim(now)
            if len(self._sends) < self._rpm:
                self._sends.append(now)
                return
            await asyncio.sleep(WINDOW_SECONDS - (now - self._sends[0]))

    def record_usage(self, total_tokens: int | None) -> None:
        if self._tpm is not None and total_tokens is not None:
            self._tokens.append((monotonic(), total_tokens))


def _retry_delay(test: dict, exc: APIError) -> float:
    """Configured delay, raised to the server's Retry-After when it is present."""
    delay = test["retry_delay_seconds"]
    if not isinstance(exc, APIStatusError):
        return delay
    value = exc.response.headers.get("retry-after")
    if value is None:
        return delay
    return max(delay, _parse_retry_after(value))


def _parse_retry_after(value: str) -> float:
    """Retry-After as seconds, or as an HTTP date counted from now."""
    try:
        return float(value)
    except ValueError:
        return (parsedate_to_datetime(value) - datetime.now(timezone.utc)).total_seconds()


async def run_tests(
    root: Path,
    project: dict,
    names: list[str],
    on_progress: Callable[[str, int, int], None] | None = None,
) -> dict:
    tests_by_name = {test["name"]: test for test in project["tests"]}
    tests = [tests_by_name[name] for name in names]
    store = Store(root / project["paths"]["database"])
    try:
        outcomes = await asyncio.gather(
            *(_run_test(test, root / project["paths"]["gshhg_archive"], store, on_progress) for test in tests)
        )
    finally:
        store.close()
    return dict(zip(names, outcomes))


async def _run_test(
    test: dict,
    archive: Path,
    store: Store,
    on_progress: Callable[[str, int, int], None] | None,
) -> dict:
    name = test["name"]
    points = generate_points(test)
    total = len(points)
    completed = store.completed_points(name)
    remaining = [point for point in points if point not in completed]
    skipped = total - len(remaining)
    if on_progress is not None:
        on_progress(name, skipped, total)
    if not remaining:
        return {"total": total, "skipped": skipped, "written": 0, "failed": 0}

    truths = await asyncio.to_thread(classify_points, archive, remaining)
    limiter = _Limiter(test["limits"])
    client = protocol.build_client(test)
    state = {"done": skipped, "failed": 0}

    async def worker(point: tuple[float, float], truth: str) -> None:
        row = await _request_point(test, limiter, client, point, truth)
        store.save_result(row)
        state["done"] += 1
        if row["status"] == "failed":
            state["failed"] += 1
        if on_progress is not None:
            on_progress(name, state["done"], total)

    try:
        await asyncio.gather(*(worker(point, truth) for point, truth in zip(remaining, truths)))
    finally:
        await client.close()
    return {"total": total, "skipped": skipped, "written": len(remaining), "failed": state["failed"]}


async def _request_point(
    test: dict,
    limiter: _Limiter,
    client,
    point: tuple[float, float],
    truth: str,
) -> dict:
    longitude, latitude = point
    prompt = test["prompt"].format(longitude=longitude, latitude=latitude)
    params = protocol.build_request(test, prompt)
    request_json = json.dumps(params, ensure_ascii=False)
    max_attempts = test["retries"] + 1
    attempts = 0
    latency = 0.0
    answer = None
    usage_json = None
    response_json = None
    input_tokens = None
    output_tokens = None
    total_tokens = None
    error = None
    status = "failed"

    while attempts < max_attempts:
        attempts += 1
        failed_now = False
        delay = 0.0
        async with limiter.semaphore:
            await limiter.wait_tpm()
            await limiter.claim_send()
            started = monotonic()
            try:
                response = await protocol.call(client, test, params)
            except APIError as exc:
                latency += monotonic() - started
                error = f"{type(exc).__name__}: {exc}"
                failed_now = True
                delay = _retry_delay(test, exc)
            else:
                latency += monotonic() - started
                extracted = protocol.extract(test, response)
                answer = extracted.answer
                usage_json = extracted.usage_json
                response_json = extracted.response_json
                input_tokens = extracted.input_tokens
                output_tokens = extracted.output_tokens
                total_tokens = extracted.total_tokens
                limiter.record_usage(total_tokens)
                text = (answer or "").strip().lower()
                status = "ok" if text in ("land", "water") else "invalid"
                error = None
        if not failed_now:
            break
        if attempts >= max_attempts:
            break
        await asyncio.sleep(delay)

    return {
        "test_name": test["name"],
        "model": test["model"],
        "longitude": longitude,
        "latitude": latitude,
        "truth": truth,
        "answer": answer,
        "status": status,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "usage_json": usage_json,
        "response_json": response_json,
        "request_json": request_json,
        "latency_seconds": latency,
        "attempts": attempts,
        "error": error,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


class RunControl:
    """One batch per object, run on a background thread.

    ``start`` returns at once; the panel polls ``snapshot`` and reads the
    SQLite-written counts itself. ``stop`` flips the state to ``stopping`` and
    cancels the in-flight async requests, leaving already-saved rows untouched.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state = "idle"
        self._names: list[str] = []
        self._total: dict[str, int] = {}
        self._skipped: dict[str, int] = {}
        self._started_at: str | None = None
        self._started: float | None = None
        self._elapsed = 0.0
        self._error: str | None = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None
        self._cancel_requested = threading.Event()

    def start(self, root: Path, project: dict, names: list[str]) -> None:
        self._root = Path(root)
        self._project = copy.deepcopy(project)
        self._names = list(names)
        self._total = {}
        self._skipped = {}
        self._elapsed = 0.0
        self._error = None
        self._cancel_requested.clear()
        self._started_at = datetime.now(timezone.utc).isoformat()
        self._started = monotonic()
        self._state = "running"
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._lock:
            if self._state not in ("running", "stopping"):
                return
            self._state = "stopping"
        self._cancel_requested.set()
        if self._loop is not None and self._task is not None:
            self._loop.call_soon_threadsafe(self._task.cancel)

    def snapshot(self) -> dict:
        with self._lock:
            elapsed = (
                monotonic() - self._started
                if self._state in ("running", "stopping") and self._started is not None
                else self._elapsed
            )
            return {
                "state": self._state,
                "names": list(self._names),
                "total": dict(self._total),
                "skipped": dict(self._skipped),
                "started_at": self._started_at,
                "elapsed_seconds": elapsed,
                "error": self._error,
            }

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        completed = False
        try:
            self._task = loop.create_task(self._execute())
            if self._cancel_requested.is_set():
                self._task.cancel()
            loop.run_until_complete(self._task)
            completed = True
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            with self._lock:
                self._state = "failed"
                self._error = f"{type(exc).__name__}: {exc}"
        finally:
            with self._lock:
                if self._state == "running":
                    self._state = "completed" if completed else "stopped"
                elif self._state == "stopping":
                    self._state = "stopped"
                self._elapsed = monotonic() - self._started
            asyncio.set_event_loop(None)
            loop.close()

    async def _execute(self) -> None:
        self._load_totals()
        await run_tests(self._root, self._project, self._names)

    def _load_totals(self) -> None:
        store = Store(self._root / self._project["paths"]["database"])
        try:
            for test in self._project["tests"]:
                name = test["name"]
                if name not in self._names:
                    continue
                with self._lock:
                    self._total[name] = len(generate_points(test))
                    self._skipped[name] = len(store.completed_points(name))
        finally:
            store.close()


def _error_response_text(exc: APIError) -> str | None:
    """Raw HTTP body of a failed call, or None when the SDK got no response."""
    if isinstance(exc, APIStatusError):
        return exc.response.text
    return None


async def test_connection(test: dict) -> dict:
    """One real SDK call on a random point: no retry, no store, no truth.

    ``usable`` is True only when the final answer text is Land or Water, the
    benchmark's accepted outputs; every other shape is reported with the raw
    request/response so the panel can show it verbatim.
    """
    points = generate_points(test)
    longitude, latitude = random.choice(points)
    prompt = test["prompt"].format(longitude=longitude, latitude=latitude)
    params = protocol.build_request(test, prompt)
    request_json = json.dumps(params, ensure_ascii=False)
    client = protocol.build_client(test)
    started = monotonic()
    try:
        try:
            response = await protocol.call(client, test, params)
        except APIError as exc:
            return {
                "usable": False,
                "request_json": request_json,
                "response_json": _error_response_text(exc),
                "answer": None,
                "error": f"{type(exc).__name__}: {exc}",
                "latency_seconds": monotonic() - started,
                "longitude": longitude,
                "latitude": latitude,
            }
    finally:
        await client.close()

    latency = monotonic() - started
    try:
        extracted = protocol.extract(test, response)
    except (KeyError, IndexError, TypeError) as exc:
        return {
            "usable": False,
            "request_json": request_json,
            "response_json": json.dumps(response.model_dump()),
            "answer": None,
            "error": f"{type(exc).__name__}: {exc}",
            "latency_seconds": latency,
            "longitude": longitude,
            "latitude": latitude,
        }
    answer = extracted.answer
    return {
        "usable": (answer or "").strip().lower() in ("land", "water"),
        "request_json": request_json,
        "response_json": extracted.response_json,
        "answer": answer,
        "error": None,
        "latency_seconds": latency,
        "longitude": longitude,
        "latitude": latitude,
    }
