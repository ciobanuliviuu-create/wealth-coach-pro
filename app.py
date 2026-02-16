import os
import re
import io
import ssl
import json
import math
import time
import hashlib
import secrets
from datetime import datetime, date

import streamlit as st
import pandas as pd

# PDF (Facturi)
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors

# DB: SQLite or Postgres (Supabase)
import sqlite3
try:
    import psycopg2
    from psycopg2.extras import RealDictCursor
except Exception:
    psycopg2 = None


# -------------------- CONFIG --------------------
APP_TITLE = "🛠️ Service + Depozit PRO"
SQLITE_PATH = "service_depozit.db"

# set in Streamlit Cloud -> Settings -> Secrets:
# DATABASE_URL="postgresql://user:pass@host:5432/dbname"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

# firmă (poți pune în Secrets)
COMPANY_NAME = os.getenv("COMPANY_NAME", "Firma Ta SRL")
COMPANY_CUI = os.getenv("COMPANY_CUI", "RO12345678")
COMPANY_ADDR = os.getenv("COMPANY_ADDR", "Adresa firmei, Oraș")
COMPANY_EMAIL = os.getenv("COMPANY_EMAIL", "contact@firma.ro")
COMPANY_PHONE = os.getenv("COMPANY_PHONE", "07xx xxx xxx")

DEFAULT_ADMIN_USER = os.getenv("DEFAULT_ADMIN_USER", "admin")
DEFAULT_ADMIN_PASS = os.getenv("DEFAULT_ADMIN_PASS", "admin123")  # schimbă imediat după primul login

st.set_page_config(page_title="Service+Depozit PRO", page_icon="🛠️", layout="wide")


# -------------------- DB ABSTRACTION --------------------
@st.cache_resource
def get_db():
    """Return a dict with engine type and connection creator."""
    if DATABASE_URL and psycopg2:
        return {"type": "postgres", "url": DATABASE_URL}
    return {"type": "sqlite", "path": SQLITE_PATH}

def pg_connect(url: str):
    # psycopg2 accepts standard DATABASE_URL
    return psycopg2.connect(url, sslmode="require", cursor_factory=RealDictCursor)

def db_query(db, sql: str, params=None) -> pd.DataFrame:
    params = params or ()
    if db["type"] == "sqlite":
        conn = sqlite3.connect(db["path"], check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON;")
        df = pd.read_sql_query(sql, conn, params=params)
        conn.close()
        return df
    else:
        conn = pg_connect(db["url"])
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        conn.close()
        return pd.DataFrame(rows)

def db_exec(db, sql: str, params=None):
    params = params or ()
    if db["type"] == "sqlite":
        conn = sqlite3.connect(db["path"], check_same_thread=False)
        conn.execute("PRAGMA foreign_keys = ON;")
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
        conn.close()
        return
    else:
        conn = pg_connect(db["url"])
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
        conn.close()

def now_iso():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# -------------------- SECURITY (PASSWORDS) --------------------
def hash_password(password: str, salt: str) -> str:
    # PBKDF2-HMAC-SHA256
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000)
    return dk.hex()

def make_salt():
    return secrets.token_hex(16)

def verify_password(password: str, salt: str, stored_hash: str) -> bool:
    return hash_password(password, salt) == stored_hash


# -------------------- INIT DB --------------------
def init_db(db):
    # compatible SQL for SQLite + Postgres (mostly)
    # Note: SQLite uses INTEGER PRIMARY KEY AUTOINCREMENT; Postgres uses SERIAL
    if db["type"] == "sqlite":
        db_exec(db, """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT,
            role TEXT NOT NULL, -- ADMIN / MANAGER / STAFF
            salt TEXT NOT NULL,
            pass_hash TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            created_at TEXT
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku TEXT UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            unit TEXT DEFAULT 'buc',
            purchase_price REAL DEFAULT 0,
            sale_price REAL DEFAULT 0,
            stock REAL DEFAULT 0,
            min_stock REAL DEFAULT 0,
            location TEXT, -- depozit/raft
            created_at TEXT
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS stock_moves (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            move_type TEXT NOT NULL, -- IN / OUT / ADJ / SALE / SERVICE_USE
            qty REAL NOT NULL,
            note TEXT,
            ref_doc TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        """)

        # Service orders (fișe service)
        db_exec(db, """
        CREATE TABLE IF NOT EXISTS service_orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code TEXT UNIQUE, -- ex: SO-2026-0001
            client_id INTEGER,
            device TEXT,
            serial TEXT,
            issue TEXT,
            status TEXT, -- NOU / IN_LUCRU / GATA / LIVRAT
            labor_price REAL DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT,
            FOREIGN KEY(client_id) REFERENCES clients(id)
        );
        """)

        # Invoices
        db_exec(db, """
        CREATE TABLE IF NOT EXISTS invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            series TEXT NOT NULL,
            number INTEGER NOT NULL,
            invoice_date TEXT NOT NULL,
            client_id INTEGER,
            type TEXT NOT NULL, -- FACTURA / BON / DEVIZ
            vat_percent REAL DEFAULT 0,
            discount_percent REAL DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            UNIQUE(series, number),
            FOREIGN KEY(client_id) REFERENCES clients(id)
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS invoice_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            invoice_id INTEGER NOT NULL,
            item_type TEXT NOT NULL, -- PRODUCT / LABOR
            product_id INTEGER,
            description TEXT NOT NULL,
            qty REAL NOT NULL,
            unit_price REAL NOT NULL,
            cost_price REAL DEFAULT 0,
            FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE,
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        """)

    else:
        # Postgres DDL
        db_exec(db, """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            full_name TEXT,
            role TEXT NOT NULL,
            salt TEXT NOT NULL,
            pass_hash TEXT NOT NULL,
            active INTEGER DEFAULT 1,
            created_at TEXT
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS clients (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            phone TEXT,
            email TEXT,
            address TEXT,
            notes TEXT,
            created_at TEXT
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS products (
            id SERIAL PRIMARY KEY,
            sku TEXT UNIQUE,
            name TEXT NOT NULL,
            category TEXT,
            unit TEXT DEFAULT 'buc',
            purchase_price DOUBLE PRECISION DEFAULT 0,
            sale_price DOUBLE PRECISION DEFAULT 0,
            stock DOUBLE PRECISION DEFAULT 0,
            min_stock DOUBLE PRECISION DEFAULT 0,
            location TEXT,
            created_at TEXT
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS stock_moves (
            id SERIAL PRIMARY KEY,
            product_id INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
            move_type TEXT NOT NULL,
            qty DOUBLE PRECISION NOT NULL,
            note TEXT,
            ref_doc TEXT,
            created_at TEXT NOT NULL
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS service_orders (
            id SERIAL PRIMARY KEY,
            code TEXT UNIQUE,
            client_id INTEGER REFERENCES clients(id),
            device TEXT,
            serial TEXT,
            issue TEXT,
            status TEXT,
            labor_price DOUBLE PRECISION DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            updated_at TEXT
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS invoices (
            id SERIAL PRIMARY KEY,
            series TEXT NOT NULL,
            number INTEGER NOT NULL,
            invoice_date TEXT NOT NULL,
            client_id INTEGER REFERENCES clients(id),
            type TEXT NOT NULL,
            vat_percent DOUBLE PRECISION DEFAULT 0,
            discount_percent DOUBLE PRECISION DEFAULT 0,
            notes TEXT,
            created_at TEXT,
            UNIQUE(series, number)
        );
        """)

        db_exec(db, """
        CREATE TABLE IF NOT EXISTS invoice_items (
            id SERIAL PRIMARY KEY,
            invoice_id INTEGER NOT NULL REFERENCES invoices(id) ON DELETE CASCADE,
            item_type TEXT NOT NULL,
            product_id INTEGER REFERENCES products(id),
            description TEXT NOT NULL,
            qty DOUBLE PRECISION NOT NULL,
            unit_price DOUBLE PRECISION NOT NULL,
            cost_price DOUBLE PRECISION DEFAULT 0
        );
        """)

    # ensure default admin exists
    df = db_query(db, "SELECT * FROM users WHERE username=%s" if db["type"] == "postgres" else "SELECT * FROM users WHERE username=?", (DEFAULT_ADMIN_USER,))
    if df.empty:
        salt = make_salt()
        ph = hash_password(DEFAULT_ADMIN_PASS, salt)
        ins = """
        INSERT INTO users (username, full_name, role, salt, pass_hash, active, created_at)
        VALUES (%s, %s, %s, %s, %s, 1, %s)
        """ if db["type"] == "postgres" else """
        INSERT INTO users (username, full_name, role, salt, pass_hash, active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """
        db_exec(db, ins, (DEFAULT_ADMIN_USER, "Administrator", "ADMIN", salt, ph, now_iso()))


