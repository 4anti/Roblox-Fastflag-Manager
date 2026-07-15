"""Regression guard for the hotkey-loop version-mismatch fix (§3.5).

Before this fix, the hotkey loop called ``write_flag_at_address`` directly
whenever a bound key toggled — no version-mismatch check. If the running
Roblox was on a build the loaded offsets didn't target, the write hit
random RVAs and crashed the game.

The fix computes ``_live_writes_gated`` once per hotkey tick and adds
``not _gated`` to each of the four live-write predicates. The FLAG STATE
change (enabled / value) still lands and persists to disk so the next
matching launch picks it up via JSON.

These tests exercise the predicate directly rather than the full
_hotkey_loop, which polls the Win32 keyboard state and can't run headless.
"""

from __future__ import annotations

import threading

import pytest

from src.core import offset_loader
from src.core.flag_manager import FlagManager


class _StubRoblox:
    is_attached = True

    def __init__(self, running_build: str):
        self.running_build = running_build

    def get_running_build_string(self):
        return self.running_build


@pytest.fixture
def fm():
    m = FlagManager.__new__(FlagManager)
    m.user_flags = []
    m._rm = None
    m._lock = threading.Lock()
    return m


def _patch_versions(monkeypatch, offsets_build: str):
    monkeypatch.setattr(offset_loader, "last_source_build",
                        lambda: offsets_build)


def test_gated_when_offsets_disagree_with_running_pid(fm, monkeypatch):
    _patch_versions(monkeypatch, "version-A")
    fm._rm = _StubRoblox(running_build="version-B")

    gated, reason = fm._live_writes_gated()

    assert gated is True
    assert "version-A" in reason
    assert "version-B" in reason


def test_not_gated_when_offsets_agree_with_running_pid(fm, monkeypatch):
    _patch_versions(monkeypatch, "version-SAME")
    fm._rm = _StubRoblox(running_build="version-SAME")

    gated, reason = fm._live_writes_gated()

    assert gated is False
    assert reason == ""


def test_not_gated_when_running_pid_unknown(fm, monkeypatch):
    """A ``QueryFullProcessImageNameW`` failure returns ``"unknown"`` from
    the manager. That must NEVER cause a false-alarm skip — pass-through."""
    _patch_versions(monkeypatch, "version-something")
    fm._rm = _StubRoblox(running_build="unknown")

    gated, reason = fm._live_writes_gated()

    assert gated is False


def test_not_gated_when_rm_detached(fm, monkeypatch):
    """Roblox closed between apply and the next hotkey tick. The guard
    must fall through — the hotkey loop's own ``is_attached`` check will
    handle the "no process" case."""
    _patch_versions(monkeypatch, "version-A")

    class _Detached:
        is_attached = False

        def get_running_build_string(self):
            return "version-B"   # even if reachable, is_attached wins

    fm._rm = _Detached()
    gated, reason = fm._live_writes_gated()

    assert gated is False


def test_predicate_shape_matches_hotkey_call_sites():
    """The hotkey loop guards four write predicates with
    ``... and not _gated``. If a future edit reintroduces a bare
    ``write_flag_at_address(...)`` call without that guard, this test
    fails loudly. Static AST-style check via source scan.
    """
    import ast
    import pathlib

    src_path = (pathlib.Path(__file__).resolve().parent.parent
                / "src" / "core" / "flag_manager.py")
    tree = ast.parse(src_path.read_text(encoding="utf-8"))

    # Find _hotkey_loop function.
    hotkey_fn = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_hotkey_loop":
            hotkey_fn = node
            break
    assert hotkey_fn is not None, "_hotkey_loop function must exist"

    # Every write_flag_at_address call inside _hotkey_loop must sit under
    # at least one enclosing `if ... and not _gated:` (or `if not _gated`)
    # test. Walk the function tracking guard context.
    def collect_write_calls_with_guards(fn_node):
        results = []

        def visit(node, guarded_by_gate):
            if (isinstance(node, ast.Attribute)
                    and node.attr == "write_flag_at_address"):
                results.append((node.lineno, guarded_by_gate))
                return
            if isinstance(node, ast.If):
                # Recurse into body with a possibly-strengthened guard.
                body_guard = guarded_by_gate or _test_mentions_gate(node.test)
                for child in node.body:
                    visit(child, body_guard)
                for child in node.orelse:
                    visit(child, guarded_by_gate)
                return
            for child in ast.iter_child_nodes(node):
                visit(child, guarded_by_gate)

        visit(fn_node, False)
        return results

    def _test_mentions_gate(test_node):
        for sub in ast.walk(test_node):
            if isinstance(sub, ast.Name) and sub.id == "_gated":
                return True
        return False

    calls = collect_write_calls_with_guards(hotkey_fn)
    assert calls, "test setup mismatch — no write_flag_at_address calls found"
    unguarded = [ln for ln, guarded in calls if not guarded]
    assert not unguarded, (
        f"write_flag_at_address at lines {unguarded} is NOT under a "
        "`... _gated ...` if-guard. Every hotkey-triggered write MUST be "
        "gated so a running-vs-offsets build mismatch skips the write and "
        "falls back to JSON. Wrap the call site with `if addr_data and "
        "not _gated:` (or equivalent)."
    )
