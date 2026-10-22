/* mdev event receiver and per-endpoint child supervisor. No discovery loop. */
#define _GNU_SOURCE
#include <ctype.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <poll.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/prctl.h>
#include <sys/signalfd.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <sys/wait.h>
#include <syslog.h>
#include <time.h>
#include <unistd.h>

#ifndef SYSROOT
#define SYSROOT "/sys"
#endif
#ifndef DEVROOT
#define DEVROOT "/dev"
#endif
#ifndef RUNDIR
#define RUNDIR "/run/polis-graphics-usb"
#endif
#ifndef APP
#define APP "/usr/bin/hdmi-graphics-hello"
#endif
#ifndef MDEV
#define MDEV "/sbin/mdev"
#endif
#define SOCKET RUNDIR "/events"
#define MAX_WORKERS 128

struct event { char action[8], name[32], path[PATH_MAX]; };
struct worker {
    char name[32], path[PATH_MAX];
    ino_t inode;
    pid_t pid;
    int logfd;
    bool attached, stopping;
    long long deadline;
};
static struct worker workers[MAX_WORKERS];
static sigset_t signals;

static long long now_ms(void)
{
    struct timespec t;
    clock_gettime(CLOCK_MONOTONIC, &t);
    return (long long)t.tv_sec * 1000 + t.tv_nsec / 1000000;
}

static bool valid_name(const char *name)
{
    if (strncmp(name, "ttyACM", 6) || !name[6]) return false;
    for (const char *p = name + 6; *p; p++)
        if (!isdigit((unsigned char)*p)) return false;
    return true;
}

static bool attribute(const char *dir, const char *key, const char *expected)
{
    char path[PATH_MAX], value[256];
    if (snprintf(path, sizeof(path), "%s/%s", dir, key) >= (int)sizeof(path)) return false;
    FILE *f = fopen(path, "r");
    if (!f) return false;
    bool ok = fgets(value, sizeof(value), f) != NULL;
    fclose(f);
    if (!ok) return false;
    value[strcspn(value, "\r\n")] = 0;
    return !strcmp(value, expected);
}

/* Resolve only the endpoint supplied by mdev, never enumerate candidates. */
static bool identify(const char *name, char *event_path, ino_t *inode)
{
    char path[PATH_MAX], resolved[PATH_MAX];
    struct stat st;
    snprintf(path, sizeof(path), DEVROOT "/%s", name);
    if (stat(path, &st) || !S_ISCHR(st.st_mode)) return false;
    snprintf(path, sizeof(path), SYSROOT "/class/tty/%s", name);
    if (!realpath(path, resolved) || stat(resolved, &st)) return false;
    if (strncmp(resolved, SYSROOT "/", strlen(SYSROOT) + 1)) return false;
    strcpy(event_path, resolved + strlen(SYSROOT));
    *inode = st.st_ino;
    snprintf(path, sizeof(path), SYSROOT "/class/tty/%s/device", name);
    if (!realpath(path, resolved)) return false;
    bool control = false;
    while (strlen(resolved) > strlen(SYSROOT)) {
        if (attribute(resolved, "bInterfaceNumber", "00")) control = true;
        if (attribute(resolved, "idVendor", "2e8a"))
            return control && attribute(resolved, "idProduct", "0009") &&
                attribute(resolved, "manufacturer", "Polis") &&
                attribute(resolved, "product", "Polis Screen Graphics");
        char *slash = strrchr(resolved, '/');
        if (!slash) break;
        *slash = 0;
    }
    return false;
}

static void stop_worker(struct worker *w)
{
    if (w->pid && !w->stopping) {
        kill(-w->pid, SIGTERM);
        w->stopping = true;
        w->deadline = now_ms() + 2000;
    }
}

static void launch(struct worker *w)
{
    char path[PATH_MAX];
    ino_t inode;
    if (!identify(w->name, path, &inode) || strcmp(path, w->path) || inode != w->inode) {
        w->attached = false;
        return;
    }
    int pipes[2];
    if (pipe2(pipes, O_CLOEXEC)) goto failed;
    pid_t pid = fork();
    if (pid < 0) { close(pipes[0]); close(pipes[1]); goto failed; }
    if (!pid) {
        pid_t parent = getppid();
        prctl(PR_SET_PDEATHSIG, SIGKILL);
        if (getppid() != parent || parent == 1) _exit(1);
        setpgid(0, 0);
        sigprocmask(SIG_UNBLOCK, &signals, NULL);
        int nullfd = open("/dev/null", O_RDONLY);
        if (nullfd >= 0) { dup2(nullfd, 0); close(nullfd); }
        dup2(pipes[1], 1); dup2(pipes[1], 2);
        close(pipes[0]); close(pipes[1]);
        snprintf(path, sizeof(path), DEVROOT "/%.31s", w->name);
        execl(APP, APP, "--port", path, (char *)NULL);
        perror(APP);
        _exit(127);
    }
    setpgid(pid, pid);
    close(pipes[1]);
    fcntl(pipes[0], F_SETFL, O_NONBLOCK);
    w->pid = pid; w->logfd = pipes[0]; w->stopping = false;
    syslog(LOG_INFO, "%s started pid=%ld", w->name, (long)pid);
    return;
failed:
    syslog(LOG_ERR, "%s launch: %s", w->name, strerror(errno));
    w->deadline = now_ms() + 1000;
}

