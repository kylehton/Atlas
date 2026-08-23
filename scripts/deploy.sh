#!/usr/bin/env bash
set -euo pipefail

repo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
cd "$repo_dir"

./scripts/build.sh

for command_name in aws docker git scp ssh; do
  command -v "$command_name" >/dev/null || {
    echo "Missing required command: $command_name" >&2
    exit 1
  }
done

aws_region=${AWS_REGION:-us-west-2}
ecr_repository=${ATLAS_ECR_REPOSITORY:-atlas}
ec2_host=${ATLAS_EC2_HOST:?Set ATLAS_EC2_HOST to the EC2 hostname or IP}
ec2_user=${ATLAS_EC2_USER:-ec2-user}
remote_dir=${ATLAS_REMOTE_DIR:-atlas}
platform=${ATLAS_DEPLOY_PLATFORM:-linux/amd64}

if [[ -n $(git status --porcelain) && ${ATLAS_ALLOW_DIRTY:-0} != 1 ]]; then
  echo "Refusing to deploy a dirty worktree. Commit changes or set ATLAS_ALLOW_DIRTY=1." >&2
  exit 1
fi

account_id=$(aws sts get-caller-identity --query Account --output text)
registry="$account_id.dkr.ecr.$aws_region.amazonaws.com"
image_tag=$(git rev-parse --short=12 HEAD)
image="$registry/$ecr_repository:$image_tag"
target="$ec2_user@$ec2_host"

if ! aws ecr describe-repositories \
  --region "$aws_region" \
  --repository-names "$ecr_repository" >/dev/null 2>&1; then
  aws ecr create-repository \
    --region "$aws_region" \
    --repository-name "$ecr_repository" \
    --image-scanning-configuration scanOnPush=true >/dev/null
fi

aws ecr get-login-password --region "$aws_region" \
  | docker login --username AWS --password-stdin "$registry"

docker buildx build \
  --platform "$platform" \
  --file docker/Dockerfile \
  --tag "$image" \
  --tag "$registry/$ecr_repository:latest" \
  --push .

ssh "$target" "mkdir -p '$remote_dir/docker' '$remote_dir/models'"
scp docker-compose.prod.yml "$target:$remote_dir/docker-compose.prod.yml"
scp docker/Caddyfile "$target:$remote_dir/docker/Caddyfile"

if ! ssh "$target" "test -f '$remote_dir/.env'"; then
  echo "Missing $remote_dir/.env on EC2; refusing to deploy without production secrets." >&2
  exit 1
fi

printf 'ATLAS_APP_IMAGE=%s\n' "$image" \
  | ssh "$target" "cat > '$remote_dir/release.env'"

aws ecr get-login-password --region "$aws_region" \
  | ssh "$target" "docker login --username AWS --password-stdin '$registry'"

ssh "$target" "cd '$remote_dir' && \
  docker compose --env-file .env --env-file release.env -f docker-compose.prod.yml pull && \
  docker compose --env-file .env --env-file release.env -f docker-compose.prod.yml run --rm api alembic upgrade head && \
  docker compose --env-file .env --env-file release.env -f docker-compose.prod.yml up -d --no-build --remove-orphans"

echo "Deployed $image to $target"
