"""Runs a script on the Mini over ONE ssh connection, with nothing for a shell to re-quote and UTF-8 both ways.

    python scripts/mini.py ps  < script.ps1        PowerShell (sent as -EncodedCommand: no quoting at all)
    python scripts/mini.py py  < script.py         Python (sent on stdin to `python -X utf8 -`)
    python scripts/mini.py ps <<'EOF'              ...or a heredoc; the quoted 'EOF' stops bash touching $ and \\
    Get-Service sshd
    EOF

Why: commands typed inline went through bash, then ssh, then PowerShell, and each re-read the quotes, $ and \\
(mangled checks, a stray line break that broke vision.py); and Windows programs wrote cp1252, not UTF-8
(emoji crashes, "don’t" logged as "don�?Tt"). Here the script text never passes through a shell, and every
stream is UTF-8. Exit code: the remote script's.

For Claude Code sessions: a double backslash typed inside a Bash tool command (even in a quoted heredoc)
arrives as a single one, so 'a\\\\b' in Python became 'a\\b' = a backspace. Anything with backslash escapes
goes in a file written with the Write tool, then `python scripts/mini.py py < that_file`.
"""
import base64
import subprocess
import sys

HOST = "agent-mini"

# Makes PowerShell 5.1 read and write UTF-8 (console output, pipes to native programs, and file cmdlets)
PS_UTF8 = ("$ProgressPreference = 'SilentlyContinue'\n"      # else progress records come back as CLIXML
           "$OutputEncoding = [Console]::OutputEncoding = [Console]::InputEncoding = "
           "New-Object System.Text.UTF8Encoding $false\n"
           "$PSDefaultParameterValues['*:Encoding'] = 'utf8'\n"
           "$env:PYTHONUTF8 = '1'; $env:PYTHONIOENCODING = 'utf-8'\n")


def run(kind: str, script: str) -> int:
    if kind == "ps":
        encoded = base64.b64encode((PS_UTF8 + script).encode("utf-16-le")).decode("ascii")
        if len(encoded) > 30000:
            sys.exit("script too long for -EncodedCommand (Windows' 32K command line): copy it over with scp")
        cmd, stdin = ["ssh", HOST, f"powershell -NoProfile -NonInteractive -EncodedCommand {encoded}"], None
    elif kind == "py":
        # The remote login shell is PowerShell; this fixed string has no quotes or $ for anything to mangle
        cmd, stdin = ["ssh", HOST, "python -X utf8 -"], script.encode("utf-8")
    else:
        sys.exit("usage: mini.py ps|py  < script")
    res = subprocess.run(cmd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=False)
    sys.stdout.buffer.write(res.stdout)
    sys.stdout.flush()
    return res.returncode


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    sys.exit(run(sys.argv[1], sys.stdin.buffer.read().decode("utf-8")))
