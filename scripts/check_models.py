from google import genai
import os
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

print('🔎 מחפש מודלים זמינים למפתח הזה...')
found = False
try:
    for m in client.models.list():
        print(f" - {m.name}")
        if 'flash' in m.name:
            found = True
except Exception as e:
    print(f"Error listing models: {e}")

print('\n')
if not found:
    print('⚠️ מודל Flash לא נמצא בחיפוש (אולי יש שינוי בשמות).')