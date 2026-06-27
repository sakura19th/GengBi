"""加密基础设施。

使用 ``cryptography`` 库实现 API Key 的加密存储：
- PBKDF2HMAC 密钥派生（随机 salt + 机器 ID passphrase）
- Fernet 对称加密
- 密钥派生结果缓存（避免重复开销）
- 加密密钥导出/导入（密码保护文件，用于换机器迁移）
- 导入密钥到新机器后用新机器 ID 重新加密所有 API Key

passphrase = 机器 ID + 用户名，确保不同机器/用户的密钥不同。
salt 随机生成并存储到配置文件中。
"""
from __future__ import annotations

import base64
import getpass
import logging
import os
import platform
import socket
import threading
import uuid
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)

# PBKDF2 迭代次数（NIST 推荐 >= 600000，兼顾安全与性能）
PBKDF2_ITERATIONS = 600_000
# 派生密钥长度（Fernet 需要 32 字节 = 256 位密钥）
KEY_LENGTH = 32


def get_machine_id() -> str:
    """获取机器唯一标识。

    优先级：
    1. 配置目录中持久化的 ``.machine_id``（fallback 时生成并保存，确保跨进程稳定）
    2. Linux: /etc/machine-id 或 /var/lib/dbus/machine-id
    3. 跨平台 fallback: uuid.getnode()（MAC 地址）+ 平台信息（首次生成后持久化）

    Returns:
        机器 ID 字符串
    """
    # 解析配置目录路径（失败时跳过持久化逻辑，降级到现有路径）
    try:
        from novelforge.core.storage import get_default_storage_path

        machine_id_file = get_default_storage_path() / ".machine_id"
    except Exception:
        machine_id_file = None

    # 1. 优先读取持久化的 fallback ID（保证跨进程稳定，避免 uuid.getnode() 返回随机值）
    if machine_id_file is not None:
        try:
            if machine_id_file.exists():
                persisted = machine_id_file.read_text(encoding="utf-8").strip()
                if persisted:
                    return persisted
        except Exception:
            # 读取失败时降级到下述路径
            pass

    # 2. Linux 机器 ID
    for path in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                machine_id = f.read().strip()
                if machine_id:
                    return machine_id
        except (OSError, FileNotFoundError):
            continue

    # 3. 跨平台 fallback：MAC 地址 + 主机名 + 平台
    mac = uuid.getnode()
    hostname = socket.gethostname()
    machine_id = f"{mac}-{hostname}-{platform.system()}-{platform.machine()}"

    # 持久化 fallback ID，确保后续调用返回相同值（uuid.getnode() 在获取不到真实 MAC 时返回随机值）
    if machine_id_file is not None:
        try:
            machine_id_file.parent.mkdir(parents=True, exist_ok=True)
            machine_id_file.write_text(machine_id, encoding="utf-8")
        except Exception:
            # 持久化失败不影响本次返回值，但下次调用仍可能走 fallback
            pass

    return machine_id


def get_username() -> str:
    """获取当前用户名。"""
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER", "unknown")


def get_passphrase() -> str:
    """生成加密 passphrase = 机器 ID + 用户名。"""
    return f"{get_machine_id()}:{get_username()}"


def derive_key(salt: bytes, passphrase: str) -> bytes:
    """使用 PBKDF2HMAC 从 passphrase 派生 Fernet 密钥。

    Args:
        salt: 随机盐（16 字节以上）
        passphrase: 密码短语（机器 ID + 用户名）

    Returns:
        base64 编码的 32 字节 Fernet 密钥
    """
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=KEY_LENGTH,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    key = kdf.derive(passphrase.encode("utf-8"))
    return base64.urlsafe_b64encode(key)


