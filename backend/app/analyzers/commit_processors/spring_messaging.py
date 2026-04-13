"""Spring messaging processor — extracts JMS, Kafka, and Rabbit listeners and senders."""

import re

from . import CommitProcessor
from .git_utils import git_show_file

_PATH_PATTERNS = [
    re.compile(r"\.java$", re.IGNORECASE),
]

_CLASS_RE = re.compile(r"\bclass\s+(\w+)")

# Incoming: listener annotations
_JMS_LISTENER_RE = re.compile(
    r'@JmsListener\s*\([^)]*destination\s*=\s*"([^"]+)"')
_RABBIT_LISTENER_RE = re.compile(
    r'@RabbitListener\s*\([^)]*queues?\s*=\s*"([^"]+)"')
_KAFKA_LISTENER_RE = re.compile(
    r'@KafkaListener\s*\([^)]*topics?\s*=\s*"([^"]+)"')

# Also detect listener via constant references
_JMS_LISTENER_CONST_RE = re.compile(
    r'@JmsListener\s*\([^)]*destination\s*=\s*(\w+(?:\.\w+)*)\s*\)')
_RABBIT_LISTENER_CONST_RE = re.compile(
    r'@RabbitListener\s*\([^)]*queues?\s*=\s*(\w+(?:\.\w+)*)\s*\)')
_KAFKA_LISTENER_CONST_RE = re.compile(
    r'@KafkaListener\s*\([^)]*topics?\s*=\s*(\w+(?:\.\w+)*)\s*\)')

# Outgoing: template send patterns
_JMS_SEND_RE = re.compile(
    r'jmsTemplate\s*\.\s*(?:send|convertAndSend)\s*\(\s*"([^"]+)"')
_RABBIT_SEND_RE = re.compile(
    r'rabbitTemplate\s*\.\s*(?:send|convertAndSend|convertSendAndReceive)\s*\(\s*"([^"]+)"')
_KAFKA_SEND_RE = re.compile(
    r'kafkaTemplate\s*\.\s*send\s*\(\s*"([^"]+)"')

# Detect if file is relevant (has any messaging code)
_HAS_MESSAGING_RE = re.compile(
    r"@(?:Jms|Rabbit|Kafka)Listener\b|"
    r"(?:jms|rabbit|kafka)Template\b|"
    r"JmsTemplate\b|RabbitTemplate\b|KafkaTemplate\b",
    re.IGNORECASE,
)

# Method name after listener annotation
_METHOD_RE = re.compile(
    r"(?:public|protected|private|void)\s+\S*\s*(\w+)\s*\("
)


def _parse_messaging(content: str) -> dict | None:
    """Parse a Java file for messaging listeners and senders."""
    if not _HAS_MESSAGING_RE.search(content):
        return None

    class_match = _CLASS_RE.search(content)
    class_name = class_match.group(1) if class_match else "?"

    incoming: list[dict] = []
    outgoing: list[dict] = []

    lines = content.split("\n")
    for i, line in enumerate(lines):
        # JMS listeners
        for m in _JMS_LISTENER_RE.finditer(line):
            handler = _find_method_name(lines, i)
            incoming.append({"type": "jms", "destination": m.group(1),
                           "handler": handler})
        for m in _JMS_LISTENER_CONST_RE.finditer(line):
            if not _JMS_LISTENER_RE.search(line):  # avoid double-match
                handler = _find_method_name(lines, i)
                incoming.append({"type": "jms", "destination": m.group(1),
                               "handler": handler, "constant": True})

        # Rabbit listeners
        for m in _RABBIT_LISTENER_RE.finditer(line):
            handler = _find_method_name(lines, i)
            incoming.append({"type": "rabbit", "destination": m.group(1),
                           "handler": handler})
        for m in _RABBIT_LISTENER_CONST_RE.finditer(line):
            if not _RABBIT_LISTENER_RE.search(line):
                handler = _find_method_name(lines, i)
                incoming.append({"type": "rabbit", "destination": m.group(1),
                               "handler": handler, "constant": True})

        # Kafka listeners
        for m in _KAFKA_LISTENER_RE.finditer(line):
            handler = _find_method_name(lines, i)
            incoming.append({"type": "kafka", "destination": m.group(1),
                           "handler": handler})
        for m in _KAFKA_LISTENER_CONST_RE.finditer(line):
            if not _KAFKA_LISTENER_RE.search(line):
                handler = _find_method_name(lines, i)
                incoming.append({"type": "kafka", "destination": m.group(1),
                               "handler": handler, "constant": True})

        # Outgoing sends
        for m in _JMS_SEND_RE.finditer(line):
            outgoing.append({"type": "jms", "destination": m.group(1)})
        for m in _RABBIT_SEND_RE.finditer(line):
            outgoing.append({"type": "rabbit", "destination": m.group(1)})
        for m in _KAFKA_SEND_RE.finditer(line):
            outgoing.append({"type": "kafka", "destination": m.group(1)})

    if not incoming and not outgoing:
        return None

    return {
        "class": class_name,
        "incoming": incoming,
        "outgoing": outgoing,
    }


def _find_method_name(lines: list[str], annotation_line: int) -> str:
    """Find the method name after an annotation line."""
    for j in range(annotation_line, min(annotation_line + 5, len(lines))):
        m = _METHOD_RE.search(lines[j])
        if m:
            return m.group(1)
    return "?"


class SpringMessagingProcessor(CommitProcessor):
    name = "spring_messaging"
    label = "Spring Messaging"
    description = "Extracts JMS, Kafka, and RabbitMQ listeners (incoming) and senders (outgoing)"
    node_property = "spring_messaging"

    def detect(self, files_changed: list[str]) -> list[str]:
        matched = []
        for f in files_changed:
            for pat in _PATH_PATTERNS:
                if pat.search(f):
                    matched.append(f)
                    break
        return matched

    def process(self, repo_path: str, full_hash: str,
                matched_files: list[str],
                parent_full_hash: str | None = None) -> dict | None:
        components = []
        processed_files = []

        for file_path in matched_files:
            content = git_show_file(repo_path, full_hash, file_path)
            if not content:
                continue

            result = _parse_messaging(content)
            if result:
                result["file"] = file_path
                components.append(result)
                processed_files.append(file_path)

        if not components:
            return None

        total_in = sum(len(c["incoming"]) for c in components)
        total_out = sum(len(c["outgoing"]) for c in components)

        return {
            "files": processed_files,
            "components": components,
            "incoming_count": total_in,
            "outgoing_count": total_out,
        }
