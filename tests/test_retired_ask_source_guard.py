import importlib.util
from pathlib import Path
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts/check_retired_ask.py'
spec = importlib.util.spec_from_file_location('retirement_guard', SCRIPT)
assert spec is not None and spec.loader is not None
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


class RetiredAskSourceGuardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.routes = self.root / 'app/routes/main.py'
        self.nav = self.root / 'app/templates/base.html'
        self.routes.parent.mkdir(parents=True)
        self.nav.parent.mkdir(parents=True)
        self.routes.write_text("@bp.route('/')\ndef home(): return 'Home'\n")
        self.nav.write_text('<aside>Home</aside><nav>Deals</nav>')

    def test_clean_source_passes(self):
        self.assertEqual(guard.violations(self.root), [])

    def test_page_restoration_fails(self):
        self.routes.write_text("@bp.route('/ask')\ndef ask(): pass\n")
        self.assertTrue(guard.violations(self.root))

    def test_api_restoration_fails(self):
        self.routes.write_text("@bp.route('/api/ask', methods=['POST'])\ndef api_ask(): pass\n")
        self.assertTrue(guard.violations(self.root))

    def test_desktop_restoration_fails(self):
        self.nav.write_text('<aside><a href="{{ url_for(\'main.ask\') }}">Ask TDG</a></aside><nav>Deals</nav>')
        self.assertTrue(guard.violations(self.root))

    def test_mobile_restoration_fails(self):
        self.nav.write_text('<aside>Home</aside><nav><a href="{{ url_for(\'main.ask\') }}">Ask</a></nav>')
        self.assertTrue(guard.violations(self.root))

    def test_direct_mobile_link_fails(self):
        self.nav.write_text('<nav><a href="/ask">Ask</a></nav>')
        self.assertTrue(guard.violations(self.root))

    def test_missing_source_fails_closed(self):
        self.nav.unlink()
        self.assertTrue(guard.violations(self.root))


if __name__ == '__main__':
    unittest.main()
