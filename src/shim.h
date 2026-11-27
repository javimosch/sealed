/* sealed: the parts MFL has no builtin for.
 * Needs machin 0.146.0+, which copies every string an extern returns. So what
 * is returned here is one reusable buffer, never a heap copy (it would leak),
 * and MFL calls sh_scrub() right after taking a sensitive value. */
#include <string.h>
#include <stdlib.h>
#include <stdio.h>
#include <unistd.h>
#include <fcntl.h>
#include <errno.h>
#include <signal.h>
#include <poll.h>
#include <termios.h>
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <sys/mman.h>
#include <sys/prctl.h>
#include <sys/resource.h>
#include <sys/file.h>

static char* sh_dup(const char* s) { char* d = strdup(s ? s : ""); if (!d) abort(); return d; }  /* C-owned only */

#define SH_OUT 65538
static char sh_out[SH_OUT];
static long long sh_scrub(void) { explicit_bzero(sh_out, sizeof sh_out); return 0; }
static char* sh_give(const char* s) { sh_scrub(); strncpy(sh_out, s, sizeof sh_out - 1); return sh_out; }

/* No core dumps, no ptrace / /proc/<pid>/mem from non-root peers. */
static mode_t sh_umask = 022;
static long long sh_harden(void) {
    struct rlimit r = {0, 0};
    setrlimit(RLIMIT_CORE, &r);
    prctl(PR_SET_DUMPABLE, 0, 0, 0, 0);
    signal(SIGPIPE, SIG_IGN);
    sh_umask = umask(077);
    return 0;
}

static long long sh_pid(void) { return (long long)getpid(); }

/* ---- master key: locked pages, wiped on lock ---- */
static char sh_key[129];
static int sh_key_locked_pages = 0;
static long long sh_key_set(const char* hex) {
    if (!sh_key_locked_pages) { mlock(sh_key, sizeof sh_key); sh_key_locked_pages = 1; }
    if (strlen(hex) >= sizeof sh_key) return -1;
    strcpy(sh_key, hex);
    return 0;
}
static long long sh_key_present(void) { return sh_key[0] != 0; }
/* One reusable buffer, not a heap copy per call (MFL copies it and would never
 * free a malloc'd one). Wiped with the key. */
static char sh_key_out[sizeof sh_key];
static char* sh_key_get(void) { mlock(sh_key_out, sizeof sh_key_out); memcpy(sh_key_out, sh_key, sizeof sh_key); return sh_key_out; }
static long long sh_key_wipe(void) { explicit_bzero(sh_key, sizeof sh_key); explicit_bzero(sh_key_out, sizeof sh_key_out); return 0; }

/* ---- passphrase: only ever from the controlling terminal ---- */
static volatile sig_atomic_t sh_tty_sig = 0;
static void sh_tty_onsig(int s) { sh_tty_sig = s; }

static char* sh_tty_secret(const char* prompt) {
    int fd = open("/dev/tty", O_RDWR | O_CLOEXEC);
    if (fd < 0) return sh_give("");
    struct termios old, neu;
    if (tcgetattr(fd, &old) != 0) { close(fd); return sh_give(""); }
    neu = old; neu.c_lflag &= ~(ECHO);
    struct sigaction sa, oi, ot;
    memset(&sa, 0, sizeof sa); sa.sa_handler = sh_tty_onsig;
    sigaction(SIGINT, &sa, &oi); sigaction(SIGTERM, &sa, &ot);
    tcsetattr(fd, TCSAFLUSH, &neu);
    if (write(fd, prompt, strlen(prompt)) < 0) {}
    char buf[1024]; size_t n = 0; char c;
    while (!sh_tty_sig && read(fd, &c, 1) == 1 && c != '\n' && n < sizeof buf - 1) buf[n++] = c;
    buf[n] = 0;
    tcsetattr(fd, TCSAFLUSH, &old);
    if (write(fd, "\n", 1) < 0) {}
    close(fd);
    sigaction(SIGINT, &oi, 0); sigaction(SIGTERM, &ot, 0);
    if (sh_tty_sig) { explicit_bzero(buf, sizeof buf); raise(sh_tty_sig); return sh_give(""); }
    char* out = sh_give(buf);
    explicit_bzero(buf, sizeof buf);
    return out;
}