static void handle_event(const struct event *e)
{
    struct worker *w = NULL, *empty = NULL;
    for (int i = 0; i < MAX_WORKERS; i++) {
        if (!strcmp(workers[i].name, e->name)) w = &workers[i];
        if (!workers[i].attached && !workers[i].pid) empty = &workers[i];
    }
    if (!strcmp(e->action, "remove")) {
        if (w && !strcmp(w->path, e->path)) {
            w->attached = false;
            stop_worker(w);
        }
        return;
    }
    if (strcmp(e->action, "add")) return;
    char path[PATH_MAX];
    ino_t inode;
    if (!identify(e->name, path, &inode)) return;
    /* Reject an old queued add for a tty now at another topology path. */
    if (strcmp(path, e->path)) return;
    if (!w) w = empty;
    if (!w) { syslog(LOG_ERR, "worker capacity reached for %s", e->name); return; }
    if (w->attached && w->inode == inode && !strcmp(w->path, path)) return;
    stop_worker(w);  /* New attachment reusing a tty: reap old child first. */
    strcpy(w->name, e->name); strcpy(w->path, path); w->inode = inode;
    w->attached = true;
    if (!w->pid) w->deadline = now_ms();
}

static int send_event(void)
{
    struct event e = {0};
    const char *action = getenv("ACTION"), *name = getenv("MDEV"), *path = getenv("DEVPATH");
    char cold_path[PATH_MAX], resolved[PATH_MAX];
    if (!action || !name || !valid_name(name)) return 1;
    /* BusyBox initial_scan sets ACTION/MDEV, but does not set DEVPATH. */
    if (!path && !strcmp(action, "add")) {
        snprintf(cold_path, sizeof(cold_path), SYSROOT "/class/tty/%s", name);
        if (!realpath(cold_path, resolved) ||
            strncmp(resolved, SYSROOT "/", strlen(SYSROOT) + 1)) return 1;
        path = resolved + strlen(SYSROOT);
    }
    if (!path ||
        strlen(action) >= sizeof(e.action) || strlen(name) >= sizeof(e.name) ||
        strlen(path) >= sizeof(e.path)) return 1;
    strcpy(e.action, action); strcpy(e.name, name); strcpy(e.path, path);
    int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC, 0);
    struct sockaddr_un address = { .sun_family = AF_UNIX, .sun_path = SOCKET };
    if (fd < 0) return 1;
    struct timeval timeout = { .tv_sec = 1 };
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    bool ok = sendto(fd, &e, sizeof(e), 0, (void *)&address, sizeof(address)) == sizeof(e);
    if (!ok) syslog(LOG_ERR, "mdev event %s %s: %s", action, name, strerror(errno));
    close(fd);
    return ok ? 0 : 1;
}

