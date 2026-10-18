"""Create (or reset) an admin account directly in the database.
Run from this folder:  python make_admin.py
Only DATABASE_URL is needed in .env - no ADMIN_PASSWORD required."""
from dotenv import load_dotenv; load_dotenv()
import getpass

import auth
from database import get_conn, backend_name, init_db

init_db()
print("Database:", backend_name())  # should say 'postgres'

username = input("Admin username: ").strip().lower()
password = getpass.getpass("Admin password (typing is hidden): ")
if len(username) < 3 or len(password) < 4 or " " in username:
    raise SystemExit("Username must be 3+ chars with no spaces; password 4+ chars.")

with get_conn() as c:
    row = c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if row:
        c.execute("UPDATE users SET role='admin', status='active', password_hash=? WHERE id=?",
                  (auth.hash_password(password), row["id"]))
    else:
        c.execute("INSERT INTO users (username, password_hash, role, email_verified) VALUES (?, ?, 'admin', 1)",
                  (username, auth.hash_password(password)))
    c.commit()
print("Admin ready:", username)