static long long sh_has_tty(void) {
    int fd = open("/dev/tty", O_RDWR | O_CLOEXEC);
    if (fd < 0) return 0;
    close(fd); return 1;
}

/* ---- hidden one-line value typed or pasted by a human. Never truncates:
 * status 1 = too long for a terminal line, 2 = more than one line pasted,
 * 3 = no terminal or cancelled. */
static int sh_line_rc = 0;
static char* sh_tty_value(const char* prompt) {
    char buf[8192]; ssize_t n = -1;
    sh_line_rc = 3;
    int fd = open("/dev/tty", O_RDWR | O_CLOEXEC);
    if (fd < 0) return sh_give("");
    struct termios old, neu;
    if (tcgetattr(fd, &old) != 0) { close(fd); return sh_give(""); }
    neu = old; neu.c_lflag &= ~(ECHO);
    struct sigaction sa, oi, ot;
    memset(&sa, 0, sizeof sa); sa.sa_handler = sh_tty_onsig;
    sigaction(SIGINT, &sa, &oi); sigaction(SIGTERM, &sa, &ot);
    tcsetattr(fd, TCSAFLUSH, &neu);
    if (write(fd, prompt, strlen(prompt)) < 0) {}
    if (!sh_tty_sig) n = read(fd, buf, sizeof buf - 1);   /* one canonical line */
    struct pollfd more = {fd, POLLIN, 0};
    int extra = poll(&more, 1, 150) > 0;
    tcsetattr(fd, TCSAFLUSH, &old);                       /* drops any leftover paste */
    if (write(fd, "\n", 1) < 0) {}
    close(fd);
    sigaction(SIGINT, &oi, 0); sigaction(SIGTERM, &ot, 0);
    if (sh_tty_sig) { explicit_bzero(buf, sizeof buf); raise(sh_tty_sig); return sh_give(""); }
    if (n <= 0) return sh_give("");
    if (n >= 4095) sh_line_rc = 1;                        /* the kernel caps a line here */
    else if (extra) sh_line_rc = 2;
    else sh_line_rc = 0;
    if (buf[n - 1] == '\n') n--;
    buf[n] = 0;
    char* out = sh_give(sh_line_rc ? "" : buf);
    explicit_bzero(buf, sizeof buf);
    return out;
}
static long long sh_tty_value_status(void) { return sh_line_rc; }
static long long sh_stdin_is_tty(void) { return isatty(0); }

/* ---- desktop window: the sealed-dialog program (src/dialog.src), no shell.
 * status: 0 entered, 1 cancelled or timed out, 3 could not run, 4 too long or
 * multi-line. It is looked for next to this binary, never on PATH.
 * SEALED_DIALOG replaces it (used by the tests); like the environment in
 * general, that is controlled by whoever runs sealed. */
