"""Standalone admin site API. Shares only the database with the main site.
Bootstrap first admin with env ADMIN_USERNAME / ADMIN_PASSWORD (created on first admin login)."""
import json, os, re
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response
from pydantic import BaseModel

import auth
from database import get_conn

router = APIRouter(prefix="/api/admin")
PRICE = float(os.environ.get("PRO_PRICE_NUM", "7.99"))
_ready = False


def _ensure():
    global _ready
    if _ready:
        return
    with get_conn() as c:
        for sql in ("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'",
                    "ALTER TABLE users ADD COLUMN status TEXT NOT NULL DEFAULT 'active'"):
            try:
                c.execute(sql); c.commit()
            except Exception:
                try: c._conn.rollback()
                except Exception: pass
        c.execute("CREATE TABLE IF NOT EXISTS categories (slug TEXT PRIMARY KEY, name TEXT NOT NULL, emoji TEXT DEFAULT '')")
        c.execute("CREATE TABLE IF NOT EXISTS admin_recipes (id TEXT PRIMARY KEY, name TEXT NOT NULL, category TEXT DEFAULT '', "
                  "cook_time INTEGER, servings INTEGER, tags TEXT, ingredients TEXT, instructions TEXT, published INTEGER DEFAULT 1)")
        c.commit()
        try:  # recipe photo (https URL or small data: URL); safe to re-run
            c.execute("ALTER TABLE admin_recipes ADD COLUMN image TEXT DEFAULT ''"); c.commit()
        except Exception:
            try: c._conn.rollback()
            except Exception: pass
    _ready = True


def seed(builtin):
    """Seed DB from the built-in recipes once so they become editable."""
    _ensure()
    with get_conn() as c:
        if not c.execute("SELECT 1 FROM admin_recipes LIMIT 1").fetchone():
            for r in builtin:
                cat = next((t for t in r["tags"] if t in ("breakfast", "lunch", "dinner", "salad", "soup", "side")), "")
                c.execute("INSERT INTO admin_recipes (id,name,category,cook_time,servings,tags,ingredients,instructions,published) VALUES (?,?,?,?,?,?,?,?,1)", (str(r["id"]), r["name"], cat, r.get("cook_time"),
                          r.get("servings"), json.dumps(r["tags"]), json.dumps(r["ingredients"]), json.dumps(r["instructions"])))
            for s in ("breakfast", "lunch", "dinner", "salad", "soup", "side"):
                c.execute("INSERT INTO categories VALUES (?,?,?) ON CONFLICT (slug) DO NOTHING", (s, s.title(), ""))
            c.commit()


def _rec(r):
    d = dict(r)
    for k in ("tags", "ingredients", "instructions"):
        d[k] = json.loads(d[k] or "[]")
    d["published"] = bool(d["published"])
    d["image"] = d.get("image") or ""
    return d


