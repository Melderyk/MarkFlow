from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QTreeWidget

from markflow.outline import OutlineItem, OutlineTree
from tests.test_ui import qt_app


def build_outline():
    tree = OutlineTree()
    tree.resize(320, 600)
    tree.begin_update()
    for chapter in range(12):
        root = OutlineItem([f'Chapter {chapter}'])
        root.setData(0, Qt.ItemDataRole.UserRole, chapter*100)
        tree.addTopLevelItem(root)
        for section in range(40):
            node = OutlineItem([f'Section {chapter}.{section}'])
            node.setData(0, Qt.ItemDataRole.UserRole, chapter*100+section+1)
            root.addChild(node)
        root.setExpanded(True)
    tree.end_update()
    return tree


def test_hundreds_of_nested_headings_have_only_flat_native_providers(qt_app):
    tree = build_outline()
    model = tree.model()
    assert tree.topLevelItemCount() == 12
    assert model.rowCount() == 492
    for row in range(model.rowCount()):
        index = model.index(row, 0)
        assert not index.parent().isValid()
        assert model.rowCount(index) == 0
    root = tree.topLevelItem(0)
    assert root.childCount() == 40
    assert root.child(0).parent() is root
    root.setExpanded(False)
    assert tree._rows[root.child(0)].isHidden()
    assert not tree._rows[tree.topLevelItem(1)].isHidden()
    # Scrolling to a heading opens its ancestors without adding native children.
    tree.scrollToItem(root.child(30))
    assert root.isExpanded() and not tree._rows[root.child(30)].isHidden()
    assert model.rowCount() == 492
    tree.close()


def test_collapse_then_click_next_chapter_preserves_heading_identity(qt_app):
    tree = build_outline()
    tree.show()
    qt_app.processEvents()
    selected = []
    tree.headingClicked.connect(lambda node:selected.append(node.data(0, Qt.ItemDataRole.UserRole)))
    root = tree.topLevelItem(0)
    rect = tree.visualItemRect(root)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(rect.left()+5, rect.center().y()))
    assert not root.isExpanded() and not selected
    next_root = tree.topLevelItem(1)
    rect = tree.visualItemRect(next_root)
    QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier, QPoint(rect.left()+90, rect.center().y()))
    assert selected == [100]
    assert QTreeWidget.topLevelItem(tree, 0).childCount() == 0
    tree.close()


def test_keyboard_collapse_expand_and_heading_activation(qt_app):
    tree = build_outline()
    tree.show()
    qt_app.processEvents()
    root = tree.topLevelItem(0)
    tree.setCurrentItem(root)
    selected = []
    tree.headingClicked.connect(selected.append)
    QTest.keyClick(tree, Qt.Key.Key_Left)
    assert not root.isExpanded()
    QTest.keyClick(tree, Qt.Key.Key_Right)
    assert root.isExpanded()
    tree.setCurrentItem(root.child(0))
    QTest.keyClick(tree, Qt.Key.Key_Left)
    assert tree._current_node() is root
    QTest.keyClick(tree, Qt.Key.Key_Return)
    assert selected == [root]
    assert '文档大纲' == tree.accessibleName()
    tree.close()