# -------------------- AUTH UI --------------------
def login_box(db):
    st.sidebar.subheader("🔐 Login")
    u = st.sidebar.text_input("User", value="", placeholder="admin")
    p = st.sidebar.text_input("Parolă", value="", type="password")

    if st.sidebar.button("Autentificare", type="primary"):
        if not u.strip() or not p:
            st.sidebar.error("Completează user + parolă.")
            return None

        sel = "SELECT * FROM users WHERE username=%s AND active=1" if db["type"] == "postgres" else "SELECT * FROM users WHERE username=? AND active=1"
        df = db_query(db, sel, (u.strip(),))
        if df.empty:
            st.sidebar.error("User inexistent sau inactiv.")
            return None

        row = df.iloc[0].to_dict()
        if verify_password(p, row["salt"], row["pass_hash"]):
            st.session_state["auth"] = {"username": row["username"], "role": row["role"], "full_name": row.get("full_name") or row["username"]}
            st.sidebar.success(f"Salut, {st.session_state['auth']['full_name']} ({row['role']})")
            st.rerun()
        else:
            st.sidebar.error("Parolă greșită.")
            return None

    return st.session_state.get("auth")

def require_role(allowed_roles):
    auth = st.session_state.get("auth")
    if not auth:
        st.warning("Te rog autentifică-te în sidebar.")
        st.stop()
    if auth["role"] not in allowed_roles:
        st.error("Nu ai permisiuni pentru această secțiune.")
        st.stop()


# -------------------- BUSINESS HELPERS --------------------
def next_service_code(db):
    year = datetime.now().year
    prefix = f"SO-{year}-"
    q = "SELECT code FROM service_orders WHERE code LIKE %s ORDER BY id DESC LIMIT 1" if db["type"] == "postgres" else "SELECT code FROM service_orders WHERE code LIKE ? ORDER BY id DESC LIMIT 1"
    df = db_query(db, q, (prefix + "%",))
    if df.empty:
        return f"{prefix}0001"
    last = df.iloc[0]["code"]
    m = re.search(r"(\d+)$", last)
    n = int(m.group(1)) + 1 if m else 1
    return f"{prefix}{n:04d}"

def next_invoice_number(db, series):
    q = "SELECT MAX(number) AS mx FROM invoices WHERE series=%s" if db["type"] == "postgres" else "SELECT MAX(number) AS mx FROM invoices WHERE series=?"
    df = db_query(db, q, (series,))
    mx = df.iloc[0]["mx"]
    return int(mx) + 1 if pd.notna(mx) else 1

def money(x):
    return f"{float(x):,.2f} lei".replace(",", " ")

def compute_invoice_totals(items_df, vat_percent, discount_percent):
    subtotal = float((items_df["qty"] * items_df["unit_price"]).sum()) if not items_df.empty else 0.0
    discount = subtotal * (float(discount_percent) / 100.0)
    after_discount = subtotal - discount
    vat = after_discount * (float(vat_percent) / 100.0)
    total = after_discount + vat

    # profit estimat (doar pentru PRODUCT dacă avem cost_price)
    profit = 0.0
    if not items_df.empty:
        for _, r in items_df.iterrows():
            line_rev = float(r["qty"]) * float(r["unit_price"])
            line_cost = float(r.get("cost_price", 0.0)) * float(r["qty"])
            if r["item_type"] == "PRODUCT":
                profit += (line_rev - line_cost)
            else:
                profit += line_rev  # manopera ~ profit brut

    return {
        "subtotal": subtotal,
        "discount": discount,
        "after_discount": after_discount,
        "vat": vat,
        "total": total,
        "profit_est": profit - (profit * (float(discount_percent)/100.0)),  # aproximare simplă
    }


