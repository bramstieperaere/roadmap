import base64
import os
from pathlib import Path

import yaml

from app.models import AppConfig
from app.crypto import encrypt_value, decrypt_value, is_encrypted, generate_salt
from app.session import session

CONFIG_PATH = Path(os.environ.get("ROADMAP_CONFIG_PATH", Path(__file__).parent.parent / "config.yaml"))


def load_config() -> AppConfig:
    if not CONFIG_PATH.exists():
        config = AppConfig()
        save_config(config)
        return config
    with open(CONFIG_PATH) as f:
        data = yaml.safe_load(f) or {}
    return AppConfig(**data)


def load_config_decrypted() -> AppConfig:
    config = load_config()
    key = session.get_key()
    if key is None:
        return config
    if is_encrypted(config.neo4j.password):
        config.neo4j.password = decrypt_value(config.neo4j.password, key)
    if is_encrypted(config.atlassian.api_token):
        config.atlassian.api_token = decrypt_value(config.atlassian.api_token, key)
    if is_encrypted(config.atlassian.bitbucket_app_password):
        config.atlassian.bitbucket_app_password = decrypt_value(config.atlassian.bitbucket_app_password, key)
    for provider in config.ai_providers:
        if is_encrypted(provider.api_key):
            provider.api_key = decrypt_value(provider.api_key, key)
    if is_encrypted(config.whisper.api_key):
        config.whisper.api_key = decrypt_value(config.whisper.api_key, key)
    if is_encrypted(config.logzio.api_token):
        config.logzio.api_token = decrypt_value(config.logzio.api_token, key)
    return config


def _normalize_path(p: str) -> str:
    """Normalize a filesystem path to the OS-native separator."""
    if not p:
        return p
    return str(Path(p))


def save_config(config: AppConfig) -> None:
    for repo in config.repositories:
        repo.path = _normalize_path(repo.path)
        for mod in repo.modules:
            mod.relative_path = _normalize_path(mod.relative_path)
    config.atlassian.cache_dir = _normalize_path(config.atlassian.cache_dir)
    key = session.get_key()
    if key:
        if not config.encryption_salt:
            salt = generate_salt()
            config.encryption_salt = base64.b64encode(salt).decode()
        if not is_encrypted(config.neo4j.password):
            config.neo4j.password = encrypt_value(config.neo4j.password, key)
        if config.atlassian.api_token and not is_encrypted(config.atlassian.api_token):
            config.atlassian.api_token = encrypt_value(config.atlassian.api_token, key)
        if config.atlassian.bitbucket_app_password and not is_encrypted(config.atlassian.bitbucket_app_password):
            config.atlassian.bitbucket_app_password = encrypt_value(config.atlassian.bitbucket_app_password, key)
        for provider in config.ai_providers:
            if not is_encrypted(provider.api_key):
                provider.api_key = encrypt_value(provider.api_key, key)
        if config.whisper.api_key and not is_encrypted(config.whisper.api_key):
            config.whisper.api_key = encrypt_value(config.whisper.api_key, key)
        if config.logzio.api_token and not is_encrypted(config.logzio.api_token):
            config.logzio.api_token = encrypt_value(config.logzio.api_token, key)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, sort_keys=False)


def has_encrypted_fields() -> bool:
    if not CONFIG_PATH.exists():
        return False
    with open(CONFIG_PATH) as f:
        content = f.read()
    return "ENC(" in content
