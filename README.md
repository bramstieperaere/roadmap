# Roadmap

Software project documentation tool. Analyzes Java and Angular Maven projects from git repositories and stores extracted information in a Neo4j graph database.

## Prerequisites

- Python 3.13+
- Node.js 22+
- Docker (for Neo4j)

## Getting Started

### 1. Start Neo4j

```bash
docker compose up -d
```

Neo4j browser will be available at http://localhost:7474 (credentials: `neo4j` / `roadmap`).

### 2. Start the Backend

```bash
cd backend
python -m venv venv          # first time only
source venv/Scripts/activate # Windows (Git Bash)
# source venv/bin/activate   # Linux/Mac
pip install -r requirements.txt  # first time only
uvicorn app.main:app --reload --port 8081
```

Backend API runs at http://localhost:8081. API docs at http://localhost:8081/docs.

### 3. Start the Frontend

```bash
cd frontend
npm install    # first time only
npx ng serve
```

Frontend runs at http://localhost:4200 and proxies `/api/*` requests to the backend on port 8081.

## Configuration

Copy the example config and edit it:

```bash
cp backend/config.yaml.example backend/config.yaml
```

Settings can also be edited from the UI at http://localhost:4200/settings.

## Project Structure

```
roadmap/
├── backend/           # Python FastAPI backend
│   ├── app/
│   │   ├── main.py    # FastAPI app entry point
│   │   ├── config.py  # YAML config loading
│   │   ├── models.py  # Pydantic models
│   │   └── routers/
│   │       └── settings.py  # Settings REST API
│   ├── config.yaml.example
│   └── requirements.txt
├── frontend/          # Angular 21 frontend
│   └── src/app/
│       ├── header/    # App header with settings cog
│       ├── home/      # Home page
│       ├── settings/  # Settings page (Neo4j + repos config)
│       └── services/  # HTTP services
├── playwright/        # E2E tests (Playwright)
│   ├── pages/         # Page Object classes
│   ├── tests/         # Test specs
│   ├── run-tests.sh   # Orchestration script
│   ├── docker-compose.test.yml  # Test Neo4j container
│   └── playwright.config.ts
├── docker-compose.yml # Neo4j container
└── README.md
```

## E2E Testing (Playwright)

The Playwright test suite spins up its own isolated environment:

- A separate Neo4j container (`roadmap-testing`) on ports 7475/7688
- Backend on port 8082 with a test-specific config
- Frontend on port 4201 proxying to the test backend

### Run all tests

```bash
cd playwright
npm install              # first time only
npx playwright install   # first time only
bash run-tests.sh
```

### Run with options

```bash
bash run-tests.sh --headed          # watch in browser
bash run-tests.sh --debug           # step-through debugger
bash run-tests.sh --grep "Neo4j"    # filter by test name
```

### View test report

```bash
cd playwright
npx playwright show-report
```

The test suite uses the **Page Object pattern** — each page has a corresponding class in `playwright/pages/` with helper methods for navigation and element interaction.
