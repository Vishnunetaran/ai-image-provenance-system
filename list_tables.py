import sqlite3
conn = sqlite3.connect('data/provenance.db')
cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table';")
for row in cursor.fetchall():
    print(row[0])
