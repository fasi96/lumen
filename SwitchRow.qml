import QtQuick
import qs.Commons
import qs.Ui

// One row of Lumen's Record tab: icon, label (and an optional line or control
// under it), and an on/off switch on the right.
Item {
  id: sr
  property string icon: ""
  property string label: ""
  property string note: ""
  property bool checked: false
  property color foreground: Color.foreground
  property string fontFamily: Style.font.family
  default property alias extra: extraBox.data
  signal toggled()

  width: parent ? parent.width : Style.space(360)
  implicitHeight: Math.max(sw.implicitHeight, labels.implicitHeight) + Style.space(12)

  Text {
    id: glyph
    anchors.left: parent.left
    anchors.verticalCenter: parent.verticalCenter
    width: Style.space(24)
    text: sr.icon
    color: sr.checked ? Color.accent : Qt.darker(sr.foreground, 1.5)
    font.family: sr.fontFamily
    font.pixelSize: Style.font.icon
  }
  Column {
    id: labels
    anchors.left: glyph.right
    anchors.leftMargin: Style.space(8)
    anchors.right: sw.left
    anchors.rightMargin: Style.space(8)
    anchors.verticalCenter: parent.verticalCenter
    spacing: Style.space(3)
    Text {
      width: parent.width
      text: sr.label
      color: sr.foreground
      elide: Text.ElideRight
      font.family: sr.fontFamily
      font.pixelSize: Style.font.body
    }
    Text {
      visible: sr.note !== ""
      width: parent.width
      text: sr.note
      wrapMode: Text.WordWrap
      color: Qt.darker(sr.foreground, 1.5)
      font.family: sr.fontFamily
      font.pixelSize: Style.font.caption
    }
    Column {
      id: extraBox
      width: parent.width
      spacing: Style.space(4)
    }
  }
  ToggleSwitch {
    id: sw
    anchors.right: parent.right
    anchors.verticalCenter: parent.verticalCenter
    checked: sr.checked
    onToggled: sr.toggled()
  }
}
