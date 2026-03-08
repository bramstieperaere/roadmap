"""HTTP client for Bitbucket Cloud API.

Uses dedicated bitbucket_username + bitbucket_app_password from AtlassianConfig.
Falls back to email + api_token if no dedicated Bitbucket credentials are set.
"""

import base64
import json
import re
import urllib.request
import urllib.error

from fastapi import HTTPException

from app.models import AtlassianConfig

_BASE = "https://api.bitbucket.org/2.0"

_PR_URL_RE = re.compile(
    r"https?://bitbucket\.org/(?P<workspace>[^/]+)/(?P<repo>[^/]+)/pull-requests/(?P<id>\d+)"
)


def require_bitbucket_configured(atl: AtlassianConfig) -> None:
    if atl.bitbucket_username and atl.bitbucket_app_password:
        return
    if atl.email and atl.api_token:
        return
    raise HTTPException(
        status_code=400,
        detail="Bitbucket credentials not configured. Set Bitbucket username + app password in Atlassian settings.",
    )


def parse_pr_url(url: str) -> tuple[str, str, int]:
    """Extract (workspace, repo_slug, pr_id) from a Bitbucket PR URL.

    Raises HTTPException if the URL doesn't match.
    """
    m = _PR_URL_RE.match(url.strip())
    if not m:
        raise HTTPException(
            status_code=400,
            detail="Invalid Bitbucket PR URL. Expected: https://bitbucket.org/{workspace}/{repo}/pull-requests/{id}",
        )
    return m.group("workspace"), m.group("repo"), int(m.group("id"))


def _get_credentials(atl: AtlassianConfig) -> tuple[str, str]:
    """Return (username, password) for Bitbucket API auth."""
    if atl.bitbucket_username and atl.bitbucket_app_password:
        return atl.bitbucket_username, atl.bitbucket_app_password
    return atl.email, atl.api_token


def bitbucket_request(atl: AtlassianConfig, path: str) -> dict:
    """Make an authenticated GET request to the Bitbucket Cloud API."""
    url = _BASE + path
    user, password = _get_credentials(atl)
    credentials = base64.b64encode(f"{user}:{password}".encode()).decode()
    req = urllib.request.Request(
        url,
        headers={"Authorization": f"Basic {credentials}", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            detail = json.loads(body).get("error", {}).get("message", body[:200])
        except Exception:
            detail = body[:200]
        raise HTTPException(status_code=400, detail=f"Bitbucket returned {e.code}: {detail}")
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
