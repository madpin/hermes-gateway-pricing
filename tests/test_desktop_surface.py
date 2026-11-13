"""desktop/plugin.js must pass the "desktop surface" check of ``hermes plugins validate``."""

import pytest
from helpers import ROOT


def test_desktop_half_passes_the_admission_lint():
    lint = pytest.importorskip("hermes_cli.plugin_validate_desktop")
    assert (ROOT / "desktop" / "plugin.js").is_file()
    assert lint.desktop_surface_hits(ROOT) == []
    assert lint.desktop_surface_hits(ROOT, lint.desktop_review_findings) == []


def test_ui_harness_files_are_not_part_of_the_desktop_surface():
    lint = pytest.importorskip("hermes_cli.plugin_validate_desktop")
    assert lint.is_desktop_surface("desktop/plugin.js")
    assert not lint.is_desktop_surface("tests/ui/sdk-stub.js")
