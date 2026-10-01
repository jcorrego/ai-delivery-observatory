from contextlib import contextmanager, redirect_stderr, redirect_stdout
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from observatory.cli import main
from observatory.storage import read

ROOT = Path(__file__).parents[1]


class CliContracts(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.path = Path(self.folder.name)

    def tearDown(self):
        self.folder.cleanup()

    def run_cli(self, args):
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            return main(args)

    def export_html(self, target):
        return self.run_cli(["html", "--data", str(ROOT / "examples/synthetic.json"), "--config", str(ROOT / "examples/config.json"),
                             "--from", "2026-09-01", "--to", "2026-09-30", "--output", str(target)])

    def test_html_is_private_before_first_write_and_replaces_atomically(self):
        target = self.path / "report.html"
        create_temp = tempfile.NamedTemporaryFile
        for existing in (False, True):
            with self.subTest(existing=existing):
                target.unlink(missing_ok=True)
                if existing:
                    target.write_text("previous report")
                    target.chmod(0o644)
                observed = []

                @contextmanager
                def checked_temp(*args, **kwargs):
                    with create_temp(*args, **kwargs) as handle:
                        write = handle.write

                        def checked_write(content):
                            self.assertEqual(os.fstat(handle.fileno()).st_mode & 0o777, 0o600)
                            self.assertEqual(Path(handle.name).parent, target.parent)
                            self.assertEqual(target.read_text() if existing else target.exists(), "previous report" if existing else False)
                            observed.append(True)
                            return write(content)

                        with patch.object(handle, "write", side_effect=checked_write):
                            yield handle

                old_umask = os.umask(0)
                try:
                    with patch("observatory.cli.tempfile.NamedTemporaryFile", checked_temp):
                        self.assertEqual(self.export_html(target), 0)
                finally:
                    os.umask(old_umask)
                self.assertTrue(observed)
                self.assertEqual(target.stat().st_mode & 0o777, 0o600)
                self.assertIn('"avery"', target.read_text())
                self.assertEqual(list(self.path.iterdir()), [target])

    def test_failed_html_export_keeps_previous_report_and_cleans_temporary_file(self):
        target = self.path / "report.html"
        target.write_text("previous report")
        for operation in ("fsync", "replace"):
            with self.subTest(operation=operation):
                with patch("observatory.cli.os." + operation, side_effect=OSError("synthetic write failure")):
                    self.assertEqual(self.export_html(target), 2)
                self.assertEqual(target.read_text(), "previous report")
                self.assertEqual(list(self.path.iterdir()), [target])

    def test_usage_import_preserves_zero_and_unknown_and_review_flags(self):
        source, target = self.path / "usage.csv", self.path / "usage.json"
        source.write_text("date,actor,provider,scope,credits,tokens,prompts,code_review_active,code_review_passive\n2026-09-03,github:demo,github-copilot,enterprise,420,,0,true,false\n")
        self.assertEqual(self.run_cli(["import-usage", "--csv", str(source), "--output", str(target)]), 0)
        row = read(target)["usage"][0]
        self.assertIsNone(row["tokens"])
        self.assertEqual(row["prompts"], 0)
        self.assertTrue(row["code_review_active"])
        self.assertFalse(row["code_review_passive"])

    def test_invalid_usage_import_does_not_write_output(self):
        source, target = self.path / "usage.csv", self.path / "usage.json"
        for content in ("date,actor,provider,credits\n2026-09-03,github:demo,github-copilot,NaN\n",
                        "date,actor,provider\n2026-09-03,,github-copilot\n"):
            source.write_text(content)
            self.assertEqual(self.run_cli(["import-usage", "--csv", str(source), "--output", str(target)]), 2)
            self.assertFalse(target.exists())

    def test_person_filtered_json_report(self):
        target = self.path / "report.json"
        self.assertEqual(self.run_cli(["report", "--data", str(ROOT / "examples/synthetic.json"), "--config", str(ROOT / "examples/config.json"),
                                      "--from", "2026-09-01", "--to", "2026-09-30", "--person", "avery", "--output", str(target)]), 0)
        self.assertEqual(read(target)["summary"]["merged"], 24)

    def test_collect_rejects_future_period_without_network(self):
        target = self.path / "report.json"
        self.assertEqual(self.run_cli(["collect", "--platform", "github", "--repository", "demo/service", "--token-env", "SYNTHETIC_NOT_SET",
                                      "--from", "2099-01-01", "--to", "2099-01-02", "--output", str(target)]), 2)
        self.assertFalse(target.exists())

    def test_static_html_contains_person_reports_and_no_external_requests(self):
        target = self.path / "report.html"
        self.assertEqual(self.run_cli(["html", "--data", str(ROOT / "examples/synthetic.json"), "--config", str(ROOT / "examples/config.json"),
                                      "--from", "2026-09-01", "--to", "2026-09-30", "--output", str(target)]), 0)
        text = target.read_text()
        self.assertIn('"synthetic": true', text)
        self.assertIn('"avery"', text)
        self.assertNotIn('<script src=', text)
        self.assertNotIn('https://', text)
