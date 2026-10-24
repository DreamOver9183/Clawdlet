"""Package Clawdlet into a standalone Windows executable (dist/clawdlet.exe) using PyInstaller."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


def build_executable(repo_root: Path | None = None) -> int:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]

    sprites_dir = repo_root / "assets" / "sprites"
    if not (sprites_dir / "manifest.json").exists():
        from clawdlet.tools.gen_sprites import generate_all_sprites

        generate_all_sprites(sprites_dir)

    icon_path = sprites_dir / "clawdlet.ico"
    entry_script = repo_root / "src" / "clawdlet" / "__main__.py"

    if shutil.which("pyinstaller") is None:
        print(
            "PyInstaller is not installed. Install it first with:\n"
            "  pip install pyinstaller\n"
            "Then re-run:\n"
            "  python -m clawdlet.tools.build_exe",
            file=sys.stderr,
        )
        return 1

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--noconsole",
        "--name",
        "clawdlet",
        "--icon",
        str(icon_path),
        "--add-data",
        f"{sprites_dir};assets/sprites",
        "--exclude-module",
        "numpy",
        "--exclude-module",
        "pytest",
        "--paths",
        str(repo_root / "src"),
        str(entry_script),
    ]
    print("Running:", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(repo_root), check=False)
    return completed.returncode


def main() -> int:
    return build_executable()


if __name__ == "__main__":
    raise SystemExit(main())