static int sh_sibling(char* out, size_t cap) {
    ssize_t n = readlink("/proc/self/exe", out, cap - 1);
    if (n < 0) return -1;
    out[n] = 0;
    char* slash = strrchr(out, '/');
    if (!slash || (size_t)(slash - out) + sizeof "/sealed-dialog" > cap) return -1;
    strcpy(slash, "/sealed-dialog");
    return 0;
}
static int sh_dialog_rc = 3;
static char* sh_dialog(const char* title, const char* heading, const char* body, long long confirm) {
    static char buf[65536 + 2];
    sh_dialog_rc = 3;
    const char* other = getenv("SEALED_DIALOG");
    char beside[4096];
    char* av[8]; int n = 0;
    if (other && *other) av[n++] = (char*)other;
    else { if (sh_sibling(beside, sizeof beside)) return sh_give(""); av[n++] = beside; }
    av[n++] = (char*)title; av[n++] = (char*)heading; av[n++] = (char*)body;
    av[n++] = confirm ? "1" : "0";
    av[n] = 0;
    int p[2];
    if (pipe(p)) return sh_give("");
    pid_t pid = fork();
    if (pid < 0) { close(p[0]); close(p[1]); return sh_give(""); }
    if (pid == 0) {
        int nul = open("/dev/null", O_RDWR);
        dup2(nul, 0); dup2(p[1], 1); dup2(nul, 2);
        close(p[0]); close(p[1]);
        unsetenv("SEALED_TOKEN"); unsetenv("SEALED_TOKEN_FILE");
        execv(av[0], av);
        _exit(127);
    }
    close(p[1]);
    size_t len = 0; ssize_t r; int too_long = 0; char sink[4096];
    for (;;) {
        if (len < sizeof buf - 1) r = read(p[0], buf + len, sizeof buf - 1 - len);
        else { r = read(p[0], sink, sizeof sink); if (r > 0) too_long = 1; }
        if (r < 0 && errno == EINTR) continue;
        if (r <= 0) break;
        if (!too_long) len += (size_t)r;
    }
    close(p[0]);
    int st = 0;
    while (waitpid(pid, &st, 0) < 0 && errno == EINTR) {}
    buf[len] = 0;
    int code = WIFEXITED(st) ? WEXITSTATUS(st) : -1;
    if (code == 0) sh_dialog_rc = (too_long || len > 65535 || memchr(buf, '\n', len) || memchr(buf, 0, len)) ? 4 : 0;
    else if (code == 1) sh_dialog_rc = 1;
    else if (code == 4) sh_dialog_rc = 4;
    char* out = sh_give(sh_dialog_rc == 0 ? buf : "");
    explicit_bzero(buf, sizeof buf);
    return out;
}
static long long sh_dialog_status(void) { return sh_dialog_rc; }

/* held until this process exits */
static long long sh_lock(const char* path) {
    int fd = open(path, O_RDWR | O_CREAT | O_CLOEXEC | O_NOFOLLOW, 0600);
    if (fd < 0) return -1;
    if (flock(fd, LOCK_EX | LOCK_NB)) { close(fd); return -1; }
    return 0;
}

/* ---- secret value from stdin: rejects NUL (it would silently truncate) ---- */
#define SH_MAX_VALUE 65536
static int sh_stdin_rc = 0;
static char* sh_stdin_value(void) {
    static char buf[SH_MAX_VALUE + 2];
    size_t n = 0; ssize_t r;
    sh_stdin_rc = 0;
    if (isatty(0)) { sh_stdin_rc = 3; return sh_give(""); }
    while (n <= SH_MAX_VALUE && (r = read(0, buf + n, SH_MAX_VALUE + 1 - n)) > 0) n += (size_t)r;
    if (n > SH_MAX_VALUE) sh_stdin_rc = 2;
    else if (memchr(buf, 0, n)) sh_stdin_rc = 1;
    if (sh_stdin_rc) { explicit_bzero(buf, sizeof buf); return sh_give(""); }
    buf[n] = 0;
    char* out = sh_give(buf);
    explicit_bzero(buf, sizeof buf);
    return out;
}
static long long sh_stdin_status(void) { return sh_stdin_rc; }

/* ---- 0600 files, written atomically, never through a symlink ---- */
static int sh_write_all(int fd, const char* p, size_t n) {
    while (n) { ssize_t w = write(fd, p, n); if (w < 0) { if (errno == EINTR) continue; return -1; } p += w; n -= (size_t)w; }
    return 0;
}
static long long sh_write_private(const char* path, const char* data) {
    char tmp[4096];
    if ((size_t)snprintf(tmp, sizeof tmp, "%s.XXXXXX", path) >= sizeof tmp) return -1;
    int fd = mkstemp(tmp);
    if (fd < 0) return -1;
    if (fchmod(fd, 0600) || sh_write_all(fd, data, strlen(data)) || fsync(fd)) { close(fd); unlink(tmp); return -1; }
    close(fd);
    if (rename(tmp, path)) { unlink(tmp); return -1; }
    return 0;
}

