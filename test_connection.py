import os
from dotenv import load_dotenv
import google.generativeai as genai
from pymongo.mongo_client import MongoClient

load_dotenv()

print("🔄 Testing connections...")

# --- בדיקת מונגו ---
mongo_uri = os.getenv("MONGO_URI")
try:
    client = MongoClient(mongo_uri)
    client.admin.command('ping')
    print("✅ MongoDB: Connected successfully!")
except Exception as e:
    print(f"❌ MongoDB Error: {e}")

# --- בדיקת ג'מיני (עם המודל החדש) ---
gemini_key = os.getenv("GEMINI_API_KEY")
try:
    genai.configure(api_key=gemini_key)
    model = genai.GenerativeModel('gemini-2.0-flash')
    response = model.generate_content("Say 'Hello Fixi'")
    print(f"✅ Gemini AI: Connected! Bot said: {response.text.strip()}")
except Exception as e:
    print(f"❌ Gemini Error: {e}")

# --- בדיקת Green API ---
if os.getenv("GREEN_API_TOKEN"):
    print("✅ Green API: Keys found.")
else:
    print("⚠️ Green API: Missing keys in .env")
