"""章节列表组件（含虚拟滚动）。

使用 QTreeView + 自定义 QAbstractItemModel 实现虚拟滚动：
- ``setUniformRowHeights(True)`` 启用虚拟滚动优化
- 扁平单层列表，按 index 排序
- 续写不在列表中显示（仅在续写面板可见）；接受续写会提升为独立章节插入
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
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from novelforge.models import Chapter, Continuation

logger = logging.getLogger(__name__)


class ChapterTreeModel(QAbstractItemModel):
    """章节列表数据模型（扁平单层，仅章节）。

    支持虚拟滚动（配合 QTreeView.setUniformRowHeights(True)）。
    续写不在此列表中显示（仅在续写面板可见）；接受续写会提升为
    独立章节插入到列表中（见 ChapterService.promote_continuation_to_chapter）。

    角色：
    - ``Qt.ItemDataRole.DisplayRole``: 章节标题
    - ``Qt.ItemDataRole.UserRole``: Chapter 对象
    - ``Qt.ItemDataRole.UserRole + 1``: 节点类型（恒为 "chapter"）
    """

    NODE_CHAPTER = "chapter"

    def __init__(self, parent=None) -> None:
        """初始化空模型。"""
        super().__init__(parent)
        self._chapters: list[Chapter] = []
        self._filtered_indices: list[int] = []
        self._filter_kw: str = ""
        self._current_chapter_id: str | None = None

    # ===== 数据接口 =====

    def _sorted_chapters(self) -> list[Chapter]:
        """按 index 排序的章节列表。"""
        return sorted(self._chapters, key=lambda c: c.index)

    def _rebuild_filter(self) -> None:
        """根据 _chapters 与过滤关键词重建 _filtered_indices。"""
        kw = self._filter_kw
        sorted_chs = self._sorted_chapters()
        if kw:
            self._filtered_indices = [
                i for i, ch in enumerate(sorted_chs) if kw in ch.title.lower()
            ]
        else:
            self._filtered_indices = list(range(len(sorted_chs)))

    def set_chapters(self, chapters: list[Chapter]) -> None:
        """设置章节列表（替换全部）。"""
        self.beginResetModel()
        self._chapters = list(chapters)
        self._rebuild_filter()
        self.endResetModel()

    def set_filter(self, keyword: str) -> None:
        """按标题关键词过滤章节。"""
        self.beginResetModel()
        self._filter_kw = keyword.strip().lower() if keyword.strip() else ""
        self._rebuild_filter()
        self.endResetModel()

    def get_all_chapters(self) -> list[Chapter]:
        """获取所有章节（未过滤，按 index 排序）。"""
        return self._sorted_chapters()

    def find_chapter(self, chapter_id: str) -> Chapter | None:
        """按 ID 查找章节。"""
        for ch in self._chapters:
            if ch.id == chapter_id:
                return ch
        return None

    def update_chapter(self, chapter: Chapter) -> None:
        """更新单个章节数据。"""
        for i, ch in enumerate(self._chapters):
            if ch.id == chapter.id:
                self._chapters[i] = chapter
                break
        self.beginResetModel()
        self._rebuild_filter()
        self.endResetModel()

    # ===== QAbstractItemModel 实现 =====

    def index(self, row: int, column: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        """创建模型索引。"""
        if parent.isValid() or not self.hasIndex(row, column, parent):
            return QModelIndex()
        if 0 <= row < len(self._filtered_indices):
            return self.createIndex(row, column, None)
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:
        """获取父索引（扁平列表无父节点）。"""
        return QModelIndex()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """行数。"""
        if parent.isValid():
            return 0
        return len(self._filtered_indices)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """列数。"""
        return 1

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        """获取节点数据。"""
        if not index.isValid():
            return None
        row = index.row()
        if row < 0 or row >= len(self._filtered_indices):
            return None
        sorted_chapters = self._sorted_chapters()
        chapter = sorted_chapters[self._filtered_indices[row]]
        if role == Qt.ItemDataRole.DisplayRole:
            return chapter.title
        elif role == Qt.ItemDataRole.UserRole:
            return chapter
        elif role == Qt.ItemDataRole.UserRole + 1:
            return self.NODE_CHAPTER
        elif role == Qt.ItemDataRole.ToolTipRole:
            return f"字数: {chapter.word_count}"
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

    # ===== 当前章节高亮 =====

    def _row_of_chapter(self, chapter_id: str) -> int | None:
        """查找章节 ID 在过滤后可见列表中的行号，不可见返回 None。"""
        sorted_chs = self._sorted_chapters()
        for row, idx in enumerate(self._filtered_indices):
            if sorted_chs[idx].id == chapter_id:
                return row
        return None

    def set_current_chapter(self, chapter_id: str | None) -> None:
        """设置当前选中章节 ID，触发旧/新行 BackgroundRole 重绘。

        持续高亮不依赖视图焦点：失焦时 BackgroundRole 仍显现，
        避免长列表中找不到当前章节。
        """
        if chapter_id == self._current_chapter_id:
            return
        old_id = self._current_chapter_id
        self._current_chapter_id = chapter_id
        for cid in (old_id, chapter_id):
            if not cid:
                continue
            row = self._row_of_chapter(cid)
            if row is None:
                continue
            idx = self.index(row, 0)
            self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.BackgroundRole])


class ChapterHighlightDelegate(QStyledItemDelegate):
    """章节列表项委托：为当前选中章节绘制持续高亮背景。

    绕开 QSS ``::item:selected`` 对 BackgroundRole 的覆盖问题：
    在 ``paint()`` 中先填充高亮背景，再调用父类绘制选中/hover 态，
    确保失焦时高亮仍可见。
    """

    HIGHLIGHT_COLOR = QColor(0, 122, 255, 51)  # System Blue alpha≈0.2

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index: QModelIndex,
    ) -> None:
        """绘制项：当前章节先填高亮背景，再走父类绘制。"""
        chapter = index.data(Qt.ItemDataRole.UserRole)
        model = index.model()
        current_id = getattr(model, "_current_chapter_id", None)
        if chapter is not None and current_id and chapter.id == current_id:
            painter.save()
            painter.fillRect(option.rect, self.HIGHLIGHT_COLOR)
            painter.restore()
        super().paint(painter, option, index)


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
        rename_requested(str): 请求重命名（chapter_id）
        delete_requested(str): 请求删除（chapter_id）
        merge_requested(str): 请求合并（chapter_id）
        split_requested(str): 请求拆分（chapter_id）
        export_requested(str): 请求导出（chapter_id，M1 灰显）
    """

    chapter_selected = Signal(str)
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
        self._tree.setRootIsDecorated(False)
        self._tree.setExpandsOnDoubleClick(False)
        self._tree.setSelectionMode(QTreeView.SelectionMode.SingleSelection)
        self._tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)

        self._model = ChapterTreeModel()
        self._tree.setModel(self._model)
        # 安装自定义委托：绘制当前选中章节持续高亮（绕开 QSS 选中态覆盖）
        self._tree.setItemDelegate(ChapterHighlightDelegate(self._tree))
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

    # ===== 外部接口 =====

    def set_chapters(self, chapters: list[Chapter]) -> None:
        """设置章节列表。"""
        self._model.set_chapters(chapters)
        self._hint_label.setVisible(not chapters)

    def update_chapter(self, chapter: Chapter) -> None:
        """更新单个章节显示。"""
        self._model.update_chapter(chapter)

    def get_current_chapter(self) -> Chapter | None:
        """获取当前选中的章节。"""
        idx = self._tree.currentIndex()
        if not idx.isValid():
            return None
        return idx.data(Qt.ItemDataRole.UserRole)

    def select_chapter(self, chapter_id: str) -> None:
        """选中指定章节。"""
        chapters = self._model.get_all_chapters()
        for i, ch in enumerate(chapters):
            if ch.id == chapter_id:
                idx = self._model.index(i, 0)
                self._tree.setCurrentIndex(idx)
                self._model.set_current_chapter(chapter_id)
                return

    def set_current_chapter(self, chapter_id: str | None) -> None:
        """设置当前选中章节高亮（持续高亮，失焦不失色）。

        三路径同步：用户点击 ``_on_tree_clicked`` / 程序化 ``select_chapter``
        / MainWindow ``_on_chapter_selected`` 直接调用。
        """
        self._model.set_current_chapter(chapter_id)

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
        chapter = index.data(Qt.ItemDataRole.UserRole)
        if chapter:
            self._model.set_current_chapter(chapter.id)
            self.chapter_selected.emit(chapter.id)

    # ===== 右键菜单 =====

    def _on_context_menu(self, pos) -> None:
        """右键菜单。"""
        index = self._tree.indexAt(pos)
        if not index.isValid():
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
