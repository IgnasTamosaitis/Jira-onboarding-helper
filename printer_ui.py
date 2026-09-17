import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from pathlib import Path
import sys

from PIL import Image, ImageDraw, ImageFont, ImageTk


BG = "#F7F8FA"
WHITE = "#FFFFFF"
ACCENT = "#0C66E4"
GRAY = "#5E6C84"
TEXT = "#172B4D"
BORDER = "#DFE3EA"
SOFT_BLUE = "#E9F2FF"
PREVIEW_WIDTH = 430
PREVIEW_HEIGHT = 278
NAME_LETTER_SPACING = 1


class PrinterPanel(tk.Frame):
	"""Two-pane card-printer form with a live preview."""

	def __init__(self, parent):
		super().__init__(parent, bg=BG)
		self.full_name = tk.StringVar()
		self.number = tk.StringVar()
		self._status = tk.StringVar(value="Enter the card details on the left.")
		self._preview_name = None
		self._preview_number = None
		self._build()

	def _build(self):
		bottom = tk.Frame(self, bg="#EEF2F7", height=46)
		bottom.pack(fill="x", side="bottom")
		bottom.pack_propagate(False)
		tk.Label(bottom, textvariable=self._status, bg="#EEF2F7", fg=GRAY,
				 font=("Segoe UI", 9)).pack(side="left", padx=12)
		tk.Button(bottom, text="Print card", command=self._submit,
				  bg=ACCENT, fg=WHITE, relief="flat", bd=0,
				  font=("Segoe UI", 9, "bold"), padx=14, pady=4,
				  activebackground="#0055B8", activeforeground=WHITE,
				  cursor="hand2").pack(side="right", padx=10, pady=8)

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
		for label, variable in (
			("Full name", self.full_name),
			("Number", self.number),
		):
			tk.Label(form, text=label, bg=WHITE, fg=TEXT,
					 font=("Segoe UI", 9, "bold"), anchor="w").pack(fill="x", pady=(8, 3))
			entry = tk.Entry(form, textvariable=variable, relief="solid", bd=1,
							 font=("Segoe UI", 10), highlightthickness=1,
							 highlightbackground="#C7D1DB", highlightcolor=ACCENT)
			if variable is self.number:
				entry.configure(validate="key", validatecommand=number_validate)
			entry.pack(fill="x", ipady=5)

		right = tk.Frame(body, bg=BG)
		right.pack(side="left", fill="both", expand=True)
		tk.Label(right, text="Preview", bg=BG, fg=GRAY,
				 font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=24, pady=(14, 6))

		preview = tk.Frame(right, bg=WHITE, highlightbackground=BORDER,
						   highlightthickness=1, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT)
		preview.pack(anchor="n", padx=24, pady=4)
		preview.pack_propagate(False)
		asset_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
		background = Image.open(asset_root / "card-bg" / "card.png")
		background = background.resize((PREVIEW_WIDTH, PREVIEW_HEIGHT), Image.Resampling.LANCZOS)
		self._preview_background = ImageTk.PhotoImage(background)
		canvas = tk.Canvas(preview, width=PREVIEW_WIDTH, height=PREVIEW_HEIGHT,
										 bg=WHITE, highlightthickness=0)
		canvas.pack()
		canvas.create_image(0, 0, image=self._preview_background, anchor="nw")
		self._preview_name_items = []
		self._preview_name_font = tkfont.Font(family="Arial", size=20, weight="bold")
		self._preview_number = canvas.create_text(
			14, PREVIEW_HEIGHT - 12, text="LT****", fill=GRAY,
			font=("Consolas", 10), anchor="sw")
		self._preview_canvas = canvas

		for variable in (self.full_name, self.number):
			variable.trace_add("write", self._update_preview)
		self._update_preview()

	def _update_preview(self, *_args):
		full_name = self.full_name.get().strip()
		self._set_preview_name(self._wrap_name(full_name) if full_name else ("Your name",))
		self._preview_canvas.itemconfigure(
			self._preview_number, text=f"LT{self.number.get().strip() or '****'}")

	def _set_preview_name(self, lines):
		for item in self._preview_name_items:
			self._preview_canvas.delete(item)
		self._preview_name_items = []
		line_height = self._preview_name_font.metrics("linespace")
		start_y = PREVIEW_HEIGHT * 0.59
		for line_number, line in enumerate(lines):
			line_width = self._preview_name_font.measure(line)
			line_width += max(0, len(line) - 1) * NAME_LETTER_SPACING
			x = (PREVIEW_WIDTH - line_width) / 2
			for character in line:
				self._preview_name_items.append(self._preview_canvas.create_text(
					x, start_y + line_number * line_height, text=character,
					fill="#0B5669", font=self._preview_name_font, anchor="nw"))
				x += self._preview_name_font.measure(character) + NAME_LETTER_SPACING

	def _wrap_name(self, name):
		lines = []
		current = ""
		for word in name.split():
			candidate = f"{current} {word}".strip()
			width = self._preview_name_font.measure(candidate)
			width += max(0, len(candidate) - 1) * NAME_LETTER_SPACING
			if current and width > 250:
				lines.append(current)
				current = word
			else:
				current = candidate
		if current:
			lines.append(current)
		return tuple(lines)

	def _submit(self):
		if not all((self.full_name.get().strip(),
					self.number.get().strip())):
			self._status.set("Complete both fields before printing.")
			return

		asset_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
		card_path = asset_root / "card-bg" / "card.png"
		output_path = filedialog.asksaveasfilename(
			title="Save card image",
			defaultextension=".png",
			filetypes=(("PNG image", "*.png"), ("All files", "*.*")),
			initialfile=f"{self.full_name.get().strip().replace(' ', '_')}_card.png",
		)
		if not output_path:
			return

		try:
			card = Image.open(card_path).convert("RGB")
			draw = ImageDraw.Draw(card)
			scale_x = card.width / PREVIEW_WIDTH
			scale_y = card.height / PREVIEW_HEIGHT
			name_font = self._load_font("arialbd.ttf", round(20 * scale_y))
			number_font = self._load_font("consolab.ttf", round(12 * scale_y))
			name_lines = self._wrap_name(self.full_name.get().strip())
			self._draw_spaced_name(draw, card, name_font, name_lines, scale_x, scale_y)
			draw.text(
				(14 * scale_x, card.height - (12 * scale_y)),
				f"LT{self.number.get().strip()}", font=number_font, fill=GRAY, anchor="ls")
			card.save(output_path, "PNG")
		except (OSError, ValueError) as error:
			messagebox.showerror("Card export failed", str(error), parent=self)
			return
		self._status.set(f"Card image saved to {output_path}")

	@staticmethod
	def _load_font(filename, size):
		font_path = Path("C:/Windows/Fonts") / filename
		if font_path.exists():
			return ImageFont.truetype(str(font_path), size)
		return ImageFont.load_default()

	@staticmethod
	def _draw_spaced_name(draw, card, font, lines, scale_x, scale_y):
		if not lines:
			return
		spacing = NAME_LETTER_SPACING * scale_x
		line_height = font.getbbox("Ag")[3] - font.getbbox("Ag")[1]
		start_y = PREVIEW_HEIGHT * 0.59 * scale_y
		for line_number, line in enumerate(lines):
			widths = [draw.textlength(character, font=font) for character in line]
			line_width = sum(widths) + max(0, len(line) - 1) * spacing
			x = (card.width - line_width) / 2
			for character, character_width in zip(line, widths):
				draw.text((x, start_y + line_number * line_height), character,
						font=font, fill="#0B5669")
				x += character_width + spacing

	@staticmethod
	def _validate_number(value):
		return len(value) <= 4 and value.isdigit() or value == ""


def main():
	root = tk.Tk()
	root.title("Card Printer")
	root.geometry("820x380")
	root.minsize(760, 340)
	PrinterPanel(root).pack(fill="both", expand=True)
	root.mainloop()


if __name__ == "__main__":
	main()
