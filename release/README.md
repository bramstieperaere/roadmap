# Roadmap

Software project documentation tool. Analyzes Java and Angular Maven projects
and stores extracted information in a Neo4j graph database.

## Prerequisites

- **Python 3.13+** - [Download](https://www.python.org/downloads/)
- **Neo4j 5+** - one of:
  - **Docker (recommended):**
    ```
    docker run -d --name neo4j -p 7474:7474 -p 7687:7687 -e NEO4J_AUTH=neo4j/roadmap neo4j:5
    ```
  - **Standalone:** [Download Neo4j](https://neo4j.com/download/)

## Quick Start

### Linux / macOS

```bash
chmod +x start.sh
./start.sh
```

### Windows

```
start.bat
```

The application starts at **http://localhost:8081**.

## Options

```
--port PORT      Listen port (default: 8081)
--config PATH    Path to config.yaml (default: created automatically)
```

Examples:

```bash
./start.sh --port 9090
./start.sh --port 9090 --config /etc/roadmap/config.yaml
```

```
start.bat --port 9090
start.bat --port 9090 --config C:\roadmap\config.yaml
```

## First Run

On first launch the start script will:

1. Create a Python virtual environment (`venv/` in this directory)
2. Install all dependencies from the bundled `vendor/` directory (no internet required)
3. Start the server

Subsequent launches skip steps 1-2 and start immediately.

## Configuration

On first access the application creates a `config.yaml` file next to the `app/` directory.
You can also copy and edit the included example:

```bash
cp config.yaml.example config.yaml
```

All settings can also be managed from the web UI at **http://localhost:8081/settings**.

### Encryption

On first visit the application prompts for an encryption password. This password encrypts
sensitive fields (database password, API keys) in `config.yaml`. You will need this
password every time the server restarts. There is no password recovery — if lost, delete
`config.yaml` and reconfigure.

### Neo4j Connection

Default connection settings (configurable in Settings):

| Setting  | Default                |
|----------|------------------------|
| URI      | `bolt://localhost:7687`|
| Username | `neo4j`                |
| Password | `roadmap`              |
| Database | `neo4j`                |

### AI-Powered Analysis

To use the repository analysis feature:

1. Go to **Settings > AI Providers** and add an OpenAI-compatible API provider
2. Go to **Settings > AI Tasks** and map "Repository Analysis" to your provider
3. Add a repository path and click the **Analyze** button

## Directory Structure

```
roadmap/
  app/            Application code (do not modify)
  vendor/         Bundled Python packages
  venv/           Created automatically on first run
  config.yaml     Your configuration (created on first run)
  start.sh        Linux/macOS launcher
  start.bat       Windows launcher
```

## Troubleshooting

**"Python 3.13+ is required but not found"**
Install Python 3.13 or later and ensure it is on your PATH.

**Port already in use**
Use `--port` to pick a different port, e.g. `./start.sh --port 9090`.

**Cannot connect to Neo4j**
Ensure Neo4j is running and the URI/credentials in Settings are correct.
With Docker: `docker start neo4j` or re-run the `docker run` command above.

**Reset dependencies**
Delete the `venv/` directory and restart. The start script will recreate it.

**Reset configuration**
Delete `config.yaml` and restart. A fresh default config will be created.
