"""
Suggest `strengths` labels for each model in providers.yaml.

Queries:
  - HuggingFace /api/models/{id} for tags and language metadata
  - OpenRouter /api/v1/models for supported_parameters and descriptions

Outputs YAML snippets ready to paste as `strengths:` fields in providers.yaml.

Usage:
    python3 scripts/fetch_strengths.py

Requires: requests, pyyaml (already in requirements.txt)
"""

import re
import sys
import time
from pathlib import Path

import requests
import yaml

HERE = Path(__file__).parent

# Same mapping as bank.py CANONICAL_HF_IDS.
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

FAST_PATTERNS = re.compile(
    r"(^|[-/])("
    r"llama-?3[\.\-]?[12][-_]?8b"
    r"|llama[-_]?3[\.\-]?2[-_]?3b"
    r"|8b[-_]?instant|instant"
    r"|flash[-_]?lite|flash-lite"
    r"|nano|scout"
    r")",
    re.IGNORECASE,
)
REASONING_PATTERNS = re.compile(
    r"(thinking|reasoning|r1[-_]|[-_]r1|qwq)",
    re.IGNORECASE,
)
CODING_PATTERNS = re.compile(
    r"(coder|deepseek[-_]?v[34]|v3[\.\-]?[12]$|deepseek-v3)",
    re.IGNORECASE,
)
AGENTIC_IDS = {"moonshotai/kimi-k2-instruct", "moonshotai/Kimi-K2-Instruct"}
MULTILINGUAL_PATTERNS = re.compile(
    r"(llama|gemini|qwen|gemma|glm|llava|mistral|minimax)",
    re.IGNORECASE,
)
GPT_OSS_REASONING = re.compile(r"gpt[-_]?oss[-_]?120b", re.IGNORECASE)


def fetch_hf_meta(hf_id: str) -> dict:
    url = f"https://huggingface.co/api/models/{hf_id}?full=false"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return {}


def fetch_openrouter_models() -> dict[str, dict]:
    """Returns {model_id: model_data} from OpenRouter's public model list."""
    try:
        r = requests.get("https://openrouter.ai/api/v1/models", timeout=20)
        if r.status_code == 200:
            data = r.json()
            return {m["id"]: m for m in data.get("data", [])}
    except Exception:
        pass
    return {}


def strengths_from_hf(hf_id: str, data: dict) -> set[str]:
    result: set[str] = set()
    tags = [t.lower() for t in (data.get("tags") or [])]
    card = data.get("cardData") or {}
    languages = card.get("language") or []
    if isinstance(languages, str):
        languages = [languages]

    if "code" in tags or "coding" in tags:
        result.add("coding")
    if len(languages) > 4:
        result.add("multilingual")
    # Architecture-level hints
    model_type = (data.get("model_type") or "").lower()
    if "deepseek" in model_type and "v3" in hf_id.lower():
        result.add("coding")
    return result


def strengths_from_openrouter(model_id: str, or_data: dict) -> set[str]:
    result: set[str] = set()
    # Strip :free suffix for lookup
    base_id = model_id.removesuffix(":free")
    entry = or_data.get(model_id) or or_data.get(base_id) or {}
    if not entry:
        return result
    params = entry.get("supported_parameters") or []
    if "reasoning" in params:
        result.add("reasoning")
    desc = (entry.get("description") or "").lower()
    if "code" in desc or "coding" in desc or "programm" in desc:
        result.add("coding")
    modalities = entry.get("input_modalities") or []
    # vision label reserved for future use — don't add yet
    return result


def strengths_from_name(model_id: str) -> set[str]:
    result: set[str] = set()
    name = model_id.lower()

    if FAST_PATTERNS.search(name):
        result.add("fast")
    if REASONING_PATTERNS.search(name):
        result.add("reasoning")
    if CODING_PATTERNS.search(name):
        result.add("coding")
    if model_id in AGENTIC_IDS or "kimi-k2-instruct" in name:
        result.add("agentic")
        result.add("coding")
    if GPT_OSS_REASONING.search(name):
        result.add("reasoning")
    if MULTILINGUAL_PATTERNS.search(name):
        result.add("multilingual")

    return result


def derive_strengths(model_id: str, hf_data: dict, or_data: dict) -> list[str]:
    hf_id = CANONICAL_HF_IDS.get(model_id, model_id)
    strengths: set[str] = set()

    if hf_id and hf_data:
        strengths |= strengths_from_hf(hf_id, hf_data)

    strengths |= strengths_from_openrouter(model_id, or_data)
    strengths |= strengths_from_name(model_id)

    if not strengths:
        strengths.add("general")
    if not strengths - {"fast", "multilingual"}:
        strengths.add("general")

    # Canonical ordering
    order = ["reasoning", "coding", "agentic", "fast", "multilingual", "general"]
    return [s for s in order if s in strengths]


def main():
    providers_yaml = HERE / "providers.yaml"
    with providers_yaml.open() as f:
        raw = yaml.safe_load(f)

    print("Fetching OpenRouter model catalogue...", flush=True)
    or_data = fetch_openrouter_models()
    print(f"  {len(or_data)} models loaded from OpenRouter\n")

    # Collect unique HF IDs to fetch
    hf_fetch: dict[str, str] = {}
    for p in raw:
        for m in p.get("models", []):
            mid = m if isinstance(m, str) else m["id"]
            hf_id = CANONICAL_HF_IDS.get(mid, mid)
            if hf_id and hf_id not in hf_fetch:
                hf_fetch[hf_id] = mid

    print(f"Fetching HuggingFace metadata for {len(hf_fetch)} canonical IDs...\n")
    hf_cache: dict[str, dict] = {}
    for hf_id in sorted(hf_fetch):
        print(f"  {hf_id} ... ", end="", flush=True)
        data = fetch_hf_meta(hf_id)
        if data:
            hf_cache[hf_id] = data
            langs = len((data.get("cardData") or {}).get("language") or [])
            tags = [t for t in (data.get("tags") or []) if t in ("code", "coding")]
            print(f"OK (langs={langs}, code_tags={tags})")
        else:
            print("no data")
        time.sleep(0.3)

    print("\n# ── Suggested strengths (paste into scripts/providers.yaml) ──\n")
    for p in raw:
        pname = p["name"]
        printed_header = False
        for m in p.get("models", []):
            mid = m if isinstance(m, str) else m["id"]
            hf_id = CANONICAL_HF_IDS.get(mid, mid)
            hf_data = hf_cache.get(hf_id, {}) if hf_id else {}
            labels = derive_strengths(mid, hf_data, or_data)
            if not printed_header:
                print(f"# {pname}")
                printed_header = True
            print(f"  {mid}: {labels}")
        if printed_header:
            print()


if __name__ == "__main__":
    main()
