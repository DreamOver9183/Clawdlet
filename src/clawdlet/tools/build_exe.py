"""Package Clawdlet into a standalone Windows executable (dist/clawdlet.exe) using PyInstaller."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys


def pyinstaller_command(repo_root: Path) -> list[str]:
    """The PyInstaller invocation that bundles the sprites and the behavior table."""
    sprites_dir = repo_root / "assets" / "sprites"
    behaviors_dir = repo_root / "assets" / "behaviors"
    return [
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
        str(sprites_dir / "clawdlet.ico"),
        "--add-data",
        f"{sprites_dir};assets/sprites",
        "--add-data",
        f"{behaviors_dir};assets/behaviors",
        "--exclude-module",
        "numpy",
        "--exclude-module",
        "pytest",
        "--paths",
        str(repo_root / "src"),
        str(repo_root / "src" / "clawdlet" / "__main__.py"),
    ]


def validate_default_behaviors(repo_root: Path) -> list[str]:
    """Problems with the bundled behavior table (an empty list means it is safe to ship)."""
    from clawdlet.behavior_table import load_bundled_behavior_table

    path = repo_root / "assets" / "behaviors" / "behaviors.default.json"
    table = load_bundled_behavior_table(path)
    problems = list(table.warnings)
    if table.origin != str(path):
        problems.append(f"{path} was not used (origin: {table.origin})")
    return problems


def build_executable(repo_root: Path | None = None) -> int:
    if repo_root is None:
        repo_root = Path(__file__).resolve().parents[3]

    sprites_dir = repo_root / "assets" / "sprites"
    if not (sprites_dir / "manifest.json").exists():
        from clawdlet.tools.gen_sprites import generate_all_sprites

        generate_all_sprites(sprites_dir)

    problems = validate_default_behaviors(repo_root)
    if problems:
        print("The bundled behavior table is invalid:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if shutil.which("pyinstaller") is None:
        print(
            "PyInstaller is not installed. Install it first with:\n"
            "  pip install pyinstaller\n"
            "Then re-run:\n"
            "  python -m clawdlet.tools.build_exe",
            file=sys.stderr,
        )
        return 1

    cmd = pyinstaller_command(repo_root)
    print("Running:", " ".join(cmd))
    completed = subprocess.run(cmd, cwd=str(repo_root), check=False)
    return completed.returncode


def main() -> int:
    return build_executable()


if __name__ == "__main__":
    raise SystemExit(main())
