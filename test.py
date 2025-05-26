import asyncpg
import asyncio

async def test_connection():
    conn = await asyncpg.connect("postgresql://postgres:dbasE4671133@db.dfoghuwgbtxazwhxeoom.supabase.co:5432/postgres")
    print("✅ Koneksi berhasil!")
    await conn.close()

asyncio.run(test_connection())
