"""SQLite + 文件系统存储层。

实现数据持久化的核心基础设施：
- SQLite schema（projects/chapters/continuations/history_log/cache 五张表）
- WAL 模式 + busy_timeout=5000（网络文件系统降级为 DELETE 模式）
- 原子文件写入（临时文件 → fsync → rename）
- SQLite 写入失败时删除已写入文件并回滚
- 关键操作前 .bak 备份
- 数据损坏恢复（启动时校验、.bak 恢复）

存储路径约定：
- 章节正文：``~/.novelforge/projects/{project_id}/chapters/{chapter_id}.txt``
- 预设：``~/.novelforge/presets/{preset_id}.json``
- 数据库：``~/.novelforge/novelforge.db``

线程模型：aiosqlite 每个连接在独立后台线程运行，本模块每个 Storage 实例持有一个连接。
多线程访问应各自创建 Storage 实例或通过主线程代理。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

import aiosqlite

from novelforge.models import Chapter

logger = logging.getLogger(__name__)

# JSON 文件大小上限（50MB），防止加载超大文件导致内存溢出
MAX_JSON_SIZE = 50 * 1024 * 1024

# SQLite schema 建表语句
SCHEMA_SQL = """
-- 项目表
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    source_file TEXT DEFAULT '',
    novel_profile TEXT DEFAULT '{}',
    preset_id TEXT DEFAULT 'default',
    regex_script_ids TEXT DEFAULT '[]',
    extract_config TEXT,
    chapter_split_rule TEXT DEFAULT '{}',
    world_ontology TEXT,
    worldbook_id TEXT DEFAULT '',
    custom_audit_rules TEXT
);

-- 章节元数据表（正文存文件系统）
-- 注意：index 是 SQLite 保留字，需用双引号引用
CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    "index" INTEGER NOT NULL DEFAULT 0,
    title TEXT DEFAULT '',
    word_count INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    protagonist_profile TEXT,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chapters_project ON chapters(project_id, "index");

