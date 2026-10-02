"""Drive `lumen setup` in a fake, empty HOME through a real terminal, answering each menu.

    python3 setup/test_setup_pty.py /tmp/fakehome   (videos on this computer, AI off, no bubble)
"""
import os, pty, re, select, sys, time

FAKE = sys.argv[1]
env = dict(os.environ, HOME=FAKE, XDG_DATA_HOME=f"{FAKE}/.local/share", XDG_CONFIG_HOME=f"{FAKE}/.config",
           TERM="xterm-256color", COLUMNS="120", LINES="40")
env["PATH"] = f"{FAKE}/.local/bin:" + env["PATH"]
DOWN, ENTER = ["\x1b[B"], ["\r"]
# (text that shows the prompt, keys to send)
STEPS = [
    ("Where should your videos go", DOWN + ENTER),        # Just this computer
    ("Where should the speech AI run", DOWN + DOWN + ENTER),   # Off (no Cloudflare option offered)
    ("older copy of the AI tools", ["n"]),                        # keep the real environment on this machine
    ("Your name on share pages", ENTER),                   # keep the default
    ("Turn the face bubble on", ["n"]),                    # no
]
pid, fd = pty.fork()
if pid != 0:
    import fcntl, struct, termios
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))   # a real terminal has a size
if pid == 0:
    time.sleep(0.3)
    os.execvpe(os.path.join(os.path.dirname(os.path.abspath(__file__)), "setup.sh"), ["setup.sh"], env)
out, step, t0 = "", 0, time.time()
while time.time() - t0 < 240:
    r, _, _ = select.select([fd], [], [], 0.5)
    if r:
        try:
            chunk = os.read(fd, 65536).decode(errors="replace")
        except OSError:
            break
        out += chunk
    plain = re.sub(r"\x1b\[[0-9;?<>=]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]", "", out)
    open(FAKE + ".screen.txt", "w").write(plain)
    if step < len(STEPS) and STEPS[step][0] in plain:
        time.sleep(1.0)
        for k in STEPS[step][1]:
            os.write(fd, k.encode()); time.sleep(0.25)
        print(f"answered: {STEPS[step][0]}", flush=True)
        step += 1
    if "All set" in plain:
        break
os.waitpid(pid, 0)
plain = re.sub(r"\x1b\[[0-9;?<>=]*[A-Za-z]|\x1b[()][A-Z0-9]|\x1b[=>]", "", out)
lines = [l.strip() for l in plain.replace("\r", "\n").split("\n") if re.search(r"[✓✗!✦]|All set", l)]
seen = []
for l in lines:
    if l not in seen:
        seen.append(l)
print("\n".join(seen[-30:]))