# -------------------- PDF INVOICE --------------------
def build_invoice_pdf(invoice, client, items, totals):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=36, leftMargin=36, topMargin=36, bottomMargin=36)
    styles = getSampleStyleSheet()
    elements = []

    title = f"{invoice['type']} {invoice['series']}-{invoice['number']}"
    elements.append(Paragraph(COMPANY_NAME, styles["Title"]))
    elements.append(Paragraph(f"CUI: {COMPANY_CUI} | {COMPANY_ADDR}", styles["BodyText"]))
    elements.append(Paragraph(f"Email: {COMPANY_EMAIL} | Tel: {COMPANY_PHONE}", styles["BodyText"]))
    elements.append(Spacer(1, 10))

    elements.append(Paragraph(f"<b>{title}</b>", styles["Heading1"]))
    elements.append(Paragraph(f"Data: {invoice['invoice_date']}", styles["BodyText"]))
    elements.append(Spacer(1, 8))

    c_name = client.get("name") if client else "—"
    c_addr = client.get("address") if client else ""
    c_phone = client.get("phone") if client else ""
    c_email = client.get("email") if client else ""
    elements.append(Paragraph("<b>Client</b>", styles["Heading2"]))
    elements.append(Paragraph(f"{c_name}", styles["BodyText"]))
    if c_addr: elements.append(Paragraph(f"Adresă: {c_addr}", styles["BodyText"]))
    if c_phone: elements.append(Paragraph(f"Telefon: {c_phone}", styles["BodyText"]))
    if c_email: elements.append(Paragraph(f"Email: {c_email}", styles["BodyText"]))
    elements.append(Spacer(1, 12))

    # Items table
    rows = [["#", "Descriere", "Cant.", "Preț", "Valoare"]]
    for i, it in enumerate(items, start=1):
        val = float(it["qty"]) * float(it["unit_price"])
        rows.append([str(i), it["description"], str(it["qty"]), money(it["unit_price"]), money(val)])

    tbl = Table(rows, colWidths=[22, 290, 60, 70, 80])
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.black),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0,0), (-1,-1), 9),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
    ]))
    elements.append(tbl)
    elements.append(Spacer(1, 12))

    # Totals
    trows = [
        ["Subtotal", money(totals["subtotal"])],
        ["Discount", money(totals["discount"])],
        ["Bază", money(totals["after_discount"])],
        [f"TVA ({invoice['vat_percent']}%)", money(totals["vat"])],
        ["TOTAL", money(totals["total"])],
    ]
    t = Table(trows, colWidths=[350, 120])
    t.setStyle(TableStyle([
        ("GRID", (0,0), (-1,-1), 0.5, colors.lightgrey),
        ("FONTSIZE", (0,0), (-1,-1), 10),
        ("BACKGROUND", (0, -1), (-1, -1), colors.whitesmoke),
    ]))
    elements.append(t)

    if invoice.get("notes"):
        elements.append(Spacer(1, 10))
        elements.append(Paragraph("<b>Note</b>", styles["Heading2"]))
        elements.append(Paragraph(invoice["notes"], styles["BodyText"]))

    doc.build(elements)
    buffer.seek(0)
    return buffer


# -------------------- APP START --------------------
db = get_db()
init_db(db)

st.title(APP_TITLE)
st.caption(f"DB: {'Postgres (Cloud)' if db['type']=='postgres' else 'SQLite (Local)'} | Login + Service + Depozit + Facturi PDF + Rapoarte")

# Sidebar auth
auth = login_box(db)
if auth:
    st.sidebar.caption(f"Logat: {auth['full_name']} ({auth['role']})")
    if st.sidebar.button("Logout"):
        st.session_state.pop("auth", None)
        st.rerun()

# Navigation
st.sidebar.divider()
menu = st.sidebar.radio(
    "Meniu",
    [
        "Dashboard",
        "Service (Fișe)",
        "Depozit (Produse)",
        "Stocuri (Mișcări)",
        "Facturi/Devize (PDF)",
        "Rapoarte",
        "Admin (Utilizatori)",
        "Setări/Export"
    ]
)

# -------------------- DASHBOARD --------------------
if menu == "Dashboard":
    require_role(["ADMIN", "MANAGER", "STAFF"])

    c1, c2, c3, c4 = st.columns(4)
    n_prod = db_query(db, "SELECT COUNT(*) AS n FROM products").iloc[0]["n"]
    n_cli = db_query(db, "SELECT COUNT(*) AS n FROM clients").iloc[0]["n"]
    n_so = db_query(db, "SELECT COUNT(*) AS n FROM service_orders").iloc[0]["n"]
    low = db_query(db, "SELECT COUNT(*) AS n FROM products WHERE min_stock>0 AND stock<=min_stock").iloc[0]["n"]

    c1.metric("Produse", int(n_prod))
    c2.metric("Clienți", int(n_cli))
    c3.metric("Fișe service", int(n_so))
    c4.metric("Alerte stoc minim", int(low))

    st.subheader("⚠️ Stoc minim")
    df_low = db_query(db, """
        SELECT id, sku, name, stock, min_stock, unit, location
        FROM products
        WHERE min_stock > 0 AND stock <= min_stock
        ORDER BY (min_stock - stock) DESC
        LIMIT 50
    """)
    if df_low.empty:
        st.info("Nicio alertă de stoc minim.")
    else:
        st.dataframe(df_low, use_container_width=True)

    st.subheader("🧾 Ultimele documente (Service + Facturi)")
    left, right = st.columns(2)
    with left:
        df_so = db_query(db, """
            SELECT id, code, status, device, created_at
            FROM service_orders
            ORDER BY id DESC
            LIMIT 10
        """)
        st.caption("Fișe service")
        st.dataframe(df_so, use_container_width=True)
    with right:
        df_inv = db_query(db, """
            SELECT id, type, series, number, invoice_date, created_at
            FROM invoices
            ORDER BY id DESC
            LIMIT 10
        """)
        st.caption("Facturi/Devize")
        st.dataframe(df_inv, use_container_width=True)


