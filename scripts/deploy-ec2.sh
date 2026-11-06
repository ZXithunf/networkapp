#!/bin/sh
# Runs ON the EC2 instance, sent by GitHub Actions through SSM Run Command.
# The workflow prepends IMAGE, AWS_REGION and VERSION before sending it.
# POSIX sh on purpose: SSM's AWS-RunShellScript runs scripts with /bin/sh.
#
# Steps: log in to ECR -> pull -> replace the container -> health check -> roll back on failure.
set -eu

: "${IMAGE:?IMAGE is required}"
: "${AWS_REGION:?AWS_REGION is required}"
: "${VERSION:?VERSION is required}"

NAME=netpulse
REGISTRY="${IMAGE%%/*}"

start() {
  docker run -d --name "$NAME" --restart unless-stopped \
    -p 80:8000 \
    -v netpulse-data:/app/data \
    --log-opt max-size=10m --log-opt max-file=3 \
    "$1"
}

echo "Deploying $IMAGE"
# The instance role (not stored keys) authorises the ECR login.
aws ecr get-login-password --region "$AWS_REGION" \
  | docker login --username AWS --password-stdin "$REGISTRY"
docker pull "$IMAGE"

# Remember what is running now so we can roll back to it.
PREVIOUS="$(docker inspect --format '{{.Config.Image}}' "$NAME" 2>/dev/null || true)"

docker rm -f "$NAME" >/dev/null 2>&1 || true
start "$IMAGE"

# Wait up to 60s for /health to report the new version.
i=0
while [ "$i" -lt 30 ]; do
  if curl -fsS http://localhost/health 2>/dev/null | grep -q "\"version\":\"$VERSION\""; then
    echo "Healthy: version $VERSION is live"
    docker image prune -f >/dev/null   # drop dangling layers to save disk
    exit 0
  fi
  i=$((i + 1))
  sleep 2
done

echo "Health check failed for $IMAGE"
docker logs --tail 50 "$NAME" || true
if [ -n "$PREVIOUS" ]; then
  echo "Rolling back to $PREVIOUS"
  docker rm -f "$NAME" >/dev/null 2>&1 || true
  start "$PREVIOUS"
fi
exit 1
