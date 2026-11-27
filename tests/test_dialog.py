#!/usr/bin/env python3
"""Drives the real sealed-dialog with X keyboard events (XTEST), so the program
needs no test hooks. Needs libXtst and python3-gi (for the clipboard).
    xvfb-run -a /usr/bin/python3 tests/test_dialog.py [shots-dir]
EXTRA=--timeout also waits out the 3 minute timeout."""
import ctypes as C, os, signal, subprocess, sys, time
BIN = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sealed-dialog")
SHOTS = sys.argv[1] if len(sys.argv) > 1 else None
x = C.CDLL("libX11.so.6"); xt = C.CDLL("libXtst.so.6")
x.XOpenDisplay.restype = C.c_void_p; D = C.c_void_p(x.XOpenDisplay(None))
x.XDefaultRootWindow.restype = C.c_ulong
x.XStringToKeysym.restype = C.c_ulong; x.XKeysymToKeycode.restype = C.c_ubyte
x.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
x.XSetInputFocus.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_ulong]
x.XFetchName.argtypes = [C.c_void_p, C.c_ulong, C.POINTER(C.c_char_p)]
x.XQueryTree.argtypes = [C.c_void_p, C.c_ulong] + [C.c_void_p] * 4
def find(title):
    r, p, kids, n = C.c_ulong(), C.c_ulong(), C.POINTER(C.c_ulong)(), C.c_uint()
    x.XQueryTree(D, x.XDefaultRootWindow(D), C.byref(r), C.byref(p), C.byref(kids), C.byref(n))
    for i in range(n.value):
        name = C.c_char_p()
        if x.XFetchName(D, kids[i], C.byref(name)) and name.value == title.encode(): return kids[i]
def code(sym): return x.XKeysymToKeycode(D, x.XStringToKeysym(sym.encode()))
def key(sym, *mods):
    for m in mods: xt.XTestFakeKeyEvent(D, code(m), 1, 0)
    xt.XTestFakeKeyEvent(D, code(sym), 1, 0); xt.XTestFakeKeyEvent(D, code(sym), 0, 0)
    for m in mods: xt.XTestFakeKeyEvent(D, code(m), 0, 0)
    x.XFlush(D); time.sleep(0.06)
def typ(s):
    for ch in s: key({"-": "minus", " ": "space"}.get(ch, ch))
def tab(n=1):
    for _ in range(n): key("Tab")
def back_tab(n=1):
    for _ in range(n): key("Tab", "Shift_L")
def paste(text):
    owner = subprocess.Popen(["/usr/bin/python3", "-I", "-c",
        "import gi,sys;gi.require_version('Gtk','3.0');from gi.repository import Gtk,Gdk;"
        "c=Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD);c.set_text(sys.argv[1],-1);print(1,flush=True);Gtk.main()", text], stdout=subprocess.PIPE)
    owner.stdout.readline(); time.sleep(0.3); key("v", "Control_L"); time.sleep(0.5); owner.kill(); owner.wait()

# ---- tests ----
fails, count = [], 0
def check(name, cond, info=""):
    global count; count += 1
    if not cond: fails.append(name); print("FAIL", name, info)
