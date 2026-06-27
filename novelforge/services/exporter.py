"""导出服务。

提供项目与章节的导出功能：
- 导出完整 TXT（按章节顺序拼接，标题可选）
- 导出单章 TXT / Markdown（Markdown 含 H1 章节标题）
- 项目备份 zip（含 manifest.json + chapters/ + continuations.json + project.json）
- 从 zip 导入恢复项目

manifest.json 结构::

    {
        "project": {...},          # 项目元数据
        "presets": [...],          # 引用的预设列表
        "regex_scripts": [...],    # 引用的正则脚本列表
        "exported_at": "...",      # 导出时间 ISO 格式
        "version": "1.0"           # manifest 版本
    }
"""
from __future__ import annotations

import json
import logging
import tempfile
import uuid
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from novelforge.models import Chapter, Continuation, Project
from novelforge.services.storage_service import StorageService

logger = logging.getLogger(__name__)

# manifest 版本
MANIFEST_VERSION = "1.0"


def _format_chapter_title(chapter: Chapter) -> str:
    """格式化章节标题（含序号）。

    Args:
        chapter: 章节对象

    Returns:
        形如 ``第N章 标题`` 的字符串
    """
    return f"第{chapter.index + 1}章 {chapter.title}".strip()


def export_full_txt(
    storage_service: StorageService,
    project_id: str,
    output_path: str | Path,
    include_titles: bool = True,
) -> int:
    """导出项目完整 TXT。

    按章节 index 顺序拼接所有章节正文（含已接受的续写，已追加到章节 content）。

    Args:
        storage_service: 存储服务
        project_id: 项目 ID
        output_path: 输出文件路径
        include_titles: 是否包含章节标题作为分隔

    Returns:
        导出字数（总字符数）
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 加载所有章节（含正文）
    chapters_meta = storage_service.list_chapters(project_id)
    chapters: list[Chapter] = []
    for meta in chapters_meta:
        full = storage_service.load_chapter(meta.id)
        if full is not None:
            chapters.append(full)

    # 按 index 排序
    chapters.sort(key=lambda c: c.index)

    parts: list[str] = []
    total_chars = 0
    for chapter in chapters:
        content = chapter.content or ""
        if include_titles:
            title = _format_chapter_title(chapter)
            parts.append(f"{title}\n\n{content}\n\n")
        else:
            parts.append(f"{content}\n\n")
        total_chars += len(content)

    text = "".join(parts)
    try:
        output_path.write_text(text, encoding="utf-8")
        logger.info(
            "导出完整 TXT: %s（%d 章, %d 字）",
            output_path,
            len(chapters),
            total_chars,
        )
    except OSError as e:
        logger.error("写入导出文件失败 %s: %s", output_path, e)
        raise

    return total_chars


def export_chapter_txt(chapter: Chapter, output_path: str | Path) -> int:
    """导出单章 TXT。

    Args:
        chapter: 章节对象（含正文）
        output_path: 输出文件路径

    Returns:
        导出字数
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content = chapter.content or ""
    try:
        output_path.write_text(content, encoding="utf-8")
        logger.info("导出单章 TXT: %s（%d 字）", output_path, len(content))
    except OSError as e:
        logger.error("写入章节 TXT 失败 %s: %s", output_path, e)
        raise

    return len(content)


