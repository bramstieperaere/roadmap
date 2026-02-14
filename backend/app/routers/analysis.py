import json
from pathlib import Path

from fastapi import APIRouter, HTTPException
from openai import OpenAI

from app.config import load_config_decrypted
from app.models import AnalyzeRequest, AnalyzeResponse, ModuleConfig
from app.session import session

router = APIRouter(prefix="/api/analysis", tags=["analysis"])


def _find_analysis_provider(config):
    task = next((t for t in config.ai_tasks if t.task_type == "repository_analysis"), None)
    if not task:
        raise HTTPException(status_code=400, detail="No AI provider configured for repository analysis")
    provider = next((p for p in config.ai_providers if p.name == task.provider_name), None)
    if not provider:
        raise HTTPException(status_code=400, detail=f"AI provider '{task.provider_name}' not found")
    return provider


def _read_repo_structure(repo_path: str) -> dict:
    path = Path(repo_path)
    if not path.exists() or not path.is_dir():
        raise HTTPException(status_code=400, detail=f"Repository path does not exist: {repo_path}")

    result = {
        "top_level_files": [],
        "top_level_dirs": [],
        "pom_xml": None,
        "package_json": None,
        "module_details": [],
    }

    for entry in sorted(path.iterdir()):
        if entry.name.startswith("."):
            continue
        if entry.is_file():
            result["top_level_files"].append(entry.name)
        elif entry.is_dir():
            result["top_level_dirs"].append(entry.name)

    pom_path = path / "pom.xml"
    if pom_path.exists():
        result["pom_xml"] = pom_path.read_text(encoding="utf-8", errors="replace")[:8000]

    pkg_path = path / "package.json"
    if pkg_path.exists():
        result["package_json"] = pkg_path.read_text(encoding="utf-8", errors="replace")[:4000]

    for dir_name in result["top_level_dirs"]:
        dir_path = path / dir_name
        detail = {"name": dir_name, "files": []}
        sub_pom = dir_path / "pom.xml"
        sub_pkg = dir_path / "package.json"
        if sub_pom.exists():
            detail["pom_xml_snippet"] = sub_pom.read_text(encoding="utf-8", errors="replace")[:4000]
        if sub_pkg.exists():
            detail["package_json_snippet"] = sub_pkg.read_text(encoding="utf-8", errors="replace")[:2000]
        try:
            detail["files"] = [e.name for e in sorted(dir_path.iterdir()) if not e.name.startswith(".")][:50]
        except PermissionError:
            pass
        result["module_details"].append(detail)

    return result


def _build_prompt(repo_structure: dict) -> str:
    return f"""Analyze this repository structure and identify all software modules.

For each module, determine:
1. **name**: A descriptive module name (use the directory name or artifact ID)
2. **type**: Either "java" (if it has pom.xml or is a Maven/Gradle module) or "angular" (if package.json contains @angular dependencies)
3. **relative_path**: The path relative to the repository root

Repository structure:
```json
{json.dumps(repo_structure, indent=2)}
```

Respond with ONLY a JSON array of objects with keys "name", "type", "relative_path". Example:
[
  {{"name": "core-api", "type": "java", "relative_path": "core-api"}},
  {{"name": "web-ui", "type": "angular", "relative_path": "frontend"}}
]

Rules:
- Only include actual software modules (skip docs, scripts, config directories)
- A directory with pom.xml is a Java module
- A directory with package.json containing @angular/* dependencies is an Angular module
- The repository root itself can be a module if it has pom.xml or package.json with @angular
- For a multi-module Maven project, list each child module, not the parent
"""


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze_repository(request: AnalyzeRequest):
    if not session.is_unlocked():
        raise HTTPException(status_code=403, detail="App is locked")

    config = load_config_decrypted()

    if request.repo_index < 0 or request.repo_index >= len(config.repositories):
        raise HTTPException(status_code=400, detail="Invalid repository index")

    repo = config.repositories[request.repo_index]
    provider = _find_analysis_provider(config)

    repo_structure = _read_repo_structure(repo.path)

    client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)

    try:
        response = client.chat.completions.create(
            model=provider.default_model,
            messages=[
                {"role": "system", "content": "You are a software architecture analyst. Respond only with valid JSON."},
                {"role": "user", "content": _build_prompt(repo_structure)},
            ],
            temperature=0.1,
            max_tokens=2000,
        )

        content = response.choices[0].message.content.strip()
        if content.startswith("```"):
            content = content.split("\n", 1)[1]
            content = content.rsplit("```", 1)[0]

        modules_data = json.loads(content)
        modules = [ModuleConfig(**m) for m in modules_data]
        return AnalyzeResponse(modules=modules)

    except json.JSONDecodeError as e:
        raise HTTPException(status_code=500, detail=f"Failed to parse AI response as JSON: {e}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"AI analysis failed: {e}")
