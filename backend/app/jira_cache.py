import json
from datetime import datetime, timezone
from pathlib import Path

from app.config import CONFIG_PATH


class JiraCache:
    def __init__(self, cache_dir: str, refresh_duration: int):
        if cache_dir:
            self._root = Path(cache_dir)
        else:
            self._root = CONFIG_PATH.parent / "cache"
        self._ttl = refresh_duration

    def is_fresh(self, path: Path) -> bool:
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cached_at = datetime.fromisoformat(data["_cached_at"])
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()
            return age < self._ttl
        except Exception:
            return False

    def read(self, path: Path) -> dict | None:
        if not self.is_fresh(path):
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def write(self, path: Path, data: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data["_cached_at"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def sprint_path(self, project_key: str, sprint_id: int) -> Path:
        return self._root / "jira" / project_key / "sprints" / f"{sprint_id}.json"

    def sprints_list_path(self, project_key: str, board_id: int) -> Path:
        return self._root / "jira" / project_key / f"board_{board_id}" / "sprints.json"

    def backlog_path(self, project_key: str, board_id: int) -> Path:
        return self._root / "jira" / project_key / f"board_{board_id}" / "backlog.json"

    def issue_path(self, project_key: str, issue_key: str) -> Path:
        return self._root / "jira" / project_key / "issues" / f"{issue_key}.json"

    def metadata_path(self, project_key: str) -> Path:
        return self._root / "jira" / project_key / "metadata.json"

    # --- Confluence paths ---

    def confluence_pages_path(self, space_key: str) -> Path:
        return self._root / "confluence" / space_key / "pages.json"

    def confluence_page_path(self, space_key: str, page_id: str) -> Path:
        return self._root / "confluence" / space_key / "pages" / f"{page_id}.json"