class CryptoManager:
    """加密管理器。

    封装 Fernet 加密/解密操作，缓存派生密钥避免重复开销。
    支持密钥导出/导入，用于换机器迁移。

    Usage:
        manager = CryptoManager(salt, passphrase)
        encrypted = manager.encrypt("sk-xxx")
        decrypted = manager.decrypt(encrypted)
    """

    # 密钥派生缓存：{(salt_hex, passphrase): key_bytes}
    _key_cache: dict[tuple[str, str], bytes] = {}
    # 缓存锁：保护跨实例的 _key_cache 读写，避免多线程派生竞态
    _key_cache_lock: threading.Lock = threading.Lock()

    def __init__(self, salt: bytes, passphrase: str | None = None) -> None:
        """初始化加密管理器。

        Args:
            salt: 随机盐（16 字节以上）
            passphrase: 密码短语，默认使用机器 ID + 用户名
        """
        if len(salt) < 16:
            raise ValueError("salt 长度必须 >= 16 字节")

        self.salt = salt
        self.passphrase = passphrase or get_passphrase()

        # 从缓存获取或派生密钥
        cache_key = (salt.hex(), self.passphrase)
        with self._key_cache_lock:
            if cache_key not in self._key_cache:
                logger.debug("派生加密密钥（PBKDF2HMAC, %d 次迭代）", PBKDF2_ITERATIONS)
                self._key_cache[cache_key] = derive_key(salt, self.passphrase)
            else:
                logger.debug("使用缓存的加密密钥")
            self._key = self._key_cache[cache_key]
        self._fernet = Fernet(self._key)

    @classmethod
    def clear_cache(cls) -> None:
        """清除内存中的密钥派生缓存。

        用于在敏感操作后清空内存中的派生密钥，或强制下次重新派生。
        """
        with cls._key_cache_lock:
            cls._key_cache.clear()

    def encrypt(self, plaintext: str) -> str:
        """加密明文字符串。

        Args:
            plaintext: 明文

        Returns:
            加密后的字符串（base64 编码）
        """
        if not plaintext:
            return ""
        token = self._fernet.encrypt(plaintext.encode("utf-8"))
        return token.decode("utf-8")

    def decrypt(self, ciphertext: str) -> str:
        """解密字符串。

        Args:
            ciphertext: 加密字符串（base64 编码）

        Returns:
            明文字符串

        Raises:
            InvalidToken: 密钥不匹配或数据损坏
        """
        if not ciphertext:
            return ""
        try:
            token = self._fernet.decrypt(ciphertext.encode("utf-8"))
            return token.decode("utf-8")
        except InvalidToken as e:
            logger.error("解密失败：密钥不匹配或数据损坏")
            raise

    def export_key(self, password: str, file_path: Path) -> None:
        """导出加密密钥到密码保护文件。

        用于换机器迁移：用用户输入的密码加密当前密钥，导出到文件。
        新机器导入时用相同密码解密获取原始密钥。

        Args:
            password: 用户设置的导出密码
            file_path: 导出文件路径
        """
        # 用密码派生新密钥加密当前密钥
        export_salt = os.urandom(16)
        export_key = derive_key(export_salt, password)
        export_fernet = Fernet(export_key)

        # 加密内容：原始密钥 + salt（新机器需要 salt 来重新加密）
        payload = {
            "key": self._key.decode("utf-8"),
            "salt": base64.b64encode(self.salt).decode("utf-8"),
        }
        import json

        encrypted_payload = export_fernet.encrypt(
            json.dumps(payload).encode("utf-8")
        )

        file_path = Path(file_path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        # 文件格式：salt(16B) + encrypted_payload
        with open(file_path, "wb") as f:
            f.write(export_salt)
            f.write(encrypted_payload)

        logger.info("加密密钥已导出到 %s", file_path)

    @classmethod
    def import_key(cls, password: str, file_path: Path) -> tuple[bytes, bytes]:
        """从密码保护文件导入加密密钥。

        导入后调用方需用新机器的 salt 重新加密所有 API Key。

        Args:
            password: 导出时设置的密码
            file_path: 导入文件路径

        Returns:
            (原始密钥, 原始 salt) 元组

        Raises:
            InvalidToken: 密码错误或文件损坏
        """
        file_path = Path(file_path)
        with open(file_path, "rb") as f:
            export_salt = f.read(16)
            encrypted_payload = f.read()

        export_key = derive_key(export_salt, password)
        export_fernet = Fernet(export_key)

        import json

        payload_bytes = export_fernet.decrypt(encrypted_payload)
        payload = json.loads(payload_bytes.decode("utf-8"))

        original_key = payload["key"].encode("utf-8")
        original_salt = base64.b64decode(payload["salt"])

        logger.info("加密密钥已从 %s 导入", file_path)
        return original_key, original_salt

    @staticmethod
    def generate_salt() -> bytes:
        """生成随机 salt（16 字节）。"""
        return os.urandom(16)


def re_encrypt_api_keys(
    old_manager: CryptoManager, new_manager: CryptoManager, api_keys_encrypted: list[str]
) -> list[str]:
    """用新加密管理器重新加密所有 API Key。

    用于换机器迁移：导入密钥后，用新机器 ID 重新加密所有 API Key。

    Args:
        old_manager: 旧加密管理器（用于解密）
        new_manager: 新加密管理器（用于加密）
        api_keys_encrypted: 加密的 API Key 列表

    Returns:
        重新加密后的 API Key 列表
    """
    re_encrypted: list[str] = []
    for encrypted_key in api_keys_encrypted:
        if not encrypted_key:
            re_encrypted.append("")
            continue
        try:
            plaintext = old_manager.decrypt(encrypted_key)
            re_encrypted.append(new_manager.encrypt(plaintext))
        except InvalidToken as e:
            logger.error("重新加密 API Key 失败（解密失败）: %s", e)
            # 保留原始加密字符串，避免数据丢失（无法解密时不应替换为空串）
            re_encrypted.append(encrypted_key)
    return re_encrypted
