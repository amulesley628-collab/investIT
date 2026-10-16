# investIT

A server-backed Ghanaian investment account and manual settlement portal. Local development uses SQLite; hosted deployments can use PostgreSQL through `DATABASE_URL`. Public registration always creates an investor account; an administrator must be created separately from the command line.

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

## Free Preview Deployment

The Render Blueprint uses a Free web service and sets `INVESTIT_PREVIEW_MODE=1`. In preview mode, registrations and account pages work, but deposits, withdrawals, investments, dividends, and balance adjustments are disabled. The dashboard hides payment instructions and marks investment controls unavailable.

1. Create a Neon Free PostgreSQL project. The Free plan is permanent but intended for prototypes and side projects: it has a 1 GB project storage limit, scale-to-zero, no scheduled backups, and writes can stop when usage limits are reached. Do not use it for customer funds or production investor records.
2. In Neon, copy the PostgreSQL connection string and keep it private. In the Render Blueprint setup, enter it as the `DATABASE_URL` secret when prompted. Use TLS (`sslmode=require`) and keep the database in a nearby region.
3. Connect the public GitHub repository `amulesley628-collab/investIT` to Render as a Blueprint. The Blueprint generates the session secret and configures HTTPS cookies.
4. Render Free has no service shell. To provision the administrator, set `DATABASE_URL` in a local PowerShell session to the Neon connection string, then run `python server.py init-admin`. This initializes the schema and prompts for the admin account. Never commit the connection string.
5. Future source changes must be committed and pushed to the repository's `main` branch to trigger updates. Git is not installed in this workspace, so use GitHub's web editor/upload flow or install Git before pushing locally.
6. A custom domain can be set in Render service settings after registration. Domain availability, purchase, trademark clearance, and registrar DNS changes are not included here.

Candidate domain: `investitcapital.com`. Availability and trademark clearance have not been verified; confirm both before purchase or launch.

Render Free services can spin down when idle. Neon Free database compute scales to zero after inactivity and has no scheduled backups. This deployment is for a restricted preview only, not a reliable production service.

## Payments and settlement

This version does not connect to MTN MoMo or another payment API. Deposit requests remain pending until an administrator verifies the incoming transfer against the provider's records and approves it. A withdrawal reserves available balance immediately; the administrator must make the payout through the provider separately, then mark the request paid. Rejection returns reserved withdrawal funds. The 15-minute review time is an operating target, not an automated guarantee.

## Before a public launch

The business must verify and publish its legal entity and applicable Ghana SEC authorization, approved product/offer documents, complete risk disclosures, customer terms, privacy/data-retention policy, and complaint contact. The provided product amounts are the owner's supplied terms; this code cannot verify the license or the terms.

Before any paid production deployment, replace free-tier storage with a production database plan that includes backups and recovery, remove preview-mode restrictions only after payment processing and settlement are properly integrated, configure monitoring and access controls, and review licensing, terms, security, and data protection with qualified professionals. This manual workflow does not itself transfer funds. The free deployment must remain a preview.