# -------------------- SERVICE ORDERS --------------------
elif menu == "Service (Fișe)":
    require_role(["ADMIN", "MANAGER", "STAFF"])

    st.subheader("🛠️ Fișe Service")

    df_clients = db_query(db, "SELECT id, name FROM clients ORDER BY name ASC")
    client_choices = [None] + (df_clients["id"].tolist() if not df_clients.empty else [])

    with st.expander("➕ Creează fișă service", expanded=True):
        col1, col2, col3 = st.columns(3)
        client_id = col1.selectbox("Client (opțional)", client_choices, format_func=lambda x: "—" if x is None else df_clients[df_clients.id==x]["name"].values[0])
        device = col2.text_input("Echipament", placeholder="ex: Laptop ASUS / Telefon Samsung")
        serial = col3.text_input("Serie/IMEI", placeholder="opțional")

        issue = st.text_area("Problemă raportată", placeholder="ex: nu pornește / display spart / încărcare lentă")
        labor_price = st.number_input("Manoperă estimată (lei)", min_value=0.0, value=0.0, step=10.0)
        notes = st.text_area("Note interne (opțional)")

        if st.button("Creează fișa", type="primary"):
            code = next_service_code(db)
            ins = """
            INSERT INTO service_orders (code, client_id, device, serial, issue, status, labor_price, notes, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """ if db["type"] == "postgres" else """
            INSERT INTO service_orders (code, client_id, device, serial, issue, status, labor_price, notes, created_at, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?)
            """
            db_exec(db, ins, (code, client_id, device, serial, issue, "NOU", float(labor_price), notes, now_iso(), now_iso()))
            st.success(f"Fișă creată: {code}")
            st.rerun()

    st.divider()

    st.subheader("📋 Listă fișe")
    f1, f2 = st.columns(2)
    status_filter = f1.selectbox("Status", ["TOATE", "NOU", "IN_LUCRU", "GATA", "LIVRAT"])
    search = f2.text_input("Caută (cod / device / serie)", placeholder="ex: SO-2026 sau Laptop")

    sql = "SELECT * FROM service_orders WHERE 1=1"
    params = []
    if status_filter != "TOATE":
        sql += " AND status=" + ("%s" if db["type"] == "postgres" else "?")
        params.append(status_filter)
    if search.strip():
        sql += " AND (code ILIKE %s OR device ILIKE %s OR serial ILIKE %s)" if db["type"] == "postgres" else " AND (code LIKE ? OR device LIKE ? OR serial LIKE ?)"
        s = f"%{search.strip()}%"
        params += [s, s, s]
    sql += " ORDER BY id DESC LIMIT 200"

    df = db_query(db, sql, tuple(params))
    st.dataframe(df[["id","code","status","device","serial","labor_price","created_at"]], use_container_width=True)

    if not df.empty:
        st.divider()
        st.subheader("✏️ Actualizează fișă + consum piese din depozit")

        so_id = st.selectbox("Alege fișa", df["id"].tolist(), format_func=lambda x: f"{df[df.id==x]['code'].values[0]} — {df[df.id==x]['device'].values[0]}")
        so = db_query(db, "SELECT * FROM service_orders WHERE id=" + ("%s" if db["type"]=="postgres" else "?"), (so_id,)).iloc[0].to_dict()

        c1, c2, c3 = st.columns(3)
        new_status = c1.selectbox("Status nou", ["NOU", "IN_LUCRU", "GATA", "LIVRAT"], index=["NOU","IN_LUCRU","GATA","LIVRAT"].index(so["status"]))
        new_labor = c2.number_input("Manoperă (lei)", min_value=0.0, value=float(so["labor_price"]), step=10.0)
        new_notes = c3.text_input("Note scurte", value=(so.get("notes") or ""))

        if st.button("Salvează fișa", type="primary"):
            upd = """
            UPDATE service_orders SET status=%s, labor_price=%s, notes=%s, updated_at=%s WHERE id=%s
            """ if db["type"]=="postgres" else """
            UPDATE service_orders SET status=?, labor_price=?, notes=?, updated_at=? WHERE id=?
            """
            db_exec(db, upd, (new_status, float(new_labor), new_notes, now_iso(), so_id))
            st.success("Fișa a fost actualizată.")
            st.rerun()

        st.markdown("### 🔧 Consum piese din depozit (SERVICE_USE)")
        dfp = db_query(db, "SELECT id, name, sku, stock, unit, sale_price, purchase_price FROM products ORDER BY name ASC")
        if dfp.empty:
            st.info("Nu ai produse în depozit.")
        else:
            pid = st.selectbox("Produs folosit", dfp["id"].tolist(), format_func=lambda x: f"{dfp[dfp.id==x]['name'].values[0]} ({dfp[dfp.id==x]['sku'].values[0] or 'no-sku'})")
            qty = st.number_input("Cantitate folosită", min_value=0.0, value=1.0, step=1.0)
            note = st.text_input("Notă", value=f"Consum service {so['code']}")

            if st.button("Scade din stoc", type="secondary"):
                cur = float(dfp[dfp.id==pid]["stock"].values[0])
                if qty <= 0:
                    st.error("Cantitatea trebuie > 0.")
                elif qty > cur:
                    st.error("Stoc insuficient.")
                else:
                    new_stock = cur - float(qty)
                    db_exec(db, "UPDATE products SET stock=" + ("%s" if db["type"]=="postgres" else "?") + " WHERE id=" + ("%s" if db["type"]=="postgres" else "?"),
                            (new_stock, pid))
                    ins = """
                    INSERT INTO stock_moves (product_id, move_type, qty, note, ref_doc, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """ if db["type"]=="postgres" else """
                    INSERT INTO stock_moves (product_id, move_type, qty, note, ref_doc, created_at)
                    VALUES (?,?,?,?,?,?)
                    """
                    db_exec(db, ins, (pid, "SERVICE_USE", float(qty), note, so["code"], now_iso()))
                    st.success(f"Stoc actualizat: {new_stock}")
                    st.rerun()


