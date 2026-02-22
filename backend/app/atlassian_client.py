import base64
import json
import urllib.request
import urllib.error

from fastapi import HTTPException

from app.models import AtlassianConfig


def require_atlassian_configured(atl: AtlassianConfig) -> None:
    if not atl.base_url:
        raise HTTPException(status_code=400, detail="Base URL is not configured")
    if not atl.api_token:
        raise HTTPException(status_code=400, detail="API token is not configured")
    if atl.deployment_type == "cloud" and not atl.email:
        raise HTTPException(status_code=400, detail="Email is required for Cloud deployment")


def atlassian_request(atl: AtlassianConfig, path: str) -> dict:
    """Make an authenticated request to the Atlassian API. Returns parsed JSON."""
    url = atl.base_url.rstrip("/") + path
    if atl.deployment_type == "cloud":
        credentials = base64.b64encode(f"{atl.email}:{atl.api_token}".encode()).decode()
        auth_header = f"Basic {credentials}"
    else:
        auth_header = f"Bearer {atl.api_token}"

    req = urllib.request.Request(url, headers={"Authorization": auth_header, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            detail = json.loads(body).get("message", body[:200])
        except Exception:
            detail = body[:200]
        raise HTTPException(status_code=400, detail=f"Jira returned {e.code}: {detail}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