def export_chapter_markdown(chapter: Chapter, output_path: str | Path) -> int:
    """导出单章 Markdown（含 H1 章节标题）。

    格式::

        # 第N章 标题

        正文

    Args:
        chapter: 章节对象（含正文）
        output_path: 输出文件路径

    Returns:
        导出字数（正文部分，不含标题）
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    title = _format_chapter_title(chapter)
    content = chapter.content or ""
    markdown = f"# {title}\n\n{content}"

    try:
        output_path.write_text(markdown, encoding="utf-8")
        logger.info("导出单章 Markdown: %s（%d 字）", output_path, len(content))
    except OSError as e:
        logger.error("写入章节 Markdown 失败 %s: %s", output_path, e)
        raise

    return len(content)


def export_project_backup(
    storage_service: StorageService,
    preset_service: Any,
    regex_service: Any,
    project_id: str,
    output_path: str | Path,
) -> str:
    """导出项目备份为 zip。

    zip 内容：
    - ``manifest.json``: 项目元数据、预设引用、正则引用、导出时间、版本
    - ``project.json``: 项目完整数据
    - ``chapters/{chapter_id}.txt``: 每章正文
    - ``chapters/{chapter_id}.meta.json``: 章节元数据
    - ``continuations.json``: 所有章节的续写版本

    Args:
        storage_service: 存储服务
        preset_service: 预设服务（用于导出引用的预设）
        regex_service: 正则服务（用于导出引用的正则脚本）
        project_id: 项目 ID
        output_path: 输出 zip 文件路径

    Returns:
        manifest.json 在 zip 中的路径
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # 加载项目
    project = storage_service.load_project(project_id)
    if project is None:
        raise ValueError(f"项目不存在: {project_id}")

    # 加载所有章节（含正文和续写）
    chapters_meta = storage_service.list_chapters(project_id)
    chapters: list[Chapter] = []
    for meta in chapters_meta:
        full = storage_service.load_chapter(meta.id)
        if full is not None:
            chapters.append(full)
    chapters.sort(key=lambda c: c.index)

    # 收集预设引用
    presets_data: list[dict[str, Any]] = []
    preset_id = project.preset_id or "default"
    try:
        preset = preset_service.load_preset(preset_id)
        if preset is not None:
            presets_data.append(preset.model_dump(mode="json", by_alias=True))
    except Exception as e:
        logger.warning("加载预设 %s 失败: %s", preset_id, e)

    # 收集正则引用（global + scoped + preset）
    regex_scripts_data: list[dict[str, Any]] = []
    try:
        ordered = regex_service.get_ordered_scripts(
            project_id=project_id,
            preset_id=preset_id,
            include_disabled=True,
        )
        for script, scope in ordered:
            data = script.model_dump(mode="json", by_alias=True)
            data["_scope"] = scope
            regex_scripts_data.append(data)
    except Exception as e:
        logger.warning("加载正则脚本失败: %s", e)

    # 构建 manifest
    manifest = {
        "project": project.model_dump(mode="json"),
        "presets": presets_data,
        "regex_scripts": regex_scripts_data,
        "exported_at": datetime.now().isoformat(),
        "version": MANIFEST_VERSION,
    }

    # 收集所有续写
    all_continuations: list[dict[str, Any]] = []
    for chapter in chapters:
        for cont in chapter.continuations:
            cont_data = cont.model_dump(mode="json")
            cont_data["chapter_id"] = chapter.id
            all_continuations.append(cont_data)

    # 写入 zip
    try:
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
            zf.writestr(
                "project.json",
                json.dumps(
                    project.model_dump(mode="json"), ensure_ascii=False, indent=2
                ),
            )
            zf.writestr(
                "continuations.json",
                json.dumps(all_continuations, ensure_ascii=False, indent=2),
            )
            for chapter in chapters:
                # 章节正文
                zf.writestr(
                    f"chapters/{chapter.id}.txt",
                    chapter.content or "",
                )
                # 章节元数据
                chapter_meta = {
                    "id": chapter.id,
                    "project_id": chapter.id and project_id,
                    "index": chapter.index,
                    "title": chapter.title,
                    "word_count": chapter.word_count,
                    "metadata": chapter.metadata,
                    "created_at": chapter.created_at.isoformat()
                    if hasattr(chapter.created_at, "isoformat")
                    else str(chapter.created_at),
                    "updated_at": chapter.updated_at.isoformat()
                    if hasattr(chapter.updated_at, "isoformat")
                    else str(chapter.updated_at),
                }
                zf.writestr(
                    f"chapters/{chapter.id}.meta.json",
                    json.dumps(chapter_meta, ensure_ascii=False, indent=2),
                )
        logger.info(
            "导出项目备份: %s（%d 章, %d 续写, %d 预设, %d 正则）",
            output_path,
            len(chapters),
            len(all_continuations),
            len(presets_data),
            len(regex_scripts_data),
        )
    except (OSError, zipfile.BadZipFile) as e:
        logger.error("写入项目备份 zip 失败 %s: %s", output_path, e)
        raise

    return "manifest.json"


