"""Incubating commit processor — AI-driven, defined by instructions and file patterns."""

import re
import subprocess

from . import CommitProcessor


def _git_show_file(repo_path: str, full_hash: str, file_path: str) -> str | None:
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


def _call_ai(instructions: str, file_contents: dict[str, str]) -> str | None:
    """Call AI provider to generate an annotation for the given files."""
    from app.config import load_config_decrypted

    config = load_config_decrypted()

    # Find AI provider — prefer "commit_processing" task, fall back to first
    provider = None
    for task_type in ("commit_processing", "repository_analysis"):
        task = next(
            (t for t in config.ai_tasks if t.task_type == task_type), None)
        if task:
            provider = next(
                (p for p in config.ai_providers
                 if p.name == task.provider_name), None)
            if provider:
                break
    if not provider and config.ai_providers:
        provider = config.ai_providers[0]
    if not provider:
        return None

    from openai import OpenAI
    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)

    files_text = ""
    for path, content in file_contents.items():
        # Truncate large files
        truncated = content[:4000] + "\n... (truncated)" if len(content) > 4000 else content
        files_text += f"\n--- {path} ---\n{truncated}\n"

    system_prompt = (
        "You are a commit analyzer. Given the instructions and file contents below, "
        "produce a concise structured annotation describing what changed. "
        "Respond with a JSON object containing:\n"
        '- "summary": a one-line summary of the change\n'
        '- "details": array of detail strings describing specific changes\n'
        "Be concise and technical. Only output valid JSON, no markdown fences."
    )

    user_content = (
        f"## Processor Instructions\n{instructions}\n\n"
        f"## Files Changed\n{files_text}"
    )

    try:
        response = client.chat.completions.create(
            model=provider.default_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=1000,
        )
        return response.choices[0].message.content.strip()
    except Exception:
        return None


class IncubatingProcessor(CommitProcessor):
    status = "incubating"

    def __init__(self, proc_name: str, proc_label: str,
                 proc_description: str, instructions: str,
                 file_patterns: list[str]):
        self.name = proc_name
        self.label = proc_label
        self.description = proc_description
        self.instructions = instructions
        self.node_property = f"proc_{proc_name}"
        self._patterns = [re.compile(p, re.IGNORECASE) for p in file_patterns]

    def detect(self, files_changed: list[str]) -> list[str]:
        matched = []
        for f in files_changed:
            for pat in self._patterns:
                if pat.search(f):
                    matched.append(f)
                    break
        return matched

    def process(self, repo_path: str, full_hash: str,
                matched_files: list[str],
                parent_full_hash: str | None = None) -> dict | None:
        # Read file contents for AI analysis
        file_contents: dict[str, str] = {}
        for f in matched_files:
            content = _git_show_file(repo_path, full_hash, f)
            if content:
                file_contents[f] = content

        if not file_contents:
            return None

        ai_response = _call_ai(self.instructions, file_contents)
        if not ai_response:
            return {"files": list(file_contents.keys()),
                    "error": "AI provider not available"}

        # Try to parse JSON response
        import json
        try:
            parsed = json.loads(ai_response)
            return {
                "files": list(file_contents.keys()),
                "summary": parsed.get("summary", ""),
                "details": parsed.get("details", []),
            }
        except json.JSONDecodeError:
            # AI didn't return valid JSON, store raw text
            return {
                "files": list(file_contents.keys()),
                "summary": ai_response[:200],
                "details": [],
            }
