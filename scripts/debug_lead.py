import os
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import uri_parser
import certifi
from dotenv import load_dotenv

load_dotenv()

async def check():
    uri = os.getenv('MONGO_URI') or os.getenv('MONGODB_URI')
    _p = uri_parser.parse_uri(uri)
    db_name = _p.get('database') or 'proli_db'
    ca = certifi.where() if '+srv' in uri else None
    client = AsyncIOMotorClient(uri, tlsCAFile=ca)
    db = client[db_name]
    doc = await db.leads.find_one({'chat_id': '972523651414@c.us'}, sort=[('created_at', -1)])
    if doc:
        # Format the output for readability
        print(f"Lead ID: {doc['_id']}")
        print(f"Status: {doc.get('status')}")
        print(f"Issue: {doc.get('issue_type')}")
        print(f"Full Address: {doc.get('full_address')}")
        print(f"City: {doc.get('city')}")
        print(f"Street: {doc.get('street')}")
        print(f"Street Num: {doc.get('street_number')}")
        print(f"Time: {doc.get('appointment_time')}")
        print(f"Pro ID: {doc.get('pro_id')}")
        print(f"Created At: {doc.get('created_at')}")
    else:
        print("No lead found.")

if __name__ == "__main__":
    asyncio.run(check())
