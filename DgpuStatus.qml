import QtQuick
import Quickshell
import Quickshell.Io
import qs.Ui
import qs.Commons

BarWidget {
  id: root
  moduleName: "reidenxerx.dgpu-status"

  property string powerState: ""
  property string runtimeStatus: ""
  property real suspendedMs: -1
  property real activeMs: -1
  readonly property real asleepPct: (suspendedMs >= 0 && activeMs >= 0 && suspendedMs + activeMs > 0)
                                    ? 100 * suspendedMs / (suspendedMs + activeMs) : -1

  // This plugin's bin/ directory as a plain path (Qt.resolvedUrl percent-encodes it).
  readonly property string pluginBin:
    decodeURIComponent(String(Qt.resolvedUrl("bin/")).replace(/^file:\/\//, ""))
  // The report launcher passes its argument through `bash -c`, so the terminal is only
  // opened on a path made of characters that mean nothing to a shell.
  readonly property bool pluginBinShellSafe:
    /^\/[A-Za-z0-9._\/-]+\/$/.test(pluginBin) && pluginBin.split("/").indexOf("..") < 0

  // sysfs directory of the PCI device bound to the nvidia driver; "" until resolved.
  // Resolved through the driver's binding rather than a hardcoded address, so this keeps
  // working if the address differs on another machine.
  property string devicePath: ""
  property int retryDelay: 30000

  readonly property string stateKind: {
    if (powerState === "D3cold") return "deep"
    if (powerState === "D3hot") return "light"
    if (powerState === "D0") return runtimeStatus === "active" ? "active" : "trans"
    return "unknown"
  }

  readonly property string stateLabel: {
    switch (stateKind) {
    case "deep": return "deep sleep — powered off"
    case "light": return "light sleep — powered, idle"
    case "active": return "awake — in use"
    case "trans": return "waking / settling"
    default: return "unknown"
    }
  }

  // Asleep is the desired state, so it must not shout: muted when powered off, normal when
  // idling but still powered, urgent when actually awake and costing battery.
  // Palette names come from Commons/Color.qml -- this shell has no Material-style
  // mOnSurface/mError roles.
  readonly property color stateColor: {
    switch (stateKind) {
    case "deep": return Color.muted
    case "light": return Color.foreground
    case "active": return Color.urgent
    default: return Color.muted
    }
  }

  // ---------------------------------------------------------------- device resolution
  // One short-lived helper at startup -- and again, with backoff, only if the device is
  // missing -- never a process per poll. Absolute interpreter, fixed argv, no shell.
  Process {
    id: resolveProc
    command: ["/usr/bin/python3", root.pluginBin + "dgpu-status", "device"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: root.acceptDevice(String(text || ""))
    }
    onRunningChanged: {
      if (running) return
      resolveWatchdog.stop()
      if (root.devicePath === "") root.scheduleResolve()
    }
  }

  // The helper lists one directory and exits in milliseconds; if it ever hangs, kill it.
  Timer {
    id: resolveWatchdog
    interval: 3000
    onTriggered: if (resolveProc.running) resolveProc.signal(9)
  }

  Timer {
    id: retryTimer
    onTriggered: root.resolveDevice()
  }

  function resolveDevice() {
    if (root.devicePath !== "" || resolveProc.running) return
    resolveProc.running = true
    resolveWatchdog.restart()
  }

  // Backoff: on a machine with no NVIDIA dGPU this settles at one helper run every ten
  // minutes, in case the driver is loaded later.
  function scheduleResolve() {
    if (retryTimer.running) return
    retryTimer.interval = root.retryDelay
    retryTimer.restart()
    root.retryDelay = Math.min(root.retryDelay * 2, 600000)
  }

  function acceptDevice(out) {
    const path = out.trim()
    if (path.length > 256 || !/^\/sys\/devices\/[A-Za-z0-9:._\/-]+$/.test(path)) return
    const parts = path.split("/")
    if (parts.indexOf("..") >= 0 || parts.indexOf(".") >= 0) return
    root.devicePath = path
  }

  // power_state became unreadable: the device was unbound or removed. Hide, look again.
  function deviceLost() {
    root.devicePath = ""
    root.powerState = ""
    root.runtimeStatus = ""
    root.suspendedMs = -1
    root.activeMs = -1
    root.scheduleResolve()
  }

  function word(raw, fallback) {
    const v = String(raw || "").trim()
    return /^[A-Za-z0-9_]{1,32}$/.test(v) ? v : fallback
  }

  function counter(raw) {
    const v = String(raw || "").trim()
    return /^[0-9]{1,15}$/.test(v) ? Number(v) : -1
  }

  // ---------------------------------------------------------------- polling
  // Reads ONLY power_state and the runtime PM counters. Do NOT add current_link_speed,
  // current_link_width or config: those touch the live PCIe link / config space and RESUME
  // the GPU, so the widget would cause the very wakeups it exists to report.
  //
  // sysfs attributes raise no inotify events, so watchChanges is off and the Timer below
  // calls reload(): a read of a tiny file, no process.
  FileView {
    id: powerStateFile
    path: root.devicePath === "" ? "" : root.devicePath + "/power_state"
    watchChanges: false
    printErrors: false
    onLoaded: {
      root.powerState = root.word(text(), "unknown")
      root.retryDelay = 30000
    }
    onLoadFailed: if (root.devicePath !== "") root.deviceLost()
  }

  FileView {
    id: runtimeStatusFile
    path: root.devicePath === "" ? "" : root.devicePath + "/power/runtime_status"
    watchChanges: false
    printErrors: false
    onLoaded: root.runtimeStatus = root.word(text(), "")
    onLoadFailed: root.runtimeStatus = ""
  }

  FileView {
    id: suspendedTimeFile
    path: root.devicePath === "" ? "" : root.devicePath + "/power/runtime_suspended_time"
    watchChanges: false
    printErrors: false
    onLoaded: root.suspendedMs = root.counter(text())
    onLoadFailed: root.suspendedMs = -1
  }

  FileView {
    id: activeTimeFile
    path: root.devicePath === "" ? "" : root.devicePath + "/power/runtime_active_time"
    watchChanges: false
    printErrors: false
    onLoaded: root.activeMs = root.counter(text())
    onLoadFailed: root.activeMs = -1
  }

  // 5s is plenty: the state only changes when something grabs or releases the GPU.
  Timer {
    interval: 5000
    running: root.devicePath !== ""
    repeat: true
    onTriggered: {
      powerStateFile.reload()
      runtimeStatusFile.reload()
      suspendedTimeFile.reload()
      activeTimeFile.reload()
    }
  }

  Component.onCompleted: root.resolveDevice()

  // ---------------------------------------------------------------- report
  // gpuwho (shipped in bin/, read-only) lists what currently holds the GPU plus its sleep
  // depth. The launcher is started detached, by absolute path: it execs (through uwsm)
  // into the terminal, which runs in its own systemd scope and belongs to the user for as
  // long as they keep it open -- so the widget holds no pipe to it and never kills it.
  // The path is single-quoted as well as checked, because the launcher runs it via bash -c.
  Timer {
    id: reportCooldown
    interval: 1500
  }

  function openReport() {
    if (!root.pluginBinShellSafe || reportCooldown.running) return
    reportCooldown.restart()
    Quickshell.execDetached(["/usr/bin/omarchy-launch-floating-terminal-with-presentation",
                             "'" + root.pluginBin + "gpuwho'"])
  }

  // Hidden entirely on machines with no NVIDIA dGPU, rather than showing "unknown".
  visible: powerState !== ""
  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  WidgetButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: "󱢢"          // nf-md-expansion_card
    foreground: root.stateColor
    fontSize: Style.font.caption
    horizontalMargin: 6
    tooltipText: "dGPU: " + root.stateLabel
                 + (root.asleepPct >= 0 ? "  •  asleep " + root.asleepPct.toFixed(1) + "%" : "")
                 + "  •  click: what is holding it"
    onPressed: function(button) { root.openReport() }
  }
}
