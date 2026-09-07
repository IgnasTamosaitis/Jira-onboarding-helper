import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from PIL import Image, ImageChops, ImageTk

import printer_ui
from printer_ui import PREVIEW_HEIGHT, PREVIEW_WIDTH, PrinterPanel, render_card
from ui import MainWindow


class CardRenderingTests(unittest.TestCase):
    def test_names_stay_centered_below_logo_and_above_number(self):
        background = Image.new("RGB", (1005, 651), "white")
        for name in (
            "Test User", "Aleksandras Aleksandravicius",
            "Ąžuolas Žemaitis", "Mary Anne O'Connor-Smith",
        ):
            with self.subTest(name=name):
                card = render_card(background, name, "1234")
                # Inspect the actual ink, excluding the separate card number.
                region = (0, 0, card.width, round(card.height * 0.88))
                ink = ImageChops.difference(card, background).crop(region).getbbox()
                self.assertIsNotNone(ink)
                left, top, right, bottom = ink
                self.assertGreaterEqual(top, int(card.height * 0.59))
                self.assertLessEqual(bottom, card.height * 0.86)
                self.assertLess(abs((left + right) / 2 - card.width / 2), 6)
                self.assertGreater(left, 0)
                self.assertLess(right, card.width)
                self.assertEqual(background.getbbox(), (0, 0, 1005, 651))
                self.assertEqual(background.getpixel((left, top)), (255, 255, 255))

    def test_name_that_cannot_fit_is_rejected_instead_of_clipped(self):
        background = Image.new("RGB", (1005, 651), "white")
        for name in ("W" * 80, "Long Name " * 20):
            with self.subTest(name=name), self.assertRaises(ValueError):
                render_card(background, name, "1234")

    def test_whitespace_is_normalized_in_rendered_name(self):
        background = Image.new("RGB", (1005, 651), "white")
        normal = render_card(background, "Test User", "1234")
        spaced = render_card(background, "  Test\t User\n", "1234")
        self.assertIsNone(ImageChops.difference(normal, spaced).getbbox())

    def test_missing_fonts_report_an_actionable_error(self):
        with patch.object(printer_ui.ImageFont, "truetype", side_effect=OSError):
            with self.assertRaisesRegex(OSError, "font"):
                render_card(Image.new("RGB", (1005, 651)), "Test User", "1234")

    def test_number_accepts_only_up_to_four_ascii_digits(self):
        for value in ("", "1", "0123", "9999"):
            self.assertTrue(PrinterPanel._validate_number(value), value)
        for value in ("12345", "LT12", "-1", "1.2", "²", "１２", "١٢", " "):
            self.assertFalse(PrinterPanel._validate_number(value), value)


class PrinterPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.root = tk.Tk()
        except tk.TclError as error:
            raise unittest.SkipTest(f"Tk display unavailable: {error}")
        cls.root.withdraw()

    @classmethod
    def tearDownClass(cls):
        cls.root.destroy()

    def setUp(self):
        self.panel = PrinterPanel(self.root)
        self.addCleanup(self.panel.destroy)

    def fill_form(self, name="Ąžuolas Žemaitis"):
        self.panel.full_name.set(name)
        self.panel.number.set("1234")

    def test_export_is_full_resolution_and_matches_preview_at_different_dpi(self):
        original_scaling = self.root.tk.call("tk", "scaling")
        self.addCleanup(self.root.tk.call, "tk", "scaling", original_scaling)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "card.png"
            for scaling in (1.0, 1.33, 2.0):
                with self.subTest(scaling=scaling):
                    self.root.tk.call("tk", "scaling", scaling)
                    self.fill_form()
                    with patch.object(printer_ui.filedialog, "asksaveasfilename", return_value=str(output)):
                        self.panel._submit()
                    with Image.open(output) as exported:
                        self.assertEqual(exported.size, (1005, 651))
                        expected = exported.resize((PREVIEW_WIDTH, PREVIEW_HEIGHT), Image.Resampling.LANCZOS)
                    actual = ImageTk.getimage(self.panel._preview_image).convert("RGB")
                    self.assertIsNone(ImageChops.difference(expected, actual).getbbox())

    def test_missing_fields_and_invalid_numbers_do_not_open_save_dialog(self):
        with patch.object(printer_ui.filedialog, "asksaveasfilename") as dialog:
            self.panel._submit()
            self.fill_form()
            self.panel.number.set("²")
            self.panel._submit()
        dialog.assert_not_called()

    def test_invalid_name_clears_stale_card_and_recovers_after_edit(self):
        self.fill_form()
        self.panel.full_name.set("W" * 80)
        self.assertIsNone(self.panel._card_image)
        self.assertEqual(str(self.panel._export_button["state"]), "disabled")
        self.fill_form()
        self.assertIsNotNone(self.panel._card_image)
        self.assertEqual(str(self.panel._export_button["state"]), "normal")

    def test_cancel_does_not_save_or_report_success(self):
        self.fill_form()
        with patch.object(printer_ui.filedialog, "asksaveasfilename", return_value=""):
            with patch.object(Image.Image, "save") as save:
                self.panel._submit()
        save.assert_not_called()
        self.assertNotIn("saved", self.panel._status.get())

    def test_write_failure_is_reported_and_export_can_be_retried(self):
        self.fill_form()
        with patch.object(printer_ui.filedialog, "asksaveasfilename", return_value="card.png"):
            with patch.object(Image.Image, "save", side_effect=PermissionError("Read only")):
                with patch.object(printer_ui.messagebox, "showerror") as error:
                    self.panel._submit()
        error.assert_called_once()
        self.assertIn("failed", self.panel._status.get())
        self.assertEqual(str(self.panel._export_button["state"]), "normal")

    def test_windows_filename_characters_are_sanitized(self):
        self.fill_form('Test/User: A?')
        with patch.object(printer_ui.filedialog, "asksaveasfilename", return_value="") as dialog:
            self.panel._submit()
        self.assertEqual(dialog.call_args.kwargs["initialfile"], "Test_User__A__card.png")
        self.assertIs(dialog.call_args.kwargs["parent"], self.panel)

    def test_missing_or_corrupt_asset_does_not_break_main_window(self):
        for problem in (FileNotFoundError("Missing"), OSError("Corrupt image")):
            with self.subTest(problem=problem):
                with patch.object(printer_ui.Image, "open", side_effect=problem):
                    window = MainWindow(self.root, [], Mock(), Mock(), Mock())
                try:
                    notebook = window._main_frame.winfo_children()[0]
                    self.assertEqual(len(notebook.tabs()), 3)
                    panel = notebook.winfo_children()[2].winfo_children()[0]
                    self.assertEqual(str(panel._export_button["state"]), "disabled")
                finally:
                    window.destroy()

    def test_packaged_asset_is_loaded_from_bundle_not_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            asset_dir = Path(directory) / "card-bg"
            asset_dir.mkdir()
            Image.new("RGB", (1005, 651), "pink").save(asset_dir / "card.png")
            with patch.object(sys, "_MEIPASS", directory, create=True):
                panel = PrinterPanel(self.root)
            try:
                self.assertEqual(panel._background.getpixel((0, 0)), (255, 192, 203))
            finally:
                panel.destroy()


@unittest.skipUnless(sys.platform == "win32", "Windows installer")
class InstallerCardAssetTests(unittest.TestCase):
    def test_pyinstaller_receives_one_complete_card_asset_argument(self):
        repo = Path(__file__).resolve().parents[1]
        command = r"""
        $ErrorActionPreference = 'Stop'
        $repoRoot = (Get-Location).Path
        $buildRoot = Join-Path $repoRoot 'build'
        $appDist = Join-Path $repoRoot 'dist/app'
        $iconFile = Join-Path $buildRoot 'app.ico'
        $versionInfo = Join-Path $buildRoot 'version.txt'
        $tokens = $null
        $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseFile(
            (Join-Path $repoRoot 'packaging/build_installer.ps1'), [ref]$tokens, [ref]$errors)
        if ($errors.Count) { throw 'Invalid build script' }
        $assignment = $ast.Find({ param($node)
            $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
            $node.Left.Extent.Text -eq '$pyInstallerArgs'
        }, $true)
        Invoke-Expression $assignment.Right.Extent.Text | ConvertTo-Json
        """
        result = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            cwd=repo, capture_output=True, text=True, check=True,
        )
        arguments = json.loads(result.stdout)
        asset_index = arguments.index("--add-data")
        self.assertEqual(arguments[asset_index + 1], str(repo / "card-bg" / "card.png") + ";card-bg")
        self.assertEqual(arguments[asset_index + 2], "--distpath")


if __name__ == "__main__":
    unittest.main()
