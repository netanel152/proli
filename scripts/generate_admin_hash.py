import bcrypt
import getpass

def generate_hash():
    print("--- Fixi Admin Password Hasher ---")
    password = getpass.getpass("Enter the admin password to hash: ")
    
    if not password:
        print("Error: Password cannot be empty.")
        return

    # Generate salt and hash
    hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    
    print("\n✅ Success! Update your .env file with this line:")
    print(f"ADMIN_PASSWORD_HASH={hashed}")
    print("\n(You can verify this works by running the script again and modifying it to check)")

if __name__ == "__main__":
    generate_hash()
