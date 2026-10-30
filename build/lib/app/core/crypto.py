"""Шифрование AI-ключей пользователей (REQ-007)."""

from cryptography.fernet import Fernet, InvalidToken


class InvalidKeyError(Exception):
    """Не удалось расшифровать: другой мастер-ключ или повреждённые данные."""


def generate_master_key() -> str:
    return Fernet.generate_key().decode()


class KeyCipher:
    def __init__(self, master_key: str) -> None:
        try:
            self._fernet = Fernet(master_key.encode() if isinstance(master_key, str) else master_key)
        except (ValueError, TypeError) as e:
            raise ValueError("MASTER_KEY должен быть ключом Fernet (urlsafe base64, 32 байта)") from e

    def encrypt(self, plain: str) -> str:
        return self._fernet.encrypt(plain.encode()).decode()

    def decrypt(self, token: str) -> str:
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as e:
            raise InvalidKeyError("ключ не расшифровывается") from e

    def __repr__(self) -> str:
        return "KeyCipher(***)"


def mask_key(key: str) -> str:
    if not key or len(key) < 8:
        return "***"
    return f"{key[:3]}…{key[-4:]}"
