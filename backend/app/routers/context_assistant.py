import re
from fastapi import APIRouter, HTTPException
from openai import OpenAI
from pydantic import BaseModel

from app.config import load_config_decrypted
from app.jira_cache import JiraCache
from app.jira_issue_service import JiraIssueService
from app.session import session

router = APIRouter(prefix="/api/context-assistant", tags=["context-assistant"])

_JIRA_KEY_RE = re.compile(r"\b([A-Z][A-Z0-9]+-\d+)\b")
_CONFLUENCE_PAGE_RE = re.compile(r"(?:page[/ ]?(?:id[: ]?)?|confluence[/ ])(\d{5,})", re.IGNORECASE)


class AssistantRequest(BaseModel):
    message: str
    context_name: str
    existing_items: list[dict] = []
    tags: list[str] = []
    description: str = ""


class ProposedAction(BaseModel):
    action: str  # "add_item" | "add_tag" | "remove_tag" | "set_description"
    # add_item fields
    type: str = ""
    id: str = ""
    label: str = ""
    # tag fields
    tag: str = ""
    # description fields
    description: str = ""
    reason: str = ""


class AssistantResponse(BaseModel):
    reply: str
    proposals: list[ProposedAction]
    fetched_issues: dict[str, dict] = {}


_SYSTEM_PROMPT = """You are a context-building assistant. The user is building a "context" — a curated collection of reference items for analysis.

You can propose these actions:

1. **add_item**: Add a reference item. Types:
   - jira_issue (id = issue key like "PROJ-123")
   - confluence_page (id = page ID number)
   - git_repo (id = repo name)
   - instructions (id = label, label = descriptive text)
   - scratch_dir (id = MUST be the exact scratch directory path shown in the context data, label = directory name) — a working directory where AI can write generated files like .md, .puml, or Python scripts. ONLY propose this if the scratch directory path is provided in the context data.

2. **add_tag**: Add a tag to the context for categorization (e.g. "analysis", "bugfix", "payment", "sprint-42")

3. **remove_tag**: Remove an existing tag

4. **set_description**: Set or update the context description (a short summary of what this context is about)

You will see:
- The user's message
- Current context name, description, tags, and existing items
- Any Jira issue data (description, linked issues, subtasks, branches, commits)
- Configured git repositories

Guidelines:
- When the user first describes their intent (e.g. "I want to analyse PROJ-123"), propose:
  1. A set_description with a clear summary (e.g. the Jira ticket summary or a paraphrase of the user's intent)
  2. An instructions item capturing the task
  3. The mentioned Jira issues + related issues/pages/repos
  4. Relevant tags
- When the user says "tag as X" or "add tag X", propose add_tag actions
- When the user says "remove tag X" or "untag X", propose remove_tag actions
- Do NOT propose add_item for items already in the context
- For git repos: match branches/commits/components to configured repo names
- For each proposal, give a brief reason

Respond with JSON only:
{
  "reply": "Brief acknowledgment",
  "proposals": [
    {"action": "set_description", "description": "Analysis of payment flow for PROJ-123", "reason": "Summarizes the context purpose"},
    {"action": "add_item", "type": "instructions", "id": "Analyse PROJ-123", "label": "Analyse payment flow in PROJ-123", "reason": "Captures the analysis intent"},
    {"action": "add_item", "type": "jira_issue", "id": "PROJ-123", "label": "Payment flow bug", "reason": "Main ticket"},
    {"action": "add_item", "type": "git_repo", "id": "payment-service", "label": "payment-service", "reason": "Contains the feature branch"},
    {"action": "add_tag", "tag": "analysis", "reason": "Categorizes this context"},
    {"action": "add_tag", "tag": "payment", "reason": "Domain tag"}
  ]
}
"""


def _require_unlocked():
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")


def _find_provider(config):
    for task_type in ("context_assistant", "cypher_generation", "repository_analysis"):
        task = next(
            (t for t in config.ai_tasks if t.task_type == task_type), None)
        if task:
            provider = next(
                (p for p in config.ai_providers
                 if p.name == task.provider_name), None)
            if provider:
                return provider
    if config.ai_providers:
        return config.ai_providers[0]
    return None


