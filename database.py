# database.py
import aiosqlite
import hashlib

DB_NAME = "bot.db"

def hash_line(line: str) -> str:
    return hashlib.sha256(line.encode('utf-8')).hexdigest()

async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                total_lines INTEGER DEFAULT 0,
                unique_seeds INTEGER DEFAULT 0,
                unique_keys INTEGER DEFAULT 0,
                balance REAL DEFAULT 0.0,
                referred_by INTEGER,
                referral_earnings REAL DEFAULT 0.0
            )
        ''')
        await db.execute('''
            CREATE TABLE IF NOT EXISTS wallets (
                hash TEXT PRIMARY KEY,
                type TEXT NOT NULL,
                submitted_by INTEGER,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        await db.commit()

async def get_or_create_user(user_id:
