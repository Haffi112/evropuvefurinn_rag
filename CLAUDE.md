# Project notes for Claude

## Deploying to production (`evropa.rhi.hi.is`)

The README's "Deployment" section is written for an interactive operator and is slightly imprecise about user/path. When deploying programmatically, use this exact recipe:

- **SSH user:** `hafsteinne` (NOT `hafsteinneinarsson` — that account exists but lacks server access)
- **Service user:** `evropuvefur`
- **Repo path:** `/opt/evropuvefur/app`
- **Venv:** `/opt/evropuvefur/app/.venv` — uses `pip3` (no `pip` shim), accessed as the `evropuvefur` user
- **Service:** `evropuvefur-api.service` (systemd)
- `hafsteinne` has `NOPASSWD: ALL` sudo

Steps from a local shell with SSH set up:

```bash
ssh hafsteinne@evropa.rhi.hi.is "
  sudo -u evropuvefur git -C /opt/evropuvefur/app pull --ff-only &&
  sudo -u evropuvefur /opt/evropuvefur/app/.venv/bin/pip3 install -e /opt/evropuvefur/app &&
  sudo systemctl restart evropuvefur-api &&
  sleep 3 &&
  sudo systemctl is-active evropuvefur-api
"
```

Verify the deploy:

```bash
curl -s https://evropa.rhi.hi.is/openapi.json | jq '.info.version'
```

Skip the `cd admin && npm ci && npm run build` step from the README unless the change touched `admin/` — that step rebuilds the React UI and is unnecessary for API-only changes.

## Test runner

`pytest` lives in the `dev` optional-dependencies group. Always invoke as:

```bash
uv run --extra dev pytest
```

Bare `uv run pytest` falls back to system Python and may fail to import test deps.
