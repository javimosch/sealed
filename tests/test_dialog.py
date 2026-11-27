#!/usr/bin/env python3
"""Drives the real GTK window. Run with the system python under a display:
    xvfb-run -a /usr/bin/python3 tests/test_dialog.py"""
import io, os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.dont_write_bytecode = True
import dialog
from dialog import GLib, Gtk

fails = []
def check(name, cond):
    if not cond: fails.append(name); print("FAIL", name)

d, first, again, show, ok, note = dialog.build("t", "Enter value for secret  a/b", "<b>plain</b> body", True)
check("hidden by default", not show.get_active() and not first.get_visibility() and not again.get_visibility())
check("OK disabled when empty", not ok.get_sensitive())
first.set_text("abc123"); check("needs a repeat", not ok.get_sensitive())
again.set_text("abc12x"); check("mismatch", not ok.get_sensitive() and "do not match" in note.get_text())
again.set_text("abc123"); check("match", ok.get_sensitive() and note.get_text() == "")
again.set_text("zzz"); show.set_active(True)
check("show reveals, keeps text, drops repeat", first.get_visibility() and first.get_text() == "abc123" and not again.get_visible() and ok.get_sensitive())
show.set_active(False)
check("unchecking hides and requires repeat again", not first.get_visibility() and again.get_visible() and not ok.get_sensitive())
labels = [w.get_text() for w in d.get_content_area().get_children() if isinstance(w, Gtk.Label)]
check("text is never markup", "<b>plain</b> body" in labels)
d.destroy()

d, first, again, show, ok, note = dialog.build("t", "h", "b", False)
check("single field", again is None)
first.set_text("pw"); check("single field ready", ok.get_sensitive())
show.set_active(True); check("single field can be shown", first.get_visibility())
d.destroy()

def run_main(text, action, confirm="0"):
    """run main() and act on its window as a person would"""
    def act():
        w = [x for x in Gtk.Window.list_toplevels() if isinstance(x, Gtk.Dialog) and x.get_visible()]
        if not w: return True
        entries = [c for c in w[0].get_content_area().get_children() if isinstance(c, Gtk.Entry)]
        entries[0].set_text(text)
        w[0].response(action)
        return False
    GLib.timeout_add(50, act)
    real, buf = sys.stdout, io.BytesIO()
    sys.stdout = type("S", (), {"buffer": buf})()
    try: rc = dialog.main(["t", "h", "b", confirm])
    finally: sys.stdout = real
    return rc, buf.getvalue()

check("OK returns the text exactly", run_main("  pässword $x ", Gtk.ResponseType.OK) == (0, "  pässword $x ".encode()))
check("cancel returns nothing", run_main("typed", Gtk.ResponseType.CANCEL) == (1, b""))
got = run_main("a\nb", Gtk.ResponseType.OK)
check("multi-line refused: %r" % (got,), got == (4, b""))
print("dialog:", "all checks passed" if not fails else f"{len(fails)} failed")
sys.exit(1 if fails else 0)
