import sqlite3
conn = sqlite3.connect('data/provenance.db')
cursor = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='provenance_records';")
print(cursor.fetchone()[0])
