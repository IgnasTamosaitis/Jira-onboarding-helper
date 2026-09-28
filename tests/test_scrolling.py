import tkinter as tk
import unittest
import sys

from scrolling import install_scrolling


class ScrollingTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        install_scrolling(self.root)
        self.canvas = tk.Canvas(self.root, width=240, height=120)
        self.canvas.pack()
        self.frame = tk.Frame(self.canvas)
        self.canvas.create_window((0, 0), window=self.frame, anchor="nw")
        self.text = tk.Text(self.frame, height=3, width=20)
        self.text.pack()
        tk.Frame(self.frame, height=800, width=220).pack()
        self.root.update()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(0)

    def wheel(self, widget, delta):
        widget.event_generate("<MouseWheel>", delta=delta)
        self.root.update()

    def touchpad(self, widget, *, x=0, y=0):
        if self.root.tk.call("package", "vcompare", self.root.tk.call("package", "provide", "Tk"), "9.0") < 0:
            self.skipTest("TouchpadScroll requires Tk 9")
        # Tk packs two signed 16-bit pixel deltas into the event's delta field.
        widget.event_generate("<TouchpadScroll>", delta=(x << 16) | (y & 0xffff))
        self.root.update()

    def test_native_touchpad_over_empty_input_scrolls_page_both_directions(self):
        self.touchpad(self.text, y=-7)
        self.assertGreater(self.canvas.yview()[0], 0)
        self.touchpad(self.text, y=7)
        self.assertEqual(self.canvas.yview()[0], 0)

    def test_native_touchpad_over_canvas(self):
        self.touchpad(self.canvas, y=-1)
        self.assertGreater(self.canvas.yview()[0], 0)

    def test_native_touchpad_text_boundary_hands_off_to_page(self):
        self.text.insert("1.0", "\n".join(str(i) for i in range(80)))
        self.root.update()
        self.touchpad(self.text, y=-7)
        self.assertGreater(self.text.yview()[0], 0)
        self.assertEqual(self.canvas.yview()[0], 0)
        self.text.yview_moveto(1)
        self.touchpad(self.text, y=-7)
        self.assertGreater(self.canvas.yview()[0], 0)

    def test_horizontal_touchpad_does_not_scroll_vertically(self):
        self.touchpad(self.text, x=-12)
        self.assertEqual(self.canvas.yview()[0], 0)

    def test_diagonal_touchpad_preserves_signed_vertical_delta(self):
        self.touchpad(self.text, x=-4, y=-7)
        self.assertGreater(self.canvas.yview()[0], 0)

    @unittest.skipUnless(sys.platform == "win32", "Windows input messages")
    def test_windows_high_resolution_wheel_message_reaches_page(self):
        import ctypes
        from ctypes import wintypes

        # Tk resolves native wheel messages using the window at the supplied
        # screen coordinates, so another application must not cover our widget.
        self.root.attributes("-topmost", True)
        self.root.lift()
        self.root.update()
        send = ctypes.WinDLL("user32").SendMessageW
        send.argtypes = (wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)
        send.restype = ctypes.c_ssize_t
        x, y = self.text.winfo_rootx() + 5, self.text.winfo_rooty() + 5
        # Deliver to our own test window: Tk 9 converts this Windows message
        # to TouchpadScroll because its delta is not a multiple of 120.
        send(self.text.winfo_id(), 0x020A, ((-30) & 0xffff) << 16,
             ((y & 0xffff) << 16) | (x & 0xffff))
        self.root.update()
        self.assertGreater(self.canvas.yview()[0], 0)

    def test_small_trackpad_delta_over_input_scrolls_page(self):
        self.wheel(self.text, -30)
        self.assertGreater(self.canvas.yview()[0], 0)
        self.wheel(self.text, 30)
        self.assertAlmostEqual(self.canvas.yview()[0], 0, places=2)

    def test_text_scrolls_then_hands_off_at_boundary(self):
        self.text.insert("1.0", "\n".join(str(i) for i in range(80)))
        self.root.update()
        self.wheel(self.text, -120)
        self.assertGreater(self.text.yview()[0], 0)
        self.assertEqual(self.canvas.yview()[0], 0)
        self.text.yview_moveto(1)
        self.wheel(self.text, -120)
        self.assertGreater(self.canvas.yview()[0], 0)

    def test_dynamically_added_entry_routes_to_canvas(self):
        entry = tk.Entry(self.frame)
        entry.pack(before=self.text)
        self.root.update()
        self.wheel(entry, -30)
        self.assertGreater(self.canvas.yview()[0], 0)


if __name__ == "__main__":
    unittest.main()
