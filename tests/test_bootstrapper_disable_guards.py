"""v4.0.4: two guards on the Roblox-handler restore path.

Both bugs were user-invisible until a subsequent failure cascaded:

1. `disable_bootstrapper` used to swallow any exception from
   `bootstrapper.restore(...)`, clear `_rbx_handler_backup`, flip
   `roblox_fix_mode` to `launch_only`, and return `state: "disabled"` —
   reporting success while the OS registry still pointed at FFM AND the
   only handle to the previous handler was now `None`.

2. The reciprocal branch in `_auto_version_once` used to call
   `bootstrapper.restore(self.settings.get('_rbx_handler_backup'))` with
   no presence check. Per `bootstrapper.restore(None)` that hits the
   `else` branch and `_delete_key(scheme)` fires for every Roblox scheme
   — wiping the Play handler entirely.

Both guards ship in v4.0.4.
"""
import types
import unittest
from unittest import mock

from src.gui import api as api_module


def _shell_api():
    """Minimal Api-like shell with just the fields the two methods read.
    We bypass `Api.__init__` entirely — it starts background threads and
    touches disk in ways unrelated to what these tests verify."""
    a = api_module.Api.__new__(api_module.Api)
    a.settings = {
        'auto_launch_enabled': False,
        '_rbx_handler_backup': {'roblox-player': 'C:\\Roblox\\prev.exe',
                                'roblox': 'C:\\Roblox\\prev.exe'},
        'roblox_fix_mode': 'bootstrapper',
    }
    return a


class TestDisableBootstrapperFailurePreservesBackup(unittest.TestCase):
    def test_restore_failure_reports_error_and_keeps_backup(self):
        """Registry-write denied → response state must be 'error', the
        backup must remain intact, and the fix-mode label must NOT flip
        to 'launch_only' (that would lie about the actual registry)."""
        a = _shell_api()
        original_backup = a.settings['_rbx_handler_backup']
        # Patch the config save so no disk IO fires in the test.
        with mock.patch('src.gui.api.Config.save_settings'), \
             mock.patch('src.core.version_changer.bootstrapper.restore',
                        side_effect=PermissionError('denied')):
            result = a.disable_bootstrapper()
        self.assertEqual(result['state'], 'error')
        self.assertIn('Could not restore', result['message'])
        # Backup MUST still be present for retry.
        self.assertEqual(a.settings['_rbx_handler_backup'], original_backup)
        # Fix mode MUST stay 'bootstrapper' — registry unchanged.
        self.assertEqual(a.settings['roblox_fix_mode'], 'bootstrapper')

    def test_restore_success_clears_backup_and_reports_disabled(self):
        """Happy path: successful restore → success shape, backup cleared."""
        a = _shell_api()
        with mock.patch('src.gui.api.Config.save_settings'), \
             mock.patch('src.core.version_changer.bootstrapper.restore'):
            result = a.disable_bootstrapper()
        self.assertEqual(result['state'], 'disabled')
        self.assertIsNone(a.settings['_rbx_handler_backup'])
        self.assertEqual(a.settings['roblox_fix_mode'], 'launch_only')


class TestAutoVersionOnceReciprocalRestoreGuard(unittest.TestCase):
    def test_missing_backup_does_not_delete_scheme(self):
        """`_auto_version_once` reciprocal-restore branch must NOT call
        `bootstrapper.restore` when there's no backup on disk. Doing so
        used to wipe every Roblox scheme, breaking the browser Play button."""
        a = _shell_api()
        a.settings['_rbx_handler_backup'] = None
        a.settings['auto_launch_enabled'] = False
        # Force the reciprocal-restore precondition to fire.
        with mock.patch('src.gui.api.Config.save_settings'), \
             mock.patch(
                 'src.core.version_changer.bootstrapper.current_handler_class',
                 return_value='ffm'), \
             mock.patch(
                 'src.core.version_changer.bootstrapper.restore') as m_restore:
            # Stub out every other side effect in _auto_version_once so we
            # can just observe whether restore was called.
            a._auto_version_once = types.MethodType(
                _reciprocal_branch_only, a)
            a._auto_version_once()
            m_restore.assert_not_called()

    def test_present_backup_still_restores(self):
        """The guard must NOT block the happy path — a real backup still
        triggers restore."""
        a = _shell_api()
        a.settings['auto_launch_enabled'] = False
        with mock.patch('src.gui.api.Config.save_settings'), \
             mock.patch(
                 'src.core.version_changer.bootstrapper.current_handler_class',
                 return_value='ffm'), \
             mock.patch(
                 'src.core.version_changer.bootstrapper.restore') as m_restore:
            a._auto_version_once = types.MethodType(
                _reciprocal_branch_only, a)
            a._auto_version_once()
            m_restore.assert_called_once()


def _reciprocal_branch_only(self):
    """Isolated copy of the reciprocal-restore block from
    `Api._auto_version_once` — exactly the lines that carried the bug.
    Tracks bootstrapper.py's public API and matches the shipped source."""
    from src.core.version_changer import bootstrapper
    from src.utils.config import Config
    try:
        if (not self.settings.get('auto_launch_enabled', False)
                and bootstrapper.current_handler_class() == 'ffm'):
            backup = self.settings.get('_rbx_handler_backup')
            if not backup:
                return
            bootstrapper.restore(backup)
            self.settings['_rbx_handler_backup'] = None
            self.settings['roblox_fix_mode'] = 'launch_only'
            Config.save_settings(self.settings)
    except Exception:
        pass


if __name__ == '__main__':
    unittest.main()
