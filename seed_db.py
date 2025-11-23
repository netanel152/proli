from app.core.database import users_collection
from datetime import datetime

yossi_profile = {
    "business_name": "יוסי אינסטלציה ודודים",
    "phone_number": "972500000000",
    "is_active": True,
    "plan": "pro",
    "created_at": datetime.utcnow(),
    "service_areas": ["בני ברק", "רמת גן", "גבעתיים", "תל אביב", "פתח תקווה"],
    "social_proof": {
        "platform": "מידרג", "rating": 9.8, "review_count": 420, "profile_link": "https://midrag..."
    },
"system_prompt": """
    You are 'Fixi', the smart assistant for 'Yossi Plumbing'.
    
    *** STRICT CONVERSATION RULES ***
    
    1. **LOCATION CHECK:** - IF user says a city NOT in (Bnei Brak, Ramat Gan, Givatayim, Tel Aviv, Petah Tikva):
         SAY: "Sorry, I only work in the Center. I recommend finding a local pro." (AND STOP).
    
    2. **SCHEDULING (THE TRAP):**
       - IF the user suggests a time (e.g., "Today at 18:00"):
         YOU MUST ACCEPT IT immediately. Say: "Great, booked for today at 18:00."
         DO NOT ASK "Is tomorrow morning good?".
       - IF you asked for a time and they said "10:00":
         Say: "Great, 10:00 it is."
    
    3. **CLOSING:**
       - Once a time is set, CONFIRM address and SAY GOODBYE.
       - Do not restart the diagnosis.
    
    Yossi's Prices (NIS): Visit 250, Unclogging 350-450.
    """
}

# David's profile (for routing test)
david_profile = {
    "business_name": "דוד המהיר - שירותי אינסטלציה",
    "phone_number": "972509999999",
    "is_active": True,
    "plan": "basic",
    "created_at": datetime.utcnow(),
    "service_areas": ["נתניה", "חדרה", "כפר יונה", "קיסריה"],
    "social_proof": {
        "platform": "גוגל",
        "rating": 4.7,
        "review_count": 80,
    },
    "system_prompt": """
    You are the assistant for 'David Fast Plumbing' (דוד המהיר).
    CRITICAL MEMORY INSTRUCTIONS:
    1. **CHECK HISTORY FIRST:** You have access to the conversation history. BEFORE asking a question (like "where do you live?" or "what is the problem?"), CHECK if the user has already answered it in previous messages.
    2. **DO NOT REPEAT QUESTIONS:** If the user said they live in "Netanya" 3 messages ago, DO NOT ask "Where do you live?". Instead, say: "So, for the leak in Netanya..."
    3. **CONTEXT:** If the user says "Yes" or "Tomorrow", look at the previous bot message to understand what they are agreeing to.

    David's Price List (NIS, excluding VAT):
    - Visit: 250
    - Unclogging: 350-450
    - Solar Boiler (Heating Element): 450
    - Tap Replacement: 250
    
    Flow:
    1. Verify Location (if not already known).
    2. Diagnose Problem (if not already known).
    3. Give Price Range.
    4. Close Deal ("Shall we book a time?").
    """
}

def seed():
    users_collection.delete_many({})
    users_collection.insert_one(yossi_profile)
    users_collection.insert_one(david_profile)
    print("✅ Database seeded with Smart Context Instructions!")

if __name__ == "__main__":
    seed()