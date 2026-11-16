"""Check publishable source files without printing private values."""
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent


def run(*arguments):
    return subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True)


def main():
    scanner = shutil.which("gitleaks")
    if not scanner:
        print("Gitleaks is required. Add the approved local scanner to PATH.")
        return 1
    files = run("git", "ls-files", "--cached", "--others", "--exclude-standard", "-z")
    if files.returncode:
        print("The Git file list could not be read.")
        return 1
    paths = sorted(set(files.stdout.split("\0")) - {""})
    failures = []
    sensitive = re.compile(r"(^|/)(config\.json|\.env|adbkey(?:\.pub)?|[^/]*\.(?:jks|keystore|pem|key|apk|aab|log))$", re.I)
    private_address = re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3})\b")
    home_path = re.compile(r"(?<![A-Za-z0-9_.-])(?:[A-Za-z]:[/\\]Users[/\\]|/Users/|/home/)")
    with tempfile.TemporaryDirectory(prefix="flick-privacy-") as temporary:
        target = Path(temporary)
        for name in paths:
            source = ROOT / name
            if not source.is_file():
                continue
            if sensitive.search(name):
                failures.append(f"Private or generated file: {name}")
            destination = target / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            if source.suffix in {".kt", ".kts", ".md", ".json", ".xml", ".yml", ".yaml", ".properties", ".py"}:
                value = source.read_text(encoding="utf-8", errors="replace")
                # Scanner rules are literal patterns; do not scan this file against itself.
                if source != Path(__file__).resolve() and (private_address.search(value) or home_path.search(value)):
                    failures.append(f"Private address or home path: {name}")
        result = subprocess.run([scanner, "dir", str(target), "--no-banner", "--redact", "--log-level", "error"], capture_output=True)
        if result.returncode:
            failures.append("Gitleaks reported a source finding; inspect its redacted output locally.")
    if run("git", "rev-parse", "--verify", "HEAD").returncode == 0:
        result = run(scanner, "git", ".", "--log-opts=HEAD", "--no-banner", "--redact", "--log-level", "error")
        if result.returncode:
            failures.append("Gitleaks reported a finding in the published branch history.")
    for failure in failures:
        print(failure)
    print(f"Privacy check: {len(paths)} source files, {len(failures)} findings.")
    return bool(failures)


if __name__ == "__main__":
    sys.exit(main())
