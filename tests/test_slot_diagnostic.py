"""v4.0.4: dev-mode diagnostic that self-reports why the four ad slots
may or may not render, straight into the FFM console panel. Lets us
diagnose "no ads" without DevTools."""
import unittest

from src.gui.api import _slot_verdict


class TestSlotVerdict(unittest.TestCase):
    def test_missing_slot_is_named_first(self):
        v = _slot_verdict(False, 'block', 0, 0, 0, 0, 0)
        self.assertIn('MISSING', v)
        self.assertIn('index.html', v)

    def test_display_none_is_hidden_with_breakpoint_hint(self):
        v = _slot_verdict(True, 'none', 300, 250, 0, 0, 0)
        self.assertIn('HIDDEN', v)
        self.assertIn('1280', v)      # rail breakpoint mentioned
        self.assertIn('900', v)       # top strip
        self.assertIn('700', v)       # bottom strip

    def test_zero_rect_is_collapsed(self):
        v = _slot_verdict(True, 'block', 0, 0, 0, 0, 0)
        self.assertIn('COLLAPSED', v)

    def test_no_iframe_names_the_shim(self):
        v = _slot_verdict(True, 'block', 728, 90, 0, 0, 0)
        self.assertIn('NO IFRAME', v)
        self.assertIn('intersection-polyfill.js', v)

    def test_iframe_zero_dims_is_iframe_collapsed(self):
        v = _slot_verdict(True, 'block', 728, 90, 1, 0, 0)
        self.assertIn('IFRAME COLLAPSED', v)

    def test_all_healthy_is_ok(self):
        v = _slot_verdict(True, 'block', 728, 90, 1, 728, 90)
        self.assertIn('OK', v)


class TestReportSlotDiagnosticNeverRaises(unittest.TestCase):
    """Backend endpoint must be crash-safe for any garbage payload —
    it's a diagnostic, not a critical path."""

    def _shell_api(self):
        from src.gui import api as api_module
        a = api_module.Api.__new__(api_module.Api)
        return a

    def test_non_dict_payload_returns_ok_false(self):
        a = self._shell_api()
        self.assertEqual(a.report_slot_diagnostic('nope'), {'ok': False})
        self.assertEqual(a.report_slot_diagnostic(None), {'ok': False})
        self.assertEqual(a.report_slot_diagnostic(123), {'ok': False})

    def test_missing_fields_do_not_raise(self):
        a = self._shell_api()
        result = a.report_slot_diagnostic({'slots': [{}, {'id': 'x'}]})
        self.assertEqual(result, {'ok': True})

    def test_full_payload_returns_ok(self):
        a = self._shell_api()
        payload = {
            'winW': 996, 'winH': 738,
            'slots': [
                {'id': 'aux-pane-1', 'exists': True, 'display': 'none',
                 'rectW': 0, 'rectH': 0, 'frames': 0},
                {'id': 'aux-pane-3', 'exists': True, 'display': 'flex',
                 'rectW': 728, 'rectH': 90, 'frames': 1,
                 'frameW': 728, 'frameH': 90,
                 'frameSrc': 'about:srcdoc'},
            ],
        }
        self.assertEqual(a.report_slot_diagnostic(payload), {'ok': True})


if __name__ == '__main__':
    unittest.main()