/* a directory we own, mode 0700 (created if missing) */
static long long sh_private_dir(const char* path) {
    struct stat st;
    if (mkdir(path, 0700) && errno != EEXIST) return -1;
    if (lstat(path, &st) || !S_ISDIR(st.st_mode) || st.st_uid != getuid()) return -1;
    return chmod(path, 0700) ? -1 : 0;
}
static long long sh_chmod(const char* path, long long mode) { return chmod(path, (mode_t)mode); }

/* ---- copy a program (install), atomically ---- */
static long long sh_copy_program(const char* src, const char* dst) {
    char tmp[4096];
    if ((size_t)snprintf(tmp, sizeof tmp, "%s.XXXXXX", dst) >= sizeof tmp) return -1;
    int in = open(src, O_RDONLY | O_CLOEXEC);
    if (in < 0) return -1;
    int out = mkstemp(tmp);
    if (out < 0) { close(in); return -1; }
    char b[65536]; ssize_t r; int bad = 0;
    while ((r = read(in, b, sizeof b)) > 0) if (sh_write_all(out, b, (size_t)r)) { bad = 1; break; }
    if (r < 0 || fchmod(out, 0755)) bad = 1;
    close(in); close(out);
    if (bad || rename(tmp, dst)) { unlink(tmp); return -1; }
    return 0;
}
static char* sh_self_path(void) {
    char b[4096]; ssize_t n = readlink("/proc/self/exe", b, sizeof b - 1);
    if (n < 0) return sh_give("");
    b[n] = 0; return sh_give(b);
}

/* ---- argv builder: no shell is ever involved ---- */
#define SH_MAX 256
static char* sh_argv[SH_MAX + 1]; static int sh_argc = 0;
static char* sh_envk[SH_MAX]; static char* sh_envv[SH_MAX]; static int sh_envf[SH_MAX]; static int sh_envn = 0;
static char* sh_needle[SH_MAX * 4]; static size_t sh_nlen[SH_MAX * 4]; static int sh_nn = 0;
static int sh_overflow = 0;
static char sh_paths[SH_MAX][512]; static int sh_npaths = 0; static char sh_tmpdir[400];

static long long sh_arg(const char* a) { if (sh_argc >= SH_MAX) { sh_overflow = 1; return -1; } sh_argv[sh_argc++] = sh_dup(a); sh_argv[sh_argc] = 0; return 0; }
static long long sh_env(const char* k, const char* v, long long as_file) {
    if (sh_envn >= SH_MAX) { sh_overflow = 1; return -1; }
    sh_envk[sh_envn] = sh_dup(k); sh_envv[sh_envn] = sh_dup(v); sh_envf[sh_envn] = (int)as_file; sh_envn++; return 0;
}
static long long sh_redact(const char* s) {
    if (strlen(s) < 4) return 0;
    if (sh_nn >= SH_MAX * 4) { sh_overflow = 1; return -1; }
    sh_needle[sh_nn] = sh_dup(s); sh_nlen[sh_nn] = strlen(s); sh_nn++; return 0;
}

/* start `serve` detached, stdio to a log file */
static long long sh_spawn_detached(const char* log) {
    pid_t p = fork();
    if (p < 0) return -1;
    if (p == 0) {
        setsid();
        int nul = open("/dev/null", O_RDONLY);
        int out = log[0] ? open(log, O_WRONLY | O_CREAT | O_APPEND, 0600) : open("/dev/null", O_WRONLY);
        if (nul < 0 || out < 0) _exit(127);
        dup2(nul, 0); dup2(out, 1); dup2(out, 2);
        for (int f = 3; f < 1024; f++) close(f);
        execvp(sh_argv[0], sh_argv);
        _exit(127);
    }
    sh_argc = 0; sh_argv[0] = 0;
    return (long long)p;
}

