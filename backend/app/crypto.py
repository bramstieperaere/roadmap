import base64
import os
import re

from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes

ENC_PATTERN = re.compile(r"^ENC\((.+)\)$")


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(password.encode()))


def generate_salt() -> bytes:
    return os.urandom(16)


def encrypt_value(value: str, key: bytes) -> str:
    f = Fernet(key)
    encrypted = f.encrypt(value.encode())
    return f"ENC({base64.b64encode(encrypted).decode()})"


def decrypt_value(enc_value: str, key: bytes) -> str:
    match = ENC_PATTERN.match(enc_value)
    if not match:
        return enc_value
    encrypted = base64.b64decode(match.group(1))
    f = Fernet(key)
    return f.decrypt(encrypted).decode()


def is_encrypted(value: str) -> bool:
    return bool(ENC_PATTERN.match(value))
