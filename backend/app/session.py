from typing import Optional


class SessionStore:
    def __init__(self):
        self._key: Optional[bytes] = None

    def set_key(self, key: bytes) -> None:
        self._key = key

    def get_key(self) -> Optional[bytes]:
        return self._key

    def clear_key(self) -> None:
        self._key = None

    def is_unlocked(self) -> bool:
        return self._key is not None


session = SessionStore()