/* ---- streaming redaction ----
 * Emit everything that cannot be the start of a secret. Only a tail that is a
 * proper prefix of some needle is held back, so ordinary output has no delay. */
typedef struct { char* b; size_t n, cap; int out; } sh_stream;

static void sh_pump(sh_stream* s, int final) {
    size_t i = 0, start = 0;
    while (i < s->n) {
        size_t best = 0; int partial = 0; size_t rest = s->n - i;
        for (int k = 0; k < sh_nn; k++) {
            size_t L = sh_nlen[k];
            if (L <= rest) { if (L > best && !memcmp(s->b + i, sh_needle[k], L)) best = L; }
            else if (!final && !memcmp(s->b + i, sh_needle[k], rest)) partial = 1;
        }
        if (partial) break;
        if (best) {
            sh_write_all(s->out, s->b + start, i - start);
            sh_write_all(s->out, "***", 3);
            i += best; start = i;
        } else i++;
    }
    sh_write_all(s->out, s->b + start, i - start);
    memmove(s->b, s->b + i, s->n - i);
    s->n -= i;
}

static volatile sig_atomic_t sh_child = 0;
static void sh_forward(int s) { if (sh_child > 0) kill(-sh_child, s); }

static void sh_cleanup_files(void) {
    for (int i = 0; i < sh_npaths; i++) unlink(sh_paths[i]);
    if (sh_tmpdir[0]) rmdir(sh_tmpdir);
    sh_npaths = 0; sh_tmpdir[0] = 0;
}

