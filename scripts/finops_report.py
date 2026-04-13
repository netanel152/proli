import asyncio
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import users_collection

async def generate_finops_report():
    """
    Queries and displays the total_tokens_used for all professionals.
    """
    print("📊 Proli FinOps Report: Gemini Token Usage\n")
    print(f"{'Professional/Business':<30} | {'Phone':<15} | {'Tokens Used':<12}")
    print("-" * 65)

    cursor = users_collection.find(
        {"role": "professional", "total_tokens_used": {"$exists": True, "$gt": 0}}
    ).sort("total_tokens_used", -1)
    
    pros = await cursor.to_list(length=100)

    if not pros:
        print("No token usage data found yet.")
        return

    total_all = 0
    for pro in pros:
        name = pro.get("business_name") or pro.get("name") or "Unnamed Pro"
        phone = pro.get("phone_number", "Unknown")
        tokens = pro.get("total_tokens_used", 0)
        total_all += tokens
        
        # Trim name if too long
        display_name = (name[:27] + "...") if len(name) > 30 else name
        
        print(f"{display_name:<30} | {phone:<15} | {tokens:>12,}")

    print("-" * 65)
    print(f"{'TOTAL':<48} | {total_all:>12,}\n")

if __name__ == "__main__":
    asyncio.run(generate_finops_report())
