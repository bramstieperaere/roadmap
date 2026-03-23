import httpx
from fastapi import APIRouter, HTTPException, UploadFile, File
from openai import OpenAI

from app.config import load_config_decrypted
from app.session import session

router = APIRouter(prefix="/api/whisper", tags=["whisper"])

_POSTPROCESS_PROMPT = """Clean up this speech-to-text transcription. Fix grammar, punctuation, and phrasing while preserving the original meaning and intent. Do not add or remove information. Return only the cleaned text, no explanation."""


def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


def _find_provider(config, provider_name: str):
    for p in config.ai_providers:
        if p.name == provider_name:
            return p
    return None


def _postprocess(text: str, provider, model: str) -> str:
    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
    resp = client.chat.completions.create(
        model=model or provider.default_model,
        messages=[
            {"role": "system", "content": _POSTPROCESS_PROMPT},
            {"role": "user", "content": text},
        ],
        temperature=0.3,
        max_tokens=2000,
    )
    return resp.choices[0].message.content.strip()


@router.get("/status")
def whisper_status():
    """Check whether Whisper is configured (without leaking the key)."""
    _require_unlocked()
    config = load_config_decrypted()
    return {"configured": bool(config.whisper.api_key)}


@router.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    _require_unlocked()
    config = load_config_decrypted()
    w = config.whisper
    if not w.api_key:
        raise HTTPException(status_code=400,
                            detail="Whisper not configured — set API key in Settings")

    audio_bytes = await file.read()
    filename = file.filename or "audio.webm"

    url = f"{w.base_url.rstrip('/')}/audio/transcriptions"

    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(
            url,
            headers={"Authorization": f"Bearer {w.api_key}"},
            files={"file": (filename, audio_bytes, file.content_type or "audio/webm")},
            data={"model": w.model},
        )

    if resp.status_code != 200:
        detail = resp.text[:300]
        raise HTTPException(status_code=resp.status_code,
                            detail=f"Whisper API error: {detail}")

    body = resp.json()
    raw_text = body.get("text", "")

    # Post-process if configured
    if w.postprocess_provider and raw_text:
        provider = _find_provider(config, w.postprocess_provider)
        if provider:
            try:
                cleaned = _postprocess(raw_text, provider, w.postprocess_model)
                return {"text": cleaned, "raw": raw_text}
            except Exception as e:
                return {"text": raw_text, "raw": raw_text,
                        "postprocess_error": str(e)}

    return {"text": raw_text}
