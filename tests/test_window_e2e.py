#!/usr/bin/env python3
"""sealed together with the real sealed-dialog it finds next to itself, typed
into with XTEST. Throwaway store only.
    xvfb-run -a /usr/bin/python3 tests/test_window_e2e.py"""
import os, pty, select, subprocess, sys, time, tempfile, shutil, runpy
HERE = os.path.dirname(os.path.abspath(__file__))
src = open(HERE + "/test_dialog.py").read()
H = {"__file__": HERE + "/test_dialog.py"}; exec(src.split("# ---- tests ----")[0], H)   # keyboard helpers
B = os.path.join(os.path.dirname(HERE), "sealed"); PW = "throwaway-passphrase-123"
tmp = tempfile.mkdtemp()
env = {"PATH": os.environ["PATH"], "HOME": tmp, "SEALED_HOME": tmp + "/s", "SEALED_PORT": "18833", "DISPLAY": os.environ["DISPLAY"],
       "XAUTHORITY": os.environ.get("XAUTHORITY", ""), "FEEDBACK_RELAY": "off"}
def run(*a): return subprocess.run([B, *a], env=env, capture_output=True, text=True, timeout=30, start_new_session=True)
def human_init():
    pid, fd = pty.fork()
    if pid == 0:
        os.dup2(os.open(os.devnull, os.O_WRONLY), 1); os.execve(B, [B, "init"], env)
    buf = b""
    while True:
        if select.select([fd], [], [], 0.05)[0]:
            try: buf += os.read(fd, 4096)
            except OSError: time.sleep(.02)
            if buf.rstrip().endswith(b":"): time.sleep(.1); os.write(fd, PW.encode() + b"\n"); buf = b""
        if os.waitpid(pid, os.WNOHANG)[0]: return
def windowed(args, title, keys):
    p = subprocess.Popen([B, *args], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    for _ in range(100):
        w = H["find"](title)
        if w: break
        time.sleep(0.05)
    time.sleep(0.5); H["x"].XSetInputFocus(H["D"], w, 1, 0); H["x"].XFlush(H["D"]); time.sleep(0.2); keys()
    out, err = p.communicate(timeout=20); return p.returncode, out + err
ok = []
assert run("daemon", "status").returncode == 3, "port busy"
try:
    human_init(); run("daemon", "start")
    rc, o = windowed(["unlock", "--dialog"], "sealed: unlock", lambda: (H["typ"](PW), H["key"]("Return")))
    ok.append(("unlock --dialog", rc == 0 and '"locked":false' in o and PW not in o, o))
    rc, o = windowed(["request", "demo/key"], "sealed: enter a value", lambda: (H["typ"]("fake-value-42"), H["tab"](), H["typ"]("fake-value-42"), H["key"]("Return")))
    ok.append(("request stores, no value in output", rc == 0 and "fake-value-42" not in o, o))
    r = run("exec", "--env", "K=demo/key", "--", "sh", "-c", 'test "$K" = fake-value-42 && echo exact')
    ok.append(("stored value exact", r.stdout == "exact\n", r.stdout + r.stderr))
    rc, o = windowed(["request", "demo/other"], "sealed: enter a value", lambda: H["key"]("Escape"))
    ok.append(("cancel stores nothing", rc == 85 and "demo/other" not in run("ls").stdout, o))
finally:
    run("daemon", "stop"); shutil.rmtree(tmp)
for n, good, info in ok: print("PASS" if good else "FAIL", n, "" if good else info[:300])
sys.exit(0 if all(g for _, g, _ in ok) else 1)
