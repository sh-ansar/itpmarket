from __future__ import annotations

import unittest
from pathlib import Path

from task_manager import (
    structured_progress,
    structured_progress_history,
)


ROOT = Path(__file__).parents[1]


class OperationProgressHistoryTests(
    unittest.TestCase
):
    def test_internal_ratios_are_not_global_progress(
        self,
    ) -> None:
        text = (
            "SOURCE 1/1\n"
            "PAGE 2/100\n"
            "FOUND 8\n"
        )

        self.assertIsNone(
            structured_progress(
                text
            )
        )

    def test_structured_progress_history(
        self,
    ) -> None:
        text = (
            'SPYON_PROGRESS '
            '{"phase":"workflow",'
            '"phase_label":"Catalog",'
            '"phase_current":1,'
            '"phase_total":2,'
            '"current":0,'
            '"total":2,'
            '"state":"running",'
            '"timestamp":"2026-09-04T10:00:00+05:00"}\n'
            'SPYON_PROGRESS '
            '{"phase":"workflow",'
            '"phase_label":"Catalog",'
            '"phase_current":1,'
            '"phase_total":2,'
            '"current":1,'
            '"total":2,'
            '"state":"completed",'
            '"timestamp":"2026-09-04T10:01:00+05:00"}\n'
        )

        history = (
            structured_progress_history(
                text
            )
        )

        self.assertEqual(
            2,
            len(history),
        )

        self.assertEqual(
            "running",
            history[0]["state"],
        )

        self.assertEqual(
            "completed",
            history[1]["state"],
        )

        self.assertEqual(
            "2026-09-04T10:00:00+05:00",
            history[0]["timestamp"],
        )

    def test_ui_has_operation_history_not_process_log(
        self,
    ) -> None:
        template = (
            ROOT
            / "templates"
            / "app.html"
        ).read_text(
            encoding="utf-8"
        )

        js = (
            ROOT
            / "static"
            / "js"
            / "app.js"
        ).read_text(
            encoding="utf-8"
        )

        self.assertNotIn(
            "PROCESS LOG",
            template,
        )

        self.assertIn(
            'id="operationHistory"',
            template,
        )

        self.assertIn(
            "taskHistoryHtml",
            js,
        )

        self.assertIn(
            "taskProgressPercent",
            js,
        )

    def test_raw_log_is_guarded_for_superadmin(
        self,
    ) -> None:
        source = (
            ROOT
            / "app.py"
        ).read_text(
            encoding="utf-8"
        )

        start = source.index(
            "def api_task_log("
        )

        end = source.index(
            '@app.delete("/api/tasks/<task_id>")',
            start,
        )

        block = source[
            start:end
        ]

        self.assertIn(
            "show_technical_log",
            block,
        )

        self.assertIn(
            "is_superadmin(user)",
            block,
        )

        self.assertIn(
            "progress_history",
            block,
        )


if __name__ == "__main__":
    unittest.main()
