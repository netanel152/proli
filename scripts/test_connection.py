import os
from dotenv import load_dotenv
from google import genai
from pymongo.mongo_client import MongoClient

load_dotenv()

print("🔄 Testing connections...")

# --- בדיקת מונגו ---
mongo_uri = os.getenv("MONGO_URI")
try:
    client_mongo = MongoClient(mongo_uri)
    client_mongo.admin.command('ping')
    print("✅ MongoDB: Connected successfully!")
except Exception as e:
    print(f"❌ MongoDB Error: {e}")

# --- בדיקת ג'מיני (עם המודל החדש) ---
gemini_key = os.getenv("GEMINI_API_KEY")
try:
    client_ai = genai.Client(api_key=gemini_key)
    # Using 'gemini-2.5-flash-lite' as a safe default, or keep what was there if known to work
    model_name = 'gemini-2.5-flash-lite' 
    response = client_ai.models.generate_content(
        model=model_name,
        contents="Say 'Hello Fixi'"
    )
    print(f"✅ Gemini AI: Connected! Bot said: {response.text.strip()}")
except Exception as e:
    print(f"❌ Gemini Error: {e}")

# --- בדיקת Green API ---
if os.getenv("GREEN_API_TOKEN"):
    print("✅ Green API: Keys found.")
else:
    print("⚠️ Green API: Missing keys in .env")