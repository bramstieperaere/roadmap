import json
import urllib.request
import urllib.error

from fastapi import HTTPException

from app.models import LogzioConfig


def require_logzio_configured(cfg: LogzioConfig) -> None:
    if not cfg.base_url:
        raise HTTPException(status_code=400, detail="Logz.io base URL is not configured")
    if not cfg.api_token:
        raise HTTPException(status_code=400, detail="Logz.io API token is not configured")


def logzio_search(cfg: LogzioConfig, query: str,
                  from_time: str | None = None,
                  to_time: str | None = None,
                  size: int = 50) -> dict:
    """Execute a log search against the Logz.io API. Returns parsed JSON."""
    url = cfg.base_url.rstrip("/") + "/v2/search"
    body: dict = {
        "query": {
            "query_string": {
                "query": query,
            },
        },
        "size": min(size, 1000),
    }
    if from_time:
        body["from"] = from_time
    if to_time:
        body["to"] = to_time

    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        headers={
            "X-API-TOKEN": cfg.api_token,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        try:
            detail = json.loads(body_text).get("message", body_text[:200])
        except Exception:
            detail = body_text[:200]
        raise HTTPException(status_code=400, detail=f"Logz.io returned {e.code}: {detail}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
