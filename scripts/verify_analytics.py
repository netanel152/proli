"""Manual smoke-check of the analytics aggregations against a live database.

PRO-140: repointed from the deleted async ``app/services/analytics_service``
to ``admin_panel/core/analytics_queries`` — the single (sync) implementation
the admin panel renders. Run inside the project venv, against whatever
``MONGO_URI`` the environment provides (e.g. ``railway run`` for staging).
"""

import os
import sys

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import certifi
from pymongo import MongoClient

from admin_panel.core import analytics_queries as aq
from app.core.config import settings
from app.core.database import DB_NAME


def _connect():
    mongo_uri = (
        settings.MONGO_URI.get_secret_value()
        if settings.MONGO_URI
        else "mongodb://localhost:27017"
    )
    kwargs = {}
    if mongo_uri.startswith("mongodb+srv://") or "mongodb.net" in mongo_uri:
        kwargs["tlsCAFile"] = certifi.where()
    return MongoClient(mongo_uri, **kwargs)[DB_NAME]


def verify_analytics():
    db = _connect()
    print("🧪 Verifying Proli Analytics (admin_panel.core.analytics_queries)...\n")

    print("📊 LEAD FUNNEL (30 Days)")
    print("-" * 30)
    for status, count in aq.get_lead_funnel(db, days=30).items():
        print(f"{status:<20} | {count:>5}")
    print("-" * 30 + "\n")

    print("💵 REVENUE / GMV (30 Days, PRO-33)")
    print("-" * 30)
    for metric, value in aq.get_revenue_stats(db, days=30).items():
        print(f"{metric:<20} | {value}")
    print("-" * 30 + "\n")

    print("💰 FINOPS: AI TOKEN USAGE")
    print("-" * 65)
    print(f"{'Professional':<30} | {'Tokens Used':<12}")
    print("-" * 65)
    for pro in aq.get_finops_stats(db):
        name = pro.get("name") or "Unknown"
        print(f"{name[:30]:<30} | {pro.get('tokens', 0):>12,}")
    print("-" * 65 + "\n")

    print("🏆 TOP PERFORMING PROFESSIONALS (30 Days)")
    print("-" * 80)
    print(
        f"{'Name':<25} | {'Total':<8} | {'Completed':<10} | {'Rate':<10} | "
        f"{'Declined':<9} | {'Decl%':<8} | {'Rating':<8}"
    )
    print("-" * 80)

    def _pct(v):
        # PRO-157: None means "no leads in this window", not 0%.
        return f"{v:>7.1f}%" if v is not None else f"{'—':>8}"

    for p in aq.get_pro_performance(db, days=30)[:5]:
        print(
            f"{p['name'][:25]:<25} | {p['total_leads']:<8} | {p['completed']:<10} | "
            f"{_pct(p['completion_rate'])} | {p['rejected']:<9} | "
            f"{_pct(p['rejection_rate'])} | {p['avg_rating']}"
        )
    print("-" * 80 + "\n")

    print("✅ Analytics verification complete.")


if __name__ == "__main__":
    verify_analytics()
