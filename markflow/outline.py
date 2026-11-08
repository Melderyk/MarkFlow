"""Collapsible outline with flat native rows and a separate heading hierarchy.

Do not expose hundreds of recursively nested QTreeWidget accessible providers.
On affected Windows UIA clients, collapsing then focusing such a tree can stall
the GUI and allocate gigabytes outside Python. Native rows here are siblings;
indentation and collapse state remain part of the outline's own small model.
"""
import weakref

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem


NODE_ROLE = Qt.ItemDataRole.UserRole + 1


class OutlineItem:
    def __init__(self, titles):
        self.title = titles[0]
        self.children = []
        self._parent = None
        self._tree = None
        self._expanded = False
        self._data = {}
        self.depth = 0

    def text(self, column):
        return self.title

    def setData(self, column, role, value):
        self._data[(column, role)] = value

    def data(self, column, role):
        return self._data.get((column, role))

    def childCount(self):
        return len(self.children)

    def child(self, index):
        return self.children[index]

    def parent(self):
        return self._parent

    def addChild(self, item):
        item._parent = self
        item.depth = self.depth + 1
        self.children.append(item)
        tree = self._tree() if self._tree else None
        if tree is not None:
            tree._append(item)

    def isExpanded(self):
        return self._expanded

    def setExpanded(self, expanded):
        self._expanded = bool(expanded)
        tree = self._tree() if self._tree else None
        if tree is not None:
            tree._refresh()


class OutlineTree(QTreeWidget):
    headingClicked = Signal(object)

    def __init__(self):
        super().__init__()
        self._roots = []
        self._nodes = []
        self._rows = {}
        self._updating = False
        self._disclosure_pressed = False
        self.setHeaderHidden(True)
        self.setRootIsDecorated(False)
        self.setIndentation(0)
        self.setUniformRowHeights(True)
        self.setExpandsOnDoubleClick(False)
        self.setAccessibleName('文档大纲')
        self.setAccessibleDescription('上下降选择标题，回车定位，左右方向键折叠或展开。')
        self.itemClicked.connect(self._clicked)

    def begin_update(self):
        self._updating = True
        self.setUpdatesEnabled(False)

    def end_update(self):
        self._updating = False
        self._refresh()
        self.setUpdatesEnabled(True)

    def clear(self):
        self._roots.clear()
        self._nodes.clear()
        self._rows.clear()
        super().clear()

    def _append(self, node):
        node._tree = weakref.ref(self)
        self._nodes.append(node)
        row = QTreeWidgetItem([node.title])
        row.setData(0, NODE_ROLE, node)
        # Always a native top-level row, including headings at levels 2–6.
        QTreeWidget.addTopLevelItem(self, row)
        self._rows[node] = row
        for child in node.children:
            self._append(child)
        self._refresh()

    def addTopLevelItem(self, item):
        self._roots.append(item)
        self._append(item)

    def topLevelItemCount(self):
        return len(self._roots)

    def topLevelItem(self, index):
        return self._roots[index]

    @staticmethod
    def _indent(node):
        return '\u2002' * (node.depth * 2)

    def _refresh(self):
        if self._updating:
            return
        hidden = {}
        for node in self._nodes:
            parent = node.parent()
            hidden[node] = bool(parent and (hidden[parent] or not parent.isExpanded()))
            row = self._rows[node]
            marker = ('▼ ' if node.isExpanded() else '▶ ') if node.children else '  '
            label = self._indent(node) + marker + node.title
            if row.text(0) != label:
                row.setText(0, label)
            row.setHidden(hidden[node])
            state = ('已展开' if node.isExpanded() else '已折叠') if node.children else ''
            row.setData(0, Qt.ItemDataRole.AccessibleTextRole,
                        f'{node.title}，第 {node.depth+1} 级 {state}')
            row.setToolTip(0, node.title)

    def _clicked(self, row, column):
        node = row.data(0, NODE_ROLE)
        if node is not None:
            self.headingClicked.emit(node)

    def _current_node(self):
        row = QTreeWidget.currentItem(self)
        return row.data(0, NODE_ROLE) if row else None

    def mousePressEvent(self, event):
        row = self.itemAt(event.position().toPoint())
        node = row.data(0, NODE_ROLE) if row else None
        if node and node.children and event.button() == Qt.MouseButton.LeftButton:
            rect = QTreeWidget.visualItemRect(self, row)
            left = rect.left() + self.fontMetrics().horizontalAdvance(self._indent(node))
            if left <= event.position().x() < left + 20:
                self._disclosure_pressed = True
                self.setCurrentItem(node)
                self.setFocus()
                node.setExpanded(not node.isExpanded())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._disclosure_pressed:
            self._disclosure_pressed = False
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event):
        row = self.itemAt(event.position().toPoint())
        node = row.data(0, NODE_ROLE) if row else None
        if node and node.children:
            node.setExpanded(not node.isExpanded())
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self, event):
        node = self._current_node()
        if node:
            key = event.key()
            if key == Qt.Key.Key_Right and node.children:
                node.setExpanded(True)
            elif key == Qt.Key.Key_Left:
                if node.children and node.isExpanded():
                    node.setExpanded(False)
                elif node.parent():
                    self.setCurrentItem(node.parent())
            elif key == Qt.Key.Key_Space and node.children:
                node.setExpanded(not node.isExpanded())
            elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.headingClicked.emit(node)
            else:
                return super().keyPressEvent(event)
            event.accept()
            return
        super().keyPressEvent(event)

    def setCurrentItem(self, item):
        return QTreeWidget.setCurrentItem(self, self._rows[item])

    def scrollToItem(self, item, *args):
        parent = item.parent()
        while parent:
            parent._expanded = True
            parent = parent.parent()
        self._refresh()
        return QTreeWidget.scrollToItem(self, self._rows[item], *args)

    def visualItemRect(self, item):
        return QTreeWidget.visualItemRect(self, self._rows[item])

    def findItems(self, text, flags, column=0):
        return [node for node in self._nodes if node.title == text]

    def collapseAll(self):
        for node in self._nodes:
            node._expanded = False
        self._refresh()

    def expandAll(self):
        for node in self._nodes:
            node._expanded = True
        self._refresh()
