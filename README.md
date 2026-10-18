# What Can I Cook - Admin site (separate project)
Own server, own login, own deployment. It only shares the Postgres database with the main site.

## Setup
    python -m venv venv
    venv\Scripts\activate          (Mac/Linux: source venv/bin/activate)
    pip install -r requirements.txt
    copy .env.example .env         (then paste the SAME DATABASE_URL as the main site)

## Create your admin account
    python make_admin.py           (asks username + password; stored hashed in the users table, role='admin')

## Run
    uvicorn app:app --port 8001 --reload
Open http://localhost:8001 and sign in with the username/password you gave make_admin.py.

## Deploy (Vercel)
Import this folder as its own project. Set env var DATABASE_URL (same as main site) and PRO_PRICE_NUM.
Run make_admin.py once locally against the same DATABASE_URL to create the admin.