int main(int argc, char **argv)
{
    openlog("polis-graphics-usb", LOG_PID | LOG_PERROR, LOG_DAEMON);
    if (argc == 2 && !strcmp(argv[1], "event")) return send_event();
    if (argc != 1) return 2;
    umask(0077);
    if (mkdir(RUNDIR, 0700) && errno != EEXIST) { perror(RUNDIR); return 1; }
    /* Persistent inode: never unlink a flock file while another starter waits. */
    int lock = open(RUNDIR "/lock", O_CREAT | O_RDWR | O_CLOEXEC, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) return 1;
    sigemptyset(&signals);
    sigaddset(&signals, SIGCHLD); sigaddset(&signals, SIGTERM); sigaddset(&signals, SIGINT);
    sigprocmask(SIG_BLOCK, &signals, NULL);
    int signal_fd = signalfd(-1, &signals, SFD_CLOEXEC | SFD_NONBLOCK);
    int fd = socket(AF_UNIX, SOCK_DGRAM | SOCK_CLOEXEC | SOCK_NONBLOCK, 0);
    if (fd < 0 || signal_fd < 0) { perror("event sockets"); return 1; }
    struct sockaddr_un address = { .sun_family = AF_UNIX, .sun_path = SOCKET };
    unlink(SOCKET);
    if (bind(fd, (void *)&address, sizeof(address))) { perror(SOCKET); return 1; }
    /* mdev owns coldplug, including service restarts; it runs outside the
     * receiver loop so a full socket cannot deadlock the supervisor. */
    pid_t scan = fork();
    if (!scan) {
        pid_t parent = getppid();
        prctl(PR_SET_PDEATHSIG, SIGKILL);
        if (getppid() != parent || parent == 1) _exit(1);
        sigprocmask(SIG_UNBLOCK, &signals, NULL);
        execl(MDEV, MDEV, "-s", (char *)NULL);
        _exit(127);
    }
    bool quitting = false;
    for (;;) {
        struct pollfd polls[MAX_WORKERS + 2] = {{fd, POLLIN, 0}, {signal_fd, POLLIN, 0}};
        int timeout = -1, alive = 0;
        long long now = now_ms();
        for (int i = 0; i < MAX_WORKERS; i++) {
            struct worker *w = &workers[i];
            if (w->attached && !w->pid && now >= w->deadline) launch(w);
            if (w->stopping && now >= w->deadline) {
                kill(-w->pid, SIGKILL);
                w->deadline = now + 2000;
            }
            if ((w->attached && !w->pid) || w->stopping) {
                int delay = (int)(w->deadline - now);
                if (delay < 0) delay = 0;
                if (timeout < 0 || delay < timeout) timeout = delay;
            }
            polls[i + 2] = (struct pollfd){ w->pid ? w->logfd : -1, POLLIN, 0 };
            alive += w->pid != 0;
        }
        if (quitting && !alive) break;
        if (poll(polls, MAX_WORKERS + 2, timeout) < 0 && errno != EINTR) break;
        /* Drain output before reaping so the final success/error line survives. */
        for (int i = 0; i < MAX_WORKERS; i++) {
            if (!polls[i + 2].revents) continue;
            char buffer[1024];
            ssize_t n;
            /* Bound each pass so a noisy worker cannot starve detach events. */
            for (int j = 0; j < 8 && (n = read(workers[i].logfd, buffer, sizeof(buffer) - 1)) > 0; j++) {
                buffer[n] = 0;
                syslog(LOG_INFO, "%s: %s", workers[i].name, buffer);
            }
            if (n == 0) { close(workers[i].logfd); workers[i].logfd = -1; }
        }
        struct signalfd_siginfo info;
        while (read(signal_fd, &info, sizeof(info)) == sizeof(info)) {
            if (info.ssi_signo == SIGCHLD) continue;
            quitting = true;
            for (int i = 0; i < MAX_WORKERS; i++) {
                workers[i].attached = false;
                stop_worker(&workers[i]);
            }
        }
        int status;
        pid_t pid;
        while ((pid = waitpid(-1, &status, WNOHANG)) > 0) {
            if (pid == scan) {
                if (status) syslog(LOG_ERR, "mdev coldplug failed status=%d", status);
                scan = 0;
            }
            for (int i = 0; i < MAX_WORKERS; i++) {
                struct worker *w = &workers[i];
                if (w->pid != pid) continue;
                syslog(LOG_INFO, "%s exited status=%d", w->name, status);
                kill(-pid, SIGKILL); /* Do not retain descendant pipe holders. */
                close(w->logfd); w->pid = 0; w->stopping = false;
                w->deadline = now_ms() + 1000;
            }
        }
        /* Process a bounded batch, preserving mdev's add/remove ordering. */
        struct event event;
        for (int i = 0; !quitting && i < 128; i++) {
            ssize_t n = recv(fd, &event, sizeof(event), 0);
            if (n < 0) break;
            if (n != sizeof(event) || !memchr(event.action, 0, sizeof(event.action)) ||
                !memchr(event.name, 0, sizeof(event.name)) || !memchr(event.path, 0, sizeof(event.path)) ||
                !valid_name(event.name)) continue;
            handle_event(&event);
        }
    }
    for (int i = 0; i < MAX_WORKERS; i++) {
        if (!workers[i].pid) continue;
        kill(-workers[i].pid, SIGKILL);
        waitpid(workers[i].pid, NULL, 0);
    }
    if (scan > 0) { kill(scan, SIGTERM); waitpid(scan, NULL, 0); }
    unlink(SOCKET);
    close(fd); close(signal_fd); close(lock);
    return 0;
}
