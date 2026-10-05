"""Official OpenAI SDK client construction and response extraction.

`chat` calls are answered with a `success/data` envelope; `response_envelope`
is read exactly as configured and the raw payload is passed through untouched.
`responses` calls are always streamed (`stream=True`): `call` aggregates the
`response.output_text.delta` text and keeps the terminal
`response.completed.response`, whose dump is the raw payload recorded for the
point. `response_envelope` applies to `chat` only.

`build_request` merges `test["request_parameters"]` rows into the final body:
each row is `{key: <dot path>, type: <string|number|boolean|json>, value: <YAML
native>}`. `type` is editor metadata and is not sent; `value` goes into the body
at `key`, where dots build nested objects (`reasoning.effort` -> `{"reasoning":
{"effort": ...}}`). Any row whose top-level key is a managed field
(`model`, `messages`, `input`, `stream`, `max_tokens`, `max_output_tokens`,
`temperature`) is ignored: those are fixed here and always win. The returned
dict is the exact body sent upstream; `call` passes unknown keys through the
SDK's `extra_body` so arbitrary JSON reaches the gateway.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from openai import AsyncOpenAI, DefaultAsyncHttpxClient


@dataclass
class Extraction:
    answer: str | None
    usage_json: str | None
    response_json: str
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None


@dataclass
class StreamedResponse:
    """Aggregated `responses` stream: text deltas plus the terminal payload.

    `model_dump` forwards to the terminal `response.completed.response`, so
    callers record the raw object exactly as the non-streamed route did.
    """

    text: str
    response: Any

    def model_dump(self) -> dict:
        return self.response.model_dump()


def build_client(test: dict) -> AsyncOpenAI:
    """Official async client: SDK retries disabled, transport from the test config."""
    return AsyncOpenAI(
        base_url=test["base_url"],
        api_key=test["api_key"],
        max_retries=0,
        timeout=test["timeout_seconds"],
        http_client=DefaultAsyncHttpxClient(
            trust_env=False,
            proxy=test["proxy"],
            timeout=test["timeout_seconds"],
        ),
    )


MANAGED_ROOT_KEYS = frozenset(
    {"model", "messages", "input", "stream", "max_tokens", "max_output_tokens", "temperature"}
)


def build_request(test: dict, prompt: str) -> dict:
    """Final request body: managed fields over the configured parameter rows.

    Managed fields (`model`, `messages`/`input`, `stream`, the budget key and
    `temperature`) always win; rows in `request_parameters` whose top-level key
    is managed are ignored. Returns the exact body to send, so callers can log
    it verbatim.
    """
    params = _extra_parameters(test)
    if test["protocol"] == "chat":
        params.update(
            {
                "model": test["model"],
                "messages": [{"role": "user", "content": prompt}],
            }
        )
        budget_key = "max_tokens"
    else:
        params.update({"model": test["model"], "input": prompt, "stream": True})
        budget_key = "max_output_tokens"
    if test["max_output_tokens"] is not None:
        params[budget_key] = test["max_output_tokens"]
    if test["temperature"] is not None:
        params["temperature"] = test["temperature"]
    return params


def _extra_parameters(test: dict) -> dict:
    """Configured parameter rows as body fragments: dot paths build nested dicts."""
    body: dict[str, Any] = {}
    for row in test["request_parameters"]:
        path = row["key"].split(".")
        if path[0] in MANAGED_ROOT_KEYS:
            continue
        node = body
        for part in path[:-1]:
            node = node.setdefault(part, {})
        node[path[-1]] = row["value"]
    return body


_DIRECT_KEYS = {"chat": ("model", "messages"), "responses": ("model", "input", "stream")}


def _sdk_arguments(protocol: str, params: dict) -> dict:
    """Split the final body: SDK-required fields as kwargs, everything else via extra_body."""
    direct = {key: params[key] for key in _DIRECT_KEYS[protocol]}
    extra = {key: value for key, value in params.items() if key not in direct}
    if extra:
        direct["extra_body"] = extra
    return direct


async def call(client: AsyncOpenAI, test: dict, params: dict) -> Any:
    arguments = _sdk_arguments(test["protocol"], params)
    if test["protocol"] == "chat":
        return await client.chat.completions.create(**arguments)
    chunks: list[str] = []
    terminal = None
    async with await client.responses.create(**arguments) as stream:
        async for event in stream:
            if event.type == "response.output_text.delta":
                chunks.append(event.delta)
            elif event.type == "response.completed":
                terminal = event.response
    return StreamedResponse("".join(chunks), terminal)


def extract(test: dict, response: Any) -> Extraction:
    """Pull answer text, reported usage and the raw dump out of a response.

    `chat` unwraps the configured envelope; `responses` receives the
    `StreamedResponse` whose dump is the terminal `response.completed.response`.
    """
    dump = response.model_dump()
    if test["protocol"] == "chat":
        root = dump["data"] if test["response_envelope"] == "success_data" else dump
        answer = root["choices"][0]["message"]["content"]
    else:
        root = dump
        answer = response.text
    response_json = json.dumps(dump)
    usage = root["usage"]
    usage_json = None if usage is None else json.dumps(usage)
    input_tokens, output_tokens, total_tokens = _token_counts(test["protocol"], usage)
    return Extraction(answer, usage_json, response_json, input_tokens, output_tokens, total_tokens)


def _token_counts(protocol: str, usage: dict | None) -> tuple[int | None, int | None, int | None]:
    if usage is None:
        return None, None, None
    if protocol == "chat":
        return usage["prompt_tokens"], usage["completion_tokens"], usage["total_tokens"]
    return usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]
