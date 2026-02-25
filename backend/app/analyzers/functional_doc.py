"""
AI-powered functional documentation analyzer.

Reads cached Confluence pages, strips HTML to clean text,
sends to an AI provider to extract structured functional documentation
following a generic metamodel, and writes JSON output files.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup
from openai import OpenAI

from app.config import CONFIG_PATH, load_config_decrypted
from app.job_store import job_store
from app.jira_cache import JiraCache

SYSTEM_PROMPT = """\
You are a functional documentation analyst. You convert unstructured documentation
pages into a structured JSON format following a generic functional documentation metamodel.

Analyze the provided document and return a JSON object with this exact structure:

{
  "doc_type": "<one of: requirement | process | specification | guide | test | decision | overview | reference>",
  "domain": "<business domain or capability area, e.g. 'Order Management', 'Loyalty', 'Product Information'>",
  "summary": "<2-3 sentence summary of what this document describes>",
  "tags": ["<relevant free-form tags>"],
  "sections": [
    {
      "heading": "<section heading or inferred heading>",
      "content": "<cleaned content in markdown>",
      "section_type": "<one of: description | rule | mapping | diagram | example | note | technical | test_case>",
      "entities_mentioned": ["<system names, APIs, services, products mentioned>"]
    }
  ],
  "references": [
    {
      "ref_type": "<one of: jira_issue | confluence_page | external_system | url>",
      "ref_id": "<identifier, e.g. 'IRM-5913' or 'ChannelEngine'>",
      "label": "<display label>"
    }
  ],
  "field_mappings": [
    {
      "source_system": "<source system name>",
      "source_field": "<field name in source>",
      "target_system": "<target system name>",
      "target_field": "<field name in target>",
      "transform": "<transformation rule if any, else null>",
      "remarks": "<additional notes, else null>"
    }
  ],
  "metadata": {
    "<key>": "<value>"
  }
}

Rules:
1. Always return valid JSON. No markdown fences, no explanation outside the JSON.
2. For doc_type, pick the most specific match. "specification" for integration specs,
   "process" for workflow descriptions, "test" for test evidence pages,
   "decision" for architectural decisions, "guide" for how-to/user guides,
   "reference" for mapping/lookup tables, "overview" for index/navigation pages.
3. Extract ALL Jira issue keys (patterns like IRM-1234, DEV-123, TR-45, TJ-67) as references.
4. Extract ALL external system names mentioned (e.g. M3, CEGID, ChannelEngine, Talon.one) as references.
5. For field_mappings, only populate if the document contains actual field mapping tables.
   Otherwise return an empty array.
6. For metadata, extract any structured sidebar data (StreamID, SME, Use, Critical, Applications)
   if present. Include any other structured key-value data you find.
7. Keep section content concise but complete. Convert HTML tables to markdown tables.
8. If the page is mostly empty or just navigation links, set doc_type to "overview"
   and keep sections minimal.
