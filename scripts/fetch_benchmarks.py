"""
Fetch benchmark scores from HuggingFace Hub for models in providers.yaml.
Writes results to scripts/benchmarks.yaml (keyed by canonical HF model ID).

Usage:
    python3 scripts/fetch_benchmarks.py

Requires: requests, pyyaml (already in requirements.txt)
"""

import sys
import time
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).parent

CANONICAL_HF_IDS: dict[str, str | None] = {
    "llama3.1-8b":                            "meta-llama/Llama-3.1-8B-Instruct",
    "llama-3.3-70b-versatile":                "meta-llama/Llama-3.3-70B-Instruct",
    "llama-3.1-8b-instant":                   "meta-llama/Llama-3.1-8B-Instruct",
    "Meta-Llama-3.3-70B-Instruct":            "meta-llama/Llama-3.3-70B-Instruct",
    "Llama-4-Maverick-17B-128E-Instruct":     "meta-llama/Llama-4-Maverick-17B-128E-Instruct",
    "meta-llama/llama-4-scout-17b-16e-instruct": "meta-llama/Llama-4-Scout-17B-16E-Instruct",
    "meta/llama-3.3-70b-instruct":            "meta-llama/Llama-3.3-70B-Instruct",
    "DeepSeek-V3.2":                          "deepseek-ai/DeepSeek-V3-0324",
    "DeepSeek-V3.1":                          "deepseek-ai/DeepSeek-V3",
    "deepseek-ai/DeepSeek-V3.2":              "deepseek-ai/DeepSeek-V3-0324",
    "qwen-3-235b-a22b-instruct-2507":         "Qwen/Qwen3-235B-A22B-Instruct-2507",
    "qwen/qwen3-32b":                         "Qwen/Qwen3-32B",
    "Qwen/Qwen3-Next-80B-A3B-Instruct":       "Qwen/Qwen3-30B-A3B",
    "qwen/qwen3-next-80b-a3b-instruct":       "Qwen/Qwen3-30B-A3B",
    "gemma-3-12b-it":                         "google/gemma-3-12b-it",
    "moonshotai/kimi-k2-instruct":            "moonshotai/Kimi-K2-Instruct",
    "moonshotai/kimi-k2-thinking":            "moonshotai/Kimi-K2-Thinking",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5": "nvidia/Llama-3.3-Nemotron-Super-49B-v1",
    "z-ai/glm5":                              "THUDM/GLM-4-9B-Chat",
    "z-ai/glm-4.5-air:free":                  None,
    "gemini-2.5-flash":                       None,
    "gemini-2.5-flash-lite":                  None,
    "gpt-oss-120b":                           None,
    "openai/gpt-oss-120b":                    None,
    "openai/gpt-oss-120b:free":               None,
    "openai/gpt-oss-20b":                     None,
    "openai/gpt-oss-20b:free":                None,
    "deepseek-ai/deepseek-v4-pro":            None,
    "deepseek-ai/deepseek-v4-flash":          None,
    "minimax/minimax-m2.5:free":              None,
    "nvidia/nemotron-3-super-120b-a12b:free": None,
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": None,
}

DATASET_NAME_NORMALISE = {
    "mmlu": "mmlu",
    "massive multitask language understanding": "mmlu",
    "hellaswag": "hellaswag",
    "arc": "arc",
    "ai2 reasoning challenge": "arc",
    "truthfulqa": "truthfulqa",
    "humaneval": "humaneval",
    "mbpp": "mbpp",
    "math": "math",
    "gsm8k": "gsm8k",
    "gpqa": "gpqa",
    "bbh": "bbh",
    "big-bench hard": "bbh",
    "ifeval": "ifeval",
    "aime": "aime",
    "musr": "musr",
    "mmlu-pro": "mmlu_pro",
}


def normalise_name(raw: str) -> str:
    lower = raw.lower().strip()
    for k, v in DATASET_NAME_NORMALISE.items():
        if k in lower:
            return v
    return lower.replace(" ", "_").replace("-", "_")[:32]


def fetch_hf_benchmarks(hf_id: str) -> dict:
    url = f"https://huggingface.co/api/models/{hf_id}?full=true"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            return {}
        data = r.json()
    except Exception:
        return {}

    benchmarks: dict[str, float] = {}

    # Primary: structured eval_results from model card model-index block
    for result in data.get("eval_results") or []:
        dataset = result.get("dataset", {}).get("name", "")
        for metric in result.get("metrics") or []:
            val = metric.get("value")
            if val is None:
                continue
            try:
                fval = float(val)
            except (TypeError, ValueError):
                continue
            # Normalise 0-1 scores to 0-100
            if fval <= 1.0:
                fval = round(fval * 100, 1)
            else:
                fval = round(fval, 1)
            key = normalise_name(dataset or metric.get("name", "unknown"))
            if key and key not in benchmarks:
                benchmarks[key] = fval

    return benchmarks


def collect_hf_ids(providers_yaml: Path) -> dict[str, str]:
    """Returns {canonical_hf_id: representative_model_id} for all models."""
    with providers_yaml.open() as f:
        raw = yaml.safe_load(f)

    seen: dict[str, str] = {}
    for p in raw:
        for m in p.get("models", []):
            model_id = m if isinstance(m, str) else m["id"]
            hf_id = CANONICAL_HF_IDS.get(model_id, model_id)
            if hf_id and hf_id not in seen:
                seen[hf_id] = model_id
    return seen


def main():
    providers_yaml = HERE / "providers.yaml"
    out_path = HERE / "benchmarks.yaml"

    hf_ids = collect_hf_ids(providers_yaml)
    print(f"Fetching benchmarks for {len(hf_ids)} unique HF model IDs...\n")

    results: dict[str, dict] = {}
    found = []
    missing = []

    for hf_id, original_id in sorted(hf_ids.items()):
        print(f"  {hf_id} ... ", end="", flush=True)
        bm = fetch_hf_benchmarks(hf_id)
        if bm:
            results[hf_id] = bm
            found.append(hf_id)
            print(f"OK ({', '.join(f'{k}={v}' for k, v in list(bm.items())[:3])}{'...' if len(bm) > 3 else ''})")
        else:
            missing.append(hf_id)
            print("no eval_results")
        time.sleep(0.3)  # be polite to HF API

    # Merge with existing file to preserve manual entries
    existing: dict = {}
    if out_path.exists():
        with out_path.open() as f:
            existing = yaml.safe_load(f) or {}

    merged = {**existing, **results}

    header = (
        "# Benchmark scores keyed by canonical HuggingFace model ID.\n"
        "# Auto-populated by scripts/fetch_benchmarks.py — safe to edit manually.\n"
        "# Scores are percentages (0-100). Null = not available from HF Hub.\n\n"
    )
    with out_path.open("w") as f:
        f.write(header)
        yaml.dump(merged, f, default_flow_style=False, allow_unicode=True, sort_keys=True)

    print(f"\nResults written to {out_path}")
    print(f"  Found: {len(found)} models")
    print(f"  Missing: {len(missing)} models")
    if missing:
        print(f"  No data: {', '.join(missing)}")
    print("\nManually add scores for missing models directly in scripts/benchmarks.yaml")


if __name__ == "__main__":
    main()
