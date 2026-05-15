# Web Hunt Preflight Design

## Goal

Add a safe preflight step to the web hunt flow so an operator can see whether the selected account, organizations, prefixes, target count, and VM profile are ready before creating a real hunt job.

## Scope

The first version is local-data preflight only. It reads data already stored in the YC Hunter database and does not call Yandex Cloud or create resources.

## Backend

Add `POST /api/hunts/preflight` using the same request body as `POST /api/hunts`. The response contains an overall `ready` flag, a short `summary`, a `capacity` block, and a list of checks. Checks are deterministic:

- scopes must be present;
- prefixes must be present;
- target count must be between `1` and the configured cloud target limit;
- each selected account must exist and be active;
- each selected organization must exist under the selected account;
- each selected account should have at least one active billing account;
- VM profile preview should include platform, CPU/RAM/disk, and Debian image family.

## Frontend

The Hunts page will run preflight from the launch form before a hunt starts. It shows a glass panel with ready/warning/error checks, capacity preview, and the exact VM profile that will be used. `Start hunt` remains disabled until at least one organization and one prefix are selected; after preflight returns, warnings are visible but only hard errors make the launch unsafe.

## Testing

Backend tests cover the preflight endpoint and ensure it does not echo secrets. Frontend tests cover the operator flow: select an organization, run preflight, see readiness output, then start the hunt.