static long long sh_run_once(void) {
    if (sh_overflow || sh_argc == 0) return -1;
    int need_dir = 0;
    for (int i = 0; i < sh_envn; i++) if (sh_envf[i]) need_dir = 1;

    int sigs[4] = {SIGINT, SIGTERM, SIGHUP, SIGQUIT};
    sigset_t block, prev; sigemptyset(&block);
    for (int i = 0; i < 4; i++) sigaddset(&block, sigs[i]);
    sigprocmask(SIG_BLOCK, &block, &prev);      /* nothing may interrupt before we can clean up */

    if (need_dir) {
        const char* base = getenv("XDG_RUNTIME_DIR");
        if (!base || !*base) base = "/dev/shm";
        if ((size_t)snprintf(sh_tmpdir, sizeof sh_tmpdir, "%s/sealed.XXXXXX", base) >= sizeof sh_tmpdir || !mkdtemp(sh_tmpdir)) {
            sh_tmpdir[0] = 0; sigprocmask(SIG_SETMASK, &prev, 0); return -1;
        }
    }
    int po[2], pe[2];
    if (pipe(po) || pipe(pe)) { sh_cleanup_files(); return -1; }
    for (int i = 0; i < sh_envn; i++) {
        if (!sh_envf[i]) continue;
        snprintf(sh_paths[sh_npaths], 512, "%s/f%d", sh_tmpdir, sh_npaths);
        int fd = open(sh_paths[sh_npaths], O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
        if (fd < 0 || sh_write_all(fd, sh_envv[i], strlen(sh_envv[i]))) { if (fd >= 0) close(fd); sh_npaths++; sh_cleanup_files(); return -1; }
        close(fd);
        free(sh_envv[i]); sh_envv[i] = sh_dup(sh_paths[sh_npaths]);
        sh_npaths++;
    }
    pid_t pid = fork();
    if (pid < 0) { sh_cleanup_files(); return -1; }
    if (pid == 0) {
        setsid();                               /* no /dev/tty: output cannot skip the redactor */
        dup2(po[1], 1); dup2(pe[1], 2);
        close(po[0]); close(po[1]); close(pe[0]); close(pe[1]);
        unsetenv("SEALED_TOKEN"); unsetenv("SEALED_TOKEN_FILE");
        for (int i = 0; i < sh_envn; i++) setenv(sh_envk[i], sh_envv[i], 1);
        signal(SIGPIPE, SIG_DFL);
        umask(sh_umask);               /* the command keeps the caller's umask */
        sigprocmask(SIG_SETMASK, &prev, 0);
        execvp(sh_argv[0], sh_argv);
        fprintf(stderr, "sealed: cannot run %s: %s\n", sh_argv[0], strerror(errno));
        _exit(127);
    }
    sh_child = pid;
    for (int i = 0; i < 4; i++) { struct sigaction sa; memset(&sa, 0, sizeof sa); sa.sa_handler = sh_forward; sigaction(sigs[i], &sa, 0); }
    sigprocmask(SIG_SETMASK, &prev, 0);
    close(po[1]); close(pe[1]);

    size_t maxn = 0;
    for (int k = 0; k < sh_nn; k++) if (sh_nlen[k] > maxn) maxn = sh_nlen[k];
    sh_stream st[2];
    for (int i = 0; i < 2; i++) { st[i].cap = maxn + 65536; st[i].b = malloc(st[i].cap); st[i].n = 0; st[i].out = i + 1; if (!st[i].b) abort(); }
    struct pollfd pf[2] = {{po[0], POLLIN, 0}, {pe[0], POLLIN, 0}};
    int open_n = 2, status = 0, exited = 0, idle = 0;
    while (open_n > 0) {
        int pr = poll(pf, 2, 100);
        if (pr < 0 && errno != EINTR) break;
        if (!exited && waitpid(pid, &status, WNOHANG) == pid) exited = 1;
        if (pr <= 0) {
            /* a grandchild may keep the pipes open forever; stop 2s after the child exits */
            if (exited && ++idle > 20) break;
            continue;
        }
        idle = 0;
        for (int i = 0; i < 2; i++) {
            if (pf[i].fd < 0 || !(pf[i].revents & (POLLIN | POLLHUP | POLLERR))) continue;
            ssize_t r = read(pf[i].fd, st[i].b + st[i].n, st[i].cap - st[i].n);
            if (r > 0) { st[i].n += (size_t)r; sh_pump(&st[i], 0); }
            else if (r == 0 || errno != EINTR) { close(pf[i].fd); pf[i].fd = -1; open_n--; }
        }
    }
    for (int i = 0; i < 2; i++) { sh_pump(&st[i], 1); explicit_bzero(st[i].b, st[i].cap); free(st[i].b); }
    if (!exited) while (waitpid(pid, &status, 0) < 0 && errno == EINTR) {}
    if (need_dir) kill(-pid, SIGKILL);          /* leftovers must not outlive the secret files */
    sh_cleanup_files();
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return 128 + (WIFSIGNALED(status) ? WTERMSIG(status) : 0);
}
static long long sh_exists(const char* p) { return access(p, F_OK) == 0; }

/* returns the child's exit code (128+signal), or -1 if sealed itself failed.
 * The builder is cleared so the next command starts empty. */
static long long sh_run(void) {
    long long rc = sh_run_once();
    for (int i = 0; i < sh_argc; i++) free(sh_argv[i]);
    for (int i = 0; i < sh_envn; i++) { free(sh_envk[i]); explicit_bzero(sh_envv[i], strlen(sh_envv[i])); free(sh_envv[i]); }
    for (int i = 0; i < sh_nn; i++) { explicit_bzero(sh_needle[i], sh_nlen[i]); free(sh_needle[i]); }
    sh_argc = 0; sh_argv[0] = 0; sh_envn = 0; sh_nn = 0; sh_overflow = 0; sh_child = 0;
    return rc;
}
