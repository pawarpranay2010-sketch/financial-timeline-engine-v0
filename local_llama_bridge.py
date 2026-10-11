from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import requests
import os

from backend.model_provider.platrixa_prompt import (
    build_prompt,
    is_alpaca_prompt,
    MAX_NEW_TOKENS,
    TEMPERATURE,
    TOP_P,
)

app = FastAPI(title="Platrixa Local Llama Bridge")

LLAMA_URL = "http://127.0.0.1:11435/v1/completions"
BRIDGE_KEY = os.environ.get("PLATRIXA_LOCAL_BRIDGE_KEY", "").strip()
if not BRIDGE_KEY:
    raise RuntimeError(
        "PLATRIXA_LOCAL_BRIDGE_KEY must be configured before starting the bridge"
    )

class InterpretRequest(BaseModel):
    text: str


@app.get("/health")
def health():
    try:
        r = requests.get(
            "http://127.0.0.1:11435/health",
            timeout=3,
        )
        r.raise_for_status()
        return {
            "status": "ok",
            "provider": "local-llama.cpp",
        }
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Local model unavailable",
        )


@app.post("/interpret")
def interpret(
    req: InterpretRequest,
    x_platrixa_android_bridge_key: str | None = Header(default=None),
):
    if x_platrixa_android_bridge_key != BRIDGE_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")
    if not req.text.strip():
        raise HTTPException(
            status_code=422,
            detail="Empty input",
        )

    prompt = build_prompt(req.text)

    if not is_alpaca_prompt(prompt):
        raise HTTPException(
            status_code=500,
            detail="Canonical Alpaca prompt contract failed",
        )

    try:
        r = requests.post(
            LLAMA_URL,
            json={
                "model": "default",
                "prompt": prompt,
                "temperature": TEMPERATURE,
                "top_p": TOP_P,
                "max_tokens": MAX_NEW_TOKENS,
                "stop": [
                    "\n### " + "Instruction:",
                    "\n### " + "Input:",
                    "\n### " + "Response:",
                    "\nBelow is an " + "instruction",
                    "\n" + "---",
                ],
                "stream": False,
            },
            timeout=180,
        )

        r.raise_for_status()
        body = r.json()

        choices = body["choices"]
        if not choices:
            raise ValueError("No completion choices")

        choice = choices[0]
        content = choice["text"]
        finish_reason = choice.get("finish_reason")

        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty completion")

        return {
            "interpretation_text": content,
            "completion_status": (
                "TRUNCATED" if finish_reason == "length" else "COMPLETE"
            ),
            "finish_reason": finish_reason,
            "model": {
                "provider": "local-llama.cpp",
                "adapter_loaded": None,
            },
        }

    except HTTPException:
        raise
    except requests.RequestException:
        raise HTTPException(
            status_code=503,
            detail="Local inference unavailable",
        )
    except (KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(
            status_code=502,
            detail="Malformed model response",
        )
