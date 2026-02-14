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
    for provider in config.ai_providers:
        if is_encrypted(provider.api_key):
            provider.api_key = decrypt_value(provider.api_key, key)
    return config


def save_config(config: AppConfig) -> None:
    key = session.get_key()
    if key:
        if not config.encryption_salt:
            salt = generate_salt()
            config.encryption_salt = base64.b64encode(salt).decode()
        if not is_encrypted(config.neo4j.password):
            config.neo4j.password = encrypt_value(config.neo4j.password, key)
        for provider in config.ai_providers:
            if not is_encrypted(provider.api_key):
                provider.api_key = encrypt_value(provider.api_key, key)
    with open(CONFIG_PATH, "w") as f:
        yaml.dump(config.model_dump(), f, default_flow_style=False, sort_keys=False)


def has_encrypted_fields() -> bool:
    if not CONFIG_PATH.exists():
        return False
    with open(CONFIG_PATH) as f:
        content = f.read()
    return "ENC(" in content
