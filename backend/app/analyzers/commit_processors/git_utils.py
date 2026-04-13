"""Shared git utilities for commit processors."""

import subprocess


def git_show_file(repo_path: str, full_hash: str, file_path: str) -> str | None:
    """Retrieve file content at a specific commit using git show."""
    try:
        proc = subprocess.run(
            ["git", "show", f"{full_hash}:{file_path}"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if proc.returncode != 0:
            return None
        return proc.stdout
    except Exception:
        return None