# -------------------- PRODUCTS (DEPOZIT) --------------------
elif menu == "Depozit (Produse)":
    require_role(["ADMIN", "MANAGER", "STAFF"])

    st.subheader("📦 Depozit — Produse")

    with st.expander("➕ Adaugă produs", expanded=True):
        c1, c2, c3, c4 = st.columns(4)
        sku = c1.text_input("SKU (unic)", placeholder="ex: P-1001")
        name = c2.text_input("Denumire*", placeholder="ex: Display iPhone 11")
        category = c3.text_input("Categorie", placeholder="ex: Piese / Consumabile")
        unit = c4.text_input("Unitate", value="buc")

        c5, c6, c7, c8 = st.columns(4)
        purchase_price = c5.number_input("Cost achiziție (lei)", min_value=0.0, value=0.0, step=1.0)
        sale_price = c6.number_input("Preț vânzare (lei)", min_value=0.0, value=0.0, step=1.0)
        stock = c7.number_input("Stoc inițial", min_value=0.0, value=0.0, step=1.0)
        min_stock = c8.number_input("Stoc minim", min_value=0.0, value=0.0, step=1.0)

        loc = st.text_input("Locație (raft/depozit)", placeholder="ex: R1-A2")

        if st.button("Salvează produs", type="primary"):
            if not name.strip():
                st.error("Denumirea e obligatorie.")
            else:
                ins = """
                INSERT INTO products (sku,name,category,unit,purchase_price,sale_price,stock,min_stock,location,created_at)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                """ if db["type"]=="postgres" else """
                INSERT INTO products (sku,name,category,unit,purchase_price,sale_price,stock,min_stock,location,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
                """
                try:
                    db_exec(db, ins, (sku.strip() or None, name.strip(), category.strip(), unit.strip() or "buc",
                                      float(purchase_price), float(sale_price), float(stock), float(min_stock), loc.strip(), now_iso()))
                    st.success("Produs adăugat.")
                    st.rerun()
                except Exception:
                    st.error("Eroare la salvare (probabil SKU duplicat).")

    st.divider()
    st.subheader("Listă produse")
    s1, s2, s3 = st.columns(3)
    search = s1.text_input("Caută (SKU/nume)", placeholder="ex: display / P-1001")
    only_low = s2.checkbox("Doar stoc minim")
    cat = s3.text_input("Categorie (filtru)")

    sql = "SELECT * FROM products WHERE 1=1"
    params = []
    if search.strip():
        if db["type"]=="postgres":
            sql += " AND (COALESCE(sku,'') ILIKE %s OR name ILIKE %s)"
            params += [f"%{search.strip()}%", f"%{search.strip()}%"]
        else:
            sql += " AND (COALESCE(sku,'') LIKE ? OR name LIKE ?)"
            params += [f"%{search.strip()}%", f"%{search.strip()}%"]
    if cat.strip():
        if db["type"]=="postgres":
            sql += " AND category ILIKE %s"
            params += [f"%{cat.strip()}%"]
        else:
            sql += " AND category LIKE ?"
            params += [f"%{cat.strip()}%"]
    if only_low:
        sql += " AND min_stock>0 AND stock<=min_stock"

    sql += " ORDER BY id DESC LIMIT 500"
    dfp = db_query(db, sql, tuple(params))
    st.dataframe(dfp[["id","sku","name","category","stock","unit","min_stock","location","purchase_price","sale_price"]], use_container_width=True)


# -------------------- STOCK MOVES --------------------
elif menu == "Stocuri (Mișcări)":
    require_role(["ADMIN", "MANAGER", "STAFF"])
    st.subheader("🔁 Mișcări stoc (IN / OUT / ADJ)")

    dfp = db_query(db, "SELECT id, name, sku, stock, unit FROM products ORDER BY name ASC")
    if dfp.empty:
        st.info("Adaugă produse întâi.")
        st.stop()

    pid = st.selectbox("Produs", dfp["id"].tolist(), format_func=lambda x: f"{dfp[dfp.id==x]['name'].values[0]} ({dfp[dfp.id==x]['sku'].values[0] or 'no-sku'})")
    cur_stock = float(dfp[dfp.id==pid]["stock"].values[0])
    unit = dfp[dfp.id==pid]["unit"].values[0]
    st.caption(f"Stoc curent: **{cur_stock} {unit}**")

    t = st.radio("Tip", ["IN (Intrare)", "OUT (Ieșire)", "ADJ (Ajustare stoc nou)"], horizontal=True)
    qty = st.number_input("Cantitate", min_value=0.0, value=1.0, step=1.0)
    note = st.text_input("Notă", placeholder="ex: recepție / retur / inventar")

    if st.button("Aplică", type="primary"):
        if qty <= 0:
            st.error("Cantitatea > 0.")
            st.stop()

        if t.startswith("IN"):
            new_stock = cur_stock + float(qty)
            mtype = "IN"
            mv_qty = float(qty)
        elif t.startswith("OUT"):
            if qty > cur_stock:
                st.error("Stoc insuficient.")
                st.stop()
            new_stock = cur_stock - float(qty)
            mtype = "OUT"
            mv_qty = float(qty)
        else:
            new_stock = float(qty)
            mtype = "ADJ"
            mv_qty = float(qty)

        db_exec(db, "UPDATE products SET stock=" + ("%s" if db["type"]=="postgres" else "?") + " WHERE id=" + ("%s" if db["type"]=="postgres" else "?"),
                (new_stock, pid))

        ins = """
        INSERT INTO stock_moves (product_id, move_type, qty, note, ref_doc, created_at)
        VALUES (%s,%s,%s,%s,%s,%s)
        """ if db["type"]=="postgres" else """
        INSERT INTO stock_moves (product_id, move_type, qty, note, ref_doc, created_at)
        VALUES (?,?,?,?,?,?)
        """
        db_exec(db, ins, (pid, mtype, mv_qty, note.strip(), None, now_iso()))
        st.success(f"Stoc nou: {new_stock} {unit}")
        st.rerun()

    st.divider()
    st.subheader("📜 Istoric (ultimele 200)")
    dfm = db_query(db, """
        SELECT sm.id, sm.created_at, p.sku, p.name, sm.move_type, sm.qty, sm.ref_doc, sm.note
        FROM stock_moves sm
        JOIN products p ON p.id = sm.product_id
        ORDER BY sm.id DESC
        LIMIT 200
    """)
    st.dataframe(dfm, use_container_width=True)