def import_project_backup(
    storage_service: StorageService,
    preset_service: Any,
    regex_service: Any,
    zip_path: str | Path,
) -> str:
    """从 zip 导入恢复项目。

    解压到临时目录，读取 manifest.json，恢复项目数据：
    - 创建新项目（生成新 project_id，避免 ID 冲突）
    - 恢复所有章节（含正文与续写）
    - 恢复引用的预设与正则脚本（若不存在则创建）

    Args:
        storage_service: 存储服务
        preset_service: 预设服务
        regex_service: 正则服务
        zip_path: zip 文件路径

    Returns:
        新 project_id
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"备份文件不存在: {zip_path}")

    # 解压到临时目录
    with tempfile.TemporaryDirectory(prefix="novelforge_import_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(tmp_path)
        except (OSError, zipfile.BadZipFile) as e:
            logger.error("解压备份 zip 失败 %s: %s", zip_path, e)
            raise

        # 读取 manifest
        manifest_path = tmp_path / "manifest.json"
        if not manifest_path.exists():
            raise ValueError("备份文件缺少 manifest.json")
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            raise ValueError(f"manifest.json 解析失败: {e}") from e

        project_data = manifest.get("project", {})
        if not project_data or "id" not in project_data:
            raise ValueError("manifest.json 缺少 project 字段或项目 ID")

        # 生成新 project_id 避免冲突
        new_project_id = "proj_" + uuid.uuid4().hex[:12]
        project_data["id"] = new_project_id
        # 重置时间戳
        now = datetime.now()
        if isinstance(project_data.get("created_at"), str):
            try:
                project_data["created_at"] = datetime.fromisoformat(
                    project_data["created_at"]
                )
            except ValueError:
                project_data["created_at"] = now
        else:
            project_data["created_at"] = now
        project_data["updated_at"] = now

        # 创建项目
        try:
            project = Project.model_validate(project_data)
        except Exception as e:
            raise ValueError(f"项目数据校验失败: {e}") from e

        storage_service._runner.run(
            storage_service.storage.save_project(project.model_dump(mode="json"))
        )
        logger.info("已创建导入项目: %s (%s)", new_project_id, project.name)

        # 恢复预设（若不存在则创建）
        for preset_data in manifest.get("presets", []):
            try:
                preset_id = preset_data.get("id", "")
                if preset_id and preset_service.load_preset(preset_id) is None:
                    # 预设不存在，保存之
                    from novelforge.models import WritingPreset

                    preset = WritingPreset.model_validate(preset_data)
                    preset_service.save_preset(preset)
                    logger.info("已恢复预设: %s", preset_id)
            except Exception as e:
                logger.warning("恢复预设失败: %s", e)

        # 恢复正则脚本
        for script_data in manifest.get("regex_scripts", []):
            try:
                scope = script_data.pop("_scope", "global")
                script_id = script_data.get("id", "")
                if not script_id:
                    continue
                # 检查是否已存在（按 id 查找）
                existing = regex_service.load_scripts(scope)
                if not any(s.id == script_id for s in existing):
                    from novelforge.models import RegexScript

                    script = RegexScript.model_validate(script_data)
                    regex_service.add_script(
                        script, scope=scope, project_id=new_project_id
                    )
                    logger.info("已恢复正则脚本: %s (scope=%s)", script_id, scope)
            except Exception as e:
                logger.warning("恢复正则脚本失败: %s", e)

        # 恢复章节
        chapters_dir = tmp_path / "chapters"
        if chapters_dir.exists():
            # 收集章节元数据
            meta_files = sorted(
                chapters_dir.glob("*.meta.json"),
                key=lambda p: json.loads(p.read_text(encoding="utf-8")).get("index", 0),
            )
            # 旧 chapter_id -> 新 chapter_id 映射
            chapter_id_map: dict[str, str] = {}
            for meta_file in meta_files:
                try:
                    meta = json.loads(meta_file.read_text(encoding="utf-8"))
                except json.JSONDecodeError as e:
                    logger.warning("章节元数据解析失败 %s: %s", meta_file, e)
                    continue

                old_chapter_id = meta.get("id", "")
                new_chapter_id = "chap_" + uuid.uuid4().hex[:12]
                chapter_id_map[old_chapter_id] = new_chapter_id

                # 读取正文
                content_path = chapters_dir / f"{old_chapter_id}.txt"
                content = ""
                if content_path.exists():
                    try:
                        content = content_path.read_text(encoding="utf-8")
                    except OSError as e:
                        logger.warning("读取章节正文失败 %s: %s", content_path, e)

                # 构建章节数据
                chapter = Chapter(
                    id=new_chapter_id,
                    project_id=new_project_id,
                    index=meta.get("index", 0),
                    title=meta.get("title", ""),
                    content=content,
                    word_count=meta.get("word_count", len(content)),
                    metadata=meta.get("metadata", {}),
                )
                storage_service.save_chapter(chapter)

        # 恢复续写
        continuations_path = tmp_path / "continuations.json"
        if continuations_path.exists():
            try:
                all_conts = json.loads(
                    continuations_path.read_text(encoding="utf-8")
                )
            except json.JSONDecodeError as e:
                logger.warning("continuations.json 解析失败: %s", e)
                all_conts = []

            for cont_data in all_conts:
                old_chapter_id = cont_data.get("chapter_id", "")
                new_chapter_id = chapter_id_map.get(old_chapter_id, "")
                if not new_chapter_id:
                    logger.warning(
                        "续写所属章节 %s 未找到映射，跳过", old_chapter_id
                    )
                    continue

                # 生成新 continuation id
                cont_data["id"] = "cont_" + uuid.uuid4().hex[:12]
                try:
                    cont = Continuation.model_validate(cont_data)
                    storage_service.save_continuation(cont, new_chapter_id)
                except Exception as e:
                    logger.warning("恢复续写失败: %s", e)

    logger.info("项目导入完成: %s", new_project_id)
    return new_project_id
