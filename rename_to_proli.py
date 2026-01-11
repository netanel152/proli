import os

# הגדרת ההחלפות לפי סדר חשיבות (מהספציפי לכללי)
REPLACEMENTS = {
    # 1. Database & Infrastructure specific names
    "proli_db": "proli_db",
    "proli_network": "proli_network",
    "proli_cache": "proli_cache",
    "proli.log": "proli.log",
    
    # 2. Variable Names & Constants (Code)
    "PROLI_PRO_NAME": "PROLI_PRO_NAME",
    "PROLI_SCHEDULER_ROLE": "PROLI_SCHEDULER_ROLE",
    "proli_lang": "proli_lang",
    "proli_auth_token": "proli_auth_token",
    "proli_auth_manager": "proli_auth_manager",
    "proli_leads": "proli_leads",
    
    # 3. Titles & User Facing Strings
    "Proli Bot Server": "Proli Bot Server",
    "Proli Admin": "Proli Admin",
    "Welcome to Proli": "Welcome to Proli",
    "Proli's Smart Dispatcher": "Proli's Smart Dispatcher",
    "אנחנו ב-Proli": "אנחנו ב-Proli",
    
    # 4. General fallback (Case Sensitive)
    "Proli": "Proli",
    "PROLI": "PROLI",
    "proli": "proli"
}

# תיקיות שצריך להתעלם מהן
IGNORE_DIRS = {'.git', '__pycache__', 'venv', '.idea', '.vscode', 'node_modules', '.pytest_cache'}
# סיומות קבצים שמותר לגעת בהן
ALLOWED_EXTENSIONS = {'.py', '.md', '.yml', '.yaml', '.txt', '.env', '.json', '.conf'}

def perform_rename(root_dir):
    count = 0
    print(f"🚀 Starting automated rename from Proli to Proli in: {root_dir}")
    
    for subdir, dirs, files in os.walk(root_dir):
        # סינון תיקיות לא רלוונטיות
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        for file in files:
            # בדיקת סיומת
            if not any(file.endswith(ext) for ext in ALLOWED_EXTENSIONS):
                continue
                
            file_path = os.path.join(subdir, file)
            
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                new_content = content
                file_changed = False
                
                # ביצוע כל ההחלפות
                for old, new in REPLACEMENTS.items():
                    if old in new_content:
                        new_content = new_content.replace(old, new)
                        file_changed = True
                
                if file_changed:
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(new_content)
                    print(f"✅ Updated: {file_path}")
                    count += 1
                    
            except Exception as e:
                print(f"⚠️ Skipped {file_path}: {e}")

    print(f"\n✨ Mission Complete! Modified {count} files.")
    print("⚠️ Please manually rename the folder 'proli-backend' to 'proli-backend' if it exists.")

if __name__ == "__main__":
    # הרצה על התיקייה הנוכחית
    perform_rename(os.getcwd())