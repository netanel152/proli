import asyncio
import os
import sys
from datetime import datetime, timezone

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.analytics_service import (
    get_lead_funnel,
    get_pro_performance,
    get_overview_metrics,
)
from app.core.database import users_collection

async def verify_analytics():
    print("🧪 Verifying Proli Analytics Systems...\n")

    # 1. Lead Funnel
    print("📊 LEAD FUNNEL (30 Days)")
    print("-" * 30)
    funnel = await get_lead_funnel(days=30)
    for status, count in funnel.items():
        print(f"{status:<20} | {count:>5}")
    print("-" * 30 + "\n")

    # 2. Overview Metrics
    print("📈 OVERVIEW METRICS")
    print("-" * 30)
    overview = await get_overview_metrics()
    for metric, value in overview.items():
        print(f"{metric:<20} | {value}")
    print("-" * 30 + "\n")

    # 3. FinOps: AI Token Usage per Pro
    print("💰 FINOPS: AI TOKEN USAGE")
    print("-" * 65)
    print(f"{'Professional':<30} | {'Tokens Used':<12} | {'Role':<15}")
    print("-" * 65)
    
    cursor = users_collection.find(
        {"role": "professional"},
        {"business_name": 1, "total_tokens_used": 1, "role": 1}
    ).sort("total_tokens_used", -1)
    
    async for pro in cursor:
        name = pro.get("business_name", "Unknown")
        tokens = pro.get("total_tokens_used", 0)
        role = pro.get("role", "N/A")
        print(f"{name[:30]:<30} | {tokens:>12,} | {role:<15}")
    print("-" * 65 + "\n")

    # 4. Pro Performance (Top Performers)
    print("🏆 TOP PERFORMING PROFESSIONALS (Completed Jobs)")
    print("-" * 80)
    print(f"{'Name':<25} | {'Total':<8} | {'Completed':<10} | {'Rate':<10} | {'Rating':<8}")
    print("-" * 80)
    perf = await get_pro_performance(days=30)
    for p in perf[:5]: # Top 5
        print(f"{p['name'][:25]:<25} | {p['total_leads']:<8} | {p['completed']:<10} | {p['completion_rate']:>8.1f}% | {p['avg_rating'] or 'N/A':<8}")
    print("-" * 80 + "\n")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(verify_analytics())
