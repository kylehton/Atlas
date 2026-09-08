import argparse
import subprocess
from pathlib import Path

COMMANDS: dict[str, tuple[tuple[str, ...], str]] = {
    "build": (
        ("build.sh",),
        "Validate the project, build its image, and smoke-test the local container.",
    ),
    "deploy": (
        ("deploy.sh",),
        "Run the build gate, publish to ECR, migrate, and deploy to EC2.",
    ),
    "format": (("quality.sh", "fix"), "Fix Python and Markdown lint errors and format."),
    "lint": (
        ("quality.sh", "check"),
        "Lint and type-check Python and Markdown files without changing them.",
    ),
    "start": (("start.sh",), "Build, migrate, and start the local Atlas stack."),
    "stop": (("stop.sh",), "Stop the local Atlas stack while preserving database data."),
    "telegram-id": (
        ("telegram-id.sh",),
        "Read your numeric Telegram user ID from a private message to your Atlas bot.",
    ),
    "test": (("test.sh",), "Run the deterministic unit, integration, and E2E suites."),
    "test-e2e": (("test-e2e.sh",), "Run complete local user-flow tests."),
    "test-integration": (
        ("test-integration.sh",),
        "Run service and disposable PostgreSQL integration tests.",
    ),
    "test-telegram-live": (
        ("test-telegram-live.sh",),
        "Run the interactive real-Telegram test through a temporary HTTPS tunnel.",
    ),
    "test-unit": (("test-unit.sh",), "Run focused unit tests."),
    "typecheck": (("typecheck.sh",), "Run strict Python package type checking."),
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
    task_parsers = run_parser.add_subparsers(dest="task", required=True, metavar="TASK")
    for task_name, (_, task_help) in COMMANDS.items():
        task_parsers.add_parser(task_name, help=task_help, description=task_help)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    repo_root = find_repo_root()
    script, _ = COMMANDS[args.task]
    script_name, *script_args = script
    result = subprocess.run(
        [repo_root / "scripts" / script_name, *script_args],
        cwd=repo_root,
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