def _fetch_issue_data(svc: JiraIssueService, key: str) -> dict | None:
    """Fetch and return a compact view of a Jira issue."""
    data = svc.get_issue(key)
    if not data:
        return None
    return {
        "key": data.get("key", key),
        "summary": data.get("summary", ""),
        "status": data.get("status", ""),
        "issuetype": data.get("issuetype", ""),
        "description": (data.get("description", "") or "")[:3000],
        "issuelinks": data.get("issuelinks", []),
        "subtasks": data.get("subtasks", []),
        "labels": data.get("labels", []),
        "components": data.get("components", []),
        "branches": data.get("branches", []),
        "commits": data.get("commits", [])[:10],
        "pull_requests": data.get("pull_requests", []),
    }


@router.post("", response_model=AssistantResponse)
def assist(request: AssistantRequest):
    _require_unlocked()
    config = load_config_decrypted()

    provider = _find_provider(config)
    if not provider:
        raise HTTPException(status_code=400,
                            detail="No AI provider configured. Set up in Settings → AI Tasks.")

    # Extract Jira keys from user message
    mentioned_keys = _JIRA_KEY_RE.findall(request.message)

    # Fetch Jira issue data for mentioned keys
    fetched: dict[str, dict] = {}
    try:
        atl = config.atlassian
        cache = JiraCache(atl.cache_dir if atl else "", atl.refresh_duration if atl else 3600)
        svc = JiraIssueService(atl, cache)
        for key in mentioned_keys:
            data = _fetch_issue_data(svc, key)
            if data:
                fetched[key] = data
                # Also fetch linked issues for richer context
                for link in data.get("issuelinks", []):
                    lk = link.get("key", "")
                    if lk and lk not in fetched:
                        linked = _fetch_issue_data(svc, lk)
                        if linked:
                            fetched[lk] = linked
    except Exception:
        pass  # Best-effort fetch; AI can still work with the message alone

    # Build AI prompt
    existing_ids = {(i.get("type", ""), i.get("id", "")) for i in request.existing_items}
    existing_desc = "\n".join(
        f"- [{i.get('type')}] {i.get('id')} — {i.get('label', '')}"
        for i in request.existing_items
    ) or "(empty)"

    issue_context = ""
    if fetched:
        import json
        issue_context = "\n\nJira issue data:\n" + json.dumps(fetched, indent=2, default=str)

    repo_names = [r.name for r in config.repositories]
    repos_context = f"\n\nConfigured git repositories: {', '.join(repo_names)}" if repo_names else ""

    from pathlib import Path
    scratch_path = ""
    if config.scratch_base_dir:
        scratch_path = str(Path(config.scratch_base_dir) / request.context_name)

    tags_desc = ", ".join(request.tags) if request.tags else "(none)"
    user_content = (
        f"Context: {request.context_name}\n"
        f"Description: {request.description or '(not set)'}\n"
        f"Tags: {tags_desc}\n"
        f"Existing items:\n{existing_desc}\n\n"
        f"User message: {request.message}"
        f"{issue_context}"
        f"{repos_context}"
        f"{f'\n\nScratch directory for this context: {scratch_path}' if scratch_path else ''}"
    )

    # Call AI
    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
    try:
        response = client.chat.completions.create(
            model=provider.default_model,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.3,
            max_tokens=2000,
        )
        text = response.choices[0].message.content.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()

        import json
        result = json.loads(text)

        # Filter out already-existing items for add_item actions
        proposals = []
        for p in result.get("proposals", []):
            action = p.get("action", "add_item")
            if action == "add_item" and (p.get("type", ""), p.get("id", "")) in existing_ids:
                continue
            proposals.append(ProposedAction(**p))

        return AssistantResponse(
            reply=result.get("reply", ""),
            proposals=proposals,
            fetched_issues=fetched,
        )
    except Exception as e:
        raise HTTPException(status_code=500,
                            detail=f"AI assistant failed: {e}")
