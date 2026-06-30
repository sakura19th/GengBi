"""项目数据模型。

定义 ``Project``，每个项目对应一本小说。
字段语义参见 spec.md「Project 数据结构」一节。
"""
from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from novelforge.models.ontology import WorldOntology


class ManualOverride(BaseModel):
    """手动拆分/合并操作记录。

    - ``action``：split（拆分）/ merge（合并）
    - ``chapter_id``：操作的目标章节 ID
    - ``position``：split 时为字符偏移量，merge 时忽略
    """

    model_config = ConfigDict(populate_by_name=True)

    action: str = "split"
    chapter_id: str = ""
    position: int = 0


class ChapterSplitRule(BaseModel):
    """章节拆分规则。

    - ``pattern``：章节标题正则（默认 ``^第[一二三四五六七八九十百千零\\d]+[章节回卷]``）
    - ``include_title_in_content``：拆分后章节正文是否包含标题行
    - ``manual_overrides``：手动拆分/合并操作记录列表
    """

    model_config = ConfigDict(populate_by_name=True)

    pattern: str = r"^第[一二三四五六七八九十百千零\d]+[章节回卷]"
    include_title_in_content: bool = False
    manual_overrides: list[ManualOverride] = Field(default_factory=list)


class NovelProfile(BaseModel):
    """小说档案。

    记录小说的基本元信息，用于上下文提取与提示词组装。
    """

    title: str = ""
    author: str = ""
    protagonist: str = ""
    synopsis: str = ""
    world_setting: str = ""
    writing_style: str = ""


class Project(BaseModel):
    """项目（一本小说）。

    字段说明：
    - ``novel_profile``：小说档案（标题/作者/主角/简介/世界观/写作风格）
    - ``preset_id``：绑定的写作预设 ID
    - ``regex_script_ids``：绑定的正则脚本 ID 列表
    - ``extract_config``：上下文提取配置覆盖（null 时用全局配置），
      字段结构：extractor_model/cache_enabled/cache_ttl_hours/
      extractor_prompt_override/lookback_chapters
    - ``chapter_split_rule``：章节拆分规则
    - ``worldbook_id``：项目专属世界书 ID（存储世界观底层条目，由 OntologyExtractor 创建）
    - ``world_ontology``：底层世界观元描述（全文提取一次固化，不随章节变化；
      底层世界观是世界运行的元规则，核心字段变化率 < 5%）
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    name: str = ""
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)
    source_file: str = ""
    novel_profile: NovelProfile = Field(default_factory=NovelProfile)
    preset_id: str = "default"
    regex_script_ids: list[str] = Field(default_factory=list)
    extract_config: dict[str, Any] | None = None
    chapter_split_rule: ChapterSplitRule = Field(default_factory=ChapterSplitRule)
    worldbook_id: str = ""  # 项目专属世界书 ID（存储世界观底层条目）
    world_ontology: Any | None = None  # WorldOntology 模型实例（全文提取一次固化）