# -------------------- INVOICES / QUOTES --------------------
elif menu == "Facturi/Devize (PDF)":
    require_role(["ADMIN", "MANAGER"])

    st.subheader("🧾 Facturi / Devize (PDF)")
    st.caption("Poți face DEVIZ (service), FACTURA, BON. Produsele scad din stoc automat la FACTURA/BON.")

    # Clients
    dfc = db_query(db, "SELECT id, name, phone, email, address FROM clients ORDER BY name ASC")
    if dfc.empty:
        st.info("Adaugă clienți în Setări/Export -> sau creează rapid mai jos.")
    with st.expander("➕ Client rapid (opțional)", expanded=False):
        n = st.text_input("Nume client*", key="q_client_name")
        ph = st.text_input("Telefon", key="q_client_phone")
        em = st.text_input("Email", key="q_client_email")
        ad = st.text_input("Adresă", key="q_client_addr")
        if st.button("Salvează client"):
            if not n.strip():
                st.error("Numele e obligatoriu.")
            else:
                ins = """
                INSERT INTO clients (name, phone, email, address, notes, created_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                """ if db["type"]=="postgres" else """
                INSERT INTO clients (name, phone, email, address, notes, created_at)
                VALUES (?,?,?,?,?,?)
                """
                db_exec(db, ins, (n.strip(), ph.strip(), em.strip(), ad.strip(), "", now_iso()))
                st.success("Client adăugat.")
                st.rerun()

    dfc = db_query(db, "SELECT id, name, phone, email, address FROM clients ORDER BY name ASC")
    client_id = st.selectbox("Client (opțional)", [None] + dfc["id"].tolist(),
                             format_func=lambda x: "—" if x is None else dfc[dfc.id==x]["name"].values[0])

    # Invoice header
    c1, c2, c3, c4 = st.columns(4)
    inv_type = c1.selectbox("Tip document", ["DEVIZ", "FACTURA", "BON"])
    series = c2.text_input("Serie", value="WC")
    inv_date = c3.date_input("Data", value=date.today())
    vat_percent = c4.number_input("TVA (%)", min_value=0.0, value=0.0, step=1.0)

    discount_percent = st.number_input("Discount (%)", min_value=0.0, value=0.0, step=1.0)
    notes = st.text_area("Note (opțional)")

    # Items builder
    st.markdown("### 🧱 Linii document (Produse + Manoperă)")
    if "cart" not in st.session_state:
        st.session_state["cart"] = []

    dfp = db_query(db, "SELECT id, sku, name, stock, unit, sale_price, purchase_price FROM products ORDER BY name ASC")

    colA, colB, colC = st.columns([2, 1, 1])
    item_kind = colA.selectbox("Tip linie", ["PRODUCT", "LABOR"])
    if item_kind == "PRODUCT":
        if dfp.empty:
            st.warning("Nu ai produse.")
        else:
            pid = colA.selectbox("Produs", dfp["id"].tolist(),
                                 format_func=lambda x: f"{dfp[dfp.id==x]['name'].values[0]} ({dfp[dfp.id==x]['sku'].values[0] or 'no-sku'})")
            qty = colB.number_input("Cant.", min_value=0.0, value=1.0, step=1.0)
            unit_price = colC.number_input("Preț", min_value=0.0,
                                           value=float(dfp[dfp.id==pid]["sale_price"].values[0]),
                                           step=1.0)
            desc = dfp[dfp.id==pid]["name"].values[0]
            cost_price = float(dfp[dfp.id==pid]["purchase_price"].values[0])

            if st.button("Adaugă produs"):
                st.session_state["cart"].append({
                    "item_type": "PRODUCT",
                    "product_id": int(pid),
                    "description": desc,
                    "qty": float(qty),
                    "unit_price": float(unit_price),
                    "cost_price": float(cost_price),
                })
                st.rerun()
    else:
        desc = colA.text_input("Descriere manoperă", value="Manoperă service")
        qty = colB.number_input("Ore / unități", min_value=0.0, value=1.0, step=0.5)
        unit_price = colC.number_input("Tarif", min_value=0.0, value=150.0, step=10.0)
        if st.button("Adaugă manoperă"):
            st.session_state["cart"].append({
                "item_type": "LABOR",
                "product_id": None,
                "description": desc.strip() or "Manoperă",
                "qty": float(qty),
                "unit_price": float(unit_price),
                "cost_price": 0.0,
            })
            st.rerun()

    if st.session_state["cart"]:
        cart_df = pd.DataFrame(st.session_state["cart"])
        cart_df["valoare"] = cart_df["qty"] * cart_df["unit_price"]
        st.dataframe(cart_df[["item_type","description","qty","unit_price","valoare"]], use_container_width=True)

        if st.button("🧹 Golește", type="secondary"):
            st.session_state["cart"] = []
            st.rerun()

    # Create invoice + PDF
    st.divider()
    if st.button("✅ Generează document + PDF", type="primary"):
        items_df = pd.DataFrame(st.session_state["cart"]) if st.session_state["cart"] else pd.DataFrame(columns=["item_type","product_id","description","qty","unit_price","cost_price"])
        if items_df.empty:
            st.error("Adaugă cel puțin o linie.")
            st.stop()

        # if FACTURA/BON, validate stock for product lines
        if inv_type in ["FACTURA", "BON"]:
            for _, r in items_df.iterrows():
                if r["item_type"] == "PRODUCT":
                    pid = int(r["product_id"])
                    need = float(r["qty"])
                    cur = float(dfp[dfp.id==pid]["stock"].values[0])
                    if need > cur:
                        st.error(f"Stoc insuficient pentru {dfp[dfp.id==pid]['name'].values[0]} (ai {cur}, ceri {need}).")
                        st.stop()

        # Create invoice header
        number = next_invoice_number(db, series)
        ins_inv = """
        INSERT INTO invoices (series, number, invoice_date, client_id, type, vat_percent, discount_percent, notes, created_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """ if db["type"]=="postgres" else """
        INSERT INTO invoices (series, number, invoice_date, client_id, type, vat_percent, discount_percent, notes, created_at)
        VALUES (?,?,?,?,?,?,?,?,?)
        """
        db_exec(db, ins_inv, (series.strip(), int(number), str(inv_date), client_id, inv_type, float(vat_percent), float(discount_percent), notes.strip(), now_iso()))

        # fetch invoice id
        if db["type"] == "postgres":
            df_last = db_query(db, "SELECT id FROM invoices WHERE series=%s AND number=%s", (series.strip(), int(number)))
        else:
            df_last = db_query(db, "SELECT id FROM invoices WHERE series=? AND number=?", (series.strip(), int(number)))
        inv_id = int(df_last.iloc[0]["id"])

        # insert items
        ins_it = """
        INSERT INTO invoice_items (invoice_id, item_type, product_id, description, qty, unit_price, cost_price)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        """ if db["type"]=="postgres" else """
        INSERT INTO invoice_items (invoice_id, item_type, product_id, description, qty, unit_price, cost_price)
        VALUES (?,?,?,?,?,?,?)
        """
        for _, r in items_df.iterrows():
            db_exec(db, ins_it, (inv_id, r["item_type"], r["product_id"], r["description"], float(r["qty"]), float(r["unit_price"]), float(r.get("cost_price", 0.0))))

        # if FACTURA/BON: decrease stock and add stock_moves
        if inv_type in ["FACTURA", "BON"]:
            for _, r in items_df.iterrows():
                if r["item_type"] == "PRODUCT":
                    pid = int(r["product_id"])
                    qty = float(r["qty"])
                    cur = float(dfp[dfp.id==pid]["stock"].values[0])
                    new_stock = cur - qty
                    db_exec(db, "UPDATE products SET stock=" + ("%s" if db["type"]=="postgres" else "?") + " WHERE id=" + ("%s" if db["type"]=="postgres" else "?"),
                            (new_stock, pid))
                    ins_mv = """
                    INSERT INTO stock_moves (product_id, move_type, qty, note, ref_doc, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    """ if db["type"]=="postgres" else """
                    INSERT INTO stock_moves (product_id, move_type, qty, note, ref_doc, created_at)
                    VALUES (?,?,?,?,?,?)
                    """
                    db_exec(db, ins_mv, (pid, "SALE", qty, f"Vânzare {inv_type}", f"{series}-{number}", now_iso()))

        # Build PDF
        client = None
        if client_id is not None and not dfc.empty:
            client = dfc[dfc.id == client_id].iloc[0].to_dict()

        invoice = {
            "type": inv_type,
            "series": series.strip(),
            "number": int(number),
            "invoice_date": str(inv_date),
            "vat_percent": float(vat_percent),
            "discount_percent": float(discount_percent),
            "notes": notes.strip()
        }

        totals = compute_invoice_totals(items_df, vat_percent, discount_percent)
        pdf_buf = build_invoice_pdf(invoice, client, items_df.to_dict(orient="records"), totals)

        st.success(f"Document creat: {inv_type} {series}-{number}")
        st.download_button(
            "⬇️ Descarcă PDF",
            data=pdf_buf,
            file_name=f"{inv_type}_{series}-{number}.pdf",
            mime="application/pdf"
        )

        # reset cart
        st.session_state["cart"] = []


