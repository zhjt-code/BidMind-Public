import asyncio, os, sys
os.chdir('h:/BidMind AI Lite/backend')
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
import asyncpg

async def verify():
    dsn = os.getenv('DATABASE_URL', '').replace('postgresql+asyncpg://', 'postgresql://')
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch(
            "SELECT enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'projecttype' ORDER BY e.enumsortorder"
        )
        print('projecttype enum values:', [r['enumlabel'] for r in rows])

        count = await conn.fetchval('SELECT COUNT(*) FROM projects')
        print('Existing projects count:', count)

        if count > 0:
            row = await conn.fetchrow('SELECT id, name, project_type FROM projects LIMIT 1')
            print('Sample project id:', row['id'], 'type:', row['project_type'])

        print('Verification passed!')
    finally:
        await conn.close()

asyncio.run(verify())
