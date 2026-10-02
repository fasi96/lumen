import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Lumen's popup, in two tabs. Record: what to capture, four switches (mic, face
// bubble, remove ums, share link) and Start. Settings: the technical choices
// (format, smoothness, sound, size, compression, delay). Choices are saved through
// `gif-record set`, so Shift+Alt+Print records with whatever was picked here last.
Panel {
  id: root
  moduleName: "lumen"
  ipcTarget: "lumen"
  manageIpc: false

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root

  readonly property color fg: bar ? bar.foreground : Color.foreground
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family

  // ---- Saved choices (~/.config/gif-record/settings.json)
  property string format: "mp4"
  property string target: "region"
  property int fps: 15
  property int mp4Fps: 30
  property int gifWidth: 960
  property int mp4Width: 1920
  property string codec: "compatible"
  property string share: "off"
  property string clean: "off"
  property string camera: "off"
  property string ai: "auto"

  // ---- Popup-only state
  property string tab: "record"

  // ---- First run: until `lumen setup` has finished, Record shows a welcome instead.
  readonly property string home: String(Qt.resolvedUrl(".")).replace("file://", "").replace(/\/$/, "")
  property bool setupDone: true
  onSetupDoneChanged: if (setupDone) settingsFile.reload()
  Timer {
    // Setup writes setup.json (and settings.json) after the bar has loaded, and a
    // watch on a file that didn't exist yet never fires. Check cheaply whether it
    // exists yet, and only then reload the FileView (once), rather than recreating
    // its watcher every tick.
    interval: 1500
    repeat: true
    running: !root.setupDone
    onTriggered: if (!setupProbe.running) setupProbe.running = true
  }
  Process {
    id: setupProbe
    command: ["test", "-s", Quickshell.env("HOME") + "/.config/lumen/setup.json"]
    onExited: function(code) { if (code === 0) setupFile.reload() }
  }
  FileView {
    id: setupFile
    path: Quickshell.env("HOME") + "/.config/lumen/setup.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: { try { root.setupDone = JSON.parse(text()).done === "yes" } catch (e) { root.setupDone = false } }
    onLoadFailed: root.setupDone = false
  }
  function runSetup(args) {
    root.close()
    // Omarchy floats its terminal at 875×600; on a screen smaller than that, open a
    // plain (tiled) terminal instead so setup fits.
    var sc = panel.screen
    var small = !!sc && (sc.width < 960 || sc.height < 680)
    Util.execArgv(["setsid", "uwsm-app", "--", "xdg-terminal-exec", "--app-id=" + (small ? "lumen.setup" : "org.omarchy.terminal"), "--title=Lumen setup",
                   "-e", "bash", "-c", '"$0" "$@"; echo; read -rsn1 -p "  Press any key to close."', root.home + "/setup/setup.sh"].concat(args || []))
  }
  // The mic switch remembers whether sound was mic-only or mic + desktop.
  property string lastAudio: "mic"
  onAudioChanged: if (audio !== "none") lastAudio = audio
  readonly property string mode: target === "region" ? "area" : (target === "window" ? "window" : "screen")
  function chooseMode(m) {
    if (m === "area") root.choose("target", "region")
    else if (m === "window") root.choose("target", "window")
    else if (root.mode !== "screen") root.choose("target", root.screenOptions.length ? root.screenOptions[0].value : "region")
  }
  readonly property bool mp4: format === "mp4"

  // ---- Size estimate for an MP4, in MB per minute. Fitted to real Lumen recordings:
  // 4K at 60 fps with "Compatible" came to ~55 MB/min for everyday screens (a calm
  // desktop 17, a moving visualizer 156), 1080p60 ~30, 1120×640 at 30 fps ~12.
  // Size grows slower than pixels or fps (x264 spends little on what doesn't change).
  readonly property var estimateScreen: {
    var best = null
    for (var i = 0; i < Quickshell.screens.length; i++) {
      var sc = Quickshell.screens[i]
      if (root.target === "monitor:" + sc.name) return sc
      if (!best || sc.width * sc.height > best.width * best.height) best = sc
    }
    return best
  }
  readonly property real estimateMBperMin: {
    var sc = root.estimateScreen
    if (!sc) return 0
    var dpr = sc.devicePixelRatio || 1
    var w = sc.width * dpr, h = sc.height * dpr
    if (root.mp4Width > 0 && root.mp4Width < w) { h = h * root.mp4Width / w; w = root.mp4Width }
    var codec = { fastest: 1.6, compatible: 1.0, smallest: 0.6 }[root.codec] || 1.0
    var video = 55 * Math.pow(w * h / (3840 * 2160), 0.45) * Math.pow(root.mp4Fps / 60, 0.7) * codec
    var audio = root.audio === "none" ? 0 : 1.2
    return video + audio
  }
  function mb(x) { return x >= 10 ? Math.round(x) : Math.round(x * 10) / 10 }
  readonly property bool micOn: mp4 && audio !== "none"
  // The size row edits whichever format is selected.
  readonly property int shownWidth: format === "mp4" ? mp4Width : gifWidth
  property int delay: 0
  property string audio: "none"

  FileView {
    id: settingsFile
    path: Quickshell.env("HOME") + "/.config/gif-record/settings.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try {
        var s = JSON.parse(text())
        if (s.format !== undefined) root.format = String(s.format)
        if (s.target !== undefined) root.target = String(s.target)
        if (s.mp4Fps !== undefined) root.mp4Fps = Number(s.mp4Fps)
        if (s.fps !== undefined) root.fps = Number(s.fps)
        if (s.width !== undefined) root.gifWidth = Number(s.width)
        if (s.mp4Width !== undefined) root.mp4Width = Number(s.mp4Width)
        if (s.codec !== undefined) root.codec = String(s.codec)
        if (s.share !== undefined) root.share = String(s.share)
        if (s.clean !== undefined) root.clean = String(s.clean)
        if (s.camera !== undefined) root.camera = String(s.camera)
        if (s.ai !== undefined && s.ai !== null) root.ai = String(s.ai)
        if (s.delay !== undefined) root.delay = Number(s.delay)
        if (s.audio !== undefined) root.audio = String(s.audio)
      } catch (e) {}
    }
  }

  // ---- Mics: `gif-record mics` lists them, the system default and which have a
  // voice profile. Refreshed whenever the panel opens or a profile is saved, so
  // headsets that connect later show up.
  property var micInfo: ({ default: "", mic: "default", mics: [] })
  readonly property string micSetting: String(micInfo.mic || "default")
  // What the next recording will use: the pinned mic if connected, else the system's.
  readonly property string activeMic: {
    var pinned = root.micSetting
    var list = micInfo.mics || []
    for (var i = 0; i < list.length; i++) if (list[i].name === pinned) return pinned
    return String(micInfo.default || "")
  }
  function micEntry(name) {
    var list = micInfo.mics || []
    for (var i = 0; i < list.length; i++) if (list[i].name === name) return list[i]
    return null
  }
  readonly property var activeMicEntry: micEntry(activeMic)

  Process {
    id: micsProc
    command: ["gif-record", "mics"]
    stdout: StdioCollector {
      id: micsOut
      waitForEnd: true
      onStreamFinished: {
        try { root.micInfo = JSON.parse(micsOut.text) } catch (e) {}
      }
    }
  }
  function refreshMics() { micsProc.running = false; micsProc.running = true }
  onOpenedChanged: if (root.opened) refreshMics()
  Component.onCompleted: refreshMics()

  FileView {
    path: Quickshell.env("HOME") + "/.config/gif-record/voice.json"
    watchChanges: true
    printErrors: false
    onFileChanged: root.refreshMics()
  }

  function choose(key, value) {
    if (key === "target" || key === "format" || key === "audio" || key === "codec" || key === "share" || key === "clean" || key === "camera" || key === "ai") root[key] = value
    else if (key === "width") root.gifWidth = Number(value)
    else if (key === "mp4Width") root.mp4Width = Number(value)
    else if (key === "mic") root.micInfo = Object.assign({}, root.micInfo, { mic: value })
    else root[key] = Number(value)
    Util.execArgv(["gif-record", "set", key, String(value)])
  }

  function start() {
    root.close()
    // Let the popup get off screen before the picker freezes it into the shot.
    startDelay.restart()
  }

  Timer {
    id: startDelay
    interval: 250
    onTriggered: Util.execArgv(["gif-record", "start",
      "--format=" + root.format, "--target=" + root.target,
      "--fps=" + root.fps, "--mp4fps=" + root.mp4Fps,
      "--width=" + root.shownWidth, "--delay=" + root.delay, "--audio=" + root.audio,
      "--codec=" + root.codec, "--share=" + root.share, "--clean=" + root.clean])
  }

  // ---- Monitors, left to right as they sit on the desk.
  function screenLabel(s) {
    if (String(s.name).indexOf("eDP") === 0) return "Laptop"
    var model = String(s.model || "").trim()
    return model !== "" ? model : String(s.name)
  }

  readonly property var screenOptions: {
    var list = []
    for (var i = 0; i < Quickshell.screens.length; i++) list.push(Quickshell.screens[i])
    list.sort(function(a, b) { return a.x - b.x })
    return list.map(function(s) {
      return { value: "monitor:" + s.name, label: root.screenLabel(s), tooltip: s.name + " — " + s.width + "×" + s.height }
    })
  }

  // ---- Uploads in flight, from vshare's progress file.
  property var uploads: []
  FileView {
    path: Quickshell.env("HOME") + "/.cache/vshare/state.json"
    watchChanges: true
    printErrors: false
    onFileChanged: reload()
    onLoaded: {
      try {
        var u = JSON.parse(text()).uploads || {}
        root.uploads = Object.keys(u).map(function(k) { return { title: u[k].title || "Video", pct: u[k].pct || 0 } })
      } catch (e) { root.uploads = [] }
    }
  }

  // ---- Last GIF, from the widget's state file.
  readonly property var recState: hostWidget ? hostWidget.recState : ({})
  readonly property string lastGif: String(recState.last || "")
  readonly property string lastName: lastGif.split("/").pop()

  function open() {
    root.controller.show()
  }

  function close() {
    root.controller.hide()
  }

  function toggle() {
    if (root.opened) root.close()
    else root.open()
  }

  function switchPanel(direction) {
    if (root.bar && typeof root.bar.switchPanelFrom === "function")
      return root.bar.switchPanelFrom(root.barIdentity, direction)
    return false
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Math.max(Style.space(380), body.implicitWidth))
    contentHeight: panel.fittedContentHeight(body.implicitHeight)

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onActivateRequested: root.start()
      onReturnRequested: root.start()
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }

      Column {
        id: body
        width: parent.width
        spacing: Style.space(10)

        // ---- Title and tabs
        Item {
          width: parent.width
          implicitHeight: Math.max(titleText.implicitHeight, tabs.implicitHeight)
          Text {
            id: titleText
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            textFormat: Text.StyledText
            text: "<font color='#8b8cf6'>✦</font>&nbsp; Lumen"
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.subtitle
            font.bold: true
          }
          ButtonGroup {
            id: tabs
            anchors.right: parent.right
            anchors.verticalCenter: parent.verticalCenter
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            fontSize: Style.font.bodySmall
            value: root.tab
            options: [
              { value: "record", label: "Record" },
              { value: "settings", label: "Settings" }
            ]
            onChanged: function(v) { root.tab = v }
          }
        }

        // ================= First run =================
        Column {
          visible: root.tab === "record" && !root.setupDone
          width: parent.width
          spacing: Style.space(10)
          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Record your screen, your face in a bubble, ums removed, and a share link the moment you stop."
            color: root.fg
            font.family: root.fontFamily
            font.pixelSize: Style.font.body
          }
          Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: "Setup takes about 3 minutes: it installs what's missing, asks where the AI runs and where videos go, and checks your mic."
            color: Qt.darker(root.fg, 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
          Button {
            width: parent.width
            bordered: true
            selected: true
            foreground: root.fg
            fontFamily: root.fontFamily
            iconText: "✦"
            text: "Set up Lumen"
            onClicked: root.runSetup([])
          }
        }

        // ================= Record tab =================
        Column {
          visible: root.tab === "record" && root.setupDone
          width: parent.width
          spacing: Style.space(10)

          ButtonGroup {
            width: parent.width
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            value: root.mode
            options: [
              { value: "area", label: "Area", icon: "󰩭", tooltip: "Drag a box around what to record" },
              { value: "window", label: "Window", icon: "󰖳", tooltip: "Click one window to record" },
              { value: "screen", label: "Screen", icon: "󰍹", tooltip: "Everything on a monitor" }
            ]
            onChanged: function(v) { root.chooseMode(v) }
          }
          ButtonGroup {
            visible: root.mode === "screen" && root.screenOptions.length > 1
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            fontSize: Style.font.bodySmall
            value: root.target
            options: root.screenOptions
            onChanged: function(v) { root.choose("target", v) }
          }

          Column {
            width: parent.width
            spacing: 0

            SwitchRow {
              foreground: root.fg
              fontFamily: root.fontFamily
              visible: root.mp4
              icon: "󰍬"
              label: "Mic"
              note: root.micOn ? "" : "Silent"
              checked: root.micOn
              onToggled: root.choose("audio", root.micOn ? "none" : root.lastAudio)
              Column {
                visible: root.micOn
                width: parent.width
                spacing: Style.space(4)
                Dropdown {
                  width: parent.width
                  showLabel: false
                  foreground: root.fg
                  fontFamily: root.fontFamily
                  value: root.micSetting === "default" || !root.micEntry(root.micSetting) ? "default" : root.micSetting
                  options: {
                    var def = root.micEntry(root.micInfo.default)
                    var opts = [{ value: "default", label: "System" + (def ? " (" + def.label + ")" : "") }]
                    var list = root.micInfo.mics || []
                    for (var i = 0; i < list.length; i++) opts.push({ value: list[i].name, label: list[i].label })
                    return opts
                  }
                  onChanged: function(v) { root.choose("mic", v) }
                }
              }
            }
            SwitchRow {
              foreground: root.fg
              fontFamily: root.fontFamily
              icon: "󰄀"
              label: "Face bubble (beta)"
              checked: root.camera === "on"
              onToggled: root.choose("camera", root.camera === "on" ? "off" : "on")
              TextLink {
                  fontFamily: root.fontFamily
                visible: root.camera === "on"
                text: "Center me"
                onActivated: Util.execArgv(["lumen-cam", "center"])
              }
            }
            SwitchRow {
              foreground: root.fg
              fontFamily: root.fontFamily
              visible: root.micOn
              icon: "󰃢"
              label: "Remove ums & pauses"
              note: root.clean !== "on" ? "" : (root.ai === "local" ? "Runs on this computer only"
                    : root.ai === "cloud" ? "Runs on your Cloudflare's AI"
                    : "Graphics card if you have one, else your Cloudflare, else this computer") + " · uncut copy kept"
              checked: root.clean === "on"
              onToggled: root.choose("clean", root.clean === "on" ? "off" : "on")
            }
            SwitchRow {
              foreground: root.fg
              fontFamily: root.fontFamily
              visible: root.mp4
              icon: "󰌷"
              label: "Share link when done"
              note: root.share === "cloudflare" ? "Link copied the moment you stop" : "Keep it on this computer"
              checked: root.share === "cloudflare"
              onToggled: root.choose("share", root.share === "cloudflare" ? "off" : "cloudflare")
            }
            Text {
              visible: !root.mp4
              width: parent.width
              topPadding: Style.space(4)
              wrapMode: Text.WordWrap
              text: "GIF: silent, loops, pastes anywhere. Mic, clean-up and share links are for MP4 (Settings)."
              color: Qt.darker(root.fg, 1.5)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Row {
            visible: root.mp4
            width: parent.width
            spacing: Style.space(10)
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: "Quality"
              color: Qt.darker(root.fg, 1.3)
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
            ButtonGroup {
              anchors.verticalCenter: parent.verticalCenter
              focusable: false
              foreground: root.fg
              fontFamily: root.fontFamily
              fontSize: Style.font.bodySmall
              value: String(root.mp4Width)
              options: [
                { value: "1920", label: "1080p", tooltip: "Sharp text on any screen, small files, saves about 3× faster than Full" },
                { value: "2560", label: "1440p", tooltip: "A little sharper, a little bigger" },
                { value: "0", label: "Full", tooltip: "Your screen's native resolution: biggest files, slowest to save" }
              ]
              onChanged: function(v) { root.choose("mp4Width", v) }
            }
            Text {
              anchors.verticalCenter: parent.verticalCenter
              text: root.mp4Fps + " fps"
              color: Qt.darker(root.fg, 1.6)
              font.family: root.fontFamily
              font.pixelSize: Style.font.caption
            }
          }

          Button {
            width: parent.width
            bordered: true
            selected: true
            foreground: root.fg
            fontFamily: root.fontFamily
            iconText: "●"
            text: "Start recording"
            tooltipText: "Enter · click the bar again to stop"
            onClicked: root.start()
          }
          Text {
            width: parent.width
            text: (root.mp4 && root.estimateMBperMin > 0
                   ? "About " + root.mb(root.estimateMBperMin) + " MB per minute" + (root.mode === "screen" ? "" : " (full screen)") + "\n"
                   : "") + "Enter or Shift+Alt+Print · click ✦ again to stop"
            horizontalAlignment: Text.AlignHCenter
            color: Qt.darker(root.fg, 1.6)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          Repeater {
            model: root.uploads
            delegate: Text {
              required property var modelData
              width: body.width
              elide: Text.ElideMiddle
              text: "󰕒  Uploading " + modelData.title + "  ·  " + modelData.pct + "%"
              color: Qt.darker(root.fg, 1.3)
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }
          }

          PanelSeparator { width: parent.width; visible: root.lastGif !== "" }

          Row {
            visible: root.lastGif !== ""
            width: parent.width
            spacing: Style.space(6)

            Text {
              anchors.verticalCenter: parent.verticalCenter
              width: parent.width - actions.width - parent.spacing
              elide: Text.ElideMiddle
              text: "Last: " + root.lastName + (root.recState.lastSize ? "  ·  " + root.recState.lastSize : "")
              color: Qt.darker(root.fg, 1.3)
              font.family: root.fontFamily
              font.pixelSize: Style.font.bodySmall
            }

            Row {
              id: actions
              anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)

              PanelActionButton {
                iconText: "󰈈"
                tooltipText: "Open"
                foreground: root.fg
                fontFamily: root.fontFamily
                onClicked: { Util.execArgv(["xdg-open", root.lastGif]); root.close() }
              }
              PanelActionButton {
                iconText: "󰆏"
                tooltipText: "Copy to clipboard"
                foreground: root.fg
                fontFamily: root.fontFamily
                // A GIF pastes as an image; an MP4 as the file itself.
                onClicked: root.lastGif.endsWith(".gif")
                  ? Util.execArgv(["bash", "-c", 'wl-copy --type image/gif < "$1"', "_", root.lastGif])
                  : Util.execArgv(["wl-copy", "--type", "text/uri-list", "file://" + root.lastGif])
              }
              PanelActionButton {
                visible: root.lastGif.endsWith(".mp4")
                iconText: "󰌷"
                tooltipText: "Share: copy a link and upload"
                foreground: root.fg
                fontFamily: root.fontFamily
                onClicked: Util.execArgv(["setsid", "-f", "vshare", "upload", root.lastGif])
              }
              PanelActionButton {
                iconText: "󰉋"
                tooltipText: "Show folder"
                foreground: root.fg
                fontFamily: root.fontFamily
                onClicked: {
                  Util.execArgv(["xdg-open", root.lastGif.substring(0, root.lastGif.lastIndexOf("/"))])
                  root.close()
                }
              }
            }
          }
        }

        // ================= Settings tab =================
        Column {
          visible: root.tab === "settings"
          width: parent.width
          spacing: Style.space(8)

          PanelSectionHeader { text: "FORMAT"; foreground: root.fg; fontFamily: root.fontFamily }
          ButtonGroup {
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            value: root.format
            options: [
              { value: "mp4", label: "MP4", icon: "󰕧", tooltip: "Video with sound — smooth, small files" },
              { value: "gif", label: "GIF", icon: "󰵸", tooltip: "Silent looping image — pastes anywhere" }
            ]
            onChanged: function(v) { root.choose("format", v) }
          }

          PanelSectionHeader { text: "SMOOTHNESS"; foreground: root.fg; fontFamily: root.fontFamily }
          ButtonGroup {
            visible: root.format === "gif"
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            value: String(root.fps)
            options: [
              { value: "10", label: "10 fps" },
              { value: "15", label: "15 fps" },
              { value: "24", label: "24 fps" },
              { value: "30", label: "30 fps" }
            ]
            onChanged: function(v) { root.choose("fps", v) }
          }
          ButtonGroup {
            visible: root.mp4
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            value: String(root.mp4Fps)
            options: [
              { value: "30", label: "30 fps" },
              { value: "60", label: "60 fps" }
            ]
            onChanged: function(v) { root.choose("mp4Fps", v) }
          }

          PanelSectionHeader { visible: root.mp4; text: "SOUND"; foreground: root.fg; fontFamily: root.fontFamily }
          ButtonGroup {
            visible: root.mp4
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            value: root.audio
            options: [
              { value: "none", label: "Silent", icon: "󰖁" },
              { value: "mic", label: "Mic", icon: "󰍬", tooltip: "Your voice" },
              { value: "both", label: "Mic + desktop", icon: "󰕾", tooltip: "Your voice and whatever the computer plays" }
            ]
            onChanged: function(v) { root.choose("audio", v) }
          }

          PanelSectionHeader { visible: !root.mp4; text: "GIF SIZE"; foreground: root.fg; fontFamily: root.fontFamily }
          ButtonGroup {
            visible: !root.mp4
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            value: String(root.gifWidth)
            options: [
              { value: "480", label: "Small", tooltip: "Up to 480 px wide" },
              { value: "720", label: "Medium", tooltip: "Up to 720 px wide" },
              { value: "960", label: "Large", tooltip: "Up to 960 px wide" },
              { value: "0", label: "Full", tooltip: "Native resolution — big files" }
            ]
            onChanged: function(v) { root.choose("width", v) }
          }

          PanelSectionHeader { visible: root.mp4; text: "AI RUNS ON"; foreground: root.fg; fontFamily: root.fontFamily }
          ButtonGroup {
            visible: root.mp4
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            fontSize: Style.font.bodySmall     // three long names: at body size "Cloudflare" ran off the edge
            value: root.ai === "gpu" || root.ai === "cpu" ? "local" : root.ai
            options: [
              { value: "auto", label: "Automatic", icon: "󰁨", tooltip: "Your graphics card if it has room, else your Cloudflare's AI, else this computer's processor" },
              { value: "local", label: "This computer", icon: "󰌾", tooltip: "Your audio never leaves this computer. Slower without an NVIDIA card" },
              { value: "cloud", label: "Cloudflare", icon: "󰅟", tooltip: "Your own Cloudflare's AI. Falls back to this computer if it can't be reached" }
            ]
            onChanged: function(v) { root.choose("ai", v) }
          }

          PanelSectionHeader { visible: root.mp4; text: "COMPRESSION"; foreground: root.fg; fontFamily: root.fontFamily }
          ButtonGroup {
            visible: root.mp4
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            value: root.codec
            options: [
              { value: "fastest", label: "Fastest", icon: "󱐋", tooltip: "Saves instantly, biggest files" },
              { value: "compatible", label: "Compatible", icon: "󰄬", tooltip: "H.264, plays everywhere, about half the size" },
              { value: "smallest", label: "Smallest", icon: "󰁅", tooltip: "AV1, about a third of the size; some apps and older iPhones can't play it" }
            ]
            onChanged: function(v) { root.choose("codec", v) }
          }
          Text {
            visible: root.mp4 && root.estimateMBperMin > 0
            width: parent.width
            wrapMode: Text.WordWrap
            text: "About " + root.mb(root.estimateMBperMin) + " MB per minute for a whole screen at these settings, "
                  + "so a 5-minute video is about " + root.mb(root.estimateMBperMin * 5) + " MB. "
                  + "A smaller area is smaller; a busy screen (video, fast scrolling) can be 2–3× more."
            color: Qt.darker(root.fg, 1.4)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }

          PanelSectionHeader { text: "DELAY"; foreground: root.fg; fontFamily: root.fontFamily }
          ButtonGroup {
            focusable: false
            foreground: root.fg
            fontFamily: root.fontFamily
            value: String(root.delay)
            options: [
              { value: "0", label: "None" },
              { value: "3", label: "3 s" },
              { value: "5", label: "5 s" }
            ]
            onChanged: function(v) { root.choose("delay", v) }
          }

          Item { width: 1; height: Style.space(2) }
          Button {
            width: parent.width
            foreground: root.fg
            fontFamily: root.fontFamily
            iconText: "󰒓"
            text: "Setup & accounts…"
            tooltipText: "Run Lumen's setup again: where the AI runs, Cloudflare, your name, the face bubble"
            onClicked: root.runSetup([])
          }
          PanelSectionHeader { visible: root.mp4 && root.audio !== "none"; text: "ADVANCED"; foreground: root.fg; fontFamily: root.fontFamily }
          Button {
            visible: root.mp4 && root.audio !== "none"
            width: parent.width
            foreground: root.fg
            fontFamily: root.fontFamily
            iconText: "󰓃"
            text: root.activeMicEntry
              ? "Tune your mic's sound · now " + (root.activeMicEntry.tuned ? root.activeMicEntry.preset : "not tuned")
              : "Tune your mic's sound…"
            tooltipText: "Record a sample and adjust how this mic sounds, by ear"
            onClicked: { Util.execArgv(["voice-tune"]); root.close() }
          }
          Button {
            visible: root.mp4
            width: parent.width
            foreground: root.fg
            fontFamily: root.fontFamily
            iconText: "󰕧"
            text: "Shared videos…"
            tooltipText: "Views, rename, password, delete"
            onClicked: { Util.execArgv(["vshare", "dashboard"]); root.close() }
          }
          Text {
            width: parent.width
            text: "Saved as you change them"
            horizontalAlignment: Text.AlignHCenter
            color: Qt.darker(root.fg, 1.6)
            font.family: root.fontFamily
            font.pixelSize: Style.font.caption
          }
        }
      }
    }
  }
}
