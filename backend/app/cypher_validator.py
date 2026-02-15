import re

_WRITE_KEYWORDS = re.compile(
    r'\b(CREATE|MERGE|DELETE|DETACH\s+DELETE|SET|REMOVE|DROP|CALL\s*\{)',
    re.IGNORECASE,
)


def validate_read_only(cypher: str) -> tuple[bool, str]:
    """Return (is_safe, error_message). is_safe=True means read-only."""
    stripped = re.sub(r"'[^']*'", "''", cypher)
    stripped = re.sub(r'"[^"]*"', '""', stripped)
    match = _WRITE_KEYWORDS.search(stripped)
    if match:
        return False, f"Query contains write operation: {match.group()}"
    return True, ""
