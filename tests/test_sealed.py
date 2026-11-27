#!/usr/bin/env python3
"""End-to-end tests: the real binary, a real daemon, a real pty for prompts."""
import json, os, pty, select, shutil, socket, sqlite3, stat, subprocess, sys, tempfile, time, glob, urllib.request, urllib.error

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BIN = os.path.join(ROOT, "sealed")
PASS = "correct horse battery staple"
SECRET = "s3cr3t-VALUE-9f8e7d6c"
fails, count = [], 0

def check(name, cond, info=""):
    global count
    count += 1
    if not cond:
        fails.append(name)
        print(f"FAIL {name}: {str(info)[:600]}")

def free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0)); p = s.getsockname()[1]; s.close(); return p

tmp = tempfile.mkdtemp(prefix="sealed-test-")
PORT = free_port()
ENV = {"PATH": os.environ["PATH"], "HOME": tmp, "SEALED_HOME": tmp + "/home", "SEALED_PORT": str(PORT),
       "FEEDBACK_RELAY": "off", "XDG_RUNTIME_DIR": tmp + "/run"}
os.mkdir(tmp + "/run", 0o700)

def run(*args, token=None, stdin=None, env=None):
    """agent-like: no controlling terminal at all"""
    e = dict(ENV, **(env or {}))
    if token: e["SEALED_TOKEN"] = token
    p = subprocess.run([BIN, *args], env=e, input=stdin if stdin is not None else "", capture_output=True,
                       text=True, start_new_session=True, timeout=30)
    return p.returncode, p.stdout, p.stderr

def human(*args, answers=(PASS,), token=None, stdin=None, tty_stdin=False):
    """a human at a terminal: answers each prompt through a pty"""
    e = dict(ENV)
    if token: e["SEALED_TOKEN"] = token
    out, err, inp = tmp + "/o", tmp + "/e", tmp + "/i"
    open(inp, "w").write(stdin or "")
    pid, fd = pty.fork()
    if pid == 0:
        if not tty_stdin: os.dup2(os.open(inp, os.O_RDONLY), 0)
        os.dup2(os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 1)
        os.dup2(os.open(err, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 2)
        os.execve(BIN, [BIN, *args], e)
    seen, pending, buf = "", list(answers), ""
    end = time.time() + 30
    st = None
    while time.time() < end and st is None:
        r, _, _ = select.select([fd], [], [], 0.05)
        if r:
            try: chunk = os.read(fd, 4096).decode(errors="replace")
            except OSError: chunk = ""; time.sleep(0.02)  # no slave fd open right now
            seen += chunk; buf += chunk
            if buf.rstrip().endswith(":") and pending:
                time.sleep(0.05); os.write(fd, (pending.pop(0) + "\n").encode()); buf = ""
        done, status = os.waitpid(pid, os.WNOHANG)
        if done: st = status
    if st is None:
        print("HUNG", args, repr(seen)); os.kill(pid, 9); _, st = os.waitpid(pid, 0)
    os.close(fd)
    return os.waitstatus_to_exitcode(st), open(out).read(), open(err).read(), seen

def js(s):
    try: return json.loads(s)
    except Exception: return {}

def etype(s): return js(s).get("error", {}).get("type")

def http(path, body=None, method="POST", headers=None):
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}{path}", data=None if body is None else json.dumps(body).encode(), method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r: return r.status, r.read().decode()
    except urllib.error.HTTPError as ex: return ex.code, ex.read().decode()

