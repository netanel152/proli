import os
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

load_dotenv()

async def check():
    uri = os.getenv('MONGO_URI') or os.getenv('MONGODB_URI')
    if not uri:
        print("MISSING MONGO_URI")
        return
    try:
        client = AsyncIOMotorClient(uri)
        await client.admin.command('ping')
        print("MONGO_OK")
    except Exception as e:
        print(f"MONGO_ERROR: {e}")

if __name__ == "__main__":
    asyncio.run(check())
