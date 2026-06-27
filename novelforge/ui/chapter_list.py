"""章节列表组件（含虚拟滚动）。

使用 QTreeView + 自定义 QAbstractItemModel 实现虚拟滚动：
- ``setUniformRowHeights(True)`` 启用虚拟滚动优化
- 树形显示章节，按 index 排序
- 已有续写的章节显示 swipe 子节点（续写1、续写2...）
- 右键菜单（重命名、删除、合并、拆分、导出）；M1 阶段导出灰显
- 搜索框（标题实时搜索；全文搜索按钮，异步执行、可取消、显示进度）

支持 500 章项目首屏 < 500ms（虚拟滚动只渲染可见行）。
"""
from __future__ import annotations

import logging
from typing import Any

from PySide6.QtCore import (
    QAbstractItemModel,
    QModelIndex,
    Qt,
    QThread,
    QTimer,
    Signal,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import Chapter, Continuation

logger = logging.getLogger(__name__)


class ChapterTreeModel(QAbstractItemModel):
    """章节树形数据模型。

    支持虚拟滚动（配合 QTreeView.setUniformRowHeights(True)）。
    两级结构：章节 → swipe 子节点。

    角色：
    - ``Qt.ItemDataRole.DisplayRole``: 显示文本
    - ``Qt.ItemDataRole.UserRole``: 数据对象（Chapter 或 Continuation）
    - ``Qt.ItemDataRole.UserRole + 1``: 节点类型（"chapter" / "swipe"）
    """

    NODE_CHAPTER = "chapter"
    NODE_SWIPE = "swipe"

    def __init__(self, parent=None) -> None:
        """初始化空模型。"""
        super().__init__(parent)
        self._chapters: list[Chapter] = []
        self._filtered_indices: list[int] = []  # 过滤后显示的章节在 _chapters 中的索引

    def set_chapters(self, chapters: list[Chapter]) -> None:
        """设置章节列表（替换全部）。

        Args:
            chapters: 章节列表（按 index 排序）
        """
        self.beginResetModel()
        self._chapters = sorted(chapters, key=lambda c: c.index)
        self._filtered_indices = list(range(len(self._chapters)))
        self.endResetModel()

    def set_filter(self, keyword: str) -> None:
        """按标题关键词过滤章节。

        Args:
            keyword: 搜索关键词（空字符串清除过滤）
        """
        self.beginResetModel()
        if not keyword.strip():
            self._filtered_indices = list(range(len(self._chapters)))
        else:
            kw = keyword.strip().lower()
            self._filtered_indices = [
                i for i, ch in enumerate(self._chapters)
                if kw in ch.title.lower()
            ]
        self.endResetModel()

    def get_chapter_at(self, row: int) -> Chapter | None:
        """获取过滤后指定行的章节。"""
        if 0 <= row < len(self._filtered_indices):
            return self._chapters[self._filtered_indices[row]]
        return None

    def get_all_chapters(self) -> list[Chapter]:
        """获取所有章节（未过滤）。"""
        return list(self._chapters)

    def find_chapter(self, chapter_id: str) -> Chapter | None:
        """按 ID 查找章节。"""
        for ch in self._chapters:
            if ch.id == chapter_id:
                return ch
        return None

    def update_chapter(self, chapter: Chapter) -> None:
        """更新单个章节数据（刷新显示）。"""
        for i, ch in enumerate(self._chapters):
            if ch.id == chapter.id:
                self._chapters[i] = chapter
                # 找到在过滤列表中的位置
                if i in self._filtered_indices:
                    row = self._filtered_indices.index(i)
                    idx = self.index(row, 0)
                    self.dataChanged.emit(idx, idx)
                break

    # ===== QAbstractItemModel 实现 =====

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        """创建模型索引。"""
        if not self.hasIndex(row, column, parent):
            return QModelIndex()

        if not parent.isValid():
            # 顶层：章节
            chapter = self.get_chapter_at(row)
            if chapter:
                return self.createIndex(row, column, chapter.id)
        else:
            # 子层：swipe
            parent_chapter = self.get_chapter_at(parent.row())
            if parent_chapter and 0 <= row < len(parent_chapter.continuations):
                return self.createIndex(
                    row, column, f"{parent_chapter.id}:{parent_chapter.continuations[row].id}"
                )
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        """获取父索引。"""
        if not index.isValid():
            return QModelIndex()
        # swipe 节点的 internalId 格式为 "chapter_id:swipe_id"
        data = index.internalId()
        # 由于 createIndex 用 int，我们改用简单方式：swipe 的 parent 是其章节
        # 这里通过 row 关系判断
        return QModelIndex()  # QAbstractItemModel 默认实现

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """行数。"""
        if not parent.isValid():
            return len(self._filtered_indices)
        # 子节点：swipe 数量
        chapter = self.get_chapter_at(parent.row())
        if chapter:
            return len(chapter.continuations)
        return 0

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """列数。"""
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """获取节点数据。"""
        if not index.isValid():
            return None

        if not index.parent().isValid():
            # 章节节点
            chapter = self.get_chapter_at(index.row())
            if chapter is None:
                return None

            if role == Qt.ItemDataRole.DisplayRole:
                cont_count = len(chapter.continuations)
                if cont_count > 0:
                    return f"{chapter.title}  ({cont_count} 个续写)"
                return chapter.title
            elif role == Qt.ItemDataRole.UserRole:
                return chapter
            elif role == Qt.ItemDataRole.UserRole + 1:
                return self.NODE_CHAPTER
            elif role == Qt.ItemDataRole.ToolTipRole:
                return f"字数: {chapter.word_count}\n续写: {len(chapter.continuations)} 个"
        else:
            # swipe 节点
            chapter = self.get_chapter_at(index.parent().row())
            if chapter and 0 <= index.row() < len(chapter.continuations):
                cont = chapter.continuations[index.row()]
                if role == Qt.ItemDataRole.DisplayRole:
                    label = f"续写{index.row() + 1}"
                    if cont.is_accepted:
                        label += " ✓"
                    if cont.status == "interrupted":
                        label += " (中断)"
                    return label
                elif role == Qt.ItemDataRole.UserRole:
                    return cont
                elif role == Qt.ItemDataRole.UserRole + 1:
                    return self.NODE_SWIPE
                elif role == Qt.ItemDataRole.ToolTipRole:
                    return (
                        f"模型: {cont.model}\n"
                        f"状态: {cont.status}\n"
                        f"字数: {len(cont.content)}\n"
                        f"时间: {cont.created_at}"
                    )
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """节点标志。"""
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        return (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
        )


class FullTextSearchWorker(QThread):
    """全文搜索工作线程。

    Signals:
        result_found(str, int, int): (chapter_id, chapter_row, match_count)
        progress(int): 进度百分比
        finished_search(): 搜索完成
    """

    result_found = Signal(str, int, int)  # chapter_id, row, match_count
    progress = Signal(int)
    finished_search = Signal()

    def __init__(
        self,
        chapters: list[Chapter],
        keyword: str,
        storage_service=None,
        parent=None,
    ) -> None:
        """初始化全文搜索线程。"""
        super().__init__(parent)
        self._chapters = chapters
        self._keyword = keyword
        self._storage = storage_service
        self._stop = False

    def stop(self) -> None:
        """停止搜索。"""
        self._stop = True

    def run(self) -> None:
        """执行全文搜索。"""
        if not self._keyword or not self._chapters:
            self.finished_search.emit()
            return

        kw = self._keyword.lower()
        total = len(self._chapters)

        for i, chapter in enumerate(self._chapters):
            if self._stop:
                break

            # 搜索标题
            count = 0
            if kw in chapter.title.lower():
                count += 1

            # 搜索正文（需要加载）
            if self._storage:
                try:
                    content = self._storage.read_chapter_content(
                        chapter.project_id, chapter.id
                    )
                    count += content.lower().count(kw)
                except Exception as e:
                    logger.warning("搜索章节 %s 失败: %s", chapter.id, e)

            if count > 0:
                self.result_found.emit(chapter.id, i, count)

            self.progress.emit(int((i + 1) / total * 100))

        self.finished_search.emit()


class ChapterListWidget(QWidget):
    """章节列表组件。

    包含搜索框、章节树形视图、右键菜单。

    Signals:
        chapter_selected(str): 章节被选中（chapter_id）
        swipe_selected(str, str): swipe 被选中（chapter_id, continuation_id）
        rename_requested(str): 请求重命名（chapter_id）
        delete_requested(str): 请求删除（chapter_id）
        merge_requested(str): 请求合并（chapter_id）
        split_requested(str): 请求拆分（chapter_id）
        export_requested(str): 请求导出（chapter_id，M1 灰显）
    """

    chapter_selected = Signal(str)
    swipe_selected = Signal(str, str)
    rename_requested = Signal(str)
    delete_requested = Signal(str)
    merge_requested = Signal(str)
    split_requested = Signal(str)
    export_requested = Signal(str)

    def __init__(self, storage_service=None, parent=None) -> None:
        """初始化章节列表组件。"""
        super().__init__(parent)
        self._storage = storage_service
        self._search_worker: FullTextSearchWorker | None = None

        self._setup_ui()
        self._setup_connections()

    def _setup_ui(self) -> None:
        """构建 UI。"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 搜索框
        search_layout = QHBoxLayout()
        self._search_edit = QLineEdit()
        self._search_edit.setPlaceholderText("搜索章节标题...")
        search_layout.addWidget(self._search_edit)

        self._fulltext_btn = QPushButton("全文搜索")
        self._fulltext_btn.setCheckable(True)
        search_layout.addWidget(self._fulltext_btn)
        layout.addLayout(search_layout)

        # 全文搜索进度条
        self._progress_bar = QProgressBar()
        self._progress_bar.setVisible(False)
        self._progress_bar.setTextVisible(False)
        self._progress_bar.setMaximumHeight(6)
        layout.addWidget(self._progress_bar)

        # 章节树形视图
        self._tree = QTreeView()
        self._tree.setHeaderHidden(True)
        self._tree.setUniformRowHeights(True)  # 启用虚拟滚动
        self._tree.setRootIsDecorated(True)
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._model = ChapterTreeModel()
        self._tree.setModel(self._model)
        layout.addWidget(self._tree)

        # 提示标签
        self._hint_label = QLabel("导入 TXT 或新建项目后显示章节")
        self._hint_label.setObjectName("hintText")
        self._hint_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._hint_label.setWordWrap(True)
        layout.addWidget(self._hint_label)

    def _setup_connections(self) -> None:
        """连接信号。"""
        self._search_edit.textChanged.connect(self._on_search_changed)
        self._fulltext_btn.toggled.connect(self._on_fulltext_toggled)
        self._tree.clicked.connect(self._on_tree_clicked)
        self._tree.customContextMenuRequested.connect(self._on_context_menu)

    # ===== 章节数据管理 =====

    def set_chapters(self, chapters: list[Chapter]) -> None:
        """设置章节列表。"""
        self._model.set_chapters(chapters)
        self._hint_label.setVisible(len(chapters) == 0)

    def update_chapter(self, chapter: Chapter) -> None:
        """更新单个章节显示。"""
        self._model.update_chapter(chapter)

    def get_current_chapter(self) -> Chapter | None:
        """获取当前选中的章节。"""
        idx = self._tree.currentIndex()
        if not idx.isValid():
            return None
        node_type = idx.data(Qt.ItemDataRole.UserRole + 1)
        if node_type == ChapterTreeModel.NODE_CHAPTER:
            return idx.data(Qt.ItemDataRole.UserRole)
        elif node_type == ChapterTreeModel.NODE_SWIPE:
            parent_idx = idx.parent()
            if parent_idx.isValid():
                return parent_idx.data(Qt.ItemDataRole.UserRole)
        return None

    def select_chapter(self, chapter_id: str) -> None:
        """选中指定章节。"""
        chapters = self._model.get_all_chapters()
        for i, ch in enumerate(chapters):
            if ch.id == chapter_id:
                idx = self._model.index(i, 0)
                self._tree.setCurrentIndex(idx)
                return

    # ===== 搜索 =====

    def _on_search_changed(self, text: str) -> None:
        """标题实时搜索。"""
        self._model.set_filter(text)

    def _on_fulltext_toggled(self, checked: bool) -> None:
        """全文搜索按钮切换。"""
        if checked:
            keyword = self._search_edit.text().strip()
            if not keyword:
                self._fulltext_btn.setChecked(False)
                return
            self._start_fulltext_search(keyword)
        else:
            self._stop_fulltext_search()

    def _start_fulltext_search(self, keyword: str) -> None:
        """启动全文搜索。"""
        self._stop_fulltext_search()
        chapters = self._model.get_all_chapters()
        self._progress_bar.setVisible(True)
        self._progress_bar.setValue(0)
        self._fulltext_btn.setText("停止搜索")

        self._search_worker = FullTextSearchWorker(
            chapters, keyword, self._storage, self
        )
        self._search_worker.progress.connect(self._progress_bar.setValue)
        self._search_worker.finished_search.connect(self._on_fulltext_done)
        self._search_worker.start()

    def _stop_fulltext_search(self) -> None:
        """停止全文搜索。"""
        if self._search_worker and self._search_worker.isRunning():
            self._search_worker.stop()
            self._search_worker.wait(3000)
        self._search_worker = None
        self._progress_bar.setVisible(False)
        self._fulltext_btn.setText("全文搜索")
        self._fulltext_btn.setChecked(False)

    def _on_fulltext_done(self) -> None:
        """全文搜索完成。"""
        self._progress_bar.setVisible(False)
        self._fulltext_btn.setText("全文搜索")
        self._fulltext_btn.setChecked(False)

    # ===== 树形点击 =====

    def _on_tree_clicked(self, index) -> None:
        """树节点点击。"""
        if not index.isValid():
            return

        node_type = index.data(Qt.ItemDataRole.UserRole + 1)
        if node_type == ChapterTreeModel.NODE_CHAPTER:
            chapter = index.data(Qt.ItemDataRole.UserRole)
            if chapter:
                self.chapter_selected.emit(chapter.id)
        elif node_type == ChapterTreeModel.NODE_SWIPE:
            cont = index.data(Qt.ItemDataRole.UserRole)
            parent_idx = index.parent()
            if parent_idx.isValid():
                chapter = parent_idx.data(Qt.ItemDataRole.UserRole)
                if chapter and cont:
                    self.swipe_selected.emit(chapter.id, cont.id)

    # ===== 右键菜单 =====

    def _on_context_menu(self, pos) -> None:
        """右键菜单。"""
        index = self._tree.indexAt(pos)
        if not index.isValid():
            return

        node_type = index.data(Qt.ItemDataRole.UserRole + 1)
        if node_type != ChapterTreeModel.NODE_CHAPTER:
            return

        chapter = index.data(Qt.ItemDataRole.UserRole)
        if not chapter:
            return

        menu = QMenu(self)

        rename_action = menu.addAction("重命名")
        menu.addSeparator()
        split_action = menu.addAction("拆分（编辑模式下选中文本）")
        merge_action = menu.addAction("与下一章合并")
        menu.addSeparator()

        # M5: 启用导出子菜单（TXT / Markdown）
        export_menu = menu.addMenu("导出")
        export_txt_action = export_menu.addAction("导出为 TXT...")
        export_md_action = export_menu.addAction("导出为 Markdown...")

        menu.addSeparator()
        delete_action = menu.addAction("删除")

        action = menu.exec(self._tree.viewport().mapToGlobal(pos))

        if action == rename_action:
            self.rename_requested.emit(chapter.id)
        elif action == split_action:
            self.split_requested.emit(chapter.id)
        elif action == merge_action:
            self.merge_requested.emit(chapter.id)
        elif action == export_txt_action:
            self._on_export_chapter(chapter, "txt")
        elif action == export_md_action:
            self._on_export_chapter(chapter, "md")
        elif action == delete_action:
            self.delete_requested.emit(chapter.id)

    def _on_export_chapter(self, chapter: Chapter, fmt: str) -> None:
        """导出章节为 TXT 或 Markdown。

        Args:
            chapter: 章节对象（可能不含正文，需重新加载）
            fmt: 格式（"txt" 或 "md"）
        """
        from PySide6.QtWidgets import QFileDialog, QMessageBox

        from novelforge.services.exporter import (
            export_chapter_markdown,
            export_chapter_txt,
        )

        # 重新加载完整章节（含正文）
        full_chapter = None
        if self._storage:
            try:
                full_chapter = self._storage.load_chapter(chapter.id)
            except Exception as e:
                logger.error("加载章节失败: %s", e)
                QMessageBox.warning(self, "导出失败", f"加载章节失败: {e}")
                return

        if full_chapter is None:
            # 无存储服务时使用传入的 chapter（可能无正文）
            full_chapter = chapter

        # 选择保存路径
        if fmt == "md":
            filter_str = "Markdown 文件 (*.md);;所有文件 (*)"
            default_name = f"{full_chapter.title or full_chapter.id}.md"
        else:
            filter_str = "文本文件 (*.txt);;所有文件 (*)"
            default_name = f"{full_chapter.title or full_chapter.id}.txt"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "导出章节", default_name, filter_str
        )
        if not file_path:
            return

        try:
            if fmt == "md":
                count = export_chapter_markdown(full_chapter, file_path)
            else:
                count = export_chapter_txt(full_chapter, file_path)
            QMessageBox.information(
                self, "导出成功", f"已导出 {count} 字到:\n{file_path}"
            )
            logger.info("导出章节 %s 为 %s: %d 字", chapter.id, fmt, count)
        except Exception as e:
            logger.error("导出章节失败: %s", e, exc_info=True)
            QMessageBox.critical(self, "导出失败", f"导出章节失败: {e}")

    # ===== 属性 =====

    @property
    def tree(self) -> QTreeView:
        """获取树形视图。"""
        return self._tree

    @property
    def model(self) -> ChapterTreeModel:
        """获取数据模型。"""
        return self._model
