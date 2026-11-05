# Rental OS

Back office and tenant portal for rental operators who lease properties and rent them out by unit, room, bed or couch, from one-night stays to one-year terms. Built as a multi-customer platform (each operator gets an isolated workspace) by ClimaxCybra; Oceanway is the first customer.

Plan and architecture: https://claude.ai/artifact/HuLXhxnb6tgN6NcPwkgdBk

## What is in this first version

| Area | Included |
| --- | --- |
| Workspaces | Operator companies with country, base currency, time zone, language, rule pack, emergency phone, urgent-ticket recipients, fee policies; roles: owner, admin, partner, team, cleaner, tenant |
| Properties and spaces | Properties with house info and rules; spaces nested as unit > room > bed, plus couches; prices per month or night, deposit, status |
| Landlords and leases | Landlords, landlord representatives, master leases with expiry, rent, deposit, sublet consent (optional), renewal decision |
| People | Leads, tenants, guests, cleaners, technicians, officials; tenant pipeline stages from inquiry to closed |
| Tenancies | Short stays, fixed terms and month-to-month; the database blocks double booking on a space, and validation blocks a bed booked inside a rented room |
| Payments | Money in and out with purpose, month, method, currency and Kimia-style confirmation; every payment links to a tenancy or a lease |
| Deposits and agreements | Deposit deductions; agreement and house-rules status (draft, sent, viewed, signed) |
| Operations | Tickets (urgent tickets email the workspace's urgent recipients), inspections, roommate cleaning duties |
| Records | Files attached to any record (payment slips, chat screenshots, letters, photos, IDs), correspondence log with landlords, cities and government offices, incidents and warnings |
| History | Nothing is deleted: delete archives; every create, change and archive is written to an audit log |
| Staff screens | Dashboard (spaces by status, occupancy, leases ending in 90 days, urgent tickets, payments waiting for confirmation, arrivals, departures, today's cleaning) plus list, search, filter, record page, edit, archive, file upload and change history for every module, at `/app/` |
| Tenant portal | Tenants see their stay, open urgent or normal tickets with photos, upload payment slips (saved as pending until staff confirm) and see the emergency phone, at `/portal/` |
| Languages | English and Persian (right-to-left); switch from any screen. Layouts work on phones |
| Listings | One advert per room, bed, couch or unit, with photos, prices and a ready-to-paste post (never shows the street address); per-channel status for Airbnb, Booking.com, Vrbo, Telegram, Facebook groups and Marketplace, Kijiji, Craigslist, Zumper |
| Channel manager | Hostaway connection: bookings from Airbnb, Booking.com and Vrbo become tenancies and guests automatically (webhook plus a sync every 15 minutes); a booking that overlaps another one opens an urgent ticket; guest messages land in Correspondence and staff reply from Rental OS |
| Telegram | Post a listing with photos to your own channel in one click |
| n8n | Signed event webhooks (tickets, payments, bookings, checkouts, lease expiry, messages) and workspace API keys for n8n to read and write records |
| QuickBooks Online | Connect with Intuit sign-in; confirmed money in becomes a Sales Receipt (tenant as customer, product/service per purpose), money out to landlords becomes an Expense (landlord as vendor); each payment is sent once, from a start date you choose |
| Email inbox | One address per property (e.g. `regan-st@in.yourdomain`); CC or forward landlord, strata and city emails to it and they are filed as Correspondence with attachments, matched to the landlord, tenant or government sender (forwarded emails use the original sender) |
| Daily jobs | Short stays start on check-in day; each checkout schedules a turnover clean and flags the space; lease-expiry alerts at 90, 60, 30 and 7 days |
| API | REST endpoints for every module, a dashboard endpoint, and a tenant-portal ticket endpoint that shows the emergency phone |

Not yet built: e-signature, subscription billing, AI reply drafts.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env   # set SECRET_KEY, DEBUG=True, DATABASE_URL to a local PostgreSQL 16
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver
```

Create a workspace and a membership with role `owner` for your user in http://localhost:8000/admin/, then sign in at http://localhost:8000/ to use the staff screens. To give a tenant portal access, create a user, a `tenant` membership, and link the user on the tenant's Person record.

After editing translations in `locale/fa/LC_MESSAGES/django.po`, run `python manage.py compilemessages -l fa` (needs gettext).

PostgreSQL is required (the double-booking rule uses a PostgreSQL exclusion constraint; the migration enables the `btree_gist` extension, which needs a superuser or an existing extension).

## Run with Docker (e.g. on a Hostinger VPS)

```bash
cp .env.example .env   # set SECRET_KEY, ALLOWED_HOSTS, CSRF_TRUSTED_ORIGINS, email settings
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Put a reverse proxy with HTTPS (Caddy, Nginx or Traefik) in front of port 8000.

## Connecting channels

All connections live under **Settings → Connections** (owner and admin only). Secrets are encrypted with `FIELD_ENCRYPTION_KEY` and never shown again.

- **Hostaway (Airbnb, Booking.com, Vrbo).** In Hostaway, Settings → Hostaway API, create an API key. Add a Hostaway connection with the account ID and the key, press *Test connection*, then copy the webhook URL shown on the connection into Hostaway's unified webhooks. For each listing, add an Airbnb/Booking.com channel with the Hostaway listing ID. Prices, availability and the channel listings themselves are managed in Hostaway.
- **Telegram.** Create a bot with @BotFather, make it an admin of your channel, and add a Telegram connection with the bot token and the channel name (for example `@oceanway_rooms`).
- **n8n.** Add an n8n connection with your Webhook node URL and a signing secret; check `X-RentalOS-Signature` (`sha256=` HMAC of the raw body) in n8n. Create an API key under **Settings → API keys** for n8n to call `/api/` with the header `Authorization: Api-Key <key>`.
- **Facebook groups, Marketplace, Kijiji, Craigslist.** These have no posting API for this use. Each listing has a post text with a Copy button; post by hand and press *Mark as posted*.

- **QuickBooks Online.** Platform setup, once: create an app at developer.intuit.com, add the redirect URI `https://<your domain>/app/quickbooks/callback/`, and set `QBO_CLIENT_ID`, `QBO_CLIENT_SECRET`, `QBO_REDIRECT_URI` and `QBO_ENVIRONMENT` (`sandbox` while testing, `production` after Intuit approves the app). Per workspace: add a QuickBooks connection, press *Connect to QuickBooks*, then choose the product/service for each kind of money received, the account for each kind of money paid, the bank accounts, and a start date. Nothing is sent before the start date is set.
- **Email inboxes.** Point an inbound domain (for example `in.yourdomain.com`) at Mailgun or Postmark and set `INBOUND_EMAIL_DOMAIN`. Mailgun: create a catch-all route that forwards to `https://<your domain>/hooks/email/mailgun/` and set `MAILGUN_WEBHOOK_SIGNING_KEY`. Postmark: set the inbound webhook to `https://<your domain>/hooks/email/postmark/<POSTMARK_INBOUND_TOKEN>/`. Then add inboxes under **Settings → Email inboxes**.

## Background jobs

`python manage.py run_scheduler` runs everything in one process (Docker Compose starts it as the `scheduler` service): n8n delivery every minute, Hostaway sync and QuickBooks push every `SCHEDULER_SYNC_MINUTES`, and daily jobs once per workspace after `DAILY_JOBS_HOUR` local time. The single jobs are also available: `deliver_events`, `sync_channels`, `push_accounting`, `run_daily_jobs [--date YYYY-MM-DD]`.

## Tests and checks

```bash
pytest
ruff check . && ruff format --check .
python manage.py makemigrations --check --dry-run
```

## API

All staff endpoints are under `/api/` and are limited to the caller's workspace:
`dashboard/`, `properties/`, `spaces/`, `landlords/`, `landlord-representatives/`, `leases/`, `people/`, `tenancies/`, `payments/`, `tickets/`, `cleaning-duties/`, `correspondence/`, `files/`.
Tenants use `portal/tickets/`. `DELETE` archives.

## Project layout

```
config/            settings, urls
apps/workspaces/   workspaces, memberships, scoping base model and admin
apps/properties/   properties and rentable spaces
apps/landlords/    landlords, representatives, leases
apps/people/       people and tenant pipeline
apps/tenancies/    tenancies, payments, deposits, agreements
apps/operations/   tickets, inspections, cleaning duties, urgent email
apps/records/      files, correspondence, incidents, audit trail
apps/api/          REST API
apps/web/          staff screens (one generic set of views driven by apps/web/resources.py)
apps/portal/       tenant portal
apps/integrations/ listings, channels, Hostaway, Telegram, QuickBooks, email inboxes, n8n events, API keys, scheduler
templates/, static/ HTML and CSS (no build step)
locale/fa/         Persian translations
tests/             pytest suite
```