# -------------------- REPORTS --------------------
elif menu == "Rapoarte":
    require_role(["ADMIN", "MANAGER"])

    st.subheader("📊 Rapoarte (Service + Depozit)")
    st.caption("Profitul este estimat din cost preluat din produse (purchase_price) + manopera ca venit brut.")

    # date range
    c1, c2 = st.columns(2)
    start = c1.date_input("De la", value=date.today().replace(day=1))
    end = c2.date_input("Până la", value=date.today())

    # invoices + items
    if db["type"] == "postgres":
        inv = db_query(db, """
            SELECT i.*, c.name AS client_name
            FROM invoices i
            LEFT JOIN clients c ON c.id=i.client_id
            WHERE invoice_date BETWEEN %s AND %s
            ORDER BY i.id DESC
        """, (str(start), str(end)))
        items = db_query(db, """
            SELECT it.*, i.series, i.number, i.invoice_date, i.type
            FROM invoice_items it
            JOIN invoices i ON i.id=it.invoice_id
            WHERE i.invoice_date BETWEEN %s AND %s
        """, (str(start), str(end)))
    else:
        inv = db_query(db, """
            SELECT i.*, c.name AS client_name
            FROM invoices i
            LEFT JOIN clients c ON c.id=i.client_id
            WHERE invoice_date BETWEEN ? AND ?
            ORDER BY i.id DESC
        """, (str(start), str(end)))
        items = db_query(db, """
            SELECT it.*, i.series, i.number, i.invoice_date, i.type
            FROM invoice_items it
            JOIN invoices i ON i.id=it.invoice_id
            WHERE i.invoice_date BETWEEN ? AND ?
        """, (str(start), str(end)))

    if inv.empty:
        st.info("Nu există documente în perioada aleasă.")
    else:
        # compute totals per invoice
        if not items.empty:
            items["line_total"] = items["qty"].astype(float) * items["unit_price"].astype(float)
            items["line_cost"] = items["qty"].astype(float) * items["cost_price"].astype(float)

        # Overview metrics
        total_rev = float(items["line_total"].sum()) if not items.empty else 0.0
        total_cost = float(items.loc[items["item_type"]=="PRODUCT", "line_cost"].sum()) if not items.empty else 0.0
        labor_rev = float(items.loc[items["item_type"]=="LABOR", "line_total"].sum()) if not items.empty else 0.0
        profit_est = (total_rev - total_cost)  # labor treated as revenue; cost 0

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Venit brut", money(total_rev))
        c2.metric("Cost marfă (est.)", money(total_cost))
        c3.metric("Manoperă (venit)", money(labor_rev))
        c4.metric("Profit estimat", money(profit_est))

        st.divider()

        # Revenue over time
        st.subheader("📈 Venit pe zile")
        if not items.empty:
            daily = items.groupby("invoice_date")["line_total"].sum().reset_index()
            daily = daily.sort_values("invoice_date")
            st.line_chart(daily.set_index("invoice_date"))
        else:
            st.info("Nu există item-uri.")

        st.subheader("🏆 Top produse (valoare)")
        if not items.empty:
            top_prod = items[items["item_type"]=="PRODUCT"].groupby("description")["line_total"].sum().reset_index().sort_values("line_total", ascending=False).head(15)
            st.dataframe(top_prod, use_container_width=True)

        st.subheader("🏆 Top clienți (valoare)")
        if not inv.empty and not items.empty:
            # join for client name
            inv_small = inv[["id","client_name"]].rename(columns={"id":"invoice_id"})
            itj = items.merge(inv_small, on="invoice_id", how="left")
            top_clients = itj.groupby(itj["client_name"].fillna("—"))["line_total"].sum().reset_index().sort_values("line_total", ascending=False).head(15)
            st.dataframe(top_clients, use_container_width=True)

        st.divider()
        st.subheader("📦 Rotație stoc (ultimele 30 zile)")
        # rotation = sales qty / average stock approx -> simplified
        if db["type"] == "postgres":
            mv = db_query(db, """
                SELECT move_type, qty, created_at
                FROM stock_moves
                WHERE created_at >= %s
            """, (str(date.today().replace(day=max(1, date.today().day-30))),))
        else:
            mv = db_query(db, """
                SELECT move_type, qty, created_at
                FROM stock_moves
                WHERE created_at >= ?
            """, (str(date.today().replace(day=max(1, date.today().day-30))),))

        if mv.empty:
            st.info("Nu există mișcări recente.")
        else:
            mv["qty"] = mv["qty"].astype(float)
            st.write("Mișcări (sumar):")
            st.dataframe(mv.groupby("move_type")["qty"].sum().reset_index(), use_container_width=True)

        st.divider()
        st.subheader("⚠️ Alerte stoc minim")
        low = db_query(db, "SELECT sku,name,stock,min_stock,unit,location FROM products WHERE min_stock>0 AND stock<=min_stock ORDER BY (min_stock-stock) DESC LIMIT 100")
        if low.empty:
            st.info("Nicio alertă.")
        else:
            st.dataframe(low, use_container_width=True)


