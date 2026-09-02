import os
import psycopg

connection_string = os.getenv('DATABASE_URL')
if not connection_string:
    raise RuntimeError('DATABASE_URL is not configured. Add the Supabase/Postgres URL to the .env file.')

with psycopg.connect(connection_string) as conn:
    with conn.cursor() as cursor:
        cursor.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
        rows = cursor.fetchall()
        print('Database tables:')
        for row in rows:
            print(f'  ✓ {row[0]}')
        print(f'\nTotal: {len(rows)} tables')

