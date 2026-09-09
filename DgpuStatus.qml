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
  property real asleepPct: -1

  readonly property string pluginBin: String(Qt.resolvedUrl("bin/")).replace("file://", "")

  // Resolve the device through its bound driver rather than a hardcoded BDF, so this keeps
  // working if the address differs on another machine.
  //
  // Reads ONLY power_state and the runtime PM counters. Do NOT add current_link_speed or
  // current_link_width: querying those touches the live PCIe link and RESUMES the GPU, so
  // the widget would cause the very wakeups it exists to report.
  readonly property string pollCmd:
    'd=$(ls -d /sys/bus/pci/drivers/nvidia/0000:* 2>/dev/null | head -1); ' +
    '[ -n "$d" ] || exit 1; ' +
    'cat "$d/power_state" "$d/power/runtime_status" ' +
    '"$d/power/runtime_suspended_time" "$d/power/runtime_active_time" 2>/dev/null'

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

  Process {
    id: pollProc
    // -c, not -lc: a LOGIN shell re-sources /etc/profile, profile.d and the user's
    // bash_profile on every poll (42ms vs 4ms here, twelve times a minute) to run one
    // cat. Nothing in pollCmd needs the login environment.
    command: ["bash", "-c", root.pollCmd]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        const lines = String(text || "").trim().split("\n")
        if (lines.length < 4) { root.powerState = ""; return }
        root.powerState = lines[0].trim()
        root.runtimeStatus = lines[1].trim()
        const s = parseFloat(lines[2]), a = parseFloat(lines[3])
        root.asleepPct = (s + a) > 0 ? (100 * s / (s + a)) : -1
      }
    }
  }

  Process {
    id: reportProc
    // gpuwho (shipped in bin/) lists what currently holds the GPU plus its sleep depth.
    // Reading state never wakes the GPU -- it is pure /proc and sysfs.
    command: ["omarchy-launch-floating-terminal-with-presentation",
              root.pluginBin + "gpuwho"]
  }

  function refresh() { if (!pollProc.running) pollProc.running = true }

  Component.onCompleted: refresh()

  // 5s is plenty: this is a pure sysfs read, and the state only changes when something
  // grabs or releases the GPU.
  Timer {
    interval: 5000
    running: true
    repeat: true
    onTriggered: root.refresh()
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
    onPressed: function(button) { if (!reportProc.running) reportProc.running = true }
  }
}
