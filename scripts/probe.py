#!/usr/bin/env python3
"""Phase-1 connectivity + header probe for free-tier LLM providers.

Reads providers.yaml + .env, sends a minimal chat-completion to each
(provider, model) pair where the API key is present, captures the full
response (status, latency, headers, body, request_id), optionally hits the
provider's quota endpoint, and writes one JSON record per pair to
../findings/<provider>__<model_safe>__<ts>.json.

Usage:
    python probe.py                  # probe every provider with a key
    python probe.py cerebras groq    # probe only listed providers
    python probe.py --skip-quota     # skip quota-endpoint GET
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml
from dotenv import load_dotenv

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_DIR = SCRIPT_DIR.parent
PROVIDERS_YAML = SCRIPT_DIR / "providers.yaml"
ENV_FILE = SCRIPT_DIR / ".env"
FINDINGS_DIR = REPO_DIR / "findings"

PING_BODY = {
    "messages": [{"role": "user", "content": "reply with: pong"}],
    "max_tokens": 5,
    "temperature": 0,
}

REQUEST_ID_HEADERS = (
    "x-request-id",
    "x-requestid",
    "openai-request-id",
    "request-id",
    "x-amzn-requestid",
    "cf-ray",
)


def safe_model_id(model_id: str) -> str:
    return model_id.replace("/", "__").replace(":", "_")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def load_providers() -> list[dict]:
    with PROVIDERS_YAML.open() as f:
        return yaml.safe_load(f)


def extract_request_id(headers: dict) -> str | None:
    lower = {k.lower(): v for k, v in headers.items()}
    for h in REQUEST_ID_HEADERS:
        if h in lower:
            return lower[h]
    return None


def hit_quota_endpoint(client: httpx.Client, provider: dict, key: str) -> dict | None:
    qep = provider.get("quota_endpoint")
    if not qep:
        return None
    url = provider["base_url"].rstrip("/") + qep
    try:
        t0 = time.perf_counter()
        r = client.get(url, headers={"Authorization": f"Bearer {key}"}, timeout=15.0)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        try:
            body = r.json()
        except Exception:
            body = r.text
        return {
            "url": url,
            "status": r.status_code,
            "latency_ms": latency_ms,
            "headers": dict(r.headers),
            "body": body,
        }
    except Exception as e:
        return {"url": url, "error": f"{type(e).__name__}: {e}"}


def probe_pair(
    client: httpx.Client,
    provider: dict,
    model_id: str,
    key: str,
    timeout_s: float,
    retries: int,
) -> dict:
    url = provider["base_url"].rstrip("/") + "/chat/completions"
    payload = {"model": model_id, **PING_BODY}
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    record: dict = {
        "provider": provider["name"],
        "model": model_id,
        "ts": datetime.now(timezone.utc).isoformat(),
        "url": url,
        "attempts": 0,
    }
    last_err = None
    for attempt in range(retries + 1):
        record["attempts"] = attempt + 1
        t0 = time.perf_counter()
        try:
            r = client.post(url, json=payload, headers=headers, timeout=timeout_s)
            record["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            record["status"] = r.status_code
            record["headers"] = dict(r.headers)
            record["request_id"] = extract_request_id(record["headers"])
            try:
                record["body"] = r.json()
            except Exception:
                record["body"] = r.text
            try:
                record["response_text"] = (
                    record["body"]["choices"][0]["message"]["content"]  # type: ignore[index]
                )
            except Exception:
                record["response_text"] = None
            record["error"] = None
            return record
        except (httpx.ReadTimeout, httpx.ConnectTimeout, httpx.RemoteProtocolError) as e:
            record["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            last_err = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(2)
                continue
        except Exception as e:
            record["latency_ms"] = int((time.perf_counter() - t0) * 1000)
            last_err = f"{type(e).__name__}: {e}"
            break
    record["status"] = None
    record["headers"] = {}
    record["request_id"] = None
    record["body"] = None
    record["response_text"] = None
    record["error"] = last_err
    return record


def model_id_of(m) -> str:
    return m if isinstance(m, str) else m["id"]


def write_record(record: dict, ts: str) -> Path:
    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{record['provider']}__{safe_model_id(record['model'])}__{ts}.json"
    path = FINDINGS_DIR / fname
    with path.open("w") as f:
        json.dump(record, f, indent=2, default=str)
    return path


def run(selected: list[str], skip_quota: bool, timeout_override: float | None, retries: int) -> int:
    load_dotenv(ENV_FILE)
    providers = load_providers()
    if selected:
        providers = [p for p in providers if p["name"] in selected]
        if not providers:
            print(f"No providers matched {selected!r}", file=sys.stderr)
            return 2

    ts = now_iso()
    summary: list[tuple[str, str, str | int]] = []
    with httpx.Client() as client:
        for provider in providers:
            name = provider["name"]
            key_env = provider["key_env"]
            key = os.getenv(key_env)
            if not key:
                print(f"[skip] {name}: env var {key_env} not set")
                summary.append((name, "-", "no-key"))
                continue

            timeout_s = timeout_override if timeout_override is not None else float(
                provider.get("request_timeout", 60)
            )

            quota = None
            if not skip_quota:
                quota = hit_quota_endpoint(client, provider, key)
                if quota is not None:
                    qpath = FINDINGS_DIR / f"{name}__quota__{ts}.json"
                    FINDINGS_DIR.mkdir(parents=True, exist_ok=True)
                    with qpath.open("w") as f:
                        json.dump(quota, f, indent=2, default=str)
                    print(
                        f"[quota] {name}: {quota.get('status', quota.get('error'))} -> {qpath.name}"
                    )

            for m in provider.get("models", []):
                model_id = model_id_of(m)
                rec = probe_pair(client, provider, model_id, key, timeout_s, retries)
                if quota is not None:
                    rec["quota_endpoint"] = {
                        "url": quota.get("url"),
                        "status": quota.get("status"),
                    }
                path = write_record(rec, ts)
                status = rec["status"] if rec["error"] is None else rec["error"]
                print(f"[{name}] {model_id}: {status} ({rec['latency_ms']}ms, attempts={rec['attempts']}) -> {path.name}")
                summary.append((name, model_id, status))

    print("\n=== summary ===")
    for row in summary:
        print(f"  {row[0]:<20} {row[1]:<50} {row[2]}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("providers", nargs="*", help="provider names to probe; default = all")
    parser.add_argument("--skip-quota", action="store_true", help="skip quota-endpoint GETs")
    parser.add_argument("--timeout", type=float, default=None, help="override per-call timeout (seconds)")
    parser.add_argument("--retries", type=int, default=0, help="retry timeouts/transient errors N times")
    args = parser.parse_args()
    return run(args.providers, args.skip_quota, args.timeout, args.retries)


if __name__ == "__main__":
    sys.exit(main())