class W:
    def __init__(s, confirm, body="body text"):
        s.title = f"sealed-test-{time.monotonic_ns()}"
        s.p = subprocess.Popen([BIN, s.title, "Enter value for secret  a/b", body, "1" if confirm else "0"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        for _ in range(100):
            w = find(s.title)
            if w: break
            time.sleep(0.05)
        assert w, "window did not appear"
        time.sleep(0.4); x.XSetInputFocus(D, w, 1, 0); x.XFlush(D); time.sleep(0.2)
    def open(s): time.sleep(0.4); return s.p.poll() is None
    def done(s):
        try: out, err = s.p.communicate(timeout=4)
        except subprocess.TimeoutExpired: s.p.kill(); s.p.communicate(); return ("still open", b"")
        return (s.p.returncode, out)
    def shot(s, name):
        if SHOTS: time.sleep(0.3); subprocess.run(["import", "-window", "root", f"{SHOTS}/{name}.png"])
    def kill(s): s.p.kill(); s.p.communicate()

w = W(False); typ("abc-123"); key("Return"); r = w.done(); check("type + Enter returns exact text", r == (0, b"abc-123"), r)
w = W(False); typ("abc"); key("Escape"); r = w.done(); check("Escape cancels, prints nothing", r == (1, b""), r)
w = W(False); key("Return"); check("Enter on empty does nothing", w.open()); w.kill()
w = W(True); typ("abc"); key("Return"); check("needs a repeat", w.open()); tab(); typ("abd"); w.shot("hidden-mismatch")
key("Return"); check("mismatch blocks Enter", w.open()); key("BackSpace"); typ("c"); key("Return"); r = w.done(); check("matching repeat accepted", r == (0, b"abc"), r)
w = W(True); typ("fake-value"); tab(2); key("space"); w.shot("visible"); back_tab(); key("Return")
r = w.done(); check("checkbox: text kept, no repeat needed", r == (0, b"fake-value"), r)
w = W(True); typ("abc"); tab(2); key("space"); key("space"); back_tab(2); key("Return"); check("unchecking requires the repeat again", w.open()); w.kill()
w = W(False); tab(1); key("space"); back_tab(); typ("zz"); key("Return"); r = w.done(); check("single field with checkbox", r == (0, b"zz"), r)
u = "pässwörd €ñ $x `y` \"q\""
w = W(False); paste(u); key("Return"); r = w.done(); check("pasted unicode + symbols exact", r == (0, u.encode()), r)
w = W(False); paste("line1\nline2"); key("Return"); r = w.done(); check("multi-line paste refused, nothing printed", r == (4, b""), r)
w = W(False); paste("a" * 5000); key("Return"); r = w.done(); check("long paste not truncated", r == (0, b"a" * 5000), (r[0], len(r[1])))
# Keys that queue up while the window is busy must act like keys typed slowly.
# Stopping the process makes that happen every time.
def while_stalled(w, keys):
    time.sleep(0.3); w.p.send_signal(signal.SIGSTOP); keys(); time.sleep(0.2); w.p.send_signal(signal.SIGCONT); time.sleep(0.5)
w = W(True); typ("fake-value"); tab(2); while_stalled(w, lambda: (key("space"), back_tab(), key("Return")))
r = w.done(); check("queued: checkbox, Shift+Tab, Enter", r == (0, b"fake-value"), r)
w = W(True); typ("abc"); tab(); typ("abc"); while_stalled(w, lambda: (key("BackSpace"), key("Return")))
check("queued: Enter after an edit that breaks the match is refused", w.open())
typ("c"); check("a refused Enter does not submit later by itself", w.open()); key("Return"); r = w.done(); check("a new Enter submits", r == (0, b"abc"), r)
w = W(True); typ("abc"); tab(); typ("ab"); while_stalled(w, lambda: (typ("c"), key("Return"))); r = w.done(); check("queued: last repeat character, Enter", r == (0, b"abc"), r)
# buttons: from the single field, tab order is checkbox, then the two buttons
hit = []
for n in (2, 3):
    w = W(False); typ("btn"); tab(n); key("space"); hit.append(w.done())
check("OK and Cancel buttons work", sorted(hit, key=str) == sorted([(0, b"btn"), (1, b"")], key=str), hit)
w = W(False, body="<b>not bold</b> & text"); w.shot("markup"); check("markup text does not crash", w.open()); w.kill()
env = {k: v for k, v in os.environ.items() if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
r = subprocess.run([BIN, "t", "h", "b", "0"], env=env, capture_output=True, timeout=10); check("no display exits 3", r.returncode == 3, r)
r = subprocess.run([BIN, "t"], capture_output=True, timeout=10); check("bad arguments exit 3", r.returncode == 3, r)
if "--timeout" in os.environ.get("EXTRA", ""):
    w = W(False); typ("abc"); t0 = time.time()
    out, _ = w.p.communicate(timeout=200); check("times out near 180s", w.p.returncode == 1 and out == b"" and 170 < time.time() - t0 < 190, (w.p.returncode, time.time() - t0))
print(f"{count - len(fails)}/{count} passed")
sys.exit(1 if fails else 0)