def require_admin(admin_token: Optional[str] = Cookie(default=None)) -> dict:
    _ensure()
    if not admin_token:
        raise HTTPException(401, "Admin login required")
    with get_conn() as c:
        row = c.execute("SELECT u.id, u.username, u.role, u.status, s.expires_at FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                        (admin_token,)).fetchone()
    if not row or row["role"] != "admin" or row["status"] != "active" or datetime.fromisoformat(row["expires_at"]) < datetime.utcnow():
        raise HTTPException(401, "Admin login required")
    return {"id": row["id"], "username": row["username"]}


class Login(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(req: Login, request: Request, response: Response):
    _ensure()
    eu, ep = os.environ.get("ADMIN_USERNAME", "").strip().lower(), os.environ.get("ADMIN_PASSWORD", "")
    with get_conn() as c:
        if eu and ep and not c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            row = c.execute("SELECT id FROM users WHERE username=?", (eu,)).fetchone()
            if row:
                c.execute("UPDATE users SET role='admin' WHERE id=?", (row["id"],)); c.commit()
            else:
                c.execute("INSERT INTO users (username,password_hash,role,email_verified) VALUES (?,?, 'admin',1)", (eu, auth.hash_password(ep))); c.commit()
    uid = auth.authenticate(req.username, req.password)
    with get_conn() as c:
        if c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()["role"] != "admin":
            raise HTTPException(403, "This account is not an admin")
    tok = auth.create_session(uid)
    https = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie("admin_token", tok, httponly=True, max_age=60 * 60 * 12, samesite="strict", secure=https)
    return {"ok": True}


@router.post("/logout")
def logout(response: Response, admin_token: Optional[str] = Cookie(default=None)):
    if admin_token: auth.destroy_session(admin_token)
    response.delete_cookie("admin_token")
    return {"ok": True}


@router.get("/me")
def me(a=Depends(require_admin)):
    return a


def _scalar(c, sql, p=()):
    r = c.execute(sql, p).fetchone()
    return list(r.values())[0] if isinstance(r, dict) else r[0]


@router.get("/stats")
def stats(a=Depends(require_admin)):
    today = datetime.utcnow().date()
    since = (today - timedelta(days=13)).isoformat()
    with get_conn() as c:
        total = _scalar(c, "SELECT COUNT(*) FROM users WHERE role='user'")
        pro = _scalar(c, "SELECT COUNT(*) FROM users WHERE plan='pro' AND role='user'")
        signups = {}
        for r in c.execute("SELECT created_at FROM users WHERE role='user'").fetchall():
            d = str(r["created_at"])[:10]
            if d >= since: signups[d] = signups.get(d, 0) + 1
        top = c.execute("SELECT recipe_id, COUNT(*) n FROM favorites GROUP BY recipe_id ORDER BY n DESC LIMIT 5").fetchall()
        return {"users": total, "pro": pro, "free": total - pro, "mrr": round(pro * PRICE, 2),
                "recipes": _scalar(c, "SELECT COUNT(*) FROM admin_recipes"), "categories": _scalar(c, "SELECT COUNT(*) FROM categories"),
                "ai_today": _scalar(c, "SELECT COALESCE(SUM(count),0) FROM ai_usage WHERE usage_date=?", (today.isoformat(),)),
                "cooked": _scalar(c, "SELECT COUNT(*) FROM cook_history"),
                "signups": [{"d": (today - timedelta(days=i)).isoformat(), "n": signups.get((today - timedelta(days=i)).isoformat(), 0)} for i in range(13, -1, -1)],
                "top_favorites": [{"recipe_id": r["recipe_id"], "n": r["n"]} for r in top]}


# ---------------- Users ----------------
class UserPatch(BaseModel):
    plan: Optional[str] = None
    status: Optional[str] = None
    role: Optional[str] = None


@router.get("/users")
def users(q: str = "", plan: str = "", a=Depends(require_admin)):
    sql, p = "SELECT id,username,email,name,plan,plan_updated_at,status,role,auth_provider,email_verified,created_at FROM users WHERE role='user'", []
    if q: sql += " AND (username LIKE ? OR email LIKE ?)"; p += [f"%{q}%"] * 2
    if plan in ("free", "pro"): sql += " AND plan=?"; p.append(plan)
    with get_conn() as c:
        rows = [dict(r, created_at=str(r["created_at"])[:10]) for r in c.execute(sql + " ORDER BY id DESC LIMIT 500", p).fetchall()]
    return {"users": rows}


@router.patch("/users/{uid}")
def patch_user(uid: int, req: UserPatch, a=Depends(require_admin)):
    if uid == a["id"] and (req.status == "suspended" or req.role == "user"):
        raise HTTPException(400, "You can't demote or suspend yourself")
    if req.plan: auth.set_plan(uid, req.plan)
    with get_conn() as c:
        if req.status in ("active", "suspended"):
            c.execute("UPDATE users SET status=? WHERE id=?", (req.status, uid))
            if req.status == "suspended": c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
        if req.role in ("user", "admin"): c.execute("UPDATE users SET role=? WHERE id=?", (req.role, uid))
        c.commit()
    return {"ok": True}


@router.delete("/users/{uid}")
def delete_user(uid: int, a=Depends(require_admin)):
    if uid == a["id"]: raise HTTPException(400, "You can't delete yourself")
    with get_conn() as c:
        for t in ("sessions", "pantry", "cook_history", "favorites", "ai_usage", "receipts"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        c.execute("DELETE FROM users WHERE id=?", (uid,)); c.commit()
    return {"ok": True}


# ---------------- Categories ----------------
class Cat(BaseModel):
    name: str
    emoji: str = ""


def _slug(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


@router.get("/categories")
def cats(a=Depends(require_admin)):
    with get_conn() as c:
        return {"categories": [dict(r) for r in c.execute(
            "SELECT c.slug,c.name,c.emoji,(SELECT COUNT(*) FROM admin_recipes r WHERE r.category=c.slug) AS recipes FROM categories c ORDER BY c.name").fetchall()]}


@router.post("/categories")
def add_cat(req: Cat, a=Depends(require_admin)):
    s = _slug(req.name)
    if not s: raise HTTPException(400, "Name required")
    with get_conn() as c:
        if c.execute("SELECT 1 FROM categories WHERE slug=?", (s,)).fetchone(): raise HTTPException(409, "Category exists")
        c.execute("INSERT INTO categories VALUES (?,?,?)", (s, req.name.strip(), req.emoji)); c.commit()
    return {"ok": True}


@router.put("/categories/{slug}")
def edit_cat(slug: str, req: Cat, a=Depends(require_admin)):
    with get_conn() as c:
        c.execute("UPDATE categories SET name=?, emoji=? WHERE slug=?", (req.name.strip(), req.emoji, slug)); c.commit()
    return {"ok": True}


@router.delete("/categories/{slug}")
def del_cat(slug: str, a=Depends(require_admin)):
    with get_conn() as c:
        c.execute("UPDATE admin_recipes SET category='' WHERE category=?", (slug,))
        c.execute("DELETE FROM categories WHERE slug=?", (slug,)); c.commit()
    return {"ok": True}


# ---------------- Recipes ----------------
class Rec(BaseModel):
    name: str
    category: str = ""
    cook_time: Optional[int] = None
    servings: Optional[int] = None
    tags: list[str] = []
    ingredients: list[str]
    instructions: list[str]
    published: bool = True
    image: str = ""


MAX_IMG = 400_000  # chars; the admin UI shrinks uploads to ~100-250 KB
_IMG_OK = re.compile(r"^(https?://\S+|data:image/(jpeg|png|webp|gif);base64,[A-Za-z0-9+/=]+)$")


def _img(s: str) -> str:
    s = (s or "").strip()
    if not s: return ""
    if len(s) > MAX_IMG: raise HTTPException(400, "Image is too large - use a smaller photo or an image URL")
    if not _IMG_OK.match(s): raise HTTPException(400, "Image must be an http(s) URL or an uploaded photo")
    return s


def _vals(r: Rec):
    return (r.name.strip(), r.category, r.cook_time, r.servings, json.dumps([t.strip().lower() for t in r.tags if t.strip()]),
            json.dumps([i.strip().lower() for i in r.ingredients if i.strip()]), json.dumps([s.strip() for s in r.instructions if s.strip()]), int(r.published), _img(r.image))


@router.get("/recipes")
def recipes(q: str = "", category: str = "", a=Depends(require_admin)):
    sql, p = "SELECT * FROM admin_recipes WHERE name LIKE ?", [f"%{q}%"]
    if category: sql += " AND category=?"; p.append(category)
    with get_conn() as c:
        return {"recipes": [_rec(r) for r in c.execute(sql + " ORDER BY name", p).fetchall()]}


@router.post("/recipes")
def add_recipe(req: Rec, a=Depends(require_admin)):
    with get_conn() as c:
        nid = max([int(r["id"]) for r in c.execute("SELECT id FROM admin_recipes").fetchall() if str(r["id"]).isdigit()] + [0]) + 1
        c.execute("INSERT INTO admin_recipes (id,name,category,cook_time,servings,tags,ingredients,instructions,published,image) VALUES (?,?,?,?,?,?,?,?,?,?)", (str(nid),) + _vals(req)); c.commit()
    return {"ok": True, "id": str(nid)}


@router.put("/recipes/{rid}")
def edit_recipe(rid: str, req: Rec, a=Depends(require_admin)):
    with get_conn() as c:
        c.execute("UPDATE admin_recipes SET name=?,category=?,cook_time=?,servings=?,tags=?,ingredients=?,instructions=?,published=?,image=? WHERE id=?", _vals(req) + (rid,)); c.commit()
    return {"ok": True}


class ImgIn(BaseModel):
    image: str = ""


@router.patch("/recipes/{rid}/image")
def set_recipe_image(rid: str, req: ImgIn, a=Depends(require_admin)):
    img = _img(req.image)
    with get_conn() as c:
        c.execute("UPDATE admin_recipes SET image=? WHERE id=?", (img, rid)); c.commit()
    return {"ok": True}


@router.delete("/recipes/{rid}")
def del_recipe(rid: str, a=Depends(require_admin)):
    with get_conn() as c:
        c.execute("DELETE FROM admin_recipes WHERE id=?", (rid,)); c.commit()
    return {"ok": True}


# ---------------- Subscriptions ----------------
@router.get("/subscriptions")
def subscriptions(a=Depends(require_admin)):
    since = (datetime.utcnow().date() - timedelta(days=6)).isoformat()
    with get_conn() as c:
        rec = [dict(r) for r in c.execute("SELECT r.receipt_id,r.plan,r.amount,r.created_at,u.username,u.email FROM receipts r LEFT JOIN users u ON u.id=r.user_id WHERE u.role IS NULL OR u.role='user' ORDER BY r.created_at DESC LIMIT 25").fetchall()]
        pros = [dict(r) for r in c.execute("SELECT id,username,email,plan_updated_at FROM users WHERE plan='pro' AND role='user' ORDER BY plan_updated_at DESC").fetchall()]
        heavy = [dict(r) for r in c.execute("SELECT u.username,SUM(a.count) n FROM ai_usage a JOIN users u ON u.id=a.user_id WHERE a.usage_date>=? AND u.plan='free' AND u.role='user' GROUP BY u.username ORDER BY n DESC LIMIT 5", (since,)).fetchall()]
        total = _scalar(c, "SELECT COUNT(*) FROM users WHERE role='user'")
    return {"pro_count": len(pros), "conversion": round(100 * len(pros) / total, 1) if total else 0, "mrr": round(len(pros) * PRICE, 2),
            "price": PRICE, "pro_users": pros, "receipts": rec, "free_heavy_ai_users": heavy}
