import argparse
import subprocess
from pathlib import Path

COMMANDS = {
    "build": ("build.sh",),
    "deploy": ("deploy.sh",),
    "format": ("quality.sh", "fix"),
    "lint": ("quality.sh", "check"),
    "test": ("test.sh",),
    "test-e2e": ("test-e2e.sh",),
    "test-integration": ("test-integration.sh",),
    "test-unit": ("test-unit.sh",),
}


def find_repo_root() -> Path:
    candidates = (Path.cwd(), *Path(__file__).resolve().parents)
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file() and (candidate / "scripts").is_dir():
            return candidate
    raise RuntimeError("Atlas development commands must run from the project checkout")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="atlas")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run", help="run a project task")
    run_parser.add_argument("task", choices=sorted(COMMANDS))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    repo_root = find_repo_root()
    script_name, *script_args = COMMANDS[args.task]
    result = subprocess.run(
        [repo_root / "scripts" / script_name, *script_args],
        cwd=repo_root,
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
