#!/usr/bin/env python3
"""Phase-3 capability sniff: one reasoning prompt + one code prompt per model.

Iterates every (provider, model) in providers.yaml that is not in
`disabled_models`, calls it directly via the bank's HTTP path (so we get
the same headers + state side effects as production), and records a
JSON record per (model, prompt) into `findings/capsniff/`.

Auto-judges:
  - REASONING: must mention "42" (answer to a known puzzle)
  - CODE:      must include a Python `def ` and `return`

Usage:
  python scripts/capsniff.py                   # all models
  python scripts/capsniff.py groq cerebras     # subset by provider name
  python scripts/capsniff.py --tier T1         # only T1 models
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Reuse bank for keys, yaml parsing, http client, header parsing.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bank import Bank, BankError, BankResponse  # noqa: E402

OUT_DIR = Path(__file__).resolve().parents[1] / "findings" / "capsniff"

PROMPTS = [
    {
        "name": "reasoning",
        "messages": [
            {
                "role": "user",
                "content": (
                    "There are 100 people. 30 wear hats. 40 wear scarves. "
                    "12 wear both. 42 wear neither. How many people total are wearing hats or scarves but not both? "
                    "Answer with just the number."
                ),
            }
        ],
        # 2048 so thinking models have room to derive 30+40-2*12=46
        "max_tokens": 2048,
        "judge": lambda text: text and "46" in text,
    },
    {
        "name": "code",
        "messages": [
            {
                "role": "user",
                "content": (
                    "Write a Python function `is_palindrome(s: str) -> bool` that returns True "
                    "if `s` is a palindrome ignoring case and non-alphanumeric chars. Code only, no explanation."
                ),
            }
        ],
        "max_tokens": 1024,
        "judge": lambda text: text and "def " in text and "return" in text,
    },
]


def safe_id(s: str) -> str:
    return s.replace("/", "__").replace(":", "_")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("providers", nargs="*", help="provider names to sniff; default = all")
    ap.add_argument("--tier", choices=["T1", "T2", "T3"], default=None)
    ap.add_argument(
        "--yaml",
        default=str(Path(__file__).resolve().parents[1] / "scripts" / "providers.yaml"),
    )
    args = ap.parse_args()

    bank = Bank.from_yaml(args.yaml)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ts = now_iso()
    rows: list[dict] = []
    try:
        for p in bank.providers.values():
            if args.providers and p.name not in args.providers:
                continue
            for m in p.models:
                if args.tier and m.tier != args.tier:
                    continue
                if bank.state.is_model_disabled(p.name, m.model_id):
                    continue
                for prompt in PROMPTS:
                    print(
                        f"[{p.name}/{m.model_id}] {prompt['name']}…",
                        end=" ",
                        flush=True,
                    )
                    rec = {
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "provider": p.name,
                        "model": m.model_id,
                        "tier": m.tier,
                        "prompt": prompt["name"],
                        "ok": False,
                        "judged": False,
                        "latency_ms": None,
                        "text": None,
                        "error": None,
                    }
                    last_err = None
                    for retry in range(3):
                        if retry:
                            # Clear the cooldown the previous attempt set, then
                            # back off briefly before re-pinning the same model.
                            state_key = "*" if p.shared_bucket else m.model_id
                            cur = bank.state.get_state(p.name, state_key)
                            cur.cooldown_until = 0.0
                            bank.state.set_state(cur)
                            time.sleep(15)
                        try:
                            resp: BankResponse = bank.chat(
                                messages=prompt["messages"],
                                tier=None,
                                prefer_models=[m.model_id],
                                avoid=[
                                    other.name
                                    for other in bank.providers.values()
                                    if other.name != p.name
                                ],
                                max_tokens=prompt["max_tokens"],
                                max_failovers=1,
                            )
                            rec["ok"] = True
                            rec["latency_ms"] = resp.latency_ms
                            rec["text"] = resp.text
                            rec["judged"] = bool(prompt["judge"](resp.text or ""))
                            verdict = "PASS" if rec["judged"] else "FAIL"
                            print(
                                f"{verdict} ({resp.latency_ms}ms)"
                                + (f" [retry {retry}]" if retry else "")
                            )
                            last_err = None
                            break
                        except (BankError, Exception) as e:
                            last_err = f"{type(e).__name__}: {e}"
                    if last_err:
                        rec["error"] = last_err
                        print(f"ERR {last_err}")
                    rows.append(rec)
                    fname = f"{safe_id(p.name)}__{safe_id(m.model_id)}__{prompt['name']}__{ts}.json"
                    (OUT_DIR / fname).write_text(json.dumps(rec, indent=2, default=str))
    finally:
        bank.close()

    # Summary
    print("\n=== capsniff summary ===")
    print(f"{'provider':<18} {'model':<55} {'tier':<4} {'reason':<6} {'code':<6}")
    by_model: dict[tuple[str, str], dict[str, str]] = {}
    for r in rows:
        k = (r["provider"], r["model"])
        if k not in by_model:
            by_model[k] = {"tier": r["tier"]}
        if r["error"]:
            by_model[k][r["prompt"]] = "ERR"
        elif r["judged"]:
            by_model[k][r["prompt"]] = "PASS"
        else:
            by_model[k][r["prompt"]] = "FAIL"
    for (prov, mid), v in by_model.items():
        print(
            f"{prov:<18} {mid:<55} {v['tier']:<4} "
            f"{v.get('reasoning','-'):<6} {v.get('code','-'):<6}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
