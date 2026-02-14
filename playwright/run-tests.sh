#!/usr/bin/env bash
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BACKEND_PORT=8082
FRONTEND_PORT=4201
CONFIG_PATH="$SCRIPT_DIR/config.test.yaml"
PROXY_CONF="$SCRIPT_DIR/proxy.conf.test.json"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  echo ""
  echo "=== Cleaning up ==="
  if [ -n "$FRONTEND_PID" ]; then
    echo "Stopping frontend (PID $FRONTEND_PID)..."
    kill "$FRONTEND_PID" 2>/dev/null || true
  fi
  if [ -n "$BACKEND_PID" ]; then
    echo "Stopping backend (PID $BACKEND_PID)..."
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
  echo "Stopping test Neo4j container..."
  docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" down 2>/dev/null || true
  # Reset test config to clean state
  cat > "$CONFIG_PATH" <<EOF
neo4j:
  uri: bolt://localhost:7688
  username: neo4j
  password: testpassword
  database: neo4j

repositories: []
ai_providers: []
ai_tasks: []
encryption_salt: null
EOF
  echo "Cleanup complete."
}

trap cleanup EXIT

echo "=== Starting test Neo4j container (roadmap-testing) ==="
docker compose -f "$SCRIPT_DIR/docker-compose.test.yml" up -d

echo "=== Waiting for Neo4j to be ready ==="
for i in $(seq 1 30); do
  if curl -s http://localhost:7475 > /dev/null 2>&1; then
    echo "Neo4j is ready."
    break
  fi
  if [ "$i" -eq 30 ]; then
    echo "ERROR: Neo4j did not start within 30 seconds."
    exit 1
  fi
  sleep 1
done

echo "=== Starting backend on port $BACKEND_PORT ==="
cd "$PROJECT_ROOT/backend"
ROADMAP_CONFIG_PATH="$CONFIG_PATH" ./venv/Scripts/uvicorn app.main:app --port "$BACKEND_PORT" &
BACKEND_PID=$!

echo "=== Waiting for backend to be ready ==="
for i in $(seq 1 15); do
  if curl -s "http://localhost:$BACKEND_PORT/api/encryption/status" > /dev/null 2>&1; then
    echo "Backend is ready."
    break
  fi
  if [ "$i" -eq 15 ]; then
    echo "ERROR: Backend did not start within 15 seconds."
    exit 1
  fi
  sleep 1
done

echo "=== Starting frontend on port $FRONTEND_PORT ==="
cd "$PROJECT_ROOT/frontend"
npx ng serve --port "$FRONTEND_PORT" --proxy-config "$PROXY_CONF" &
FRONTEND_PID=$!

echo "=== Waiting for frontend to be ready ==="
for i in $(seq 1 60); do
  if curl -s "http://localhost:$FRONTEND_PORT" > /dev/null 2>&1; then
    echo "Frontend is ready."
    break
  fi
  if [ "$i" -eq 60 ]; then
    echo "ERROR: Frontend did not start within 60 seconds."
    exit 1
  fi
  sleep 1
done

echo "=== Running Playwright tests ==="
cd "$SCRIPT_DIR"
npx playwright test "$@"
TEST_EXIT=$?

echo "=== Tests finished with exit code $TEST_EXIT ==="
exit $TEST_EXIT
