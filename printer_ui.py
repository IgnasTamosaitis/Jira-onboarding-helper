"""Personalized access-card preview and PNG export."""

import os
from pathlib import Path
import re
import sys
import tkinter as tk
from tkinter import filedialog, messagebox

from PIL import Image, ImageDraw, ImageFont, ImageTk


BG = "#F7F8FA"
WHITE = "#FFFFFF"
ACCENT = "#0C66E4"
GRAY = "#5E6C84"
TEXT = "#172B4D"
BORDER = "#DFE3EA"
PREVIEW_WIDTH = 430
PREVIEW_HEIGHT = 278
NAME_LETTER_SPACING = 1
MAX_NAME_LENGTH = 120


def _load_font(filename: str, size: int):
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / filename
    # Let Pillow find an installed font when Windows uses a different font path.
    for candidate in (str(font_path), filename, "DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    raise OSError("No suitable card font is available. Install Arial and try again.")


def _text_width(text, font, spacing):
    return sum(font.getlength(char) for char in text) + max(0, len(text) - 1) * spacing


def _wrap_name(name, font, max_width, spacing):
    lines = []
    current = ""
    for word in name.split():
        if _text_width(word, font, spacing) > max_width:
            return None
        candidate = f"{current} {word}".strip()
        if current and _text_width(candidate, font, spacing) > max_width:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def render_card(background: Image.Image, full_name: str, number: str) -> Image.Image:
    """Render once at template resolution for both preview and export."""
    full_name = " ".join(full_name.split())
    if len(full_name) > MAX_NAME_LENGTH:
        raise ValueError(f"Use a name of at most {MAX_NAME_LENGTH} characters.")
    card = background.convert("RGB")
    draw = ImageDraw.Draw(card)
    scale_x = card.width / PREVIEW_WIDTH
    scale_y = card.height / PREVIEW_HEIGHT
    spacing = NAME_LETTER_SPACING * scale_x
    name = full_name or "Your name"
    top = card.height * 0.59
    bottom = card.height * 0.86
    for size in range(round(27 * scale_y), round(14 * scale_y) - 1, -1):
        font = _load_font("arialbd.ttf", size)
        lines = _wrap_name(name, font, 250 * scale_x, spacing)
        ascent, descent = font.getmetrics()
        line_height = ascent + descent + round(2 * scale_y)
        if lines and len(lines) <= 2 and len(lines) * line_height <= bottom - top:
            break
    else:
        raise ValueError("The name is too long to fit on the card. Use a shorter display name.")

    for index, line in enumerate(lines):
        x = (card.width - _text_width(line, font, spacing)) / 2
        baseline = top + ascent + index * line_height
        for character in line:
            draw.text((x, baseline), character, font=font, fill="#0B5669", anchor="ls")
            x += font.getlength(character) + spacing
    number_font = _load_font("consola.ttf", round(13 * scale_y))
    draw.text(
        (14 * scale_x, card.height - 12 * scale_y),
        f"LT{number or '****'}", font=number_font, fill=GRAY, anchor="ld",
    )
    return card


class PrinterPanel(tk.Frame):
    """Two-pane card form with a live preview of the exported image."""

    def __init__(self, parent):
        super().__init__(parent, bg=BG)
        self.full_name = tk.StringVar(self)
        self.number = tk.StringVar(self)
        self._status = tk.StringVar(self, value="Enter the card details on the left.")
        self._background = None
        self._card_image = None
        self._preview_image = None
        self._build()

    def _build(self):
        bottom = tk.Frame(self, bg="#EEF2F7", height=46)
        bottom.pack(fill="x", side="bottom")
        bottom.pack_propagate(False)
        self._export_button = tk.Button(
            bottom, text="Export PNG", command=self._submit,
            bg=ACCENT, fg=WHITE, relief="flat", bd=0,
            font=("Segoe UI", 9, "bold"), padx=14, pady=4,
            activebackground="#0055B8", activeforeground=WHITE, cursor="hand2",
        )
        self._export_button.pack(side="right", padx=10, pady=8)
        tk.Label(bottom, textvariable=self._status, bg="#EEF2F7", fg=GRAY,
                 font=("Segoe UI", 9), anchor="w").pack(
                     side="left", fill="x", expand=True, padx=12)

        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)
        left = tk.Frame(body, bg=WHITE, width=320)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        tk.Label(left, text="Card details", bg=WHITE, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=14, pady=(14, 6))

        form = tk.Frame(left, bg=WHITE)
        form.pack(fill="x", padx=14, pady=4)
        number_validate = (self.register(self._validate_number), "%P")
        for label, variable in (("Full name", self.full_name), ("Number (after LT)", self.number)):
            tk.Label(form, text=label, bg=WHITE, fg=TEXT,
                     font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(8, 3))
            entry = tk.Entry(
                form, textvariable=variable, relief="solid", bd=1,
                font=("Segoe UI", 10), highlightthickness=1,
                highlightbackground="#C7D1DB", highlightcolor=ACCENT,
            )
            if variable is self.number:
                entry.configure(validate="key", validatecommand=number_validate)
            entry.pack(fill="x", ipady=5)
        tk.Label(
            form, text="Enter an existing card number. Export the image, then open it in your card-printing software.",
            bg=WHITE, fg=GRAY, font=("Segoe UI", 9), wraplength=285, justify="left",
        ).pack(fill="x", pady=14)

        right = tk.Frame(body, bg=BG)
        right.pack(side="left", fill="both", expand=True)
        tk.Label(right, text="Preview", bg=BG, fg=GRAY,
                 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24, pady=(14, 6))
        preview = tk.Frame(
            right, bg=WHITE, highlightbackground=BORDER, highlightthickness=1,
            width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT,
        )
        preview.pack(anchor="n", padx=24, pady=4)
        preview.pack_propagate(False)
        self._preview_label = tk.Label(preview, bg=WHITE, fg=GRAY, wraplength=380, bd=0)
        self._preview_label.pack(fill="both", expand=True)

        asset_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        try:
            with Image.open(asset_root / "card-bg" / "card.png") as background:
                self._background = background.convert("RGB")
        except (OSError, ValueError):
            self._status.set("Card template unavailable. Reinstall the app to restore it.")
            self._preview_label.configure(text="Card template unavailable.")
            self._export_button.configure(state="disabled")
            return

        for variable in (self.full_name, self.number):
            variable.trace_add("write", self._update_preview)
        self._update_preview()

    def _update_preview(self, *_args):
        self._card_image = None
        try:
            number = self.number.get().strip()
            if not self._validate_number(number):
                raise ValueError("Enter one to four digits after LT.")
            card = render_card(self._background, self.full_name.get(), number)
        except (OSError, ValueError) as error:
            self._status.set(str(error))
            self._preview_label.configure(image="", text=str(error))
            self._export_button.configure(state="disabled")
            return
        self._card_image = card
        self._preview_image = ImageTk.PhotoImage(
            card.resize((PREVIEW_WIDTH, PREVIEW_HEIGHT), Image.Resampling.LANCZOS), master=self,
        )
        self._preview_label.configure(image=self._preview_image, text="")
        self._export_button.configure(state="normal")
        self._status.set("Ready to export." if self.full_name.get().strip() and number
                         else "Enter the card details on the left.")

    def _submit(self):
        name = " ".join(self.full_name.get().split())
        number = self.number.get().strip()
        if not name or not number:
            self._status.set("Complete both fields before exporting.")
            return
        if not self._validate_number(number):
            self._status.set("Enter one to four digits after LT.")
            return
        if self._card_image is None:
            return
        # Employee names can contain characters that Windows forbids in filenames.
        filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name).replace(" ", "_")
        try:
            output_path = filedialog.asksaveasfilename(
                parent=self, title="Save card image", defaultextension=".png",
                filetypes=(("PNG image", "*.png"),), initialfile=f"{filename}_card.png",
            )
            if not output_path:
                return
            self._card_image.save(output_path, "PNG")
        except (OSError, ValueError, tk.TclError) as error:
            self._status.set("Card export failed. Choose another location and try again.")
            messagebox.showerror("Card export failed", str(error), parent=self)
            return
        self._status.set(f"Card image saved to {output_path}")

    @staticmethod
    def _validate_number(value):
        return re.fullmatch(r"[0-9]{0,4}", value) is not None
