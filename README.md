# TCP Proxy Manager

Small **FastAPI** service with a browser dashboard and a JSON API. It runs **TCP listeners inside the same process** (asyncio) and forwards bytes to a target host and port. There are **no built-in routes**, no default upstreams, and **no Docker socket** requirement.

**Security warning:** this is a generic TCP forwarder and admin surface. Only run it on **trusted or private networks**. Anyone who can reach the dashboard or published listen ports can pivot traffic through your infrastructure. Prefer VPNs, firewalls, and strong authentication.

## What you get

- Dashboard on port **17000**
- CRUD-style management of named routes: listen port, target host, target port
- **Start** / **Stop** per route without restarting the container
- **Delete** routes
- Status (**running** / **stopped**) and **active connection counts**
- Definitions persisted in **SQLite** under `DATA_DIR` (default `/data`)
- Routes **do not** start automatically unless you enable **auto-start** on that route (then they start when the app boots)
- **GET `/health`** for orchestration checks
- REST API:
  - `GET /api/proxies`
  - `POST /api/proxies`
  - `POST /api/proxies/{id}/start`
  - `POST /api/proxies/{id}/stop`
  - `DELETE /api/proxies/{id}`

## Authentication

Two supported modes (both use **HTTP Basic** in the browser and for API calls):

1. **Environment credentials** — set **both** `APP_USERNAME` and `APP_PASSWORD` on the container. The first-run `/setup` page is **skipped**, and only these credentials are accepted.
2. **First-run setup** — if those variables are **not** set, open **`/setup`** once to create a local admin username and password. They are stored as a **bcrypt hash** in SQLite. After that, sign in through the normal browser Basic prompt on `/`.

If you rely on stored credentials, the dashboard shows a reminder that setting `APP_USERNAME` / `APP_PASSWORD` is recommended for production-style deployments.

## Quick start (Docker Compose)

```bash
docker compose up --build
```

Open `http://localhost:17000`. Complete `/setup` if prompted, then add a route from the UI.

**Important:** the compose file only publishes **17000** by default. For each route you create with listen port `P`, also publish `P:P` on the service (see comments in `docker-compose.yml`). Example mapping for two hypothetical routes:

```yaml
ports:
  - "17000:17000"
  - "18086:18086"
  - "15000:15000"
```

Replace those numbers with the listen ports you actually configure in the UI.

### Troubleshooting: UI says Running but the port is not reachable

- **Running** in the dashboard means asyncio is listening on `0.0.0.0:<listen_port>` **inside the container**. It does **not** mean Docker (or your orchestrator) published that port on the host.
- If connections to `host:<listen_port>` are refused or time out, add **`"<listen_port>:<listen_port>"`** under `ports:` for the same service that runs this app (or the equivalent in Portainer / Kubernetes). This matches what you do for a raw TCP forwarder such as `alpine/socat`: the process listens in the container network namespace; the platform maps it outward.
- After changing published ports, **recreate** the container (Compose: `docker compose up -d --force-recreate`).
- If the target speaks something other than HTTP, opening `http://host:port/` in a browser may not behave as expected even when TCP forwarding works; use a client that matches the protocol (for example `curl`, `openssl s_client`, or your application client).

## Portainer

1. Create a new stack from this repository’s `docker-compose.yml` (or paste an equivalent compose file).
2. Add a **named volume** mounted at `/data` so SQLite survives container recreation (the sample compose already uses `proxy_manager_data:/data`).
3. Under **Ports**, map `17000` to reach the dashboard.
4. For every TCP listen port you configure in the UI, add an additional published port on the same container (host → container, same port number is easiest).
5. Optionally set `APP_USERNAME` and `APP_PASSWORD` in **Environment variables** for fixed credentials.

The container runs as **UID 1000** and binds to **non-privileged** listen ports (**1024–65535** only).

## Example: add a route in the UI

1. Sign in (Basic auth after setup, or credentials from the environment).
2. In **Add route**:
   - **Name:** `example-cache`
   - **Listen port:** `18086` (must be published in Docker if you want it reachable from the host)
   - **Target host:** `backend.example.test` (use your own resolvable hostname)
   - **Target port:** `8443`
   - Leave **Auto-start** unchecked until you are confident the mapping is correct.
3. Click **Create route**, then **Start** for that row.

This example uses placeholder hostnames only; substitute values that match your environment.

## Enable password protection via environment

Set both variables on the service:

```yaml
environment:
  APP_USERNAME: "admin"
  APP_PASSWORD: "use-a-long-random-secret"
```

When both are present, `/setup` is disabled and only these credentials work (the stored admin from SQLite is not used for login).

## Optional routes from file or environment

On each startup, after the database is initialized, the app can **import** additional route rows from either or both of:

- **`PROXY_ROUTES_FILE`**: path to a JSON file containing an **array** of objects with the same fields as `POST /api/proxies` (`name`, `listen_port`, `target_host`, `target_port`, optional `auto_start`).
- **`PROXY_ROUTES_JSON`**: the same array as a **string** in the environment (useful for Portainer “stack env” fields).

Invalid entries are skipped with a log line. Existing listen ports are never overwritten (duplicates are skipped). These imports **do not** start listeners unless `auto_start` is true for that row (same rule as routes created in the UI).

## Persist data

Mount a volume at **`/data`** (or set `DATA_DIR` to another path and mount there). The SQLite file is `proxy.db` inside that directory.

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export DATA_DIR=./devdata
uvicorn app.main:app --reload --port 17000
```

## API notes

- All `/api/*` routes except **`GET /health`**, **`GET /api/setup/status`**, and **`POST /api/setup`** (only before configuration) require authentication.
- If initial setup is still required, protected APIs return **403** with a message pointing to `/setup`.

## License

Use and modify freely for your own deployments.
