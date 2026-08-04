"""
VCP Scanner Integration
Reads Tier A/B companies from BigQuery VCP tables
and syncs them to the Earnings Engine SQLite database.
"""
import datetime as dt
import sqlite3
from config import GCP_PROJECT_ID, BQ_DATASET, BQ_LOCATION, TIER_A_LOOKBACK_DAYS, TIER_B_LOOKBACK_DAYS, DB_PATH

try:
    from google.cloud import bigquery
    BQ_AVAILABLE = True
except ImportError:
    BQ_AVAILABLE = False
    print("[VCP] google-cloud-bigquery not installed — VCP integration disabled")


def get_bq_client():
    if not BQ_AVAILABLE:
        return None
    return bigquery.Client(project=GCP_PROJECT_ID, location=BQ_LOCATION)


def get_tier_a_symbols():
    """Get today's VCP watchlist — Tier A companies."""
    if not BQ_AVAILABLE:
        return []
    try:
        client = get_bq_client()
        today = dt.date.today().isoformat()
        query = f"""
        SELECT DISTINCT symbol, score, rank
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.strategy1_watchlist`
        WHERE scan_date = '{today}'
        ORDER BY rank
        """
        rows = client.query(query).result()
        return [{"symbol": r.symbol, "score": r.score, "rank": r.rank} for r in rows]
    except Exception as e:
        print(f"[VCP] Tier A fetch failed: {e}")
        return []


def get_tier_b_symbols():
    """Get companies in VCP watchlist in last 90 days — Tier B."""
    if not BQ_AVAILABLE:
        return []
    try:
        client = get_bq_client()
        cutoff = (dt.date.today() - dt.timedelta(days=TIER_B_LOOKBACK_DAYS)).isoformat()
        today  = dt.date.today().isoformat()
        query = f"""
        SELECT DISTINCT w.symbol, d.sector, d.industry, d.rs,
               d.stage2, d.high_52w, d.score
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.strategy1_watchlist` w
        LEFT JOIN `{GCP_PROJECT_ID}.{BQ_DATASET}.strategy1_stock_details` d
          ON w.symbol = d.symbol AND w.scan_date = d.scan_date
        WHERE w.scan_date BETWEEN '{cutoff}' AND '{today}'
        ORDER BY w.symbol
        """
        rows = client.query(query).result()
        return [{
            "symbol":   r.symbol,
            "sector":   r.sector,
            "industry": r.industry,
            "rs":       r.rs,
            "stage2":   r.stage2,
            "high_52w": r.high_52w,
            "score":    r.score,
        } for r in rows]
    except Exception as e:
        print(f"[VCP] Tier B fetch failed: {e}")
        return []


def get_stock_technical(symbol):
    """Get latest technical data for a symbol from VCP."""
    if not BQ_AVAILABLE:
        return {}
    try:
        client = get_bq_client()
        query = f"""
        SELECT d.*, v.vcp_pass, v.pivot_price, v.breakout,
               v.num_contractions, v.volume_dry
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.strategy1_stock_details` d
        LEFT JOIN `{GCP_PROJECT_ID}.{BQ_DATASET}.vcp_results` v
          ON d.symbol = v.symbol AND d.scan_date = v.scan_date
        WHERE d.symbol = '{symbol}'
        ORDER BY d.scan_date DESC
        LIMIT 1
        """
        rows = list(client.query(query).result())
        if rows:
            r = rows[0]
            return {
                "rs":             r.rs,
                "stage2":         r.stage2,
                "ema10":          r.ema10,
                "ema20":          r.ema20,
                "ema50":          r.ema50,
                "high_52w":       r.high_52w,
                "roc30":          r.roc30,
                "roc60":          r.roc60,
                "vcp_pass":       r.vcp_pass,
                "pivot_price":    r.pivot_price,
                "breakout":       r.breakout,
            }
        return {}
    except Exception as e:
        print(f"[VCP] Technical fetch failed for {symbol}: {e}")
        return {}


def get_sector_rankings():
    """Get current sector power rankings from VCP."""
    if not BQ_AVAILABLE:
        return []
    try:
        client = get_bq_client()
        today = dt.date.today().isoformat()
        query = f"""
        SELECT sector, avg_score, stock_count, power, rank
        FROM `{GCP_PROJECT_ID}.{BQ_DATASET}.sector_history`
        WHERE scan_date = '{today}'
        ORDER BY rank
        LIMIT 20
        """
        rows = client.query(query).result()
        return [{
            "sector":      r.sector,
            "avg_score":   r.avg_score,
            "stock_count": r.stock_count,
            "power":       r.power,
            "rank":        r.rank,
        } for r in rows]
    except Exception as e:
        print(f"[VCP] Sector rankings failed: {e}")
        return []


def sync_tiers_to_db():
    """Sync VCP Tier A/B companies into earnings engine SQLite."""
    print("[VCP] Syncing tiers from BigQuery...")
    tier_a = get_tier_a_symbols()
    tier_b = get_tier_b_symbols()

    tier_a_syms = {r["symbol"] for r in tier_a}
    tier_b_syms = {r["symbol"] for r in tier_b} - tier_a_syms

    conn = sqlite3.connect(DB_PATH)
    today = dt.date.today().isoformat()

    # Update Tier A
    for r in tier_a:
        conn.execute("""
        INSERT INTO company_profile (symbol, tier, tier_updated)
        VALUES (?, 'A', ?)
        ON CONFLICT(symbol) DO UPDATE SET tier='A', tier_updated=?
        """, (r["symbol"], today, today))

    # Update Tier B (only if not already Tier A)
    for r in tier_b:
        if r["symbol"] not in tier_a_syms:
            conn.execute("""
            INSERT INTO company_profile (symbol, sector, industry, tier, tier_updated)
            VALUES (?, ?, ?, 'B', ?)
            ON CONFLICT(symbol) DO UPDATE SET
              sector=excluded.sector,
              industry=excluded.industry,
              tier=CASE WHEN tier='A' THEN 'A' ELSE 'B' END,
              tier_updated=?
            """, (r["symbol"], r.get("sector"), r.get("industry"), today, today))

    conn.commit()
    conn.close()
    print(f"[VCP] Synced {len(tier_a_syms)} Tier A, {len(tier_b_syms)} Tier B companies")
    return len(tier_a_syms), len(tier_b_syms)


if __name__ == "__main__":
    sync_tiers_to_db()
