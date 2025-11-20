import google.generativeai as genai
import os
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

print('🔎 מחפש מודלים זמינים למפתח הזה...')
found = False
for m in genai.list_models():
    if 'generateContent' in m.supported_generation_methods:
        print(f" - {m.name}")
        if 'flash' in m.name:
            found = True

print('\n')
if not found:
    print('⚠️ מודל Flash לא נמצא. נסה לעדכן ספרייה או להשתמש ב-gemini-pro')
