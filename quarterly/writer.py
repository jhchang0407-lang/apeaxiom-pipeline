"""Quarterly Writer — AI prose generation for the quarterly earnings section.

Uses the distributor's pre-computed tables and writer template to generate
structured prose paragraphs. Reuses the existing pipeline LLM client.
"""

from __future__ import annotations

import json
import re
from typing import Any

from config.settings import OPENAI_API_KEY, WRITER_MODEL


_SYSTEM_MSG = (
    "You are an institutional equity research writer. You receive structured earnings "
    "facts and write a polished Latest Financial Highlights section for an equity "
    "research memo.\n\n"
    "CORE PRINCIPLE\n"
    "Tables are pre-computed and appear alongside your prose. The reader can see every "
    "number in the tables. Your job is to provide context, causation, and judgment that "
    "tables cannot convey. Never narrate table data back to the reader.\n\n"
    "WHAT TO WRITE\n"
    "Explain WHY numbers moved, not WHAT the numbers are.\n"
    "Identify the disconnect between results and market reaction.\n"
    "Surface management's forward commitments and whether they are credible.\n"
    "Connect segment-level drivers to the consolidated result.\n"
    "Flag what changed about the investment thesis, if anything.\n"
    "State what to monitor next quarter with specific thresholds.\n\n"
    "WHAT NOT TO WRITE\n"
    "Do not restate numbers visible in adjacent tables. Reference them only when building "
    "a causal argument.\n"
    "Do not summarize all segments sequentially.\n"
    "Do not write filler synthesis sentences.\n\n"
    "FORMATTING RULES\n"
    "Write in flowing prose paragraphs. No bullet points. No numbered lists.\n"
    "Never use markdown headers. Never use bold, italic, strikethrough.\n"
    "Never begin a sentence with: Furthermore, Moreover, Additionally, In addition.\n"
    "Never use: notably, importantly, significantly, it is worth noting.\n\n"
    "CITATION RULES\n"
    "Use [S1], [S2] etc. Place at end of sentence before the period.\n"
    "Cite the first use of data from each source.\n\n"
    "DOLLAR FORMATTING\n"
    "All dollar figures are pre-formatted. Copy exactly — never reformat.\n\n"
    "NULL HANDLING\n"
    "If a fact field is null, skip it entirely. Do not estimate or fill gaps."
)


def _extract_json(text: str) -> dict | None:
    """Robustly extract a JSON object from model output.

    Handles:
      - Clean JSON
      - Markdown fences (```json ... ```)
      - Leading/trailing prose around JSON
      - Multiple extraction attempts
    """
    if not text or not text.strip():
        return None

    text = text.strip()

    # 1. Direct parse
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Strip markdown fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)```", text, re.DOTALL)
    if fence_match:
        try:
            return json.loads(fence_match.group(1).strip())
        except (json.JSONDecodeError, TypeError):
            pass

    # 3. Find the outermost { ... } by scanning for the LAST } that pairs
    #    with the first {. Use rfind to get the largest possible JSON block.
    first_brace = text.find("{")
    if first_brace == -1:
        return None

    last_brace = text.rfind("}")
    if last_brace <= first_brace:
        return None

    # Try from the largest block first, then shrink
    candidate = text[first_brace : last_brace + 1]
    try:
        return json.loads(candidate)
    except (json.JSONDecodeError, TypeError):
        pass

    # 4. If the largest block failed, try finding valid JSON by scanning
    #    closing braces from the end backwards
    for end_pos in range(last_brace, first_brace, -1):
        if text[end_pos] == "}":
            try:
                return json.loads(text[first_brace : end_pos + 1])
            except (json.JSONDecodeError, TypeError):
                continue

    return None


async def write_quarterly(distributed: dict) -> dict:
    """Generate prose for the quarterly section.

    Args:
        distributed: Output from distribute_quarterly() containing
            writer_template, writer_schema, and precomputed tables.

    Returns:
        Writer output dict with prose paragraphs.
    """
    import openai

    client = openai.AsyncOpenAI(api_key=OPENAI_API_KEY, timeout=300)
    model = WRITER_MODEL or "gpt-5-mini"

    writer_template = distributed["writer_template"]
    writer_schema = distributed["writer_schema"]

    # Build messages
    messages = [
        {"role": "system", "content": _SYSTEM_MSG},
        {"role": "user", "content": writer_template},
    ]

    # Determine if reasoning model (o1, o3, gpt-5*)
    is_reasoning = any(model.startswith(p) for p in ("o1", "o3", "gpt-5"))

    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "max_completion_tokens": 4096,
    }

    if not is_reasoning:
        kwargs["temperature"] = 0.4
        # Structured output only for non-reasoning models
        kwargs["response_format"] = {
            "type": "json_schema",
            "json_schema": {
                "name": "quarterly_writer",
                "schema": writer_schema,
                "strict": False,
            },
        }
    else:
        # Reasoning models: ask for JSON in the prompt instead
        messages[-1]["content"] += (
            "\n\nRespect the following JSON schema for your output:\n"
            f"{json.dumps(writer_schema, indent=2)}\n\n"
            "Output ONLY valid JSON. No markdown fences, no explanation."
        )

    # Try up to 2 times (retry once on parse failure)
    for attempt in range(2):
        response = await client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content

        result = _extract_json(content)
        if result is not None:
            return result

        if attempt == 0:
            # Retry with stronger instruction
            messages[-1]["content"] += (
                "\n\nYour previous output was not valid JSON. "
                "Output ONLY the JSON object, nothing else."
            )

    # All attempts failed — return raw text with parse error flag
    return {"raw_text": content, "_parse_error": True}
