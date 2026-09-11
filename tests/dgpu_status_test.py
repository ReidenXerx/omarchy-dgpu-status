#!/usr/bin/python3
"""Tests for the dGPU status plugin.   Run: /usr/bin/python3 tests/dgpu_status_test.py

Nothing here touches a PCI device's config space. Device resolution is exercised against
a fake sysfs tree; the real-hardware checks only list the nvidia driver directory and read
the power attributes gpuwho is allowed to read.
"""
import importlib.machinery
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True

PLUGIN = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(PLUGIN, "bin")
GPUWHO = os.path.join(BIN, "gpuwho")
HELPER = os.path.join(BIN, "dgpu-status")
QML = os.path.join(PLUGIN, "DgpuStatus.qml")
LAUNCHER = "/usr/bin/omarchy-launch-floating-terminal-with-presentation"
ALLOWED_ATTRS = {"power_state", "power/runtime_status", "power/runtime_suspended_time",
                 "power/runtime_active_time", "power/control", "d3cold_allowed"}


def load(name, filename):
    loader = importlib.machinery.SourceFileLoader(name, os.path.join(BIN, filename))
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(module)
    return module


helper = load("dgpu_status_helper", "dgpu-status")


def read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


# ---------------------------------------------------------------- device resolution

class FakeSysfs:
    def __init__(self, tmp):
        self.tmp = os.path.realpath(tmp)
        self.root = os.path.join(self.tmp, "sys")
        self.driver = os.path.join(self.root, "bus/pci/drivers/nvidia")
        os.makedirs(self.driver)
        os.makedirs(os.path.join(self.root, "devices"))
        for junk in ("bind", "unbind", "new_id", "remove_id", "uevent"):
            open(os.path.join(self.driver, junk), "w").close()
        os.makedirs(os.path.join(self.root, "module/nvidia"))
        os.symlink("../../../../module/nvidia", os.path.join(self.driver, "module"))

    def device(self, bdf, parent="pci0000:00/0000:00:01.0", power_state="file", base=None):
        path = os.path.join(base or os.path.join(self.root, "devices"), parent, bdf)
        os.makedirs(path, exist_ok=True)
        os.makedirs(os.path.join(path, "power"), exist_ok=True)
        attr = os.path.join(path, "power_state")
        if power_state == "file":
            with open(attr, "w") as f:
                f.write("D3cold\n")
        elif power_state == "symlink":
            decoy = os.path.join(self.tmp, "decoy_power_state")
            open(decoy, "w").close()
            os.symlink(decoy, attr)
        return path

    def bind(self, name, target):
        os.symlink(os.path.relpath(target, self.driver), os.path.join(self.driver, name))


class ResolveDeviceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.fs = FakeSysfs(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def resolve(self):
        return helper.resolve_device(self.fs.root)

    def test_resolves_the_bound_device(self):
        dev = self.fs.device("0000:01:00.0")
        self.fs.bind("0000:01:00.0", dev)
        self.assertEqual(self.resolve(), dev)

    def test_no_driver_directory(self):
        self.assertIsNone(helper.resolve_device(os.path.join(self.fs.tmp, "nothing")))

    def test_driver_with_no_devices(self):
        self.assertIsNone(self.resolve())

    def test_rejects_names_that_are_not_pci_addresses(self):
        dev = self.fs.device("0000:01:00.0")
        for name in ("0000:01:00.0.bak", "0000:01:00.8", "0000:1:00.0", "x0000:01:00.0",
                     "0000:01:00.0 ", "0000:01:00", "01:00.0"):
            self.fs.bind(name, dev)
        self.assertIsNone(self.resolve())

    def test_rejects_uppercase_hex(self):
        dev = self.fs.device("0000:0A:00.0")
        self.fs.bind("0000:0A:00.0", dev)
        self.assertIsNone(self.resolve())

    def test_rejects_link_leading_outside_devices(self):
        outside = self.fs.device("0000:01:00.0", parent="x", base=os.path.join(self.fs.tmp, "elsewhere"))
        self.fs.bind("0000:01:00.0", outside)
        self.assertIsNone(self.resolve())

    def test_rejects_sibling_directory_sharing_the_devices_prefix(self):
        evil = self.fs.device("0000:01:00.0", parent="x", base=os.path.join(self.fs.root, "devices-evil"))
        self.fs.bind("0000:01:00.0", evil)
        self.assertIsNone(self.resolve())

    def test_rejects_link_to_a_differently_named_device(self):
        other = self.fs.device("0000:02:00.0")
        self.fs.bind("0000:01:00.0", other)
        self.assertIsNone(self.resolve())

    def test_rejects_device_without_power_state(self):
        dev = self.fs.device("0000:01:00.0", power_state=None)
        self.fs.bind("0000:01:00.0", dev)
        self.assertIsNone(self.resolve())

    def test_rejects_symlinked_power_state(self):
        dev = self.fs.device("0000:01:00.0", power_state="symlink")
        self.fs.bind("0000:01:00.0", dev)
        self.assertIsNone(self.resolve())

    def test_rejects_regular_file_named_like_an_address(self):
        open(os.path.join(self.fs.driver, "0000:01:00.0"), "w").close()
        self.assertIsNone(self.resolve())

    def test_survives_symlink_loop(self):
        os.symlink("0000:01:00.0", os.path.join(self.fs.driver, "0000:01:00.0"))
        self.assertIsNone(self.resolve())

    def test_rejects_shell_or_space_characters_in_the_path(self):
        dev = self.fs.device("0000:01:00.0", parent="pci0000:00/bad dir$(x)")
        self.fs.bind("0000:01:00.0", dev)
        self.assertIsNone(self.resolve())

    def test_first_valid_device_in_sorted_order(self):
        second = self.fs.device("0000:02:00.0")
        first = self.fs.device("0000:01:00.0")
        self.fs.bind("0000:02:00.0", second)
        self.fs.bind("0000:01:00.0", first)
        self.assertEqual(self.resolve(), first)

    def test_skips_invalid_entry_for_a_later_valid_one(self):
        broken = self.fs.device("0000:01:00.0", power_state=None)
        good = self.fs.device("0000:02:00.0")
        self.fs.bind("0000:01:00.0", broken)
        self.fs.bind("0000:02:00.0", good)
        self.assertEqual(self.resolve(), good)


class HelperCliTest(unittest.TestCase):
    def run_helper(self, *args):
        return subprocess.run(["/usr/bin/python3", "-B", HELPER, *args],
                              capture_output=True, text=True, timeout=10)

    def test_rejects_anything_but_device(self):
        for args in ((), ("device", "extra"), ("--help",), ("poll",), ("../device",)):
            with self.subTest(args=args):
                r = self.run_helper(*args)
                self.assertEqual(r.returncode, 2)
                self.assertEqual(r.stdout, "")

    @unittest.skipIf(helper.resolve_device() is None, "no nvidia-bound device on this machine")
    def test_real_device_matches_what_the_widget_accepts(self):
        r = self.run_helper("device")
        self.assertEqual(r.returncode, 0)
        path = r.stdout.strip()
        self.assertEqual(path, helper.resolve_device())
        self.assertRegex(path, r"^/sys/devices/[A-Za-z0-9:._/-]+$")
        self.assertLessEqual(len(path), 256)
        self.assertRegex(os.path.basename(path), helper.BDF.pattern)


# ---------------------------------------------------------------- gpuwho

class GpuwhoTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self._tmp.name, "home")
        self.tmpdir = os.path.join(self._tmp.name, "tmp")
        os.makedirs(self.home)
        os.makedirs(self.tmpdir)

    def tearDown(self):
        self._tmp.cleanup()

    def gpuwho(self, *args, **env):
        # PATH points nowhere on purpose: gpuwho must set its own.
        base = {"HOME": self.home, "TMPDIR": self.tmpdir, "PATH": "/nonexistent", "LANG": "C.UTF-8"}
        base.update(env)
        return subprocess.run([GPUWHO, *args], capture_output=True, text=True, env=base, timeout=60)

    def test_syntax(self):
        subprocess.run(["/usr/bin/bash", "-n", GPUWHO], check=True)

    def test_shebangs(self):
        self.assertEqual(read(GPUWHO).splitlines()[0], "#!/usr/bin/bash")
        for name in ("dgpu-status", "dgpu-status-menu-install"):
            self.assertEqual(read(os.path.join(BIN, name)).splitlines()[0], "#!/usr/bin/python3")

    def test_sets_path_and_locale_first(self):
        code = [l for l in read(GPUWHO).splitlines() if l and not l.startswith("#")]
        self.assertEqual(code[:3], ["PATH=/usr/bin", "LC_ALL=C", "export PATH LC_ALL"])

    def test_writing_commands_are_gone(self):
        for cmd in ("pin", "unpin", "allow", "unallow", "list-pins", "pins", "policy"):
            with self.subTest(cmd=cmd):
                r = self.gpuwho(cmd, "steam")
                self.assertEqual(r.returncode, 1)
                self.assertIn("unknown command", r.stderr)
        src = read(GPUWHO)
        for pattern in (r"mktemp", r"\.local/", r"environment\.d", r"\bwhich\s+-a\b|\$\(which\b|\bwhich\s+\"?\$",
                        r"command -v",
                        r"\.desktop", r"(^|[\s;|&(])(rm|mv|cp|ln|tee|touch|chmod|install|mkdir)\s"):
            self.assertIsNone(re.search(pattern, src, re.M), pattern)

    def test_sysfs_reads_go_through_the_allowlist(self):
        src = read(GPUWHO)
        self.assertEqual(set(re.findall(r'\$NV_SYS/([^"\s]+)', src)), {"$2"})
        case_line = next(l for l in src.splitlines() if l.strip().startswith("power_state|"))
        self.assertEqual(set(case_line.strip().rstrip(") ;").split("|")), ALLOWED_ATTRS)
        for line in src.splitlines():
            if re.search(r"current_link|/config\b|nvidia-smi|lspci|setpci", line):
                # Only ever named in comments or in printed explanations.
                self.assertTrue(line.lstrip().startswith("#") or "c_dim" in line or "printf" in line
                                or line.startswith("  ") and "$(" not in line, line)

    def test_argument_validation(self):
        cases = [("status", "x"), ("sleep", "x"), ("why", "x"), ("help", "x"), ("who", "x"),
                 ("watch", "abc"), ("watch", "-1"), ("watch", "1", "2"), ("watch", "86401"),
                 ("watch", "$(id)"), ("watch", "1e3"), ("watch", "٣"), ("watch", "999999"),
                 (" who",), ("--status",)]
        for args in cases:
            with self.subTest(args=args):
                r = self.gpuwho(*args)
                self.assertEqual(r.returncode, 1)
                self.assertEqual(r.stdout, "")

    def test_poll_validation(self):
        for poll in ("0", "abc", "0.01", "0.099", "61", "60.5", "1e3", "-1", " 1", "1;id", "0x10", "1.2345"):
            with self.subTest(poll=poll):
                r = self.gpuwho("watch", "1", GPUWHO_POLL=poll)
                self.assertEqual(r.returncode, 1)
                self.assertIn("GPUWHO_POLL", r.stderr)

    def test_no_terminal_escape_injection(self):
        r = self.gpuwho("\x1b]0;pwned\x07\x1b[31mred")
        self.assertEqual(r.returncode, 1)
        self.assertNotIn("\x1b", r.stderr)
        self.assertNotIn("\x07", r.stderr)

    def test_help_is_plugin_neutral(self):
        r = self.gpuwho("help")
        self.assertEqual(r.returncode, 0)
        for word in ("environment.d", "allow", "pin", "unpin", "prime-run", "gpu-unblock", "policy"):
            self.assertNotRegex(r.stdout, r"\b%s\b" % re.escape(word))

    def test_read_only(self):
        for args in (("help",), ("why",), (), ("status",), ("sleep",)):
            self.gpuwho(*args)
        if helper.resolve_device() is not None:
            self.gpuwho("watch", "1", GPUWHO_POLL="0.5")
        self.assertEqual(os.listdir(self.home), [])
        self.assertEqual(os.listdir(self.tmpdir), [])

    @unittest.skipIf(helper.resolve_device() is None, "no nvidia-bound device on this machine")
    def test_real_status_finds_the_same_device(self):
        r = self.gpuwho("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("(%s)" % os.path.basename(helper.resolve_device()), r.stdout)
        self.assertRegex(r.stdout, r"power_state    : (D0|D1|D2|D3hot|D3cold|unknown|error)\b")

    @unittest.skipIf(helper.resolve_device() is None, "no nvidia-bound device on this machine")
    def test_real_watch_runs_and_summarises(self):
        r = self.gpuwho("watch", "1", GPUWHO_POLL="0.25")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("summary", r.stdout)
        self.assertIn("baseline", r.stdout)


# ---------------------------------------------------------------- widget + menu

class WidgetTest(unittest.TestCase):
    def test_qml_runs_no_shell_and_only_absolute_commands(self):
        src = read(QML)
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))
        for token in ('"bash"', '"sh"', '"-c"', '"-lc"', "/bin/sh", "bash -c"):
            self.assertNotIn(token, code)
        heads = re.findall(r'(?:command:\s*|execDetached\()\[\s*"([^"]+)"', code)
        self.assertEqual(sorted(heads), sorted(["/usr/bin/python3", LAUNCHER]))

    def test_qml_reads_only_power_attributes(self):
        src = read(QML)
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("//"))
        attrs = set(re.findall(r'root\.devicePath \+ "/([^"]+)"', code))
        self.assertEqual(attrs, {"power_state", "power/runtime_status",
                                 "power/runtime_suspended_time", "power/runtime_active_time"})
        for token in ("current_link", "/config", "lspci", "nvidia-smi"):
            self.assertNotIn(token, code)

    def test_every_process_has_a_watchdog(self):
        src = read(QML)
        processes = re.findall(r"Process \{\s*id: (\w+)", src)
        self.assertEqual(processes, ["resolveProc"])
        self.assertRegex(src, r"if \(resolveProc\.running\) resolveProc\.signal\(9\)")

    def test_helper_never_opens_device_files(self):
        src = read(HELPER)
        code = src.split('"""', 2)[2]
        self.assertNotRegex(code, r"\bopen\(|read_text|read_bytes|current_link|lspci|nvidia-smi|subprocess")

    def test_menu_snippet_parses_and_quotes_the_path(self):
        installer = load("dgpu_status_menu_install", "dgpu-status-menu-install")
        plugin_bin = "/home/some user/.config/omarchy/plugins/reidenxerx.dgpu-status/bin"
        body = read(os.path.join(PLUGIN, "menu.jsonc")).replace("@PLUGIN_BIN@", plugin_bin)
        doc = "{\n" + body + "\n}\n"
        self.assertTrue(installer.valid(doc))
        routes = json.loads(installer.strip_jsonc(doc))
        actions = [r["action"] for r in routes.values() if "action" in r]
        self.assertEqual(len(actions), 4)
        for action in actions:
            with self.subTest(action=action):
                outer = shlex.split(action)             # the menu's bash -lc
                self.assertEqual(outer[0], LAUNCHER)
                inner = shlex.split(" ".join(outer[1:]))  # the launcher's cmd="$*"; bash -c
                self.assertEqual(inner[0], plugin_bin + "/gpuwho")
                self.assertIn(inner[1:], ([], ["status"], ["watch", "60"], ["why"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
