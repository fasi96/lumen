import QtQuick
import qs.Commons

// A small accent-coloured text link ("Center me", "tune…").
Text {
  id: tl
  property string fontFamily: Style.font.family
  signal activated()
  color: Color.accent
  font.family: tl.fontFamily
  font.pixelSize: Style.font.caption
  font.underline: linkMouse.containsMouse
  MouseArea {
    id: linkMouse
    anchors.fill: parent
    hoverEnabled: true
    cursorShape: Qt.PointingHandCursor
    onClicked: tl.activated()
  }
}
