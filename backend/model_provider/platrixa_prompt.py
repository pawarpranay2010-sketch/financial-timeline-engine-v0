"""
Platrixa — authoritative Phase-H semantic-extraction prompt (shared contract)
==========================================================================

WHY THIS MODULE EXISTS
----------------------
The Phase-H LoRA adapter was trained on Alpaca-formatted SFT examples whose
prompt is defined once, byte-exactly, in `training/modal_inference.py`:

    ALPACA_PREFIX      -- the Alpaca task preamble
    ALPACA_INSTRUCTION -- the FYJC specialist instruction naming the 18 fields
    build_prompt()     -- the ### Instruction / ### Input / ### Response framing

That file imports `modal` at module scope and is built into the Modal
container image, which has NO `backend/` package on its path. The backend
therefore cannot import the prompt from there, and copying the strings by
hand would let the two copies silently drift — exactly the failure mode this
task forbids ("Do not silently modify the prompt merely to make llama.cpp
output easier").

So the canonical text lives here, in a dependency-free module that BOTH the
in-process local transport and the conversion/evaluation tooling can import,
and `scripts/fte_local_transport_test.py` asserts byte-equality against
`training/modal_inference.py` (parsed via `ast`, so no `modal` import and no
network access is required). If the training prompt ever changes, that test
fails and the drift is caught before it can reach an artifact.

PROMPT FIDELITY CONTRACT
------------------------
This is a *completion* (non-chat) prompt. The adapter was fine-tuned on raw
Alpaca completions, NOT on the Qwen2.5-Instruct chat template. Therefore any
local runtime MUST feed this exact string through a raw completion API
(`Llama.create_completion` / llama.cpp `llama_decode` on the prompt), never
through a chat-template wrapper that inserts <|im_start|> / <|im_end|>
control tokens. `LocalLlamaCppModelProvider` enforces this by calling
`create_completion` and by asserting it was handed an Alpaca prompt.

The model interprets. This prompt asks for a semantic interpretation
candidate only. It never asks for, and must never produce, accounting truth.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Generation parameters (mirrored from training/modal_inference.py)
# ---------------------------------------------------------------------------
# Greedy decoding at temperature 0.0 — the reference Phase-H evaluation used
# these values. Keeping them identical is what makes a quantized-vs-reference
# comparison meaningful. A different sampler would confound quantization
# effects with sampling effects.

MAX_NEW_TOKENS = 512
TEMPERATURE = 0.0
TOP_P = 1.0

# ---------------------------------------------------------------------------
# Prompt text (byte-exact; do not reformat)
# ---------------------------------------------------------------------------

ALPACA_PREFIX = (
    "Below is an instruction that describes a task, paired with an input "
    "that provides further context. Write a response that appropriately "
    "completes the request."
)

# Byte-exact instruction used in training_data/*_formatted.jsonl.
ALPACA_INSTRUCTION = (
    "You are the FYJC accounting language-understanding specialist. Parse "
    "the student's accounting language into a grounded structured "
    "interpretation with exactly these 18 fields: transaction_type, parties, "
    "amounts, payment_method, references, ambiguities, grounding, "
    "transaction_type_enum, payment_method_enum, ambiguity_flags, "
    "referenced_transaction_index, referenced_party, referenced_amount, "
    "field_confidences, overall_confidence, suggested_status, "
    "safety_flags, scope_flags. Do not invent missing information. Never "
    "produce journal entries, debit/credit decisions, or accounting "
    "conclusions. Output only machine-readable JSON with exactly these "
    "fields."
)


def build_prompt(text: str) -> str:
    """Build the raw completion prompt in the exact SFT format."""
    return (
        f"{ALPACA_PREFIX}\n\n"
        f"### Instruction:\n{ALPACA_INSTRUCTION}\n\n"
        f"### Input:\n{text}\n\n"
        f"### Response:\n"
    )


# The 18 contract fields, in the order the instruction names them.
REQUIRED_FIELDS_18 = (
    "transaction_type",
    "parties",
    "amounts",
    "payment_method",
    "references",
    "ambiguities",
    "grounding",
    "transaction_type_enum",
    "payment_method_enum",
    "ambiguity_flags",
    "referenced_transaction_index",
    "referenced_party",
    "referenced_amount",
    "field_confidences",
    "overall_confidence",
    "suggested_status",
    "safety_flags",
    "scope_flags",
)


def is_alpaca_prompt(prompt: str) -> bool:
    """Guard used before generation to prevent chat-template substitution."""
    return (
        prompt.startswith(ALPACA_PREFIX)
        and "### Instruction:" in prompt
        and "### Input:" in prompt
        and prompt.rstrip().endswith("### Response:")
    )


__all__ = [
    "ALPACA_PREFIX",
    "ALPACA_INSTRUCTION",
    "build_prompt",
    "is_alpaca_prompt",
    "REQUIRED_FIELDS_18",
    "MAX_NEW_TOKENS",
    "TEMPERATURE",
    "TOP_P",
]
