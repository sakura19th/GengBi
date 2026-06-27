"""配置版本迁移。

实现 config_version 迁移函数链：备份 → 迁移 → 回滚。
当前版本为 1，暂无实际迁移函数，但基础设施已就位，
后续版本升级时只需在 ``_MIGRATIONS`` 中注册迁移函数。

迁移流程：
1. 备份原配置到 .bak
2. 按版本顺序执行迁移函数链（v1→v2, v2→v3, ...）
3. 迁移失败时从 .bak 回滚并提示用户
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Callable

from novelforge.core.storage import atomic_write_file, backup_file, load_json_with_recovery

logger = logging.getLogger(__name__)

# 当前配置版本
CURRENT_CONFIG_VERSION = 1

# 迁移函数类型：接收旧配置 dict，返回新配置 dict
MigrationFunc = Callable[[dict[str, Any]], dict[str, Any]]

# 迁移函数链：key=起始版本号，value=迁移到下一版本的函数
# 示例：_MIGRATIONS[1] = migrate_v1_to_v2
_MIGRATIONS: dict[int, MigrationFunc] = {}


def register_migration(from_version: int) -> Callable[[MigrationFunc], MigrationFunc]:
    """装饰器：注册版本迁移函数。

    Args:
        from_version: 迁移起始版本号

    Example:
        @register_migration(1)
        def migrate_v1_to_v2(config):
            config["config_version"] = 2
            # ... 迁移逻辑
            return config
    """

    def decorator(func: MigrationFunc) -> MigrationFunc:
        _MIGRATIONS[from_version] = func
        return func

    return decorator


def migrate_config(config: dict[str, Any], config_path: Any) -> dict[str, Any]:
    """迁移配置到当前版本。

    流程：
    1. 备份原配置到 .bak
    2. 按版本顺序执行迁移函数链
    3. 迁移失败时从 .bak 回滚

    Args:
        config: 当前配置 dict
        config_path: 配置文件路径（Path 对象，用于备份和回滚）

    Returns:
        迁移后的配置 dict
    """
    from pathlib import Path

    config_path = Path(config_path)
    version = config.get("config_version", 1)

    if version >= CURRENT_CONFIG_VERSION:
        logger.debug("配置版本 %d 已是最新，无需迁移", version)
        return config

    # 备份原配置
    backup_file(config_path)
    original_config = copy.deepcopy(config)

    logger.info("开始配置迁移: v%d → v%d", version, CURRENT_CONFIG_VERSION)

    try:
        current = config
        while version < CURRENT_CONFIG_VERSION:
            migrator = _MIGRATIONS.get(version)
            if migrator is None:
                logger.warning("无 v%d → v%d 迁移函数，跳过", version, version + 1)
                version += 1
                continue
            logger.info("执行迁移: v%d → v%d", version, version + 1)
            current = migrator(current)
            version = current.get("config_version", version + 1)

        logger.info("配置迁移完成: v%d", version)
        return current

    except Exception as e:
        logger.error("配置迁移失败，回滚: %s", e)
        # 回滚：恢复原始配置
        import json

        try:
            atomic_write_file(
                config_path,
                json.dumps(original_config, ensure_ascii=False, indent=2),
            )
        except OSError as rollback_err:
            logger.error("回滚失败: %s", rollback_err)
        raise
