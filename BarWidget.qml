import QtQuick
import QtQuick.Shapes
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Lumen in the bar. The recording itself lives in gif-record; this widget only
// reads the state file that script writes, so the keybinding, the panel and the
// bar always agree.
//
//   idle        "✦"            click opens the panel
//   picking     "✦ …"          the region/window picker is up
//   countdown   "● 3"          click cancels
//   recording   "● 0:07" red   click stops and saves
//   converting  "✦ 42%"        saving the video / making the GIF
//   uploading   "↑ 40%"        a share link's video is still uploading (idle otherwise)
BarWidget {
  id: root
  moduleName: "lumen"

  readonly property string statePath: Quickshell.env("HOME") + "/.cache/gif-record/state.json"

  property var recState: ({ status: "idle" })
  readonly property string status: String(recState.status || "idle")
  readonly property bool busy: status !== "idle"
  property real nowMs: Date.now()

  function reloadState() { stateFile.reload() }

  function parseState(raw) {
    try {
      var s = JSON.parse(raw)
      if (s && typeof s === "object") root.recState = s
    } catch (e) {
      // Caught mid-write; the next change or tick picks it up.
    }
  }

  function clock(ms) {
    var total = Math.max(0, Math.floor(ms / 1000))
    var m = Math.floor(total / 60)
    var s = total % 60
    return m + ":" + (s < 10 ? "0" : "") + s
  }

  // The saved format names the button, so the keybinding's output is visible.
  property string format: "gif"
  readonly property string formatLabel: String(recState.format || format) === "mp4" ? "MP4" : "GIF"

  FileView {
    id: barSettingsFile
    path: Quickshell.env("HOME") + "/.config/gif-record/settings.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try { root.format = String(JSON.parse(text()).format || "gif") } catch (e) {}
    }
  }

  // Share-link uploads in flight, from vshare's progress file.
  property int uploadPct: -1
  FileView {
    id: barUploadsFile
    path: Quickshell.env("HOME") + "/.cache/vshare/state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try {
        var u = JSON.parse(text()).uploads || {}
        var keys = Object.keys(u)
        root.uploadPct = keys.length ? Number(u[keys[0]].pct || 0) : -1
      } catch (e) { root.uploadPct = -1 }
    }
  }

  readonly property string labelText: {
    if (status === "recording") return "● " + clock(nowMs - Number(recState.since || nowMs))
    if (status === "countdown") return "● " + Math.max(1, Math.ceil((Number(recState.until || nowMs) - nowMs) / 1000))
    if (status === "converting") return Number(recState.progress || 0) + "%"
    if (status === "picking") return "…"
    if (uploadPct >= 0) return "↑ " + uploadPct + "%"
    return " "        // idle: the ✦ is drawn as a shape (see below), not text
  }
  // Idle shows only the drawn ✦; every other state is text in the same box.
  readonly property bool iconOnly: status === "idle" && uploadPct < 0

  readonly property string tooltip: {
    if (status === "recording") return "Recording " + (recState.label || "") + " — click to stop and save"
    if (status === "countdown") return "Starting soon — click to cancel"
    if (status === "converting") return formatLabel === "MP4" ? "Saving the video…" : "Making the GIF…"
    if (status === "picking") return "Pick an area or window"
    if (uploadPct >= 0) return "Uploading your share link's video"
    return "Lumen"
  }

  function run(args) { Util.execArgv(["gif-record"].concat(args)) }

  function clicked(b) {
    if (status === "recording" || status === "countdown") {
      run([b === Qt.RightButton ? "cancel" : "stop"])
      return
    }
    if (status === "idle") togglePanel()
  }

  FileView {
    id: stateFile
    path: root.statePath
    watchChanges: true
    printErrors: false
    onLoaded: root.parseState(text())
    onFileChanged: reload()
    onLoadFailed: root.recState = { status: "idle" }
  }

  // A FileView never notices a file that didn't exist when it started watching,
  // and on a fresh install none of these exist until the first recording. So
  // create them (empty) when the bar loads, then have every watcher re-read.
  Process {
    id: ensureFiles
    command: ["sh", "-c",
      "mkdir -p \"$HOME/.cache/gif-record\" \"$HOME/.cache/vshare\" \"$HOME/.config/gif-record\"\n" +
      "[ -e \"$HOME/.cache/gif-record/state.json\" ] || echo '{\"status\":\"idle\"}' > \"$HOME/.cache/gif-record/state.json\"\n" +
      "[ -e \"$HOME/.cache/vshare/state.json\" ] || echo '{\"uploads\":{}}' > \"$HOME/.cache/vshare/state.json\"\n" +
      "[ -e \"$HOME/.config/gif-record/settings.json\" ] || echo '{}' > \"$HOME/.config/gif-record/settings.json\""]
    onExited: {
      stateFile.reload()
      barSettingsFile.reload()
      barUploadsFile.reload()
      if (panelLoader.item) panelLoader.item.reloadFiles()
    }
  }
  Component.onCompleted: ensureFiles.running = true

  // Ticks the elapsed/countdown label, and re-reads the file in case a
  // change notification was missed while something is in flight.
  Timer {
    interval: 250
    repeat: true
    running: root.busy
    onTriggered: {
      root.nowMs = Date.now()
      stateFile.reload()
    }
  }

  // ---- Panel host (same contract as the clock: Bar.findPanelWidget
  //      requires open/close/opened on the bar-widget root).
  readonly property bool opened: panelLoader.item ? panelLoader.item.opened === true : false
  readonly property bool popoutSwitchClosing: panelLoader.item ? panelLoader.item.popoutSwitchClosing === true : false
  readonly property real openPanelIndicatorWidth: button.labelWidth

  function open() { if (panelLoader.item) panelLoader.item.open() }
  function close() { if (panelLoader.item) panelLoader.item.close() }
  function togglePanel() { if (panelLoader.item) panelLoader.item.toggle() }
  function closeForPopoutSwitch() { if (panelLoader.item) panelLoader.item.closeForPopoutSwitch() }

  function injectPanel() {
    var target = panelLoader.item
    if (!target) return
    target.bar = root.bar
    target.settings = root.settings
    target.anchorItem = button
    target.hostWidget = root
  }

  // "hideWhenIdle": true in shell.json keeps the widget loaded (so the left
  // dock can open the panel over IPC) but takes it off the bar until a
  // recording is running or the panel is open.
  readonly property bool collapsed: setting("hideWhenIdle", false) === true && !live && !opened
  implicitWidth: collapsed ? 0 : button.implicitWidth
  implicitHeight: button.implicitHeight
  clip: collapsed

  onBarChanged: injectPanel()
  onSettingsChanged: injectPanel()

  Loader {
    id: panelLoader
    active: true
    source: Qt.resolvedUrl("Panel.qml")
    visible: false
    onLoaded: {
      root.injectPanel()
      Qt.callLater(root.injectPanel)
    }
  }

  IpcHandler {
    target: "lumen"

    function open(): void { root.open() }
    function close(): void { root.close() }
    function show(): void { root.open() }
    function hide(): void { root.close() }
    function toggle(): void { root.togglePanel() }
  }

  // Lumen's own colour, on purpose not the theme's: a violet ✦ in a soft violet
  // rounded box, so the recorder reads as its own thing in the bar. Red while recording.
  readonly property color violet: "#8b8cf6"
  readonly property bool live: status === "recording" || status === "countdown"
  Rectangle {
    id: box
    anchors.centerIn: button
    height: Math.round(button.height * 0.68)
    width: root.iconOnly ? Math.round(height * 1.45) : button.width - Style.spaceReal(2)
    radius: Style.spaceReal(6)
    color: root.live ? Qt.rgba(0.88, 0.40, 0.31, 0.22)
                     : Qt.rgba(0.545, 0.549, 0.965, button.tooltipHovered || root.opened ? 0.30 : 0.18)
    border.width: 1
    border.color: root.live ? Qt.rgba(0.88, 0.40, 0.31, 0.55) : Qt.rgba(0.545, 0.549, 0.965, 0.5)
    Behavior on color { ColorAnimation { duration: 140 } }

    // The ✦ itself, drawn (a font glyph sat low and off to one side).
    Item {
      visible: root.iconOnly
      anchors.centerIn: parent
      width: Math.round(parent.height * 0.62)
      height: width
      Shape {
        width: 24
        height: 24
        scale: parent.width / 24
        transformOrigin: Item.TopLeft
        preferredRendererType: Shape.CurveRenderer
        ShapePath {
          strokeWidth: -1
          fillColor: "#a9aaff"
          PathSvg { path: "M12 2.5c.6 4.8 3.9 8.3 9 9.5-5.1 1.2-8.4 4.7-9 9.5-.6-4.8-3.9-8.3-9-9.5 5.1-1.2 8.4-4.7 9-9.5z" }
        }
      }
    }
  }

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.labelText
    active: root.live
    foreground: "#a9aaff"          // Lumen violet, a touch lighter so it reads on its own tint
    labelVisible: !root.iconOnly
    fixedWidth: root.iconOnly ? Math.round(button.barSize * 1.3) : -1
    tooltipText: root.tooltip
    horizontalMargin: 8.75
    verticalPadding: 8.75

    onPressed: function(b) { root.clicked(b) }
  }
}
