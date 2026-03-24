import sqlite3, os
path = os.path.join('data', 'provenance.db')
conn = sqlite3.connect(path)
c = conn.cursor()
tables = [r[0] for r in c.execute('SELECT name FROM sqlite_master WHERE type="table"')]
print(tables)
if 'api_keys' in tables:
    keys = list(c.execute('SELECT id FROM api_keys LIMIT 1'))
    print(keys)
conn.close()
