"""
SQLite database setup and operations for Earnings Intelligence Engine.
Maintains permanent historical data — never deletes.
"""
import sqlite3
import os
from config import DB_PATH


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to run multiple times."""
    conn = get_conn()
    c = conn.cursor()

    # ── Company Profile ───────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS company_profile (
        symbol          TEXT PRIMARY KEY,
        name            TEXT,
        sector          TEXT,
        industry        TEXT,
        exchange        TEXT DEFAULT 'NSE',
        isin            TEXT,
        nse_code        TEXT,
        bse_code        TEXT,
        market_cap_cr   REAL,
        tier            TEXT DEFAULT 'D',
        tier_updated    TEXT,
        created_at      TEXT DEFAULT (datetime('now')),
        updated_at      TEXT DEFAULT (datetime('now'))
    )""")

    # ── Earnings Calendar ─────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS earnings_calendar (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT NOT NULL,
        company_name    TEXT,
        result_date     TEXT NOT NULL,
        quarter         TEXT,
        fiscal_year     TEXT,
        exchange        TEXT DEFAULT 'NSE',
        board_meeting_time TEXT,
        concall_time    TEXT,
        concall_details TEXT,
        status          TEXT DEFAULT 'upcoming',
        tier            TEXT DEFAULT 'D',
        created_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, result_date, quarter)
    )""")

    # ── Earnings Financials ───────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS earnings_financials (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol              TEXT NOT NULL,
        quarter             TEXT NOT NULL,
        fiscal_year         TEXT NOT NULL,
        result_date         TEXT,
        result_type         TEXT DEFAULT 'Consolidated',
        revenue_cr          REAL,
        ebitda_cr           REAL,
        ebitda_margin_pct   REAL,
        pat_cr              REAL,
        eps                 REAL,
        cash_cr             REAL,
        debt_cr             REAL,
        net_debt_cr         REAL,
        operating_cf_cr     REAL,
        free_cf_cr          REAL,
        capex_cr            REAL,
        dividend_per_share  REAL,
        book_value          REAL,
        order_book_cr       REAL,
        employee_count      INTEGER,
        guidance_text       TEXT,
        pdf_url             TEXT,
        pdf_path            TEXT,
        source              TEXT DEFAULT 'NSE',
        raw_data            TEXT,
        created_at          TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, quarter, fiscal_year)
    )""")

    # ── AI Analysis ───────────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS earnings_ai_analysis (
        id                      INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol                  TEXT NOT NULL,
        quarter                 TEXT NOT NULL,
        fiscal_year             TEXT NOT NULL,
        executive_summary       TEXT,
        auditor_observations    TEXT,
        qoq_analysis            TEXT,
        yoy_analysis            TEXT,
        bullish_factors         TEXT,
        bearish_factors         TEXT,
        trading_notes           TEXT,
        financial_score         REAL,
        growth_score            REAL,
        quality_score           REAL,
        balance_sheet_score     REAL,
        cashflow_score          REAL,
        consistency_score       REAL,
        technical_score         REAL,
        overall_score           REAL,
        classification          TEXT,
        marathi_summary         TEXT,
        created_at              TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, quarter, fiscal_year)
    )""")

    # ── Market Reaction ───────────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS market_reaction (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT NOT NULL,
        quarter         TEXT NOT NULL,
        result_date     TEXT,
        prev_close      REAL,
        gap_pct         REAL,
        open_price      REAL,
        high_price      REAL,
        low_price       REAL,
        close_price     REAL,
        volume          INTEGER,
        delivery_pct    REAL,
        day1_return_pct REAL,
        day3_return_pct REAL,
        day5_return_pct REAL,
        created_at      TEXT DEFAULT (datetime('now')),
        UNIQUE(symbol, quarter)
    )""")

    # ── Company Memory (long-term trends) ─────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS company_memory (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT NOT NULL,
        memory_type     TEXT NOT NULL,
        observation     TEXT,
        quarter         TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    )""")

    # ── Weekly Research Reports ───────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS weekly_reports (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start      TEXT NOT NULL,
        week_end        TEXT NOT NULL,
        report_text     TEXT,
        sector_scores   TEXT,
        top10_best      TEXT,
        top10_worst     TEXT,
        high_conviction TEXT,
        vcp_intel       TEXT,
        created_at      TEXT DEFAULT (datetime('now'))
    )""")

    # ── Result Detection Log ──────────────────────────────────────────────────
    c.execute("""
    CREATE TABLE IF NOT EXISTS result_detection_log (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol          TEXT,
        announcement_id TEXT UNIQUE,
        detected_at     TEXT,
        source          TEXT,
        announcement_url TEXT,
        processed       INTEGER DEFAULT 0,
        created_at      TEXT DEFAULT (datetime('now'))
    )""")

    # Migrate existing DB — add result_type if missing
    try:
        c.execute("ALTER TABLE earnings_financials ADD COLUMN result_type TEXT DEFAULT 'Consolidated'")
    except Exception:
        pass  # column already exists

    conn.commit()
    conn.close()
    print("[DB] All tables initialized successfully")


def upsert_company(symbol, **kwargs):
    conn = get_conn()
    kwargs['symbol'] = symbol
    kwargs['updated_at'] = "datetime('now')"
    cols = ', '.join(kwargs.keys())
    placeholders = ', '.join(['?' for _ in kwargs])
    updates = ', '.join([f"{k}=excluded.{k}" for k in kwargs if k != 'symbol'])
    conn.execute(
        f"INSERT INTO company_profile ({cols}) VALUES ({placeholders}) "
        f"ON CONFLICT(symbol) DO UPDATE SET {updates}",
        list(kwargs.values())
    )
    conn.commit()
    conn.close()


def get_upcoming_earnings(days_ahead=7):
    conn = get_conn()
    from datetime import datetime, timedelta
    today = datetime.now().strftime('%Y-%m-%d')
    future = (datetime.now() + timedelta(days=days_ahead)).strftime('%Y-%m-%d')
    rows = conn.execute(
        "SELECT * FROM earnings_calendar WHERE result_date BETWEEN ? AND ? ORDER BY result_date, tier",
        (today, future)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_company_history(symbol, limit=8):
    """Get last N quarters of financials for a company."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM earnings_financials WHERE symbol=? ORDER BY fiscal_year DESC, quarter DESC LIMIT ?",
        (symbol, limit)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def mark_announcement_processed(announcement_id):
    conn = get_conn()
    conn.execute(
        "UPDATE result_detection_log SET processed=1 WHERE announcement_id=?",
        (announcement_id,)
    )
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db()
