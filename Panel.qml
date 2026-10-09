import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// Lumen's popup, in two tabs. Record: what to capture, four switches (mic, face
// bubble, remove ums, share link) and Start. Recording needs no setup; the three
// add-on switches install what they need the first time they're switched on. Settings: the technical choices
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

  // ---- Where Lumen lives; every tool is called by path, so nothing needs to be on PATH.
  readonly property string home: String(Qt.resolvedUrl(".")).replace("file://", "").replace(/\/$/, "")
  readonly property string gr: home + "/bin/gif-record"
  function gifRecord(args) { Util.execArgv([root.gr].concat(args)) }

  // ---- Add-ons: which are installed (`setup.sh status`, offline and instant), checked
  // every time the popup opens, so one that finished installing shows up straight away.
  property var ready: ({ ums: true, bubble: true, share: true, shortcut: true, webcam: true })
  Process {
    id: statusProc
    command: [root.home + "/setup/setup.sh", "status"]
    stdout: StdioCollector {
      id: statusOut
      waitForEnd: true
      onStreamFinished: { try { root.ready = JSON.parse(statusOut.text) } catch (e) {} }
    }
  }
  function refreshStatus() { statusProc.running = false; statusProc.running = true }
  // An add-on switch: on/off when installed, else a short guided install (which turns it on).
  function addon(name, key, onValue, offValue) {
    if (root.ready[name]) root.choose(key, root[key] === onValue ? offValue : onValue)
    else root.runSetup([name])
  }
  readonly property string addonNote: "Not installed · switch on to add it"

  function runSetup(args) {
    root.close()
    // Omarchy floats its terminal at 875×600; on a screen smaller than that, open a
    // plain (tiled) terminal instead so setup fits.
    var sc = panel.screen
    var small = !!sc && (sc.width < 960 || sc.height < 680)
    Util.execArgv(["setsid", "uwsm-app", "--", "xdg-terminal-exec", "--app-id=" + (small ? "lumen.setup" : "org.omarchy.terminal"), "--title=Lumen",
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
  property string audio: "mic"      // until a choice is saved: the recorder uses the mic if there is one

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
    command: [root.gr, "mics"]
    stdout: StdioCollector {
      id: micsOut
      waitForEnd: true
      onStreamFinished: {
        try { root.micInfo = JSON.parse(micsOut.text) } catch (e) {}
      }
    }
  }
  function refreshMics() { micsProc.running = false; micsProc.running = true }
  // The bubble is for framing yourself and for recordings: it shows while the popup
  // is open and goes away when it closes, unless a recording is starting.
  onOpenedChanged: {
    if (root.opened) {
      refreshMics()
      refreshStatus()
      if (root.camera === "on" && root.ready.bubble) root.gifRecord(["cam-preview"])
    } else if (root.camera === "on") {
      Util.execArgv(["setsid", "-f", root.gr, "cam-idle"])
    }
  }
  Component.onCompleted: { refreshMics(); refreshStatus() }

  FileView {
    path: Quickshell.env("HOME") + "/.config/gif-record/voice.json"
    watchChanges: true
    printErrors: false
    onFileChanged: root.refreshMics()
  }

  // The bar calls this once it has created any of these files that were missing.
  function reloadFiles() { settingsFile.reload(); uploadsFile.reload() }

  function choose(key, value) {
    if (key === "target" || key === "format" || key === "audio" || key === "codec" || key === "share" || key === "clean" || key === "camera" || key === "ai") root[key] = value
    else if (key === "width") root.gifWidth = Number(value)
    else if (key === "mp4Width") root.mp4Width = Number(value)
    else if (key === "mic") root.micInfo = Object.assign({}, root.micInfo, { mic: value })
    else root[key] = Number(value)
    root.gifRecord(["set", key, String(value)])
  }

  function start() {
    root.close()
    // Let the popup get off screen before the picker freezes it into the shot.
    startDelay.restart()
  }

  Timer {
    id: startDelay
    interval: 250
    onTriggered: root.gifRecord(["start",
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
    id: uploadsFile
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

        // ================= Record tab =================
        Column {
          visible: root.tab === "record"
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
                // How this mic sounds in recordings: a preset in one click, or the tuner.
                Dropdown {
                  width: parent.width
                  showLabel: false
                  foreground: root.fg
                  fontFamily: root.fontFamily
                  value: root.activeMicEntry && root.activeMicEntry.tuned ? String(root.activeMicEntry.preset || "Custom") : "Natural"
                  options: {
                    var o = [
                      { value: "Natural", label: "Sound: Natural" },
                      { value: "Podcast", label: "Sound: Podcast · warmer, fuller" },
                      { value: "Crisp", label: "Sound: Crisp · clearer speech" },
                      { value: "Noisy room", label: "Sound: Noisy room · strong noise removal" },
                      { value: "Raw", label: "Sound: Raw · exactly as recorded" }
                    ]
                    var cur = root.activeMicEntry && root.activeMicEntry.tuned ? String(root.activeMicEntry.preset || "Custom") : ""
                    if (cur && ["Natural", "Podcast", "Crisp", "Noisy room", "Raw"].indexOf(cur) === -1)
                      o.push({ value: cur, label: "Sound: " + cur + " (your tuning)" })
                    o.push({ value: "__tune", label: "Fine-tune by ear…" })
                    return o
                  }
                  onChanged: function(v) {
                    if (v === "__tune") { Util.execArgv([root.home + "/bin/voice-tune"]); root.close() }
                    else Util.execArgv([root.home + "/bin/voice-tune", "--set-preset", v, "--mic", root.activeMic])
                  }
                }
              }
            }
            SwitchRow {
              foreground: root.fg
              fontFamily: root.fontFamily
              icon: "󰄀"
              label: "Face bubble (beta)"
              note: !root.ready.bubble ? root.addonNote + " (0.5 GB)"
                    : root.camera === "on" && !root.ready.webcam ? "No webcam plugged in" : ""
              checked: root.camera === "on" && root.ready.bubble
              onToggled: root.addon("bubble", "camera", "on", "off")
              TextLink {
                fontFamily: root.fontFamily
                visible: root.camera === "on" && root.ready.bubble && root.ready.webcam
                text: "Center me"
                onActivated: Util.execArgv([root.home + "/bin/lumen-cam", "center"])
              }
            }
            SwitchRow {
              foreground: root.fg
              fontFamily: root.fontFamily
              visible: root.micOn
              icon: "󰃢"
              label: "Remove ums & pauses"
              note: !root.ready.ums ? root.addonNote + (root.ready.nvidia ? " (4 GB)" : " (2 GB)")
                    : root.clean !== "on" ? "" : "Cut when you stop · uncut copy kept"
              checked: root.clean === "on" && root.ready.ums
              onToggled: root.addon("ums", "clean", "on", "off")
            }
            SwitchRow {
              foreground: root.fg
              fontFamily: root.fontFamily
              visible: root.mp4
              icon: "󰌷"
              label: "Share link when done"
              note: !root.ready.share ? "Uses your own free Cloudflare · switch on to connect it"
                    : root.share === "cloudflare" ? "Link copied the moment you stop" : "Keep it on this computer"
              checked: root.share === "cloudflare" && root.ready.share
              onToggled: root.addon("share", "share", "cloudflare", "off")
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
                   : "") + (root.ready.shortcut ? "Enter or Shift+Alt+Print" : "Enter") + " · click ✦ again to stop"
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
                onClicked: root.ready.share ? Util.execArgv(["setsid", "-f", root.home + "/bin/vshare", "upload", root.lastGif])
                                            : root.runSetup(["share"])
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

          PanelSectionHeader { visible: root.mp4 && root.ready.ums; text: "AI RUNS ON"; foreground: root.fg; fontFamily: root.fontFamily }
          ButtonGroup {
            visible: root.mp4 && root.ready.ums
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
            text: "Add-ons…"
            tooltipText: "Pick what to install: remove ums, face bubble, share links, shortcut, terminal commands"
            onClicked: root.runSetup([])
          }
          Button {
            visible: !root.ready.shortcut
            width: parent.width
            foreground: root.fg
            fontFamily: root.fontFamily
            iconText: "󰌌"
            text: "Add the Shift+Alt+Print shortcut"
            tooltipText: "Adds one marked line to your Hyprland bindings; Add-ons → uninstall takes it out again"
            onClicked: { Util.execArgv([root.home + "/setup/setup.sh", "shortcut"]); shortcutRecheck.restart() }
          }
          Timer { id: shortcutRecheck; interval: 800; onTriggered: root.refreshStatus() }
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
            onClicked: { Util.execArgv([root.home + "/bin/voice-tune"]); root.close() }
          }
          Button {
            visible: root.mp4 && root.ready.share
            width: parent.width
            foreground: root.fg
            fontFamily: root.fontFamily
            iconText: "󰕧"
            text: "Shared videos…"
            tooltipText: "Views, rename, password, delete"
            onClicked: { Util.execArgv([root.home + "/bin/vshare", "dashboard"]); root.close() }
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
