#!/usr/bin/env bash
# Sandbox preflight probe (Layer A). Must run INSIDE Claude Code's Bash
# sandbox: the preflight hooks only accept it as the exact command
#     bash <repo>/scripts/sandbox_probe.sh   (absolute path)
# Each check prints "PROBE <name> PASS|FAIL". The last line is
# "PROBE RESULT PASS" only if every check passed. Nothing here writes files.

set -u
# Run from the project root whatever directory Bash is in, so the relative
# paths below always mean the project's files.
cd "$(dirname "$0")/.." || { echo "PROBE RESULT FAIL (cannot find project root)"; exit 1; }
fails=0

# macOS has no `timeout` (Homebrew's coreutils installs it as `gtimeout`).
# Without either, run checks unguarded: the network ones carry curl's own
# --max-time, and the rest return at once.
if command -v timeout >/dev/null 2>&1; then limit() { timeout 15 "$@"; }
elif command -v gtimeout >/dev/null 2>&1; then limit() { gtimeout 15 "$@"; }
else limit() { "$@"; }
fi

check() {  # check <name> <expect: ok|blocked> <command...>
  local name="$1" expect="$2"; shift 2
  if limit "$@" >/dev/null 2>&1; then got=ok; else got=blocked; fi
  if [[ "$got" == "$expect" ]]; then
    echo "PROBE $name PASS"
  else
    echo "PROBE $name FAIL (expected $expect, got $got)"
    fails=$((fails + 1))
  fi
}

# Network: the proxy lets allowed hosts through and stops everything else.
check allowed_host_reachable ok      curl -fsS -o /dev/null --max-time 10 https://example.com
check denied_host_blocked    blocked curl -sS -o /dev/null --max-time 10 https://example.org
check direct_egress_blocked  blocked curl -sS -k -o /dev/null --max-time 10 --noproxy '*' https://1.1.1.1
# Local services (e.g. Ollama) are reachable only through the frozen,
# excluded corpus_search.py, never from sandboxed commands, even via the proxy.
# Counts as "ok" only if Ollama itself answered (its reply lists "models");
# a proxy refusal page doesn't count as reaching it.
check localhost_blocked      blocked sh -c 'NO_PROXY= no_proxy= curl -sS --max-time 5 http://localhost:11434/api/tags | grep -q "\"models\""'

# Unix sockets: Docker's socket would be a full escape to the host.
check docker_socket_blocked  blocked curl -sS -o /dev/null --max-time 5 --unix-socket /var/run/docker.sock http://localhost/_ping

# Windows interop: launching Windows programs from WSL goes over a socket.
# (Off WSL there's no cmd.exe or /mnt/c, so these checks pass trivially.)
check windows_interop_blocked blocked cmd.exe /c exit 0

# Filesystem. Real write attempts that change nothing: ": >> file" opens the
# file for appending and writes zero bytes. The new-file test cleans up.
check frozen_script_present   ok      test -f scripts/run_analysis.py
check frozen_script_readonly  blocked sh -c 'test -f scripts/run_analysis.py && : >> scripts/run_analysis.py'
check home_bashrc_readonly    blocked sh -c 'test -f "$HOME/.bashrc" && : >> "$HOME/.bashrc"'
probe_file="$HOME/.sandbox_probe_$$"
check home_new_file_blocked   blocked sh -c ': > "$1"' _ "$probe_file"
rm -f "$probe_file" 2>/dev/null
# The eval set must stay invisible (it measures nothing once agents can see it).
check evals_hidden            blocked sh -c 'find "$HOME/ai-welfare-evals" -type f 2>/dev/null | grep -q .'
# ... and so must the harness beside this workspace in the repository, if it's there.
if [[ -d ../ai-welfare-eval-harness ]]; then
check harness_hidden          blocked sh -c 'find ../ai-welfare-eval-harness -type f 2>/dev/null | grep -q .'
fi
check windows_drive_unreadable blocked cat /mnt/c/Windows/System32/drivers/etc/hosts

# Diagnostics (not checks): how key paths are mounted inside this process.
# "ro" = read-only. Helps tell a real gap from a probe mistake.
if [[ -r /proc/self/mountinfo ]]; then   # Linux only
echo "DIAG mounts_total $(wc -l < /proc/self/mountinfo)"
for p in / /home "$HOME" /mnt/c "$PWD" "$PWD/scripts/run_analysis.py" "$PWD/scripts"; do
  line=$(awk -v m="$p" '$5 == m {print $6; exit}' /proc/self/mountinfo)
  echo "DIAG mount $p ${line:-<no separate mount>}"
done
fi

if (( fails == 0 )); then
  echo "PROBE RESULT PASS"
else
  echo "PROBE RESULT FAIL ($fails check(s) failed)"
  exit 1
fi
