# investIT

A server-backed Ghanaian investment account and manual settlement portal. Investor accounts, balances, investments, messages, and transaction history are stored in SQLite on the server. Public registration always creates an investor account; an administrator must be created separately from the command line.

## Run locally

In PowerShell from this folder:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:INVESTIT_SECRET_KEY = python -c "import secrets; print(secrets.token_hex(32))"
python server.py init-admin
python server.py
```

Open `http://127.0.0.1:8000`. The administrator setup prompts for a name, Ghana phone number, and password. Investor registrations require a password of at least 12 characters. Keep the generated secret and the `instance` directory private; back up the SQLite database securely.

Run the automated ledger tests with:

```powershell
python -m unittest -v test_server
```

## Deploy on Render

The `render.yaml` Blueprint describes a paid Python web service, generated secret, HTTPS-only cookies, health check, and 1 GB persistent disk for the SQLite database. The disk is required; do not switch this service to an ephemeral free instance. Render's persistent disk is single-instance storage, so keep the service at one instance.

1. Connect the public GitHub repository `amulesley628-collab/investIT` to Render as a new Blueprint. Review the service and persistent-disk pricing before confirming deployment.
2. Future source changes must be committed and pushed to the repository's `main` branch to trigger updates. Git is not installed in this workspace, so use GitHub's web editor/upload flow or install Git before pushing locally.
3. After the first deploy, open the service Shell and run `python server.py init-admin` to provision the business administrator. Do not make the first public signup an administrator.
4. Set a custom domain in the Render service settings after registering it, then follow Render's displayed DNS instructions at your registrar. Domain registration and DNS changes are not included in this project.

Candidate domain: `investitcapital.com`. Availability and trademark clearance have not been verified; confirm both before purchase or launch.

## Payments and settlement

This version does not connect to MTN MoMo or another payment API. Deposit requests remain pending until an administrator verifies the incoming transfer against the provider's records and approves it. A withdrawal reserves available balance immediately; the administrator must make the payout through the provider separately, then mark the request paid. Rejection returns reserved withdrawal funds. The 15-minute review time is an operating target, not an automated guarantee.

## Before a public launch

The business must verify and publish its legal entity and applicable Ghana SEC authorization, approved product/offer documents, complete risk disclosures, customer terms, privacy/data-retention policy, and complaint contact. The provided product amounts are the owner's supplied terms; this code cannot verify the license or the terms.

Deploy only behind HTTPS with `INVESTIT_ENV=production`, a durable random `INVESTIT_SECRET_KEY`, and `INVESTIT_HTTPS=1`. Keep the app bound to localhost behind a maintained TLS reverse proxy, restrict server/database access, configure encrypted backups and monitoring, and test recovery. A production money service should integrate a contracted payment provider with signed webhook verification and idempotent settlement; this manual workflow does not itself transfer funds. Review the deployment, security controls, payment operations, licensing, and legal disclosures with qualified Ghanaian professionals before accepting public customers or funds.
