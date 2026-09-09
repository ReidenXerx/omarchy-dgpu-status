# dGPU status

An [Omarchy](https://omarchy.org) bar widget showing whether the NVIDIA discrete GPU is
**asleep or awake**, plus [`gpuwho`](#gpuwho) — a CLI for seeing what is keeping it awake.

On an Optimus laptop the dGPU should spend almost all its time powered off. When something
quietly holds it awake you lose battery with no visible symptom. This makes that visible.

## Install

```bash
omarchy plugin add https://github.com/ReidenXerx/omarchy-dgpu-status.git --enable
```

The widget hides itself on machines with no NVIDIA dGPU.

## The widget

| dGPU state | colour |
|---|---|
| `D3cold` — deep sleep, powered off | muted (nothing to see) |
| `D3hot` — light sleep, still powered | normal |
| `D0` active — in use | urgent |

**Hover** for the sleep depth and asleep-percentage. **Click** to open `gpuwho` in a floating
terminal and see exactly which processes hold the GPU open.

## gpuwho

Also usable directly (`bin/gpuwho`):

```
gpuwho                list processes currently on the dGPU
gpuwho status         power state, RTD3 settings, asleep %
gpuwho sleep          is it in deep sleep right now? (exit 0 = yes)
gpuwho watch [secs]   log every wake and NAME the process that caused it
gpuwho policy         show which apps may reach the dGPU
gpuwho allow <app>    let an app see the dGPU (game launchers)
gpuwho allow --force <app>   force an app onto it (GL + Vulkan)
```

`gpuwho watch` is the one worth knowing: it samples the power state and, the moment the GPU
wakes, records which processes hold `/dev/nvidia*`.

```
21:33:17  D3cold  (baseline)
21:33:21  D0      vulkaninfo(27933)

summary over 9s
  wakes  : 1
  awake  : 5s  (55.6% of the window)
```

## The rule this is built around

Everything here reads **only** `power_state` and the runtime PM counters.

`current_link_speed` and `current_link_width` are never read. Querying those touches the live
PCIe link and **resumes the GPU** — a status tool built the obvious way manufactures the
wakeups it claims to observe. The same applies to `lspci` and `nvidia-smi`: both wake a
sleeping card. If you extend this, keep to `power_state`, `power/runtime_*` and `/proc`.

`gpuwho watch` likewise scans `/proc/*/fd` rather than opening `/dev/nvidia*` itself.

## Notes

- The device is resolved through its bound driver (`/sys/bus/pci/drivers/nvidia/0000:*`),
  not a hardcoded address, so it works on any machine.
- `gpuwho allow` writes a `.desktop` override plus a PATH wrapper, because the graphical
  session's `PATH` excludes `~/.local/bin` — a menu launch and a shell launch take different
  paths and both have to work. It expects `prime-run` (from `nvidia-utils`) and, for the
  non-forcing variant, a small `gpu-unblock` wrapper; see the comments in `bin/gpuwho`.
- Processes owned by other users are invisible without root.

## License

MIT — see [LICENSE](LICENSE).
