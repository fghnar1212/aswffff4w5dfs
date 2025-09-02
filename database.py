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

async def get_or_create_user(user_id: int, username: str):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        user = await cursor.fetchone()
        if not user:
            await db.execute(
                '''INSERT INTO users (user_id, username, total_lines, unique_seeds, unique_keys, balance, referred_by)
                VALUES (?, ?, 0, 0, 0, 0.0, NULL)''',
                (user_id, username)
            )
            await db.commit()
        else:
            await db.execute("UPDATE users SET username = ? WHERE user_id = ?", (username, user_id))
            await db.commit()

async def register_referral(user_id: int, referrer_id: int):
    if user_id == referrer_id:
        return
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT user_id FROM users WHERE user_id = ?", (referrer_id,))
        if await cursor.fetchone() is None:
            return
        await db.execute("UPDATE users SET referred_by = ? WHERE user_id = ? AND referred_by IS NULL", (referrer_id, user_id))
        await db.commit()

async def is_duplicate(hash_value: str) -> bool:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT 1 FROM wallets WHERE hash = ?", (hash_value,))
        return (await cursor.fetchone()) is not None

async def add_wallet_hash(hash_value: str, wallet_type: str, user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "INSERT OR IGNORE INTO wallets (hash, type, submitted_by) VALUES (?, ?, ?)",
            (hash_value, wallet_type, user_id)
        )
        await db.commit()

async def update_user_stats(user_id: int, lines: int, seeds: int, keys: int, reward: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute('''
            UPDATE users SET
                total_lines = total_lines + ?,
                unique_seeds = unique_seeds + ?,
                unique_keys = unique_keys + ?,
                balance = balance + ?
            WHERE user_id = ?
        ''', (lines, seeds, keys, reward, user_id))
        await db.commit()

async def get_user(user_id: int):
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        if row:
            return {
                "user_id": row[0],
                "username": row[1],
                "total_lines": row[2],
                "unique_seeds": row[3],
                "unique_keys": row[4],
                "balance": row[5],
                "referred_by": row[6],
                "referral_earnings": row[7]
            }
        return None

async def get_stats():
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute('''
            SELECT COUNT(*), SUM(total_lines), SUM(unique_seeds), SUM(unique_keys), SUM(balance)
            FROM users
        ''')
        row = await cursor.fetchone()
        return {
            "users": row[0],
            "lines": row[1] or 0,
            "seeds": row[2] or 0,
            "keys": row[3] or 0,
            "payout": row[4] or 0.0
        }

async def get_referral_count(user_id: int) -> int:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute(
            "SELECT COUNT(*) FROM users WHERE referred_by = ?", (user_id,)
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

async def get_referral_earnings(user_id: int) -> float:
    async with aiosqlite.connect(DB_NAME) as db:
        cursor = await db.execute("SELECT referral_earnings FROM users WHERE user_id = ?", (user_id,))
        row = await cursor.fetchone()
        return row[0] if row else 0.0

async def add_referral_bonus(referrer_id: int, bonus: float):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute(
            "UPDATE users SET referral_earnings = referral_earnings + ? WHERE user_id = ?",
            (bonus, referrer_id)
        )
        await db.commit()
