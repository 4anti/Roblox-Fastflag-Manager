"""Static AST audit: every low-level process-write syscall in
``roblox_manager.py`` MUST sit inside a ``with _mem_lock:`` block (directly
or transitively via a wrapper that holds it).

Rationale: two threads (apply + hotkey + silent-verify) running
``VirtualProtectEx`` / ``WriteProcessMemory`` / ``NtWriteVirtualMemory``
concurrently on the same page produce torn writes that corrupt Roblox
memory (see ``roblox_manager.py`` module comment near ``_mem_lock =
threading.RLock()``). Historically, the "preset-switch crash" was traced
to exactly this race.

A future edit that introduces a new bare write call without wrapping it
will fail this test — the crash class stays gone.
"""

from __future__ import annotations

import ast
import pathlib


_WRITE_ATTRS = {
    "WriteProcessMemory",
    "VirtualProtectEx",
    "NtWriteVirtualMemory",
    "VirtualAllocEx",   # allocating in the target is itself a mutation of
                        # its address space; hold the lock to serialise it
                        # against concurrent free/rewrite paths.
}


def _load_module_tree():
    src = (pathlib.Path(__file__).resolve().parent.parent
           / "src" / "core" / "roblox_manager.py")
    return ast.parse(src.read_text(encoding="utf-8")), src


def _find_write_calls(tree):
    """Yield (lineno, attr_name, enclosing_function_name) for every Call
    whose ``func`` is an attribute matching one of the write syscalls."""
    # Build parent map so we can climb.
    parent_of: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_of[id(child)] = parent

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr not in _WRITE_ATTRS:
            continue
        # Walk up to find the enclosing FunctionDef.
        cur = node
        fn_name = None
        while cur is not None:
            if isinstance(cur, ast.FunctionDef):
                fn_name = cur.name
                break
            cur = parent_of.get(id(cur))
        yield node.lineno, func.attr, fn_name or "<module>"


def _call_is_under_mem_lock(tree, target_lineno):
    """True if the Call at ``target_lineno`` is textually enclosed in a
    ``with _mem_lock:`` block anywhere up its ancestor chain."""
    parent_of: dict = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parent_of[id(child)] = parent

    # Find the Call node by lineno.
    target = None
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.lineno == target_lineno:
            target = node
            break
    if target is None:
        return False

    cur = target
    while cur is not None:
        if isinstance(cur, ast.With):
            for item in cur.items:
                ctx = item.context_expr
                # `with _mem_lock:` — a bare Name context.
                if isinstance(ctx, ast.Name) and ctx.id == "_mem_lock":
                    return True
        cur = parent_of.get(id(cur))
    return False


# Functions we deliberately exempt: pure wrappers whose ONLY job is to
# forward under the lock (``_write_raw``) or whose caller already holds it.
# Whitelist by function name so an exemption stays explicit and reviewable.
_EXEMPT_FNS = {
    # _write_raw_impl is called EXCLUSIVELY by _write_raw, which itself
    # takes _mem_lock. Every path in still holds the lock.
    "_write_raw_impl",
}


def test_every_low_level_write_sits_inside_mem_lock():
    tree, src_path = _load_module_tree()

    offenders = []
    for lineno, attr, fn in _find_write_calls(tree):
        if fn in _EXEMPT_FNS:
            continue
        if not _call_is_under_mem_lock(tree, lineno):
            offenders.append((lineno, attr, fn))

    assert not offenders, (
        "Unguarded low-level process-write call(s) in "
        f"{src_path.name}:\n  "
        + "\n  ".join(f"L{ln}: {attr} in {fn}()" for ln, attr, fn in offenders)
        + "\n\nWrap the call in `with _mem_lock:` (or route it through a "
        "wrapper that does). Two threads writing the same page without the "
        "lock produce torn writes that corrupt Roblox memory — historical "
        "preset-switch crash class."
    )
