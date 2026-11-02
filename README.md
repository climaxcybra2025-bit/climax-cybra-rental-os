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
| API | REST endpoints for every module, a dashboard endpoint, and a tenant-portal ticket endpoint that shows the emergency phone |

Not yet built: e-signature, channel manager, QuickBooks, n8n workflows, inbound email, subscription billing.

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
templates/, static/ HTML and CSS (no build step)
locale/fa/         Persian translations
tests/             pytest suite
```