# -------------------- USERS (ADMIN) --------------------
elif menu == "Admin (Utilizatori)":
    require_role(["ADMIN"])
    st.subheader("👤 Admin — Utilizatori & Roluri")

    st.warning("După primul login, schimbă parola lui admin.")
    st.caption("Roluri: ADMIN (tot), MANAGER (facturi/rapoarte), STAFF (service+stoc).")

    dfu = db_query(db, "SELECT id, username, full_name, role, active, created_at FROM users ORDER BY id DESC")
    st.dataframe(dfu, use_container_width=True)

    st.divider()
    st.subheader("➕ Creează user")
    c1, c2, c3, c4 = st.columns(4)
    username = c1.text_input("Username", placeholder="ex: ana")
    full_name = c2.text_input("Nume complet", placeholder="ex: Ana Popescu")
    role = c3.selectbox("Rol", ["STAFF", "MANAGER", "ADMIN"])
    password = c4.text_input("Parolă", type="password")

    if st.button("Creează user", type="primary"):
        if not username.strip() or not password:
            st.error("Username + parolă sunt obligatorii.")
        else:
            salt = make_salt()
            ph = hash_password(password, salt)
            ins = """
            INSERT INTO users (username, full_name, role, salt, pass_hash, active, created_at)
            VALUES (%s,%s,%s,%s,%s,1,%s)
            """ if db["type"]=="postgres" else """
            INSERT INTO users (username, full_name, role, salt, pass_hash, active, created_at)
            VALUES (?,?,?,?,?,1,?)
            """
            try:
                db_exec(db, ins, (username.strip(), full_name.strip(), role, salt, ph, now_iso()))
                st.success("User creat.")
                st.rerun()
            except Exception:
                st.error("Nu am putut crea user (probabil username duplicat).")

    st.divider()
    st.subheader("🔁 Schimbă parola / active")
    if not dfu.empty:
        uid = st.selectbox("Alege user", dfu["id"].tolist(), format_func=lambda x: f"{dfu[dfu.id==x]['username'].values[0]} ({dfu[dfu.id==x]['role'].values[0]})")
        new_pass = st.text_input("Parolă nouă", type="password")
        new_role = st.selectbox("Rol nou", ["STAFF","MANAGER","ADMIN"], index=["STAFF","MANAGER","ADMIN"].index(dfu[dfu.id==uid]["role"].values[0]))
        new_active = st.checkbox("Activ", value=bool(dfu[dfu.id==uid]["active"].values[0]))

        if st.button("Salvează modificări", type="secondary"):
            # update role/active
            upd = """
            UPDATE users SET role=%s, active=%s WHERE id=%s
            """ if db["type"]=="postgres" else """
            UPDATE users SET role=?, active=? WHERE id=?
            """
            db_exec(db, upd, (new_role, 1 if new_active else 0, uid))

            # update password optional
            if new_pass:
                salt = make_salt()
                ph = hash_password(new_pass, salt)
                upd2 = """
                UPDATE users SET salt=%s, pass_hash=%s WHERE id=%s
                """ if db["type"]=="postgres" else """
                UPDATE users SET salt=?, pass_hash=? WHERE id=?
                """
                db_exec(db, upd2, (salt, ph, uid))

            st.success("Actualizat.")
            st.rerun()


# -------------------- SETTINGS / EXPORT --------------------
elif menu == "Setări/Export":
    require_role(["ADMIN", "MANAGER", "STAFF"])
    st.subheader("⚙️ Setări / Export")

    st.info("Pentru Cloud DB (Supabase/Postgres): setează `DATABASE_URL` în Streamlit Cloud → Settings → Secrets.")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("### Export CSV")
        if st.button("Export produse"):
            df = db_query(db, "SELECT * FROM products ORDER BY id DESC")
            st.download_button("Download produse.csv", df.to_csv(index=False).encode("utf-8"), "produse.csv", "text/csv")

        if st.button("Export clienți"):
            df = db_query(db, "SELECT * FROM clients ORDER BY id DESC")
            st.download_button("Download clienti.csv", df.to_csv(index=False).encode("utf-8"), "clienti.csv", "text/csv")

        if st.button("Export mișcări stoc"):
            df = db_query(db, """
                SELECT sm.*, p.sku, p.name AS product
                FROM stock_moves sm JOIN products p ON p.id=sm.product_id
                ORDER BY sm.id DESC
            """)
            st.download_button("Download stoc_moves.csv", df.to_csv(index=False).encode("utf-8"), "stoc_moves.csv", "text/csv")

        if st.button("Export fișe service"):
            df = db_query(db, "SELECT * FROM service_orders ORDER BY id DESC")
            st.download_button("Download service_orders.csv", df.to_csv(index=False).encode("utf-8"), "service_orders.csv", "text/csv")

    with col2:
        st.markdown("### Clienți (rapid)")
        with st.form("add_client"):
            n = st.text_input("Nume*")
            ph = st.text_input("Telefon")
            em = st.text_input("Email")
            ad = st.text_input("Adresă")
            notes = st.text_area("Note")
            ok = st.form_submit_button("Salvează client")
        if ok:
            if not n.strip():
                st.error("Nume obligatoriu.")
            else:
                ins = """
                INSERT INTO clients (name, phone, email, address, notes, created_at)
                VALUES (%s,%s,%s,%s,%s,%s)
                """ if db["type"]=="postgres" else """
                INSERT INTO clients (name, phone, email, address, notes, created_at)
                VALUES (?,?,?,?,?,?)
                """
                db_exec(db, ins, (n.strip(), ph.strip(), em.strip(), ad.strip(), notes.strip(), now_iso()))
                st.success("Client adăugat.")
                st.rerun()

    st.divider()
    require_role(["ADMIN"])
    st.warning("Reset șterge TOT. Folosește doar la test.")
    if st.button("🧨 RESET TOTAL (DB)", type="primary"):
        # order matters (FK)
        for tbl in ["invoice_items","invoices","stock_moves","service_orders","products","clients","users"]:
            try:
                db_exec(db, f"DELETE FROM {tbl}")
            except Exception:
                pass
        # recreate admin
        init_db(db)
        st.success("Reset complet făcut. Admin re-creat.")
        st.rerun()
