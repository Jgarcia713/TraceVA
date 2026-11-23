"""Model tool-calling smoke test (pre-M2; seed of model-comparison tooling, brief §10E).

Sends one Adult question with the real tool schemas to a local Ollama server and reports
tool-call validity, reasoning leakage, token counts, and latency, for both the native
API and the OpenAI-compatible endpoint, with thinking on and off.

Not part of the test suite: requires a running Ollama server and a registered 'adult'
source (`python scripts/register_datasets.py adult`).

Usage (from backend/):
    python scripts/model_smoke.py --model qwen3.5:4b-q4_K_M
    python scripts/model_smoke.py --model qwen3.5:4b-q4_K_M --mode openai --repeat 3

Limitations: one question, temperature 0, no tool execution. Repeats at temperature 0
are near-identical and are not independent samples.
"""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from sqlalchemy import select

from app.analysis.registry import root_view_id
from app.config import get_settings
from app.db.models import DatasetSource
from app.db.session import create_app_engine, create_session_factory
from app.schemas.tools import InspectDatasetInput, SummarizeInput

QUESTION = "On average, how many hours per week do men and women work? Compare them."


def tool(name: str, description: str, model: type[BaseModel]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": model.model_json_schema(),
        },
    }


TOOLS = [
    tool(
        "inspect_dataset", "Schema, missingness and value summary of a source.", InspectDatasetInput
    ),
    tool("summarize", "Grouped descriptive statistics over a view.", SummarizeInput),
]


def lookup_ids() -> tuple[str, str]:
    settings = get_settings()
    engine = create_app_engine(settings.database_url)
    try:
        with create_session_factory(engine)() as session:
            source = session.scalars(
                select(DatasetSource).where(DatasetSource.name == "adult")
            ).first()
            if source is None:
                raise SystemExit("no 'adult' source registered; run scripts/register_datasets.py")
            return source.id, root_view_id(session, source.id)
    finally:
        engine.dispose()


def build_messages(source_id: str, view_id: str) -> list[dict[str, Any]]:
    system = (
        "You are a data-analysis agent. Answer questions only by calling the provided "
        "tools; never invent numbers. Dataset 'adult' (UCI Adult Income), "
        f"source_id={source_id}, root view_id={view_id}. Columns: numeric: age, fnlwgt, "
        "education_num, capital_gain, capital_loss, hours_per_week; categorical: "
        "workclass, education, marital_status, occupation, relationship, race, sex, "
        "native_country, income."
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": QUESTION}]


def check_call(name: str, args: Any, view_id: str) -> str:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError as exc:
            return f"INVALID JSON: {exc}"
    if name != "summarize":
        return f"other tool {name}: {args}"
    try:
        parsed = SummarizeInput.model_validate(args)
    except ValidationError as exc:
        return f"SCHEMA-INVALID ({exc.error_count()} errors): {args}"
    correct = (
        parsed.view_id == view_id
        and "hours_per_week" in parsed.columns
        and parsed.group_by == ["sex"]
        and "mean" in parsed.metrics
    )
    verdict = "VALID+CORRECT" if correct else "VALID-but-off-target"
    return f"{verdict}: {parsed.model_dump(mode='json')}"


def report_calls(tool_calls: list[dict[str, Any]] | None, view_id: str) -> None:
    if not tool_calls:
        print("  NO TOOL CALL")
    for tc in tool_calls or []:
        print(
            "  tool_call:", check_call(tc["function"]["name"], tc["function"]["arguments"], view_id)
        )


def run_native(base: str, model: str, messages: list, view_id: str, think: bool | None) -> None:
    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "tools": TOOLS,
        "stream": False,
        "options": {"temperature": 0},
    }
    if think is not None:
        body["think"] = think
    t0 = time.perf_counter()
    response = httpx.post(f"{base}/api/chat", json=body, timeout=900)
    elapsed = time.perf_counter() - t0
    response.raise_for_status()
    data = response.json()
    msg = data["message"]
    pe, ec = data.get("prompt_eval_count") or 0, data.get("eval_count") or 0
    ped = (data.get("prompt_eval_duration") or 0) / 1e9
    ed = (data.get("eval_duration") or 0) / 1e9
    print(f"\n[native think={think}] {elapsed:.1f}s")
    print(
        f"  prompt: {pe} tok in {ped:.1f}s; generated: {ec} tok in {ed:.1f}s "
        f"({ec / ed if ed else 0:.1f} tok/s)"
    )
    print(f"  thinking chars={len(msg.get('thinking') or '')}")
    report_calls(msg.get("tool_calls"), view_id)


def run_openai(base: str, model: str, messages: list, view_id: str, extra: dict[str, Any]) -> None:
    body = {"model": model, "messages": messages, "tools": TOOLS, "temperature": 0, **extra}
    t0 = time.perf_counter()
    response = httpx.post(f"{base}/v1/chat/completions", json=body, timeout=900)
    elapsed = time.perf_counter() - t0
    response.raise_for_status()
    data = response.json()
    msg = data["choices"][0]["message"]
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    print(f"\n[openai-compat {extra or 'default'}] {elapsed:.1f}s usage={data.get('usage')}")
    print(f"  reasoning chars={len(reasoning)}")
    report_calls(msg.get("tool_calls"), view_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Local model tool-calling smoke test")
    parser.add_argument("--model", default="qwen3.5:4b-q4_K_M")
    parser.add_argument("--base-url", default="http://localhost:11434")
    parser.add_argument("--mode", choices=["all", "native", "openai"], default="all")
    parser.add_argument("--repeat", type=int, default=1)
    args = parser.parse_args()

    source_id, view_id = lookup_ids()
    messages = build_messages(source_id, view_id)
    version = httpx.get(f"{args.base_url}/api/version").json()
    print(f"ollama {version} model={args.model} tool_schema_chars={len(json.dumps(TOOLS))}")

    # Warm-up so timings exclude model load.
    httpx.post(
        f"{args.base_url}/api/chat",
        json={
            "model": args.model,
            "messages": [{"role": "user", "content": "hi"}],
            "stream": False,
            "think": False,
            "options": {"num_predict": 1},
        },
        timeout=900,
    )
    for _ in range(args.repeat):
        if args.mode in ("all", "native"):
            run_native(args.base_url, args.model, messages, view_id, think=False)
            run_native(args.base_url, args.model, messages, view_id, think=None)
        if args.mode in ("all", "openai"):
            run_openai(args.base_url, args.model, messages, view_id, {})
            run_openai(args.base_url, args.model, messages, view_id, {"reasoning_effort": "none"})


if __name__ == "__main__":
    main()
