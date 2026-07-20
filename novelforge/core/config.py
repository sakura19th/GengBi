"""全局配置管理。

定义全局配置 JSON 结构并管理配置的加载、保存、迁移与损坏恢复。

配置文件路径：``~/.novelforge/config.json``

配置结构：
    {
        "config_version": 1,
        "api_endpoints": [
            {"id": str, "name": str, "base_url": str,
             "api_key_encrypted": str, "default_model": str,
             "models": list[str],  # 全部已获取模型列表
             "enabled_models": list[str],  # 续写面板可选模型子集；default_model 为后台流程回退（自动取首个已启用）
             "reasoning_effort": str}
        ],
        "default_endpoint_id": str,
        "appearance": {
            "theme": "dark"|"light"|"system",
            "font_family": str,
            "font_size": int,
            "line_height": float
        },
        "continuation": {
            "default_lookback_chapters": 5,
            "default_target_words": int,
            "default_temperature": float,
            "default_max_tokens": int,
            "auto_save": bool,
            "show_token_count": bool,
            "selected_worldbook_ids": list[str],  # 续写面板多选世界书，跨会话恢复
            "last_endpoint_id": str,  # 续写面板上次选中的端点 ID，跨会话恢复（面板优先于流程配置）
            "last_model_per_endpoint": dict[str, str]  # 每端点上次选中的模型，跨会话恢复（面板优先于流程配置）
        },
        "volume": {},  # 卷续写配置持久化（VolumeRunConfig 的 JSON dump）
        "context_extract": {
            "cache_enabled": true,
            "cache_ttl_hours": 24,
            "extractor_prompt_override": null,
            "lookback_chapters": 5,
            "token_limit": 0
        },
        "data": {"storage_path": "~/.novelforge"},
        "crypto_salt": str,  # base64 编码的加密 salt
        "privacy_accepted": bool
    }
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

from cryptography.fernet import InvalidToken

from novelforge.core.crypto import CryptoManager
from novelforge.core.migration import CURRENT_CONFIG_VERSION, migrate_config
from novelforge.core.storage import (
    atomic_write_file,
    backup_file,
    get_default_storage_path,
    load_json_with_recovery,
)
from novelforge.utils.paths import secure_file

logger = logging.getLogger(__name__)

# 流程默认破限等级：提取类默认 low，其余 off；正文流程由预设控制
FLOW_DEFAULT_JAILBREAKS: dict[str, str] = {
    "single_continuation": "off",
    "volume_continuation": "off",
    "single_audit": "off",
    "rewrite_analysis": "off",
    "context_extraction": "low",
    "ontology_extraction": "low",
    "protagonist_extraction": "low",
    "style_extraction": "low",
    "custom_rule_parsing": "off",
    "writing_element_analysis": "low",
    "writing_element_refinement": "low",
}


def get_default_config() -> dict[str, Any]:
    """返回默认配置字典。"""
    return {
        "config_version": CURRENT_CONFIG_VERSION,
        "api_endpoints": [],
        "default_endpoint_id": "",
        "flow_endpoints": {},  # {flow_key: endpoint_id}，未配置的流程回退默认端点
        "flow_models": {},  # {flow_key: model_str}，未配置或空串回退端点 default_model
        "flow_jailbreaks": {},  # {flow_key: level_str}，未配置回退 FLOW_DEFAULT_JAILBREAKS
        "flow_jailbreaks_custom": {},  # {flow_key: custom_text}，空串=用等级模板
        "appearance": {
            "theme": "dark",
            "font_family": "Microsoft YaHei",
            "font_size": 14,
            "line_height": 1.6,
        },
        "continuation": {
            "default_lookback_chapters": 5,
            "default_target_words": 2000,
            "default_temperature": 0.8,
            "auto_save": True,
            "show_token_count": True,
            "selected_worldbook_ids": [],
        },
        "volume": {},
        "context_extract": {
            "cache_enabled": True,
            "cache_ttl_hours": 24,
            "extractor_prompt_override": None,
            "lookback_chapters": 5,
            "token_limit": 0,
        },
        "data": {
            "storage_path": str(get_default_storage_path()),
        },
        "crypto_salt": "",
        "privacy_accepted": False,
    }


class ConfigManager:
    """配置管理器。

    负责配置的加载、保存、迁移、损坏恢复，以及 API Key 的加密/解密。

    Usage:
        manager = ConfigManager()
        manager.load()
        endpoint = manager.get_endpoint("ep1")
        api_key = manager.decrypt_api_key("ep1")
    """

    def __init__(self, config_path: Path | None = None) -> None:
        """初始化配置管理器。

        Args:
            config_path: 配置文件路径，默认 ``~/.novelforge/config.json``
        """
        if config_path is None:
            config_path = get_default_storage_path() / "config.json"
        self.config_path: Path = Path(config_path)
        self.config: dict[str, Any] = get_default_config()
        self._crypto_manager: CryptoManager | None = None
        # 线程安全锁（RLock 可重入，允许持锁方法调用其他持锁方法）
        self._lock = threading.RLock()
        # 批量保存模式标记
        self._batch_mode = False

    def load(self) -> None:
        """加载配置。

        流程：
        1. 尝试读取配置文件（支持 .bak 恢复）
        2. 若文件不存在，使用默认配置并保存
        3. 若文件损坏且 .bak 也损坏，使用默认配置（UI 弹窗提示）
        4. 检查 config_version，执行迁移
        """
        if not self.config_path.exists():
            logger.info("配置文件不存在，使用默认配置: %s", self.config_path)
            self.config = get_default_config()
            self._init_crypto_salt()
            self.save()
            return

        data, error = load_json_with_recovery(self.config_path)
        if error is not None:
            logger.error("配置加载失败: %s，使用默认配置", error)
            self.config = get_default_config()
            self._init_crypto_salt()
            self._load_error = error
            return

        # 合并默认配置（确保新字段存在）
        self.config = self._merge_defaults(data)
        self._load_error = None

        # 初始化加密 salt
        self._init_crypto_salt()

        # 执行版本迁移
        version = self.config.get("config_version", 1)
        if version < CURRENT_CONFIG_VERSION:
            logger.info("检测到旧配置版本 v%d，开始迁移", version)
            self.config = migrate_config(self.config, self.config_path)
            self.save()

    def save(self) -> None:
        """保存配置到文件（写入前 .bak 备份）。"""
        # 批量模式下不立即写入磁盘，等待 commit_batch
        if self._batch_mode:
            return
        with self._lock:
            self.config_path.parent.mkdir(parents=True, exist_ok=True)
            backup_file(self.config_path)
            content = json.dumps(self.config, ensure_ascii=False, indent=2)
            atomic_write_file(self.config_path, content)
            # 收紧权限：config.json 含加密 salt 与 API Key 密文，仅所有者可读写
            secure_file(self.config_path)
            logger.debug("配置已保存到 %s", self.config_path)

    def get_load_error(self) -> str | None:
        """获取上次加载错误信息（用于 UI 弹窗提示）。"""
        return getattr(self, "_load_error", None)

    @staticmethod
    def _merge_defaults(loaded: dict[str, Any]) -> dict[str, Any]:
        """将加载的配置与默认配置合并，确保新字段存在。"""
        defaults = get_default_config()
        result = defaults.copy()
        for key, value in loaded.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                merged = result[key].copy()
                merged.update(value)
                result[key] = merged
            else:
                result[key] = value
        return result

    # ===== 加密相关 =====

    def _init_crypto_salt(self) -> None:
        """初始化加密 salt，若不存在则生成。"""
        salt_str = self.config.get("crypto_salt", "")
        if not salt_str:
            salt = CryptoManager.generate_salt()
            self.config["crypto_salt"] = base64.b64encode(salt).decode("utf-8")
            logger.info("生成新的加密 salt")
        # salt 已存在时不覆盖，保证已有 API Key 可解密

    def get_crypto_manager(self) -> CryptoManager:
        """获取加密管理器（单例缓存）。

        使用双重检查锁定（double-checked locking）：先无锁快速判断，
        再加锁二次检查后创建，避免并发场景下创建多个管理器实例。
        """
        # 快速路径：无锁判断（已存在则直接返回）
        if self._crypto_manager is not None:
            return self._crypto_manager
        # 持锁创建，二次检查防止并发重复创建
        with self._lock:
            if self._crypto_manager is not None:
                return self._crypto_manager
            salt_str = self.config.get("crypto_salt", "")
            if not salt_str:
                # 重新初始化（防御性）
                self._init_crypto_salt()
                salt_str = self.config["crypto_salt"]
            salt = base64.b64decode(salt_str)
            self._crypto_manager = CryptoManager(salt)
            return self._crypto_manager

    def encrypt_api_key(self, api_key: str) -> str:
        """加密 API Key。"""
        return self.get_crypto_manager().encrypt(api_key)

    def decrypt_api_key(self, endpoint_id: str) -> str:
        """解密指定端点的 API Key。

        Args:
            endpoint_id: API 端点 ID

        Returns:
            解密后的 API Key，端点不存在时返回空字符串
        """
        endpoint = self.get_endpoint(endpoint_id)
        if endpoint is None:
            return ""
        encrypted = endpoint.get("api_key_encrypted", "")
        if not encrypted:
            return ""
        try:
            return self.get_crypto_manager().decrypt(encrypted)
        except (InvalidToken, ValueError, TypeError) as e:
            logger.error("解密 API Key 失败: %s", e, exc_info=True)
            return ""

    # ===== API 端点管理 =====

    def get_endpoints(self) -> list[dict[str, Any]]:
        """获取所有 API 端点。"""
        with self._lock:
            return list(self.config.get("api_endpoints", []))

    def get_endpoint(self, endpoint_id: str) -> dict[str, Any] | None:
        """获取指定 API 端点。"""
        with self._lock:
            for ep in self.config.get("api_endpoints", []):
                if ep.get("id") == endpoint_id:
                    return ep
        return None

    def get_default_endpoint(self) -> dict[str, Any] | None:
        """获取默认 API 端点。"""
        default_id = self.config.get("default_endpoint_id", "")
        if default_id:
            return self.get_endpoint(default_id)
        endpoints = self.get_endpoints()
        return endpoints[0] if endpoints else None

    def add_endpoint(self, endpoint: dict[str, Any]) -> None:
        """添加 API 端点。

        若提供 ``api_key`` 明文字段，自动加密为 ``api_key_encrypted``。
        """
        with self._lock:
            endpoint = dict(endpoint)
            # 明文 API Key 加密
            if "api_key" in endpoint:
                endpoint["api_key_encrypted"] = self.encrypt_api_key(endpoint.pop("api_key"))
            endpoint.setdefault("id", f"ep_{os.urandom(4).hex()}")
            endpoint.setdefault("name", "")
            endpoint.setdefault("base_url", "")
            endpoint.setdefault("api_key_encrypted", "")
            endpoint.setdefault("default_model", "")
            endpoint.setdefault("models", [])
            endpoint.setdefault("enabled_models", [])
            endpoint.setdefault("reasoning_effort", "")
            endpoint.setdefault("extra_payload", {})
            endpoint.setdefault("extra_headers", {})

            self.config.setdefault("api_endpoints", []).append(endpoint)
            # 若无默认端点，设为第一个
            if not self.config.get("default_endpoint_id"):
                self.config["default_endpoint_id"] = endpoint["id"]
            self.save()

    def update_endpoint(self, endpoint_id: str, updates: dict[str, Any]) -> None:
        """更新 API 端点。

        若 updates 含 ``api_key`` 明文字段，自动加密。
        """
        with self._lock:
            for ep in self.config.get("api_endpoints", []):
                if ep.get("id") == endpoint_id:
                    if "api_key" in updates:
                        ep["api_key_encrypted"] = self.encrypt_api_key(updates.pop("api_key"))
                    ep.update(updates)
                    self.save()
                    return

    def remove_endpoint(self, endpoint_id: str) -> None:
        """删除 API 端点。"""
        with self._lock:
            endpoints = self.config.get("api_endpoints", [])
            self.config["api_endpoints"] = [
                ep for ep in endpoints if ep.get("id") != endpoint_id
            ]
            if self.config.get("default_endpoint_id") == endpoint_id:
                remaining = self.config["api_endpoints"]
                self.config["default_endpoint_id"] = remaining[0]["id"] if remaining else ""
            self.save()

    def set_default_endpoint(self, endpoint_id: str) -> None:
        """设置默认 API 端点。"""
        with self._lock:
            self.config["default_endpoint_id"] = endpoint_id
            self.save()

    # ===== 流程端点配置 =====

    def get_flow_endpoints(self) -> dict[str, str]:
        """获取流程端点映射 ``{flow_key: endpoint_id}``，未配置返回 ``{}``。"""
        return self.config.get("flow_endpoints", {})

    def get_flow_endpoint(self, flow_key: str) -> dict[str, Any] | None:
        """解析流程端点。

        若 ``flow_endpoints[flow_key]`` 存在且对应端点仍存在则返回该端点，
        否则回退到默认端点。

        Args:
            flow_key: 流程标识（如 ``single_continuation``/``context_extraction``）

        Returns:
            端点字典，无可用端点时返回 None
        """
        mapping = self.config.get("flow_endpoints", {})
        ep_id = mapping.get(flow_key, "")
        if ep_id:
            ep = self.get_endpoint(ep_id)
            if ep:
                return ep
        return self.get_default_endpoint()

    def set_flow_endpoints(self, mapping: dict[str, str]) -> None:
        """保存流程端点映射并持久化。

        Args:
            mapping: ``{flow_key: endpoint_id}``，endpoint_id 为空串表示用默认端点
        """
        with self._lock:
            self.config["flow_endpoints"] = mapping
            self.save()

    # ===== 流程模型配置 =====

    def get_flow_models(self) -> dict[str, str]:
        """获取流程模型映射 ``{flow_key: model_str}``，未配置返回 ``{}``。"""
        return self.config.get("flow_models", {})

    def get_flow_model(self, flow_key: str) -> str:
        """解析流程使用的模型。

        优先读 ``flow_models[flow_key]``，非空则返回；否则回退到
        ``get_flow_endpoint(flow_key)`` 的 ``default_model``；端点也无则返回空串。

        Args:
            flow_key: 流程标识（如 ``single_continuation``/``context_extraction``）；
                空串则用默认端点的 default_model

        Returns:
            模型名字符串，空串表示无可用模型
        """
        mapping = self.config.get("flow_models", {})
        model = mapping.get(flow_key, "")
        if model:
            return model
        ep = (
            self.get_flow_endpoint(flow_key)
            if flow_key
            else self.get_default_endpoint()
        )
        return ep.get("default_model", "") if ep else ""

    def set_flow_models(self, mapping: dict[str, str]) -> None:
        """保存流程模型映射并持久化。

        Args:
            mapping: ``{flow_key: model_str}``，model_str 为空串表示用端点 default_model
        """
        with self._lock:
            self.config["flow_models"] = mapping
            self.save()

    # ===== 流程破限配置 =====

    def get_flow_jailbreaks(self) -> dict[str, str]:
        """获取流程破限等级映射 ``{flow_key: level}``，未配置返回 ``{}``。"""
        return self.config.get("flow_jailbreaks", {})

    def get_flow_jailbreak(self, flow_key: str) -> str:
        """解析流程破限等级。

        优先读 ``flow_jailbreaks[flow_key]``，未配置回退到
        ``FLOW_DEFAULT_JAILBREAKS``（提取类默认 low，其余 off）。

        Args:
            flow_key: 流程标识

        Returns:
            破限等级（``off``/``low``/``mid``/``high``）
        """
        mapping = self.config.get("flow_jailbreaks", {})
        return mapping.get(flow_key) or FLOW_DEFAULT_JAILBREAKS.get(flow_key, "off")

    def set_flow_jailbreaks(self, mapping: dict[str, str]) -> None:
        """保存流程破限等级映射并持久化。

        Args:
            mapping: ``{flow_key: level}``，level 为 ``off``/``low``/``mid``/``high``/
                ``custom``（custom 表示用自定义文本，等级文本忽略）
        """
        with self._lock:
            self.config["flow_jailbreaks"] = mapping
            self.save()

    def get_flow_jailbreak_custom(self, flow_key: str) -> str:
        """获取流程自定义破限文本，未配置返回空串。

        Args:
            flow_key: 流程标识

        Returns:
            自定义破限文本；空串表示用等级模板
        """
        mapping = self.config.get("flow_jailbreaks_custom", {})
        return mapping.get(flow_key, "")

    def set_flow_jailbreaks_custom(self, mapping: dict[str, str]) -> None:
        """保存流程自定义破限文本映射并持久化。

        Args:
            mapping: ``{flow_key: custom_text}``，空串表示用等级模板
        """
        with self._lock:
            self.config["flow_jailbreaks_custom"] = mapping
            self.save()

    # ===== 配置项访问 =====

    def get(self, key: str, default: Any = None) -> Any:
        """获取顶层配置项。"""
        with self._lock:
            return self.config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """设置顶层配置项并保存。"""
        with self._lock:
            self.config[key] = value
            self.save()

    def get_appearance(self) -> dict[str, Any]:
        """获取外观配置。"""
        return self.config.get("appearance", {})

    def set_appearance(self, appearance: dict[str, Any]) -> None:
        """设置外观配置并保存。"""
        with self._lock:
            self.config["appearance"] = appearance
            self.save()

    def get_continuation_settings(self) -> dict[str, Any]:
        """获取续写配置。"""
        return self.config.get("continuation", {})

    def get_selected_worldbook_ids(self) -> list[str]:
        """获取续写面板持久化的多选世界书 ID 列表。"""
        cont = self.config.get("continuation", {})
        ids = cont.get("selected_worldbook_ids", [])
        if not isinstance(ids, list):
            return []
        return [str(i) for i in ids if i]

    def set_selected_worldbook_ids(self, ids: list[str]) -> None:
        """设置续写面板多选世界书 ID 并保存。"""
        with self._lock:
            cont = self.config.setdefault("continuation", {})
            cont["selected_worldbook_ids"] = [str(i) for i in ids if i]
            self.save()

    def get_last_panel_endpoint_id(self) -> str:
        """获取续写面板上次选中的端点 ID（跨会话恢复，面板优先于流程配置）。"""
        cont = self.config.get("continuation", {})
        return str(cont.get("last_endpoint_id", ""))

    def set_last_panel_endpoint_id(self, endpoint_id: str) -> None:
        """设置续写面板上次选中的端点 ID 并保存。"""
        with self._lock:
            cont = self.config.setdefault("continuation", {})
            cont["last_endpoint_id"] = str(endpoint_id or "")
            self.save()

    def get_last_model_per_endpoint(self) -> dict[str, str]:
        """获取每端点上次选中的模型映射（跨会话恢复，面板优先于流程配置）。"""
        cont = self.config.get("continuation", {})
        mapping = cont.get("last_model_per_endpoint", {})
        if not isinstance(mapping, dict):
            return {}
        return {str(k): str(v) for k, v in mapping.items() if k and v}

    def set_last_model_per_endpoint(self, mapping: dict[str, str]) -> None:
        """设置每端点上次选中的模型映射并保存。"""
        with self._lock:
            cont = self.config.setdefault("continuation", {})
            cont["last_model_per_endpoint"] = {str(k): str(v) for k, v in mapping.items() if k and v}
            self.save()

    def get_volume_settings(self) -> dict[str, Any]:
        """获取卷续写配置（VolumeRunConfig 的 JSON dump）。"""
        return self.config.get("volume", {})

    def set_volume_settings(self, settings: dict[str, Any]) -> None:
        """设置卷续写配置并保存。"""
        with self._lock:
            self.config["volume"] = settings
            self.save()

    def get_context_extract_settings(self) -> dict[str, Any]:
        """获取上下文提取配置。"""
        return self.config.get("context_extract", {})

    def get_storage_path(self) -> Path:
        """获取数据存储路径。"""
        path_str = self.config.get("data", {}).get("storage_path", "")
        if not path_str:
            return get_default_storage_path()
        return Path(os.path.expanduser(path_str))

    def is_privacy_accepted(self) -> bool:
        """是否已接受隐私声明。"""
        return self.config.get("privacy_accepted", False)

    def accept_privacy(self) -> None:
        """标记已接受隐私声明。"""
        with self._lock:
            self.config["privacy_accepted"] = True
            self.save()

    # ===== 批量保存模式 =====

    def begin_batch(self) -> None:
        """开启批量保存模式。

        在此模式下，``set*`` 方法触发的 ``save()`` 不会立即写入磁盘，
        直到调用 ``commit_batch`` 时统一保存一次，减少多次磁盘 I/O。
        """
        with self._lock:
            self._batch_mode = True

    def commit_batch(self) -> None:
        """提交批量保存模式。

        关闭批量模式并立即将配置写入磁盘。
        """
        with self._lock:
            self._batch_mode = False
            self.save()
