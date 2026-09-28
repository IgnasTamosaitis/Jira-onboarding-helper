"""Route wheel gestures through nested Tk widgets before class bindings eat them."""
import tkinter as tk
from tkinter import font, ttk


def install_scrolling(window):
    if getattr(window, "_scrolling_installed", False):
        return
    window._scrolling_installed = True
    tag = f"SmoothScroll:{window}"
    remainder = {}

    def attach(event):
        widget = event.widget
        if isinstance(widget, tk.Misc) and tag not in widget.bindtags():
            widget.bindtags((tag,) + widget.bindtags())

    def scroll(widget, amount, axis="y", precise=False):
        targets = []
        while isinstance(widget, tk.Misc):
            if isinstance(widget, (tk.Text, tk.Listbox, tk.Canvas, ttk.Treeview)):
                targets.append(widget)
            elif axis == "x" and isinstance(widget, (tk.Entry, ttk.Entry)):
                targets.append(widget)
            if isinstance(widget, (tk.Tk, tk.Toplevel)):
                break
            widget = getattr(widget, "master", None)
        if not targets:
            return
        if not amount:
            return "break"
        for target in targets:
            view = target.yview if axis == "y" else target.xview
            first, last = view()
            if (amount < 0 and first <= 0) or (amount > 0 and last >= 1):
                continue
            mode = "units"
            if isinstance(target, tk.Canvas):
                # Canvas units are pixels. Touchpad deltas are already pixels.
                scale = 1 if precise else 30
                target.configure(**{f"{axis}scrollincrement": 1})
            elif precise and isinstance(target, tk.Text):
                scale, mode = 1, "pixels"
            elif precise:
                # Lists and entries scroll in rows/characters; retain fractions.
                try:
                    metrics = font.Font(root=target, font=target.cget("font"))
                    size = metrics.metrics("linespace") if axis == "y" else metrics.measure("0")
                except tk.TclError:
                    size = 20 if axis == "y" else 8
                scale = 1 / max(1, size)
            else:
                scale = 3
            key = (str(target), axis)
            previous = remainder.get(key, 0)
            total = (previous if previous * amount >= 0 else 0) + amount * scale
            units = int(total)
            for old_key in tuple(remainder):
                if old_key[0] != key[0]:
                    remainder.pop(old_key)
            remainder[key] = total - units
            if units:
                view("scroll", units, mode)
            break
        return "break"

    def wheel(event):
        delta = getattr(event, "delta", 0)
        number = getattr(event, "num", None)
        amount = -1 if number == 4 else 1 if number == 5 else -delta / 120
        return scroll(event.widget, amount, "x" if event.state & 1 else "y")

    def touchpad(event):
        # Tk 9 emits a distinct event, with signed X/Y pixel deltas packed in %D.
        packed = event.delta
        dx, dy = (packed >> 16) & 0xffff, packed & 0xffff
        dx = dx if dx < 0x8000 else dx - 0x10000
        dy = dy if dy < 0x8000 else dy - 0x10000
        if event.state & 1:
            dx, dy = dy, dx
        handled = None
        if dx:
            handled = scroll(event.widget, -dx, "x", precise=True)
        if dy:
            handled = scroll(event.widget, -dy, "y", precise=True) or handled
        return handled

    window.bind("<Map>", attach, add="+")
    sequences = ["<MouseWheel>", "<Button-4>", "<Button-5>"]
    for sequence in sequences:
        window.bind_class(tag, sequence, wheel)
    try:
        window.bind_class(tag, "<TouchpadScroll>", touchpad)
        sequences.append("<TouchpadScroll>")
    except tk.TclError:
        pass  # Tk 8.6 reports touchpad gestures as MouseWheel events instead.

    def attach_existing(widget):
        if tag not in widget.bindtags():
            widget.bindtags((tag,) + widget.bindtags())
        for child in widget.winfo_children():
            if not isinstance(child, tk.Toplevel):
                attach_existing(child)

    attach_existing(window)

    def cleanup(event):
        if event.widget is window:
            for sequence in sequences:
                window.unbind_class(tag, sequence)

    window.bind("<Destroy>", cleanup, add="+")
