from __future__ import annotations

import os
import queue
import re
import socket
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from atlas.config.config import Settings

PROJECT_NAME = "atlas-telegram-live-test"
TUNNEL_URL_PATTERN = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
DEFAULT_USER_WAIT_SECONDS = 120
REPO_ROOT = Path(__file__).resolve().parents[1]


class LiveSmokeError(RuntimeError):
    """Report a safe, actionable failure from the live smoke-test orchestration."""


def _run_compose(
    environment: dict[str, str],
    *arguments: str,
    capture_output: bool = False,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run Compose under a fixed test-only project so cleanup cannot target another stack."""

    return subprocess.run(
        ["docker", "compose", "--project-name", PROJECT_NAME, *arguments],
        cwd=REPO_ROOT,
        env=environment,
        check=check,
        text=True,
        stdout=subprocess.PIPE if capture_output else None,
        stderr=subprocess.PIPE if capture_output else None,
    )


def _telegram_request(token: str, method: str, payload: dict[str, object]) -> Any:
    """Call Telegram without allowing token-bearing request URLs into raised errors."""

    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/{method}",
            json=payload,
            timeout=15.0,
        )
        response.raise_for_status()
        body = response.json()
    except (httpx.HTTPError, ValueError):
        raise LiveSmokeError(f"Telegram {method} request failed") from None

    if not isinstance(body, dict) or body.get("ok") is not True:
        description = body.get("description") if isinstance(body, dict) else None
        detail = description if isinstance(description, str) else "request rejected"
        raise LiveSmokeError(f"Telegram {method} failed: {detail}")
    return body.get("result")


def _available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _start_quick_tunnel(origin_url: str) -> tuple[subprocess.Popen[str], str]:
    """Start cloudflared and extract its generated public URL without blocking on its logs."""

    process = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", origin_url],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output: queue.Queue[str | None] = queue.Queue()
    recent_lines: list[str] = []
    public_url: str | None = None
    connection_registered = False

    def collect_output() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output.put(line)
        output.put(None)

    threading.Thread(target=collect_output, daemon=True).start()
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                line = output.get(timeout=1)
            except queue.Empty:
                if process.poll() is not None:
                    break
                continue
            if line is None:
                break
            recent_lines.append(line.rstrip())
            match = TUNNEL_URL_PATTERN.search(line)
            if match is not None:
                public_url = match.group(0)
            if "Registered tunnel connection" in line:
                connection_registered = True
            if public_url is not None and connection_registered:
                return process, public_url
    except BaseException:
        _stop_tunnel(process)
        raise

    _stop_tunnel(process)
    detail = "\n".join(recent_lines[-8:])
    if detail:
        raise LiveSmokeError(f"cloudflared did not create a Quick Tunnel:\n{detail}")
    raise LiveSmokeError("cloudflared did not create a Quick Tunnel")


def _wait_until(
    condition: Callable[[], bool],
    *,
    description: str,
    timeout_seconds: int,
) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(1)
    raise LiveSmokeError(f"Timed out waiting for {description}")


def _wait_for_http(url: str, *, timeout_seconds: int = 30) -> None:
    last_result = "no response"

    def ready() -> bool:
        nonlocal last_result
        result = subprocess.run(
            [
                "curl",
                "--silent",
                "--show-error",
                "--output",
                "/dev/null",
                "--write-out",
                "%{http_code}",
                "--max-time",
                "3",
                url,
            ],
            capture_output=True,
            check=False,
            text=True,
        )
        last_result = result.stdout.strip() or result.stderr.strip() or "no response"
        return result.returncode == 0 and last_result == "200"

    try:
        _wait_until(ready, description=f"{url} to become ready", timeout_seconds=timeout_seconds)
    except LiveSmokeError:
        raise LiveSmokeError(
            f"Timed out waiting for {url} to become ready; last result: {last_result}"
        ) from None


def _database_scalar(environment: dict[str, str], sql: str) -> str:
    result = _run_compose(
        environment,
        "exec",
        "-T",
        "postgres",
        "psql",
        "--no-psqlrc",
        "--tuples-only",
        "--no-align",
        "--set",
        "ON_ERROR_STOP=1",
        "--username",
        "atlas",
        "--dbname",
        "atlas",
        "--command",
        sql,
        capture_output=True,
    )
    return result.stdout.strip()


def _database_count(environment: dict[str, str], table: str) -> int:
    allowed_tables = {"external_identities", "telegram_processed_update_ids"}
    if table not in allowed_tables:
        raise ValueError(f"unsupported smoke-test table: {table}")
    return int(_database_scalar(environment, f"SELECT count(*) FROM {table};"))


def _stop_tunnel(process: subprocess.Popen[str] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _start_reachable_quick_tunnel(origin_url: str) -> tuple[subprocess.Popen[str], str]:
    """Retry Quick Tunnels whose generated hostname never becomes reachable."""

    last_error: LiveSmokeError | None = None
    for attempt in range(1, 5):
        process, public_url = _start_quick_tunnel(origin_url)
        print(
            f"Quick Tunnel {attempt}/4 connected at {public_url}; waiting for DNS propagation...",
            flush=True,
        )
        # An immediate lookup can cache NXDOMAIN locally before Cloudflare publishes the hostname.
        time.sleep(15)
        try:
            _wait_for_http(f"{public_url}/health/ready", timeout_seconds=30)
            return process, public_url
        except LiveSmokeError as error:
            last_error = error
            _stop_tunnel(process)
            if attempt < 4:
                print("Generated hostname was unreachable; requesting a new Quick Tunnel...")
        except BaseException:
            _stop_tunnel(process)
            raise

    assert last_error is not None
    raise last_error


def _run_live_smoke() -> None:
    """Exercise a real Telegram round trip through an isolated local Atlas stack."""

    os.chdir(REPO_ROOT)
    settings = Settings()
    if settings.telegram_bot_token is None:
        raise LiveSmokeError("Set ATLAS_TELEGRAM_BOT_TOKEN in .env first")
    if settings.telegram_webhook_secret is None:
        raise LiveSmokeError("Set ATLAS_TELEGRAM_WEBHOOK_SECRET in .env first")

    bot_token = settings.telegram_bot_token.get_secret_value()
    webhook_secret = settings.telegram_webhook_secret.get_secret_value()
    bot_result = _telegram_request(bot_token, "getMe", {})
    webhook_info = _telegram_request(bot_token, "getWebhookInfo", {})
    if not isinstance(bot_result, dict) or not isinstance(webhook_info, dict):
        raise LiveSmokeError("Telegram returned invalid bot information")

    existing_url = webhook_info.get("url")
    if isinstance(existing_url, str) and existing_url:
        raise LiveSmokeError(
            "This bot already has a webhook. Use a dedicated test bot or delete its webhook first: "
            f"{existing_url}"
        )

    username = bot_result.get("username")
    if not isinstance(username, str) or not username:
        raise LiveSmokeError("Telegram bot has no username")

    environment = os.environ.copy()
    environment["ATLAS_APP_IMAGE"] = "atlas:telegram-live-test"
    environment["ATLAS_HTTP_PORT"] = str(_available_port())
    environment["COMPOSE_PROGRESS"] = "plain"
    wait_seconds = int(environment.get("ATLAS_TELEGRAM_TEST_TIMEOUT", DEFAULT_USER_WAIT_SECONDS))
    tunnel: subprocess.Popen[str] | None = None
    webhook_registered = False

    _run_compose(
        environment,
        "down",
        "--volumes",
        "--remove-orphans",
        check=False,
        capture_output=True,
    )
    try:
        print("Building and starting an isolated Atlas stack...")
        _run_compose(environment, "build", "api")
        _run_compose(environment, "up", "--detach", "--wait", "postgres")
        _run_compose(environment, "run", "--rm", "api", "alembic", "upgrade", "head")
        _run_compose(environment, "run", "--rm", "api", "alembic", "check")
        _run_compose(environment, "up", "--detach", "api", "caddy")

        local_url = f"http://127.0.0.1:{environment['ATLAS_HTTP_PORT']}"
        _wait_for_http(f"{local_url}/health/ready")
        tunnel, public_url = _start_reachable_quick_tunnel(local_url)

        _telegram_request(
            bot_token,
            "setWebhook",
            {
                "url": f"{public_url}/webhooks/telegram",
                "secret_token": webhook_secret,
                "allowed_updates": ["message", "callback_query"],
                "drop_pending_updates": True,
            },
        )
        webhook_registered = True
        print(f"Temporary webhook registered for @{username} through {public_url}.")

        print(f"Open https://t.me/{username} and send one private text message.")
        _wait_until(
            lambda: _database_count(environment, "external_identities") == 1,
            description="the first Telegram message",
            timeout_seconds=wait_seconds,
        )
        first_receipt_count = _database_count(environment, "telegram_processed_update_ids")
        print("First message received, mapped to an Atlas identity, and acknowledged.")

        print("Send a second private text message to the same bot.")
        _wait_until(
            lambda: (
                _database_count(environment, "telegram_processed_update_ids") > first_receipt_count
            ),
            description="the second Telegram message",
            timeout_seconds=wait_seconds,
        )
        if _database_count(environment, "external_identities") != 1:
            raise LiveSmokeError("The second message did not preserve one Atlas identity")
        print("Second message reused the same Atlas identity.")

        user_id = _database_scalar(
            environment,
            "SELECT user_id::text FROM external_identities WHERE provider = 'telegram';",
        )
        _run_compose(
            environment,
            "run",
            "--rm",
            "--no-deps",
            "api",
            "python",
            "-m",
            "atlas.telegram.live_smoke",
            user_id,
        )
        callback_receipt_count = _database_count(
            environment,
            "telegram_processed_update_ids",
        )
        print("Press the Acknowledge button in the new Telegram message.")
        _wait_until(
            lambda: (
                _database_count(environment, "telegram_processed_update_ids")
                > callback_receipt_count
            ),
            description="the Telegram button callback",
            timeout_seconds=wait_seconds,
        )
        confirmed = input("Did Telegram display the 'Received.' notification? [y/N] ")
        if confirmed.strip().lower() not in {"y", "yes"}:
            raise LiveSmokeError("Telegram callback acknowledgement was not manually confirmed")

        print("Live Telegram smoke test passed.")
    finally:
        if webhook_registered:
            try:
                _telegram_request(
                    bot_token,
                    "deleteWebhook",
                    {"drop_pending_updates": True},
                )
                print("Temporary Telegram webhook deleted.")
            except LiveSmokeError as error:
                print(f"Warning: {error}", file=sys.stderr)
        _stop_tunnel(tunnel)
        _run_compose(
            environment,
            "down",
            "--volumes",
            "--remove-orphans",
            check=False,
            capture_output=True,
        )
        print("Temporary tunnel and Atlas test stack removed.")


def main() -> None:
    try:
        _run_live_smoke()
    except (LiveSmokeError, subprocess.CalledProcessError, ValueError) as error:
        print(f"Live Telegram smoke test failed: {error}", file=sys.stderr)
        raise SystemExit(1) from None
    except KeyboardInterrupt:
        print("\nLive Telegram smoke test interrupted.", file=sys.stderr)
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