-- 续写版本（swipe）表
CREATE TABLE IF NOT EXISTS continuations (
    id TEXT PRIMARY KEY,
    chapter_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    content TEXT DEFAULT '',
    model TEXT DEFAULT '',
    is_accepted INTEGER DEFAULT 0,
    parent_id TEXT,
    status TEXT DEFAULT 'completed',
    created_by TEXT DEFAULT 'continuation',
    parameters_snapshot TEXT DEFAULT '{}',
    preset_id TEXT DEFAULT '',
    preset_snapshot TEXT DEFAULT '{}',
    regex_script_ids_snapshot TEXT DEFAULT '[]',
    extracted_context_snapshot TEXT DEFAULT '[]',
    prompt_snapshot TEXT DEFAULT '[]',
    reasoning_content TEXT,
    agent_artifacts TEXT,
    volume_artifacts TEXT,
    highlights TEXT,
    FOREIGN KEY (chapter_id) REFERENCES chapters(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_continuations_chapter ON continuations(chapter_id);

-- 续写历史日志表
CREATE TABLE IF NOT EXISTS history_log (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    chapter_id TEXT,
    swipe_id TEXT,
    started_at TEXT,
    finished_at TEXT,
    status TEXT,
    model TEXT,
    parameters_json TEXT,
    prompt_messages_json TEXT,
    output_text TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_history_project ON history_log(project_id);
CREATE INDEX IF NOT EXISTS idx_history_chapter ON history_log(chapter_id);

-- 缓存表（上下文提取缓存等）
CREATE TABLE IF NOT EXISTS cache (
    key TEXT PRIMARY KEY,
    value TEXT,
    created_at TEXT NOT NULL,
    expires_at TEXT,
    category TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cache_category ON cache(category);
"""


def get_default_storage_path() -> Path:
    """返回默认存储根目录 ``~/.novelforge``。"""
    return Path.home() / ".novelforge"


def get_chapter_file_path(storage_path: Path, project_id: str, chapter_id: str) -> Path:
    """返回章节正文文件路径。"""
    return storage_path / "projects" / project_id / "chapters" / f"{chapter_id}.txt"


def get_preset_file_path(storage_path: Path, preset_id: str) -> Path:
    """返回预设文件路径。"""
    return storage_path / "presets" / f"{preset_id}.json"


def is_network_filesystem(path: Path) -> bool:
    """检测路径是否位于网络文件系统。

    网络文件系统（NFS/SMB/CIFS 等）不支持 SQLite WAL 模式的共享内存文件，
    需降级为 DELETE 日志模式。通过检查 /proc/mounts 判断挂载类型。
    """
    try:
        resolved = path.resolve()
        # 收集所有挂载点及其文件系统类型
        mount_points: list[tuple[str, str]] = []
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 3:
                    mount_points.append((parts[1], parts[2]))
        # 找到路径所在的最长匹配挂载点
        best_fs = ""
        best_len = 0
        path_str = str(resolved)
        for mount_point, fs_type in mount_points:
            if path_str.startswith(mount_point) and len(mount_point) > best_len:
                best_fs = fs_type
                best_len = len(mount_point)
        network_fs_types = {"nfs", "nfs4", "cifs", "smb", "smbfs", "fuse.sshfs", "afp"}
        return best_fs.lower() in network_fs_types
    except (OSError, FileNotFoundError):
        # 非 Linux 系统或无法读取 /proc/mounts，保守返回 False
        return False


def atomic_write_file(file_path: Path, content: str | bytes, encoding: str = "utf-8") -> None:
    """原子写入文件。

    流程：写入临时文件 → fsync → rename 到目标路径（原子操作）。
    确保写入过程中断不会产生半成品文件。

    Args:
        file_path: 目标文件路径
        content: 写入内容（str 或 bytes）
        encoding: 文本编码（content 为 str 时使用）
    """
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    # 在同目录创建临时文件（确保 rename 是原子操作，跨目录 rename 非原子）
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(file_path.parent), prefix=".tmp_", suffix=file_path.suffix
    )
    try:
        with os.fdopen(tmp_fd, "w", encoding=encoding) if isinstance(content, str) \
                else os.fdopen(tmp_fd, "wb") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        # rename 在同文件系统下是原子操作
        os.replace(tmp_path, file_path)
    except Exception:
        # 写入失败时清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def backup_file(file_path: Path) -> Path | None:
    """为文件创建 .bak 备份。

    关键操作（保存预设、正则、项目配置）前调用。
    若源文件不存在则返回 None。

    Args:
        file_path: 源文件路径

    Returns:
        备份文件路径，源文件不存在时返回 None
    """
    file_path = Path(file_path)
    if not file_path.exists():
        return None
    bak_path = file_path.with_suffix(file_path.suffix + ".bak")
    shutil.copy2(file_path, bak_path)
    return bak_path


def load_json_with_recovery(file_path: Path) -> tuple[Any, str | None]:
    """加载 JSON 文件，支持从 .bak 恢复。

    流程：
    1. 尝试读取并解析目标文件
    2. 若损坏，尝试从 .bak 恢复
    3. .bak 也损坏时返回错误信息（由调用方决定弹窗处理）

    Args:
        file_path: JSON 文件路径

    Returns:
        (解析后的数据, 错误信息) 元组。成功时错误信息为 None。
        数据为 None 且错误信息非 None 表示加载失败。
    """
    file_path = Path(file_path)
    bak_path = file_path.with_suffix(file_path.suffix + ".bak")

    # 尝试读取主文件
    if file_path.exists():
        # 检查文件大小，防止加载超大文件导致内存溢出
        file_size = file_path.stat().st_size
        if file_size > MAX_JSON_SIZE:
            size_mb = file_size / (1024 * 1024)
            limit_mb = MAX_JSON_SIZE // (1024 * 1024)
            raise ValueError(
                f"JSON 文件过大（{size_mb:.1f}MB），超过上限 {limit_mb}MB"
            )
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                return json.load(f), None
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("文件 %s 损坏: %s，尝试从 .bak 恢复", file_path, e)

    # 尝试从 .bak 恢复
    if bak_path.exists():
        try:
            with open(bak_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            # 恢复成功，写回主文件
            atomic_write_file(file_path, json.dumps(data, ensure_ascii=False, indent=2))
            logger.info("从 .bak 恢复成功: %s", file_path)
            return data, None
        except (json.JSONDecodeError, OSError) as e:
            logger.error(".bak 文件也损坏: %s: %s", bak_path, e)
            return None, f"文件 {file_path.name} 及其备份均损坏，需要重置或手动修复"

    if not file_path.exists():
        return None, None  # 文件不存在，非错误

    return None, f"文件 {file_path.name} 损坏且无可用备份"


def save_json_with_backup(file_path: Path, data: Any) -> None:
    """保存 JSON 文件，写入前创建 .bak 备份。

    关键操作（预设、正则、项目配置）保存时调用。

    Args:
        file_path: 目标文件路径
        data: 待序列化的数据
    """
    file_path = Path(file_path)
    # 写入前备份已有文件
    backup_file(file_path)
    content = json.dumps(data, ensure_ascii=False, indent=2)
    atomic_write_file(file_path, content)


class Storage:
    """异步存储层，封装 SQLite 与文件系统操作。

    每个 Storage 实例持有一个 aiosqlite 连接（在独立后台线程运行）。
    使用前需调用 ``connect()``，使用完毕调用 ``close()``。

    线程模型：aiosqlite 连接在独立线程执行 SQL，多线程访问应各自创建实例。
    """

    def __init__(self, storage_path: Path | None = None) -> None:
        """初始化存储层。

        Args:
            storage_path: 存储根目录，默认 ``~/.novelforge``
        """
        self.storage_path: Path = storage_path or get_default_storage_path()
        self._db_path: Path = self.storage_path / "novelforge.db"
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """连接数据库并初始化 schema。

        启用 WAL 模式（网络文件系统降级为 DELETE 模式），设置 busy_timeout，
        创建所有表和索引。
        """
        self.storage_path.mkdir(parents=True, exist_ok=True)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        self._conn = await aiosqlite.connect(str(self._db_path))

        # 检测网络文件系统，决定日志模式
        if is_network_filesystem(self._db_path):
            journal_mode = "DELETE"
            logger.info("检测到网络文件系统，SQLite 使用 DELETE 日志模式")
        else:
            journal_mode = "WAL"

        await self._conn.execute(f"PRAGMA journal_mode={journal_mode}")
        await self._conn.execute("PRAGMA busy_timeout=5000")
        await self._conn.execute("PRAGMA foreign_keys=ON")
        await self._conn.execute("PRAGMA synchronous=NORMAL")

        # 创建表和索引
        await self._conn.executescript(SCHEMA_SQL)

        # 幂等迁移：为旧版 continuations 表补充 agent_artifacts / volume_artifacts 列
        await self._migrate_continuations_columns()
        # 幂等迁移：为旧版 projects 表补充 world_ontology / worldbook_id 列
        await self._migrate_projects_columns()
        # 幂等迁移：为旧版 chapters 表补充 protagonist_profile 列
        await self._migrate_chapters_columns()

        await self._conn.commit()
        logger.info("数据库连接成功: %s（日志模式: %s）", self._db_path, journal_mode)

    async def _migrate_continuations_columns(self) -> None:
        """幂等迁移：为 continuations 表补充 agent_artifacts / volume_artifacts / parent_id 列。

        SQLite 不支持 ADD COLUMN IF NOT EXISTS，需先查 PRAGMA table_info
        检测列是否存在，再决定是否 ALTER TABLE。
        """
        async with self._conn.execute(
            "PRAGMA table_info(continuations)"
        ) as cursor:
            columns = await cursor.fetchall()
        # columns 是 (cid, name, type, notnull, dflt_value, pk) 的列表
        column_names = {col[1] for col in columns}
        if "agent_artifacts" not in column_names:
            await self._conn.execute(
                "ALTER TABLE continuations ADD COLUMN agent_artifacts TEXT"
            )
            logger.info("已为 continuations 表添加 agent_artifacts 列")
        if "volume_artifacts" not in column_names:
            await self._conn.execute(
                "ALTER TABLE continuations ADD COLUMN volume_artifacts TEXT"
            )
            logger.info("已为 continuations 表添加 volume_artifacts 列")
        if "highlights" not in column_names:
            await self._conn.execute(
                "ALTER TABLE continuations ADD COLUMN highlights TEXT"
            )
            logger.info("已为 continuations 表添加 highlights 列")
        if "parent_id" not in column_names:
            await self._conn.execute(
                "ALTER TABLE continuations ADD COLUMN parent_id TEXT"
            )
            logger.info("已为 continuations 表添加 parent_id 列")
        # parent_id 索引（在 ALTER TABLE 补列之后创建，保证旧库列已存在）
        await self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_continuations_parent ON continuations(parent_id)"
        )

    async def _migrate_projects_columns(self) -> None:
        """幂等迁移：为 projects 表补充 world_ontology / worldbook_id / custom_audit_rules 列。

        SQLite 不支持 ADD COLUMN IF NOT EXISTS，需先查 PRAGMA table_info
        检测列是否存在，再决定是否 ALTER TABLE。
        """
        async with self._conn.execute(
            "PRAGMA table_info(projects)"
        ) as cursor:
            columns = await cursor.fetchall()
        column_names = {col[1] for col in columns}
        if "world_ontology" not in column_names:
            await self._conn.execute(
                "ALTER TABLE projects ADD COLUMN world_ontology TEXT"
            )
            logger.info("已为 projects 表添加 world_ontology 列")
        if "worldbook_id" not in column_names:
            await self._conn.execute(
                "ALTER TABLE projects ADD COLUMN worldbook_id TEXT DEFAULT ''"
            )
            logger.info("已为 projects 表添加 worldbook_id 列")
        if "custom_audit_rules" not in column_names:
            await self._conn.execute(
                "ALTER TABLE projects ADD COLUMN custom_audit_rules TEXT"
            )
            logger.info("已为 projects 表添加 custom_audit_rules 列")

    async def _migrate_chapters_columns(self) -> None:
        """幂等迁移：为旧版 chapters 表补充 protagonist_profile 列。

        SQLite 不支持 ADD COLUMN IF NOT EXISTS，需先查 PRAGMA table_info
        检测列是否存在，再决定是否 ALTER TABLE。
        """
        async with self._conn.execute(
            "PRAGMA table_info(chapters)"
        ) as cursor:
            columns = await cursor.fetchall()
        column_names = {col[1] for col in columns}
        if "protagonist_profile" not in column_names:
            await self._conn.execute(
                "ALTER TABLE chapters ADD COLUMN protagonist_profile TEXT"
            )
            logger.info("已为 chapters 表添加 protagonist_profile 列")

    async def close(self) -> None:
        """关闭数据库连接。"""
        if self._conn:
            await self._conn.close()
            self._conn = None
            logger.info("数据库连接已关闭")

    async def __aenter__(self) -> "Storage":
        """进入异步上下文：连接数据库。"""
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        """退出异步上下文：关闭数据库连接。"""
        await self.close()

    @property
    def conn(self) -> aiosqlite.Connection:
        """获取当前数据库连接，未连接时抛出异常。"""
        if self._conn is None:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        return self._conn

    # ===== 项目操作 =====

    async def save_project(self, project: dict[str, Any]) -> None:
        """保存项目到 SQLite。

        Args:
            project: 项目数据字典（含 id、name、novel_profile 等字段）
        """
        now = datetime.now().isoformat()
        if "created_at" not in project or not project["created_at"]:
            project["created_at"] = now
        project["updated_at"] = now

        await self.conn.execute(
            """INSERT INTO projects
               (id, name, created_at, updated_at, source_file, novel_profile,
                preset_id, regex_script_ids, extract_config, chapter_split_rule,
                world_ontology, worldbook_id, custom_audit_rules)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET
                   name=excluded.name,
                   created_at=excluded.created_at,
                   updated_at=excluded.updated_at,
                   source_file=excluded.source_file,
                   novel_profile=excluded.novel_profile,
                   preset_id=excluded.preset_id,
                   regex_script_ids=excluded.regex_script_ids,
                   extract_config=excluded.extract_config,
                   chapter_split_rule=excluded.chapter_split_rule,
                   world_ontology=excluded.world_ontology,
                   worldbook_id=excluded.worldbook_id,
                   custom_audit_rules=excluded.custom_audit_rules""",
            (
                project["id"],
                project.get("name", ""),
                project["created_at"],
                project["updated_at"],
                project.get("source_file", ""),
                json.dumps(project.get("novel_profile", {}), ensure_ascii=False),
                project.get("preset_id", "default"),
                json.dumps(project.get("regex_script_ids", []), ensure_ascii=False),
                json.dumps(project.get("extract_config"), ensure_ascii=False)
                if project.get("extract_config") is not None
                else None,
                json.dumps(project.get("chapter_split_rule", {}), ensure_ascii=False),
                json.dumps(project.get("world_ontology"), ensure_ascii=False, default=str)
                if project.get("world_ontology") is not None
                else None,
                project.get("worldbook_id", ""),
                json.dumps(project.get("custom_audit_rules"), ensure_ascii=False, default=str)
                if project.get("custom_audit_rules")
                else None,
            ),
        )
        await self.conn.commit()

    async def load_project(self, project_id: str) -> dict[str, Any] | None:
        """加载项目。"""
        async with self.conn.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_project(row)

    async def list_projects(self) -> list[dict[str, Any]]:
        """列出所有项目。"""
        async with self.conn.execute(
            "SELECT * FROM projects ORDER BY updated_at DESC"
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_project(row) for row in rows]

    async def delete_project(self, project_id: str) -> None:
        """删除项目及其所有章节、续写。

        同时删除文件系统中的章节文件目录。
        """
        # 删除 SQLite 记录（级联删除章节和续写）
        await self.conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await self.conn.execute("DELETE FROM chapters WHERE project_id = ?", (project_id,))
        await self.conn.commit()

        # 删除文件系统中的章节目录
        project_dir = self.storage_path / "projects" / project_id
        if project_dir.exists():
            try:
                shutil.rmtree(project_dir)
            except OSError as e:
                logger.error("删除项目目录失败 %s: %s", project_dir, e)

    @staticmethod
    def _row_to_project(row: aiosqlite.Row) -> dict[str, Any]:
        """将数据库行转换为项目字典。"""
        return {
            "id": row[0],
            "name": row[1],
            "created_at": row[2],
            "updated_at": row[3],
            "source_file": row[4],
            "novel_profile": json.loads(row[5]) if row[5] else {},
            "preset_id": row[6],
            "regex_script_ids": json.loads(row[7]) if row[7] else [],
            "extract_config": json.loads(row[8]) if row[8] else None,
            "chapter_split_rule": json.loads(row[9]) if row[9] else {},
            "world_ontology": json.loads(row[10]) if len(row) > 10 and row[10] else None,
            "worldbook_id": row[11] if len(row) > 11 and row[11] else "",
            "custom_audit_rules": json.loads(row[12]) if len(row) > 12 and row[12] else [],
        }

    # ===== 章节操作 =====

    async def save_chapter(self, chapter: dict[str, Any]) -> None:
        """保存章节元数据到 SQLite，正文写入文件系统。

        流程：先写章节文件（原子写入）→ 再写 SQLite。
        若 SQLite 写入失败，删除已写入的文件并回滚。

        Args:
            chapter: 章节数据字典
        """
        project_id = chapter["project_id"]
        chapter_id = chapter["id"]
        content = chapter.get("content", "")

        # 先写章节文件
        chapter_file = get_chapter_file_path(self.storage_path, project_id, chapter_id)
        # 记录写入前状态：若文件已存在（更新场景），保留旧内容以便回滚恢复，
        # 避免 SQLite 写入失败时误删原有正文导致数据丢失
        existed_before = chapter_file.exists()
        old_bytes: bytes | None = None
        if existed_before:
            try:
                old_bytes = chapter_file.read_bytes()
            except OSError as e:
                logger.warning(
                    "读取旧章节文件失败 %s: %s，回滚将无法恢复原内容", chapter_file, e
                )
        try:
            atomic_write_file(chapter_file, content)
        except OSError as e:
            logger.error("写入章节文件失败 %s: %s", chapter_file, e)
            raise

        # 再写 SQLite 元数据
        now = datetime.now().isoformat()
        if "created_at" not in chapter or not chapter["created_at"]:
            chapter["created_at"] = now
        chapter["updated_at"] = now

        try:
            await self.conn.execute(
                """INSERT INTO chapters
                   (id, project_id, "index", title, word_count, metadata,
                    created_at, updated_at, protagonist_profile)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       project_id=excluded.project_id,
                       "index"=excluded."index",
                       title=excluded.title,
                       word_count=excluded.word_count,
                       metadata=excluded.metadata,
                       created_at=excluded.created_at,
                       updated_at=excluded.updated_at,
                       protagonist_profile=excluded.protagonist_profile""",
                (
                    chapter_id,
                    project_id,
                    chapter.get("index", 0),
                    chapter.get("title", ""),
                    chapter.get("word_count", len(content)),
                    json.dumps(chapter.get("metadata", {}), ensure_ascii=False),
                    chapter["created_at"],
                    chapter["updated_at"],
                    json.dumps(chapter.get("protagonist_profile"), ensure_ascii=False)
                    if chapter.get("protagonist_profile") else None,
                ),
            )
            await self.conn.commit()
        except Exception as e:
            # SQLite 写入失败，回滚章节文件
            logger.error("SQLite 写入章节失败，回滚文件: %s", e)
            if existed_before and old_bytes is not None:
                # 已存在章节：恢复旧正文，避免误删原有内容
                try:
                    chapter_file.write_bytes(old_bytes)
                except OSError as restore_err:
                    logger.error(
                        "回滚恢复旧章节文件失败 %s: %s", chapter_file, restore_err
                    )
            else:
                # 新章节（文件原本不存在）：删除刚写入的文件
                try:
                    chapter_file.unlink(missing_ok=True)
                except OSError:
                    pass
            raise

    async def update_chapter_index(
        self, chapter_id: str, new_index: int
    ) -> None:
        """只更新章节的 index 列，不触碰正文文件。

        用于 reindex 场景，避免 save_chapter 用空 content 覆盖正文。

        Args:
            chapter_id: 章节 ID
            new_index: 新的章节序号
        """
        await self.conn.execute(
            'UPDATE chapters SET "index" = ?, updated_at = ? WHERE id = ?',
            (new_index, datetime.now().isoformat(), chapter_id),
        )
        await self.conn.commit()

    async def update_chapter_protagonist(
        self, chapter_id: str, protagonist_profile_json: str | None
    ) -> None:
        """只更新章节的 protagonist_profile 列，不触碰正文文件。

        用于主角形象提取完成落盘，避免 save_chapter 用空 content 覆盖正文。

        Args:
            chapter_id: 章节 ID
            protagonist_profile_json: ProtagonistProfile 的 JSON 字符串，None 清空
        """
        await self.conn.execute(
            "UPDATE chapters SET protagonist_profile = ? WHERE id = ?",
            (protagonist_profile_json, chapter_id),
        )
        await self.conn.commit()

    async def load_chapter(self, chapter_id: str) -> dict[str, Any] | None:
        """加载章节元数据与正文。

        正文从文件系统读取。若文件不存在，content 为空字符串。
        """
        async with self.conn.execute(
            "SELECT * FROM chapters WHERE id = ?", (chapter_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None

        chapter = self._row_to_chapter(row)
        # 从文件系统读取正文
        chapter_file = get_chapter_file_path(
            self.storage_path, chapter["project_id"], chapter_id
        )
        if chapter_file.exists():
            try:
                chapter["content"] = chapter_file.read_text(encoding="utf-8")
            except OSError as e:
                logger.error("读取章节文件失败 %s: %s", chapter_file, e)
                chapter["content"] = ""
        else:
            chapter["content"] = ""
        return chapter

    async def load_chapters_with_continuations(
        self, chapter_ids: list[str]
    ) -> dict[str, Chapter]:
        """批量加载多个章节及其续写列表（单事务，减少跨线程往返）。

        在单次连接中查询所有章节元数据、正文及其续写，避免 N 次
        ``load_chapter`` + N 次 ``list_continuations`` 的 2N 次跨线程往返。

        Args:
            chapter_ids: 章节 ID 列表

        Returns:
            ``{chapter_id: Chapter}`` 映射。未找到的章节不会出现在结果中。
        """
        if not chapter_ids:
            return {}
        placeholders = ",".join("?" for _ in chapter_ids)
        # 一次性查询所有章节元数据
        async with self.conn.execute(
            f'SELECT * FROM chapters WHERE id IN ({placeholders})',
            tuple(chapter_ids),
        ) as cursor:
            chapter_rows = await cursor.fetchall()
        if not chapter_rows:
            return {}
        # 一次性查询所有章节的续写
        async with self.conn.execute(
            "SELECT id, chapter_id, created_at, content, model, is_accepted, "
            "parent_id, status, created_by, parameters_snapshot, preset_id, "
            "preset_snapshot, regex_script_ids_snapshot, "
            "extracted_context_snapshot, prompt_snapshot, reasoning_content, "
            "agent_artifacts, volume_artifacts, highlights "
            f"FROM continuations WHERE chapter_id IN ({placeholders}) "
            "ORDER BY created_at ASC",
            tuple(chapter_ids),
        ) as cursor:
            cont_rows = await cursor.fetchall()
        # 按 chapter_id 分组续写
        conts_by_chapter: dict[str, list[dict[str, Any]]] = {}
        for row in cont_rows:
            cont = self._row_to_continuation(row)
            conts_by_chapter.setdefault(cont["chapter_id"], []).append(cont)
        # 组装 Chapter 对象
        result: dict[str, Chapter] = {}
        for row in chapter_rows:
            chapter = self._row_to_chapter(row)
            chapter_file = get_chapter_file_path(
                self.storage_path, chapter["project_id"], chapter["id"]
            )
            if chapter_file.exists():
                try:
                    chapter["content"] = chapter_file.read_text(encoding="utf-8")
                except OSError as e:
                    logger.error("读取章节文件失败 %s: %s", chapter_file, e)
                    chapter["content"] = ""
            else:
                chapter["content"] = ""
            chapter["continuations"] = conts_by_chapter.get(chapter["id"], [])
            result[chapter["id"]] = Chapter.model_validate(chapter)
        return result

    async def list_chapters(self, project_id: str) -> list[dict[str, Any]]:
        """列出项目的所有章节元数据（不含正文）。

        若 DB 无章节但磁盘有 .txt 文件（级联删除数据丢失场景），
        自动从磁盘重建 DB 行后返回。
        """
        async with self.conn.execute(
            'SELECT * FROM chapters WHERE project_id = ? ORDER BY "index" ASC',
            (project_id,),
        ) as cursor:
            rows = await cursor.fetchall()

        # 自动恢复：DB 无章节但磁盘有 .txt 文件时重建
        if not rows:
            rebuilt = await self.rebuild_chapters_from_disk(project_id)
            if rebuilt:
                async with self.conn.execute(
                    'SELECT * FROM chapters WHERE project_id = ? ORDER BY "index" ASC',
                    (project_id,),
                ) as cursor:
                    rows = await cursor.fetchall()

        return [self._row_to_chapter(row) for row in rows]

    async def rebuild_chapters_from_disk(self, project_id: str) -> int:
        """从磁盘 .txt 文件重建 chapters DB 行。

        当 DB chapters 行丢失但磁盘文件仍在时，扫描 chapters 目录，
        为每个 .txt 文件重建 DB 行。已存在的 DB 行不覆盖。

        Args:
            project_id: 项目 ID

        Returns:
            重建的章节数
        """
        chapters_dir = self.storage_path / "projects" / project_id / "chapters"
        if not chapters_dir.exists():
            return 0

        # 收集磁盘上的 chapter_id（按文件名排序）
        disk_files = sorted(chapters_dir.glob("*.txt"))
        if not disk_files:
            return 0

        # 收集 DB 中已存在的 chapter_id
        async with self.conn.execute(
            "SELECT id FROM chapters WHERE project_id = ?", (project_id,)
        ) as cursor:
            existing_ids = {row[0] for row in await cursor.fetchall()}

        rebuilt = 0
        now = datetime.now().isoformat()
        for idx, chapter_file in enumerate(disk_files):
            chapter_id = chapter_file.stem
            if chapter_id in existing_ids:
                continue  # 已存在不覆盖
            try:
                content = chapter_file.read_text(encoding="utf-8")
            except OSError as e:
                logger.warning("读取章节文件失败 %s: %s", chapter_file, e)
                continue
            # 推断 title：用正文首行或文件名
            first_line = content.strip().split("\n", 1)[0][:50] if content.strip() else ""
            title = first_line or chapter_id
            try:
                await self.conn.execute(
                    """INSERT INTO chapters
                       (id, project_id, "index", title, word_count, metadata,
                        created_at, updated_at, protagonist_profile)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(id) DO UPDATE SET
                           project_id=excluded.project_id,
                           "index"=excluded."index",
                           title=excluded.title,
                           word_count=excluded.word_count,
                           metadata=excluded.metadata,
                           created_at=excluded.created_at,
                           updated_at=excluded.updated_at,
                           protagonist_profile=excluded.protagonist_profile""",
                    (
                        chapter_id,
                        project_id,
                        idx,
                        title,
                        len(content),
                        "{}",
                        now,
                        now,
                        None,
                    ),
                )
                rebuilt += 1
            except Exception as e:
                logger.error("重建章节 %s 失败: %s", chapter_id, e)
        if rebuilt:
            await self.conn.commit()
            logger.info("从磁盘重建 %d 章 (project=%s)", rebuilt, project_id)
        return rebuilt

    async def delete_chapter(self, chapter_id: str) -> None:
        """删除章节及其所有续写。

        同时删除文件系统中的章节文件。
        """
        # 先获取 project_id 用于删除文件
        async with self.conn.execute(
            "SELECT project_id FROM chapters WHERE id = ?", (chapter_id,)
        ) as cursor:
            row = await cursor.fetchone()

        # 删除 SQLite 记录（级联删除续写）
        await self.conn.execute("DELETE FROM chapters WHERE id = ?", (chapter_id,))
        await self.conn.execute(
            "DELETE FROM continuations WHERE chapter_id = ?", (chapter_id,)
        )
        await self.conn.commit()

        # 删除文件系统中的章节文件
        if row:
            project_id = row[0]
            chapter_file = get_chapter_file_path(
                self.storage_path, project_id, chapter_id
            )
            try:
                chapter_file.unlink(missing_ok=True)
            except OSError as e:
                logger.error("删除章节文件失败 %s: %s", chapter_file, e)

    @staticmethod
    def _row_to_chapter(row: aiosqlite.Row) -> dict[str, Any]:
        """将数据库行转换为章节字典（不含正文）。"""
        return {
            "id": row[0],
            "project_id": row[1],
            "index": row[2],
            "title": row[3],
            "word_count": row[4],
            "metadata": json.loads(row[5]) if row[5] else {},
            "created_at": row[6],
            "updated_at": row[7],
            "protagonist_profile": json.loads(row[8]) if len(row) > 8 and row[8] else None,
        }

    # ===== 续写版本操作 =====

    async def save_continuation(self, continuation: dict[str, Any]) -> None:
        """保存续写版本（swipe）到 SQLite。"""
        now = datetime.now().isoformat()
        if "created_at" not in continuation or not continuation["created_at"]:
            continuation["created_at"] = now

        await self.conn.execute(
            """INSERT OR REPLACE INTO continuations
               (id, chapter_id, created_at, content, model, is_accepted,
                parent_id, status, created_by, parameters_snapshot, preset_id,
                preset_snapshot, regex_script_ids_snapshot,
                extracted_context_snapshot, prompt_snapshot, reasoning_content,
                agent_artifacts, volume_artifacts, highlights)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                continuation["id"],
                continuation["chapter_id"],
                continuation["created_at"],
                continuation.get("content", ""),
                continuation.get("model", ""),
                1 if continuation.get("is_accepted", False) else 0,
                continuation.get("parent_id"),
                continuation.get("status", "completed"),
                continuation.get("created_by", "continuation"),
                json.dumps(continuation.get("parameters_snapshot", {}), ensure_ascii=False),
                continuation.get("preset_id", ""),
                json.dumps(continuation.get("preset_snapshot", {}), ensure_ascii=False),
                json.dumps(
                    continuation.get("regex_script_ids_snapshot", []), ensure_ascii=False
                ),
                json.dumps(
                    continuation.get("extracted_context_snapshot", []), ensure_ascii=False
                ),
                json.dumps(continuation.get("prompt_snapshot", []), ensure_ascii=False),
                continuation.get("reasoning_content"),
                json.dumps(continuation.get("agent_artifacts"), ensure_ascii=False)
                if continuation.get("agent_artifacts")
                else None,
                json.dumps(continuation.get("volume_artifacts"), ensure_ascii=False)
                if continuation.get("volume_artifacts")
                else None,
                json.dumps(continuation.get("highlights", []), ensure_ascii=False)
                if continuation.get("highlights")
                else None,
            ),
        )
        await self.conn.commit()

    async def list_continuations(self, chapter_id: str) -> list[dict[str, Any]]:
        """列出章节的所有续写版本。"""
        async with self.conn.execute(
            "SELECT id, chapter_id, created_at, content, model, is_accepted, "
            "parent_id, status, created_by, parameters_snapshot, preset_id, "
            "preset_snapshot, regex_script_ids_snapshot, "
            "extracted_context_snapshot, prompt_snapshot, reasoning_content, "
            "agent_artifacts, volume_artifacts, highlights "
            "FROM continuations WHERE chapter_id = ? ORDER BY created_at ASC",
            (chapter_id,),
        ) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_continuation(row) for row in rows]

    async def delete_continuation(self, continuation_id: str) -> None:
        """删除续写版本。"""
        await self.conn.execute(
            "DELETE FROM continuations WHERE id = ?", (continuation_id,)
        )
        await self.conn.commit()

    @staticmethod
    def _row_to_continuation(row: aiosqlite.Row) -> dict[str, Any]:
        """将数据库行转换为续写字典。"""
        return {
            "id": row[0],
            "chapter_id": row[1],
            "created_at": row[2],
            "content": row[3],
            "model": row[4],
            "is_accepted": bool(row[5]),
            "parent_id": row[6] if len(row) > 6 else None,
            "status": row[7] if len(row) > 7 else "completed",
            "created_by": row[8] if len(row) > 8 else "continuation",
            "parameters_snapshot": json.loads(row[9]) if len(row) > 9 and row[9] else {},
            "preset_id": row[10] if len(row) > 10 else "",
            "preset_snapshot": json.loads(row[11]) if len(row) > 11 and row[11] else {},
            "regex_script_ids_snapshot": json.loads(row[12]) if len(row) > 12 and row[12] else [],
            "extracted_context_snapshot": json.loads(row[13]) if len(row) > 13 and row[13] else [],
            "prompt_snapshot": json.loads(row[14]) if len(row) > 14 and row[14] else [],
            "reasoning_content": row[15] if len(row) > 15 else None,
            "agent_artifacts": json.loads(row[16]) if len(row) > 16 and row[16] else None,
            "volume_artifacts": json.loads(row[17]) if len(row) > 17 and row[17] else None,
            "highlights": json.loads(row[18]) if len(row) > 18 and row[18] else [],
        }

    # ===== 历史日志操作 =====

    async def add_history_log(self, log_entry: dict[str, Any]) -> None:
        """添加续写历史日志。

        Args:
            log_entry: 日志条目字典，字段含：
                id, project_id, chapter_id, swipe_id, started_at, finished_at,
                status, model, parameters, prompt_messages, output_text, error_message
        """
        await self.conn.execute(
            """INSERT INTO history_log
               (id, project_id, chapter_id, swipe_id, started_at, finished_at,
                status, model, parameters_json, prompt_messages_json,
                output_text, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log_entry["id"],
                log_entry.get("project_id"),
                log_entry.get("chapter_id"),
                log_entry.get("swipe_id"),
                log_entry.get("started_at"),
                log_entry.get("finished_at"),
                log_entry.get("status"),
                log_entry.get("model"),
                json.dumps(log_entry.get("parameters", {}), ensure_ascii=False),
                json.dumps(log_entry.get("prompt_messages", []), ensure_ascii=False),
                log_entry.get("output_text", ""),
                log_entry.get("error_message"),
            ),
        )
        await self.conn.commit()

    async def save_history_log(self, data: dict[str, Any]) -> None:
        """保存续写历史日志（INSERT OR REPLACE 语义）。

        Args:
            data: 日志条目字典（字段同 :meth:`add_history_log`）
        """
        await self.conn.execute(
            """INSERT OR REPLACE INTO history_log
               (id, project_id, chapter_id, swipe_id, started_at, finished_at,
                status, model, parameters_json, prompt_messages_json,
                output_text, error_message)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                data.get("project_id"),
                data.get("chapter_id"),
                data.get("swipe_id"),
                data.get("started_at"),
                data.get("finished_at"),
                data.get("status"),
                data.get("model"),
                json.dumps(data.get("parameters", {}), ensure_ascii=False),
                json.dumps(data.get("prompt_messages", []), ensure_ascii=False),
                data.get("output_text", ""),
                data.get("error_message"),
            ),
        )
        await self.conn.commit()

    async def list_history_logs(self, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """按条件查询历史日志。

        Args:
            filters: 筛选条件字典，支持：
                - project_id: 项目 ID
                - chapter_id: 章节 ID
                - start_time: 开始时间 ISO 字符串
                - end_time: 结束时间 ISO 字符串
                - limit: 返回条数上限（默认 100）

        Returns:
            日志条目字典列表（按 started_at 降序）
        """
        filters = filters or {}
        conditions: list[str] = []
        params: list[Any] = []

        if filters.get("project_id"):
            conditions.append("project_id = ?")
            params.append(filters["project_id"])
        if filters.get("chapter_id"):
            conditions.append("chapter_id = ?")
            params.append(filters["chapter_id"])
        if filters.get("start_time"):
            conditions.append("started_at >= ?")
            params.append(filters["start_time"])
        if filters.get("end_time"):
            conditions.append("started_at <= ?")
            params.append(filters["end_time"])

        sql = "SELECT * FROM history_log"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY started_at DESC"
        limit = filters.get("limit", 100)
        if limit and int(limit) > 0:
            sql += f" LIMIT {int(limit)}"

        async with self.conn.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
        return [self._row_to_history_log(row) for row in rows]

    async def get_history_log(self, history_id: str) -> dict[str, Any] | None:
        """获取单条历史日志详情。

        Args:
            history_id: 日志 ID

        Returns:
            日志条目字典（含完整 prompt_messages 和 output_text），不存在时返回 None
        """
        async with self.conn.execute(
            "SELECT * FROM history_log WHERE id = ?", (history_id,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_history_log(row)

    async def delete_history_log(self, history_id: str) -> None:
        """删除单条历史日志。

        Args:
            history_id: 日志 ID
        """
        await self.conn.execute(
            "DELETE FROM history_log WHERE id = ?", (history_id,)
        )
        await self.conn.commit()

    async def clear_history_logs(self, project_id: str | None = None) -> None:
        """清空历史日志。

        Args:
            project_id: 指定项目 ID 时只清空该项目的日志，None 时清空全部
        """
        if project_id:
            await self.conn.execute(
                "DELETE FROM history_log WHERE project_id = ?", (project_id,)
            )
        else:
            await self.conn.execute("DELETE FROM history_log")
        await self.conn.commit()

    @staticmethod
    def _row_to_history_log(row: aiosqlite.Row) -> dict[str, Any]:
        """将数据库行转换为历史日志字典。"""
        return {
            "id": row[0],
            "project_id": row[1],
            "chapter_id": row[2],
            "swipe_id": row[3],
            "started_at": row[4],
            "finished_at": row[5],
            "status": row[6],
            "model": row[7],
            "parameters": json.loads(row[8]) if row[8] else {},
            "prompt_messages": json.loads(row[9]) if row[9] else [],
            "output_text": row[10] if row[10] is not None else "",
            "error_message": row[11],
        }

    # ===== 缓存操作 =====

    async def set_cache(
        self, key: str, value: Any, ttl_hours: int = 24, category: str = ""
    ) -> None:
        """设置缓存项。"""
        now = datetime.now()
        from datetime import timedelta

        expires = now + timedelta(hours=ttl_hours) if ttl_hours > 0 else None
        await self.conn.execute(
            """INSERT OR REPLACE INTO cache
               (key, value, created_at, expires_at, category)
               VALUES (?, ?, ?, ?, ?)""",
            (
                key,
                json.dumps(value, ensure_ascii=False),
                now.isoformat(),
                expires.isoformat() if expires else None,
                category,
            ),
        )
        await self.conn.commit()

    async def get_cache(self, key: str) -> Any | None:
        """获取缓存项，过期返回 None。"""
        async with self.conn.execute(
            "SELECT value, expires_at FROM cache WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        value_str, expires_at = row
        # 检查过期
        if expires_at:
            try:
                expires = datetime.fromisoformat(expires_at)
                if datetime.now() > expires:
                    await self.conn.execute("DELETE FROM cache WHERE key = ?", (key,))
                    await self.conn.commit()
                    return None
            except ValueError:
                pass
        return json.loads(value_str) if value_str else None

    async def clear_cache(self, category: str | None = None) -> None:
        """清除缓存，可按 category 过滤。"""
        if category:
            await self.conn.execute("DELETE FROM cache WHERE category = ?", (category,))
        else:
            await self.conn.execute("DELETE FROM cache")
        await self.conn.commit()

    # ===== 预设文件操作 =====

    def save_preset(self, preset_data: dict[str, Any]) -> None:
        """保存预设到文件系统（同步，写入前 .bak 备份）。"""
        preset_id = preset_data.get("id", "default")
        preset_file = get_preset_file_path(self.storage_path, preset_id)
        save_json_with_backup(preset_file, preset_data)

    def load_preset(self, preset_id: str) -> tuple[dict[str, Any] | None, str | None]:
        """加载预设，支持从 .bak 恢复。

        Returns:
            (预设数据, 错误信息) 元组
        """
        preset_file = get_preset_file_path(self.storage_path, preset_id)
        return load_json_with_recovery(preset_file)

    def list_preset_ids(self) -> list[str]:
        """列出所有已保存的预设 ID。"""
        presets_dir = self.storage_path / "presets"
        if not presets_dir.exists():
            return []
        return [
            f.stem for f in presets_dir.glob("*.json") if not f.name.endswith(".bak")
        ]