try:
    # ---- spec surface, no daemon ----
    rc, o, _ = run("help-json"); check("help-json", rc == 0 and "exec" in js(o).get("commands", {}), o)
    rc, o, _ = run("guide"); check("guide", rc == 0 and js(o)["guide"]["loop"], o)
    rc, o, _ = run("guide", "--human"); check("guide human", rc == 0 and o.startswith("# sealed"), o)
    rc, o, _ = run("version"); check("version", rc == 0 and len(js(o).get("build", "")) == 12, o)
    rc, o, e = run("nope"); check("unknown verb", rc == 80 and o == "" and etype(e) == "invalid_args", e)
    rc, _, e = run("ls"); check("no daemon", rc == 100 and js(e)["error"]["recoverable"], e)
    rc, _, e = run("init"); check("init needs tty", rc == 85, e)
    rc, o, e, _ = human("init", answers=("short",)); check("short passphrase", rc == 82, e)
    rc, o, e, _ = human("init", answers=(PASS, PASS + "x")); check("mismatch", rc == 82, e)
    rc, o, e, term = human("init", answers=(PASS, PASS)); check("init", rc == 0 and js(o).get("ok"), e)
    check("passphrase not echoed", PASS not in term, term)
    tf = tmp + "/home/token"; HUMAN = open(tf).read().strip()
    check("token file 0600", stat.S_IMODE(os.stat(tf).st_mode) == 0o600)
    check("db 0600", stat.S_IMODE(os.stat(tmp + "/home/sealed.db").st_mode) == 0o600)
    check("token not on stdout", HUMAN not in o)
    rc, _, e, _ = human("init", answers=(PASS, PASS)); check("init twice", rc == 95, e)

    # ---- daemon + locked state ----
    rc, o, _ = run("daemon", "status"); check("status stopped", rc == 3 and js(o)["daemon"] == "stopped", o)
    rc, o, e = run("daemon", "start"); check("start", rc == 0 and js(o)["daemon"] == "started", o + e)
    rc, o, _ = run("daemon", "start"); check("start idempotent", rc == 0 and js(o)["daemon"] == "already_running", o)
    rc, o, _ = run("status"); check("starts locked", js(o).get("locked") is True, o)
    rc, _, e = run("ls"); check("locked 86", rc == 86 and etype(e) == "locked", e)
    rc, _, e = run("unlock"); check("unlock needs tty", rc == 85, e)
    rc, _, e, _ = human("unlock", answers=("wrong passphrase!!",)); check("wrong pass", rc == 89, e)
    rc, o, e, _ = human("unlock"); check("unlock", rc == 0, e)

    # ---- put / ls ----
    rc, o, e = run("put", "app/api-key", stdin=SECRET + "\n"); check("put new", rc == 0 and js(o)["version"] == 1, e)
    rc, _, e = run("put", "other/db", stdin="other-secret-123456"); check("put other", rc == 0, e)
    rc, _, e = run("put", "apple/x", stdin="apple-secret-123456"); check("put apple", rc == 0, e)
    rc, _, e = run("put", "bad|name", stdin="v"); check("bad name", rc == 82, e)
    rc, _, e = run("put", "app/nul", stdin="a\0b"); check("nul rejected", rc == 82, e)
    rc, _, e = run("put", "app/empty", stdin=""); check("empty rejected", rc == 82, e)
    rc, _, e = run("put", "app/argv", "valueinargv"); check("value in argv rejected", rc == 80, e)
    rc, o, e = run("token", "create", "--kind", "agent", "--scope", "app/")
    check("human token w/o passphrase needs approval", rc == 85, o + e)
    rc, o, e, term = human("token", "create", "--kind", "agent", "--scope", "app/", "--rate", "500")
    check("create agent token", rc == 0 and js(o).get("token", "").startswith("sld_"), e + term)
    AG = js(o)["token"]; AGID = js(o)["id"]
    rc, o, e = run("ls", token=AG)
    names = [s["name"] for s in js(o).get("secrets", [])]
    check("agent ls scoped", rc == 0 and names == ["app/api-key"], o + e)
    check("ls never values", SECRET not in o)
    check("agent ls hides tokens", js(o)["secrets"][0]["scopes"] == [])
    rc, o, _ = run("ls"); check("human ls all", len(js(o)["secrets"]) == 3 and SECRET not in o, o)
    rc, _, e = run("ls", token="sld_" + "0" * 64); check("bad token", rc == 90, e)

    # ---- put with a hidden prompt ----
    def typed(name, text, again=None): return human("put", name, answers=(text, text if again is None else again), tty_stdin=True)
    rc, _, e, _ = typed("tmp/typed", "typed-SECRET-777", "typed-SECRET-778"); check("mismatch refused", rc == 82 and "do not match" in e, e)
    check("mismatch stores nothing", js(run("ls", "tmp")[1])["secrets"] == [])
    rc, o, e, term = typed("tmp/typed", "typed-SECRET-777")
    check("put prompt", rc == 0 and "typed-SECRET-777" not in term + o + e and "hidden" in term, term + e)
    rc, o, e = run("exec", "--env", "K=tmp/typed", "--", "sh", "-c", 'test "$K" = typed-SECRET-777 && echo same')
    check("prompted value exact", o == "same\n", o + e)
    rc, _, e, _ = typed("tmp/long", "a" * 6000); check("long paste refused", rc == 82, e)
    rc, _, e, _ = typed("tmp/multi", "line-one\nline-two"); check("multi-line paste refused", rc == 82, e)
    rc, _, e, _ = typed("tmp/pem", "-----BEGIN KEY-----"); check("PEM refused", rc == 82, e)
    rc, o, _ = run("ls", "tmp"); check("nothing partial stored", [s["name"] for s in js(o)["secrets"]] == ["tmp/typed"], o)
    human("rm", "tmp/typed")

    # ---- exec + redaction ----
    rc, o, e = run("exec", "--env", "K=app/api-key", "--", "sh", "-c", 'echo "v=$K"; echo "err $K" >&2; printf %s "$K" | base64; test "$K" = "' + SECRET + '" && echo match', token=AG)
    check("exec injects + redacts", rc == 0 and "v=***" in o and "match" in o and "err ***" in e and SECRET not in o + e, o + e)
    import base64
    check("base64 redacted", base64.b64encode(SECRET.encode()).decode() not in o, o)
    rc, o, _ = run("exec", "--env", "K=app/api-key", "--", "sh", "-c", 'printf %s "$K" | cut -c1-7 | tr -d "\\n"; sleep 0.3; printf %s "$K" | cut -c8-; echo', token=AG)
    check("redacts across chunks", SECRET not in o and "***" in o, o)
    rc, o, _ = run("exec", "--env", "K=app/api-key", "--", "sh", "-c", "printf 's3cr'", token=AG)
    check("partial prefix flushed at exit", o == "s3cr", repr(o))
    rc, o, _ = run("exec", "--env", "K=app/api-key", "--", "sh", "-c", "exit 7", token=AG); check("child exit code", rc == 7)
    rc, o, _ = run("exec", "--env", "K=app/api-key", "--", "sh", "-c", 'echo "[$SEALED_TOKEN]"', token=AG); check("token not inherited", o == "[]\n", o)
    rc, o, e = run("exec", "--file", "F=app/api-key", "--", "sh", "-c", 'stat -c %a "$F"; cat "$F"; echo; echo "P:$F" > ' + tmp + '/fpath', token=AG)
    fpath = open(tmp + "/fpath").read().strip()[2:]
    check("file 0600 + redacted", o.startswith("600\n") and SECRET not in o and "***" in o, o + e)
    check("file removed", not os.path.exists(fpath) and not os.listdir(tmp + "/run"), fpath)
    p = subprocess.Popen([BIN, "exec", "--file", "F=app/api-key", "--", "sleep", "30"], env=dict(ENV, SEALED_TOKEN=AG), start_new_session=True)
    for _ in range(50):
        if glob.glob(tmp + "/run/*/f0"): break
        time.sleep(0.1)
    check("file exists while running", bool(glob.glob(tmp + "/run/*/f0")))
    p.terminate(); p.wait(10)
    check("file removed on SIGTERM", not os.listdir(tmp + "/run"), os.listdir(tmp + "/run"))
    rc, _, e = run("exec", "--env", "A=app/api-key", "--env", "B=other/db", "--", "true", token=AG); check("exec out of scope", rc == 87 and etype(e) == "forbidden_scope", e)
    rc, _, e = run("exec", "--env", "A=apple/x", "--", "true", token=AG); check("prefix is segment-aware", rc == 87, e)
    rc, _, e = run("exec", "--env", "A=app/missing", "--", "true", token=AG); check("missing", rc == 92, e)

    # ---- render ----
    open(tmp + "/t.tpl", "w").write("key={{sealed:app/api-key}}\n")
    rc, o, e = run("render", tmp + "/t.tpl", "--out", tmp + "/conf", token=AG)
    check("render", rc == 0 and open(tmp + "/conf").read() == f"key={SECRET}\n" and SECRET not in o + e, o + e)
    check("render 0600", stat.S_IMODE(os.stat(tmp + "/conf").st_mode) == 0o600)

    # ---- reveal ----
    rc, o, e = run("get", "app/api-key", "--reveal", token=AG); check("agent never reveals", rc == 83 and SECRET not in o + e, e)
    rc, o, e, _ = human("get", "app/api-key", "--reveal", token=AG); check("agent never reveals even with passphrase", rc == 83 and SECRET not in o + e, e)
    rc, o, e = run("get", "app/api-key", "--reveal"); check("human token without tty", rc == 85 and SECRET not in o + e, e)
    rc, o, e, term = human("get", "app/api-key", "--reveal")
    check("human reveal", rc == 0 and js(o).get("value") == SECRET + "\n" and SECRET not in term, o + e)
    st, body = http("/v1/reveal", {"token": AG, "name": "app/api-key", "passphrase": PASS}); check("raw API: agent+passphrase refused", SECRET not in body, body)

    # ---- approvals ----
    rc, _, e = run("put", "app/api-key", token=AG, stdin="new-value-AAAA1111"); check("overwrite needs approval", rc == 85, e)
    ap = js(e)["error"]["suggestions"][0].split()[-1]
    check("suggestion has command", js(e)["error"]["suggestions"][0] == "ask human to run: sealed approve " + ap, e)
    rc, _, e2 = run("put", "app/api-key", token=AG, stdin="new-value-AAAA1111"); check("same pending id", ap in e2, e2)
    rc, _, e = run("approve", ap); check("agent cannot approve", rc == 85, e)
    st, body = http("/v1/approve", {"id": ap, "token": AG}); check("raw approve w/o passphrase", js(body)["error"]["code"] == 89, body)
    rc, o, e, term = human("approve", ap); check("approve", rc == 0 and js(o)["state"] == "approved" and "app/api-key" in term, e + term)
    rc, _, e = run("put", "app/api-key", token=AG, stdin="DIFFERENT-value-2222"); check("approval bound to value", rc == 85, e)
    rc, o, e = run("put", "app/api-key", token=AG, stdin="new-value-AAAA1111"); check("approved put", rc == 0 and js(o)["version"] == 2, e)
    rc, _, e = run("put", "app/api-key", token=AG, stdin="new-value-AAAA1111"); check("approval single use", rc == 85, e)
    rc, o, e, _ = human("put", "app/api-key", stdin=SECRET); check("human overwrite with passphrase", rc == 0 and js(o)["version"] == 3, e)
    rc, _, e = run("rm", "app/api-key", token=AG); check("rm needs approval", rc == 85, e)
    rc, _, e = run("rm", "other/db", token=AG); check("rm out of scope", rc == 87, e)
    rc, o, e, _ = human("rm", "apple/x"); check("human rm", rc == 0, e)

    # forged approval: flip the row in the DB, the MAC must reject it
    rc, _, e = run("rm", "app/api-key", token=AG); ap = js(e)["error"]["suggestions"][0].split()[-1]
    c = sqlite3.connect(tmp + "/home/sealed.db"); c.execute("UPDATE approvals SET state='approved' WHERE id=?", (ap,)); c.commit()
    rc, _, e = run("rm", "app/api-key", token=AG); check("forged approval refused", rc == 85, e)

    # ---- the approver sees exactly what is granted, and cannot be spoofed ----
    rc, _, e = run("token", "create", "--kind", "human", "--scope", "*", env={"SEALED_HOST_LABEL": "vm\x1b[2J\rAPPROVE ls"})
    ap = js(e)["error"]["suggestions"][0].split()[-1]
    rc, o, e, term = human("approve", ap, answers=("",))
    check("prompt names token kind + scope", "human token, scope *" in term, term)
    check("prompt has no injected escapes", "\x1b" not in term and "\rAPPROVE" not in term, repr(term))
    check("empty passphrase cancels", rc == 85, e)
    old = os.umask(0o022)
    rc, o, _ = run("exec", "--env", "K=app/api-key", "--", "sh", "-c", "umask", token=AG)
    os.umask(old)
    check("child keeps caller umask", o.strip() == "0022", o)

    # ---- KNOWN LIMIT (documented in README): not a sandbox against a hostile caller ----
    rc, o, _ = run("exec", "--env", "K=app/api-key", "--", "sh", "-c", 'printf %s "$K" | rev', token=AG)
    check("known limit: transforming command defeats redaction", SECRET[::-1] in o, o)
    st, body = http("/v1/resolve", {"token": AG, "names": ["app/api-key"], "purpose": "exec"})
    check("known limit: agent token can call resolve directly", SECRET in body, body)

    # ---- token hierarchy ----
    rc, o, e = run("token", "create", "--kind", "human", "--scope", "app/", token=AG); check("agent cannot mint human", rc == 83, e)
    rc, _, e = run("token", "create", "--kind", "agent", "--scope", "*", token=AG); check("no wider scope", rc == 87, e)
    rc, _, e = run("token", "create", "--kind", "agent", "--scope", "apple/", token=AG); check("no sibling scope", rc == 87, e)
    rc, _, e = run("token", "create", "--kind", "agent", "--scope", "app/", "--rate", "501", token=AG); check("no higher rate", rc == 87, e)
    rc, o, e = run("token", "create", "--kind", "agent", "--scope", "app/sub/", "--rate", "3", token=AG); check("narrow child", rc == 0, o + e)
    CH = js(o)["token"]
    codes = [run("ls", token=CH)[0] for _ in range(5)]; check("rate limit", codes == [0, 0, 0, 101, 101], codes)
    c.execute("UPDATE tokens SET scope='*' WHERE id=?", (AGID,)); c.commit()
    rc, _, e = run("ls", token=AG); check("tampered token row refused", rc == 90, e)
    c.execute("UPDATE tokens SET scope='app/' WHERE id=?", (AGID,)); c.commit()
    rc, o, e = run("token", "create", "--kind", "agent", "--scope", "app/", token=AG); GC = js(o)["token"]
    rc, _, e, _ = human("token", "revoke", AGID); check("revoke", rc == 0, e)
    check("revoked", run("ls", token=AG)[0] == 90)
    check("revocation cascades", run("ls", token=GC)[0] == 90)

    # ---- swapping ciphertext between names must fail ----
    c.execute("UPDATE secrets SET (wiv,wdk,iv,ct)=(SELECT wiv,wdk,iv,ct FROM secrets WHERE name='other/db') WHERE name='app/api-key' AND version=3"); c.commit()
    rc, o, e = run("exec", "--env", "K=app/api-key", "--", "sh", "-c", "echo $K"); check("row swap detected", rc == 110 and "other-secret" not in o, o + e)

    # ---- at rest + audit ----
    blob = b"".join(open(f, "rb").read() for f in glob.glob(tmp + "/home/*"))
    check("no plaintext at rest", all(x.encode() not in blob for x in (SECRET, "other-secret-123456", PASS, "new-value-AAAA1111", AG)))
    rc, o, _ = run("audit"); verbs = {(x["verb"], x["result"].split(":")[0]) for x in js(o)["entries"]}
    check("audit", ("exec", "ok") in verbs and ("reveal", "forbidden_kind") in verbs and ("put", "approval_required") in verbs and SECRET not in o, verbs)
    try:
        c.execute("DELETE FROM audit"); ok = False
    except sqlite3.Error: ok = True
    check("audit append-only trigger", ok)
    c.close()

    # ---- HTTP hardening ----
    st, b = http("/_health", method="GET"); check("health", st == 200 and js(b)["service"] == "sealed" and js(b)["pid"] > 0, b)
    st, b = http("/_shutdown", headers={"Origin": "http://evil.example"}); check("browser origin refused", st == 403, b)
    st, b = http("/_health", method="GET", headers={"Host": "evil.example"}); check("foreign host refused", st == 403, b)
    st, b = http("/v1/ls", {}); check("api needs token", js(b)["error"]["code"] == 90, b)
    rc, _, e = run("ls", env={"SEALED_URL": "http://10.0.0.1:1"}); check("refuse remote plaintext", rc == 80, e)

    # ---- feedback scrubbing ----
    rc, o, e = run("feedback", f"exec failed for app/api-key and other/db with {SECRET} here")
    msg = js(o)["data"]["sent_message"]
    check("feedback scrubbed", rc == 0 and "api-key" not in msg and "other" not in msg and SECRET not in msg and "failed" in msg, o + e)

    # ---- no telemetry; update, install ----
    rc, o, e = run("telemetry"); check("no telemetry command", rc == 80 and etype(e) == "invalid_args", e)
    rc, _, e = run("update"); check("update w/o server", rc == 100 and etype(e) == "no_update_server", e)
    pre = tmp + "/bin"
    # update: a real swap from a local release server, then a rejected one
    import hashlib, functools, threading, http.server
    rel = tmp + "/rel"; os.mkdir(rel)
    new = open(BIN, "rb").read() + b"\nnewer-build"
    open(rel + "/sealed.bin", "wb").write(new)
    full = hashlib.sha256(new).hexdigest()
    def publish(sha): json.dump({"ok": True, "version": sha[:12], "download": "/sealed.bin", "sha256": sha}, open(rel + "/version", "w"))
    publish(full)
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=rel))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    os.mkdir(tmp + "/up"); shutil.copy2(BIN, tmp + "/up/sealed")
    def upd(*a):
        return subprocess.run([tmp + "/up/sealed", "update", *a], env=dict(ENV, SEALED_SERVER=f"http://127.0.0.1:{srv.server_port}"), capture_output=True, text=True, timeout=30, start_new_session=True)
    r = upd("--check"); check("update --check", r.returncode == 5, r.stdout + r.stderr)
    publish(full[:12] + "0" * 52)
    r = upd(); check("bad sha rejected", r.returncode == 100 and open(tmp + "/up/sealed", "rb").read() != new, r.stdout + r.stderr)
    publish(full)
    r = upd(); check("update swaps", r.returncode == 0 and open(tmp + "/up/sealed", "rb").read() == new and os.path.exists(tmp + "/up/sealed.bak"), r.stdout + r.stderr)
    check("update stdout is one JSON object", js(r.stdout).get("updated") is True, r.stdout)
    r = upd(); check("up to date", r.returncode == 0 and js(r.stdout).get("updated") is False, r.stdout + r.stderr)
    srv.shutdown()

    rc, o, e = run("install", "--prefix", pre); check("install", rc == 0 and os.access(pre + "/sealed", os.X_OK), o + e)
    rc, o, e = run("install", "--prefix", pre); check("install idempotent", rc == 0, e)
    rc, o, _ = run("uninstall", "--prefix", pre); check("uninstall", js(o)["removed"] is True, o)
    rc, o, _ = run("uninstall", "--prefix", pre); check("uninstall noop", rc == 0 and js(o)["removed"] is False, o)

    # ---- lock, restart, brute-force throttle ----
    rc, o, e = run("lock"); check("lock", rc == 0, e)
    check("locked after lock", run("ls")[0] == 86)
    human("unlock")
    run("daemon", "stop"); run("daemon", "start")
    check("locked after restart", run("ls")[0] == 86)
    human("unlock")
    check("data survives restart", len(js(run("ls")[1])["secrets"]) == 2)
    run("lock", env={})
    time.sleep(max(0, 61 - time.time() % 60) if time.time() % 60 > 50 else 0)
    codes = [human("unlock", answers=("bad bad bad bad",))[0] for _ in range(7)]
    check("passphrase throttle", 101 in codes, codes)

    rc, o, _ = run("daemon", "stop"); check("stop", rc == 0 and js(o)["daemon"] == "stopped", o)
    rc, o, _ = run("daemon", "stop"); check("stop idempotent", js(o)["daemon"] == "already_stopped", o)
finally:
    run("daemon", "stop")
    shutil.rmtree(tmp, ignore_errors=True)

print(f"{count - len(fails)}/{count} passed")
sys.exit(1 if fails else 0)
