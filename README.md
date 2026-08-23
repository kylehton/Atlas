# Atlas

Atlas is a privacy-focused personal productivity agent for managing communication and schedules
through natural conversation.

---

## Current integrations

- **Telegram:** messaging with the agent, conversational follow-ups, approval actions, and proactive
  notifications
- **Gmail:** Google account connection, email drafting, inbox management, and important-email
  notifications
- **Google Calendar:** calendar lookup and creating, updating, or deleting events

> Integration implementation is in progress.

## Technical foundation

- FastAPI service with generated OpenAPI documentation
- Separate API, worker, and scheduler process entry points
- SQLAlchemy and Alembic database foundation
- Locked Python dependency management with uv
- Local Docker Compose stack with Postgres, Caddy, and optional llama.cpp
- Production Compose configuration for an EC2 host and Supabase Postgres
- Commands for formatting, linting, unit tests, integration tests, and E2E tests
- Isolated container build and smoke validation
- ECR image publishing and EC2 deployment automation

---

## Prerequisites

Required for local development:

- Git
- [uv](https://docs.astral.sh/uv/getting-started/installation/)
- [Docker Desktop for macOS](https://docs.docker.com/desktop/setup/install/mac-install/) or
  [Docker Desktop for Windows](https://docs.docker.com/desktop/setup/install/windows-install/)
- `curl`, which is used by the container smoke check

uv manages Python 3.13 and the virtual environment, so a separate `pip` installation is unnecessary.

Deployment additionally requires:

- [AWS CLI v2](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
- AWS credentials with STS and ECR access
- SSH and SCP access to the EC2 host
- Docker with Compose on EC2

### Install uv on macOS

Using Homebrew:

```bash
brew install uv
```

Or use uv's official installer:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

---

## Local setup

Clone the repository and synchronize the locked environment:

```bash
git clone <repository-url> Atlas
cd Atlas
uv python install 3.13
uv sync --locked
```

Activate the environment if you want to call `atlas` directly:

```bash
source .venv/bin/activate
atlas --help
```

Without activation, prefix commands with `uv run`:

```bash
uv run atlas run lint
```

VS Code is configured to discover `.venv` and use uv instead of invoking `pip`.

---

## Local configuration

Docker Compose has development defaults, so `.env` is optional for the current scaffold. Create one
when overriding these values:

```dotenv
ATLAS_POSTGRES_PASSWORD=atlas
ATLAS_HTTP_PORT=8080
ATLAS_MODEL_FILE=model.gguf
ATLAS_MODEL_CONTEXT_SIZE=4096
ATLAS_LLAMA_CPP_IMAGE=ghcr.io/ggml-org/llama.cpp:server-b10524
```

`.env` files and model weights are ignored by Git. Never commit secrets, OAuth credentials, tokens,
or private user data.

### Local model

Place a GGUF model in `models/` and make `ATLAS_MODEL_FILE` match its filename:

```text
models/
└── model.gguf
```

### Start the local stack

To start API, scheduler, local Postgres, and Caddy without llama.cpp:

```bash
docker compose up --build postgres api scheduler caddy
```

The FastAPI schema is then available through Caddy at:

```text
http://localhost:8080/openapi.json
```

After adding a model file, start the complete stack:

```bash
docker compose up --build
```

Stop the stack while preserving local database data:

```bash
docker compose down
```

To also delete the local Postgres and Caddy volumes:

```bash
docker compose down --volumes
```

Run migrations manually with:

```bash
docker compose run --rm api alembic upgrade head
```

---

## Project commands

After activating `.venv`, the following shortcuts are available:

| Command | Purpose |
| --- | --- |
| `atlas run lint` | Check all Python and Markdown files without changing them. |
| `atlas run format` | Apply Python and Markdown lint fixes and formatting. |
| `atlas run test` | Run unit, integration, and E2E suites in that order. |
| `atlas run test-unit` | Run only focused unit tests. |
| `atlas run test-integration` | Run only boundary and service integration tests. |
| `atlas run test-e2e` | Run only complete user-flow tests. |
| `atlas run build` | Format, lint, test, build the app image, run an isolated container smoke test, and tear it down. |
| `atlas run deploy` | Run the build gate, push the app image to ECR, migrate, and deploy it to EC2. |

Arguments can be passed directly to the individual test scripts when finer pytest control is needed:

```bash
./scripts/test-unit.sh -k token
./scripts/test-integration.sh -x
```

The test commands explicitly report empty suites during initial development.

---

## Build validation

Run the complete local gate with:

```bash
atlas run build
```

The build command:

1. Applies formatting.
2. Checks linting and formatting.
3. Runs all three test suites.
4. Builds the shared API/worker/scheduler image.
5. Creates an isolated temporary Compose project.
6. Starts Postgres, runs migrations, and checks worker/scheduler imports.
7. Starts API, scheduler, and Caddy.
8. Requests `/openapi.json` through Caddy.
9. Removes its temporary containers, network, and volumes even on failure.

The smoke stack uses port `18080` by default. Override it with `ATLAS_BUILD_HTTP_PORT` if necessary.
It does not start llama.cpp because model selection is still pending.

---

## Deployment

Production deployment uses `docker-compose.prod.yml`. Unlike local Compose, it pulls the application
image from ECR, connects to Supabase, and exposes Caddy on ports 80 and 443.

### EC2 preparation

Before the first deployment, the EC2 host must have:

- Docker Engine and the Compose plugin
- Inbound HTTP/HTTPS access on ports 80 and 443
- DNS for `ATLAS_SITE_ADDRESS` pointing to the host
- A production `.env` in the remote Atlas directory
- The selected GGUF file in the configured model directory

Minimum production `.env` values:

```dotenv
ATLAS_DATABASE_URL=postgresql+psycopg://<user>:<password>@<supabase-host>:5432/<database>
ATLAS_SITE_ADDRESS=atlas.example.com
ATLAS_MODEL_FILE=model.gguf
ATLAS_MODEL_CONTEXT_SIZE=4096
```

The database password must be URL-encoded when it contains reserved URL characters. Keep the EC2
`.env` readable only by the deployment user.

### Deployment variables

Set these locally before deployment:

```bash
export AWS_REGION=us-west-2
export ATLAS_EC2_HOST=<hostname-or-ip>
export ATLAS_EC2_USER=ec2-user
export ATLAS_ECR_REPOSITORY=atlas
export ATLAS_REMOTE_DIR=atlas
export ATLAS_DEPLOY_PLATFORM=linux/amd64
```

Only `ATLAS_EC2_HOST` is mandatory; the other values above are defaults. Use `linux/arm64` when
deploying to a Graviton host.

Deploy with:

```bash
atlas run deploy
```

Deployment performs the following:

1. Runs the complete local build gate.
2. Refuses a dirty Git worktree by default.
3. Creates the ECR repository if it does not exist.
4. Builds and pushes commit-tagged and `latest` application images.
5. Copies the production Compose and Caddy files to EC2.
6. Confirms the remote production `.env` exists.
7. Authenticates Docker on EC2 with ECR.
8. Pulls the exact commit-tagged image.
9. Runs Alembic migrations.
10. Recreates the EC2 services without building source on the server.

`ATLAS_ALLOW_DIRTY=1` bypasses the clean-worktree guard, but should only be used for deliberate test
deployments.