"""


def _clean_confluence_html(body_html: str) -> str:
    """Strip Confluence storage format XML/HTML to clean readable text."""
    soup = BeautifulSoup(body_html, "html.parser")

    # Remove draw.io / plantuml macros (binary diagram data)
    for macro in soup.find_all("ac:structured-macro"):
        macro_name = macro.get("ac:name", "")
        if macro_name in ("drawio", "plantumlcloud", "plantuml"):
            label = macro.find("ac:parameter", attrs={"ac:name": "diagramName"})
            name = label.string if label and label.string else macro_name
            macro.replace_with(f"[Diagram: {name}]")
        elif macro_name == "status":
            title_param = macro.find("ac:parameter", attrs={"ac:name": "title"})
            if title_param and title_param.string:
                macro.replace_with(title_param.string)
        elif macro_name == "details":
            # Keep the rich-text-body content inside details macros
            body = macro.find("ac:rich-text-body")
            if body:
                macro.replace_with(body)
        elif macro_name in ("children", "include", "detailssummary",
                            "recently-updated", "contributors"):
            macro.replace_with(f"[Macro: {macro_name}]")
        elif macro_name == "jira":
            key_param = macro.find("ac:parameter", attrs={"ac:name": "key"})
            if key_param and key_param.string:
                macro.replace_with(f"[JIRA: {key_param.string}]")

    # Remove placeholders
    for ph in soup.find_all("ac:placeholder"):
        ph.decompose()

    # Convert ac:link page references to text
    for link in soup.find_all("ac:link"):
        body = link.find("ac:link-body")
        if body:
            link.replace_with(body.get_text())
        else:
            page_ref = link.find("ri:page")
            if page_ref:
                link.replace_with(page_ref.get("ri:content-title", ""))
            else:
                link.replace_with("")

    # Remove user references, replace with placeholder
    for user in soup.find_all("ri:user"):
        user.replace_with("[User]")

    # Get text, collapse excessive whitespace
    text = soup.get_text(separator="\n")
    # Collapse 3+ blank lines into 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _collect_page_ids(cache: JiraCache, space_key: str) -> list[str]:
    """Get all page IDs from the pages.json index."""
    pages_path = cache.confluence_pages_path(space_key)
    if not pages_path.exists():
        return []
    data = json.loads(pages_path.read_text(encoding="utf-8"))

    ids = []

    def _walk(pages):
        for p in pages:
            ids.append(p["id"])
            if p.get("children"):
                _walk(p["children"])

    _walk(data.get("pages", []))
    return ids


def _output_root() -> Path:
    return CONFIG_PATH.parent / "poc" / "functional-desc-structured"


class FunctionalDocAnalyzer:
    """Processes cached Confluence pages into structured functional docs via AI."""

    def __init__(self, job_id: str):
        self.job_id = job_id

    def _log(self, level: str, msg: str):
        job_store.add_log(self.job_id, level, msg)
        print(f"[FUNC-DOC] [{level}] {msg}", flush=True)

    def run(self, space_key: str, page_ids: list[str] | None = None) -> str:
        config = load_config_decrypted()
        atl = config.atlassian
        cache = JiraCache(atl.cache_dir, atl.refresh_duration)

        # Find AI provider
        provider = self._find_provider(config)
        client = OpenAI(api_key=provider.api_key, base_url=provider.base_url)
        model = provider.default_model

        # Determine pages to process
        if page_ids:
            all_ids = page_ids
        else:
            all_ids = _collect_page_ids(cache, space_key)

        if not all_ids:
            self._log("warn", f"No pages found for space {space_key}")
            return "No pages to process"

        self._log("info", f"Processing {len(all_ids)} pages from space {space_key} "
                          f"using model {model}")

        output_dir = _output_root() / space_key / "docs"
        output_dir.mkdir(parents=True, exist_ok=True)

        index_entries = []
        processed = 0
        errors = 0

        for i, page_id in enumerate(all_ids):
            page_path = cache.confluence_page_path(space_key, page_id)
            if not page_path.exists():
                self._log("warn", f"[{i+1}/{len(all_ids)}] Page {page_id} not cached, skipping")
                continue

            page_data = json.loads(page_path.read_text(encoding="utf-8"))
            title = page_data.get("title", "Untitled")
            self._log("info", f"[{i+1}/{len(all_ids)}] Processing: {title}")

            try:
                clean_text = _clean_confluence_html(page_data.get("body_html", ""))

                if len(clean_text.strip()) < 20:
                    # Nearly empty page, skip AI call
                    result = {
                        "doc_type": "overview",
                        "domain": "",
                        "summary": "Empty or navigation-only page.",
                        "tags": [],
                        "sections": [],
                        "references": [],
                        "field_mappings": [],
                        "metadata": {},
                    }
                else:
                    result = self._call_ai(client, model, title, clean_text,
                                           page_data.get("ancestors", []))

                # Build output document
                doc = {
                    "source_id": page_id,
                    "source_type": "confluence",
                    "space_key": space_key,
                    "title": title,
                    "ancestors": page_data.get("ancestors", []),
                    "version": page_data.get("version"),
                    "version_by": page_data.get("version_by"),
                    "version_when": page_data.get("version_when"),
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    **result,
                }

                # Write individual doc
                doc_path = output_dir / f"{page_id}.json"
                doc_path.write_text(
                    json.dumps(doc, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

                index_entries.append({
                    "page_id": page_id,
                    "title": title,
                    "doc_type": result.get("doc_type", ""),
                    "domain": result.get("domain", ""),
                    "summary": result.get("summary", ""),
                })
                processed += 1

            except Exception as e:
                self._log("error", f"Failed to process {title}: {e}")
                errors += 1

        # Write index
        index_path = _output_root() / space_key / "_index.json"
        index_data = {
            "space_key": space_key,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "total_pages": len(all_ids),
            "processed": processed,
            "errors": errors,
            "documents": index_entries,
        }
        index_path.write_text(
            json.dumps(index_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        summary = f"Processed {processed}/{len(all_ids)} pages ({errors} errors)"
        self._log("info", summary)
        return summary

    def _find_provider(self, config):
        task = next(
            (t for t in config.ai_tasks if t.task_type == "functional_doc"), None)
        if task:
            provider = next(
                (p for p in config.ai_providers if p.name == task.provider_name), None)
            if provider:
                return provider
        if config.ai_providers:
            return config.ai_providers[0]
        raise RuntimeError("No AI provider configured. Add one in Settings > AI Providers.")

    def _call_ai(self, client: OpenAI, model: str, title: str,
                 clean_text: str, ancestors: list) -> dict:
        """Call AI to extract structured data from a single page."""
        breadcrumb = " > ".join(a["title"] for a in ancestors) if ancestors else ""
        user_content = f"Document title: {title}\n"
        if breadcrumb:
            user_content += f"Location: {breadcrumb} > {title}\n"
        user_content += f"\n---\n\n{clean_text}"

        # Truncate very long pages to stay within context limits
        if len(user_content) > 30000:
            user_content = user_content[:30000] + "\n\n[... truncated ...]"

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.1,
            max_tokens=4000,
        )

        raw = response.choices[0].message.content.strip()

        # Strip markdown fences if the model wraps them
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0].strip()

        return json.loads(raw)
