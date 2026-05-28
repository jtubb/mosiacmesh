import sqlite3, sys, hashlib

db = sys.argv[1] if len(sys.argv) > 1 else "TrustStore.original.sqlite3"
con = sqlite3.connect(db)
cur = con.cursor()

print("=== tables ===")
for (name, sql) in cur.execute("SELECT name, sql FROM sqlite_master WHERE type='table'"):
    print(name)
    print("  ", (sql or "").replace("\n", " "))

print("\n=== row count ===")
try:
    n = cur.execute("SELECT COUNT(*) FROM tsettings").fetchone()[0]
    print("tsettings rows:", n)
except Exception as e:
    print("no tsettings:", e)

print("\n=== sample rows (first 5) ===")
try:
    cols = [d[0] for d in cur.execute("SELECT * FROM tsettings LIMIT 1").description]
    print("columns:", cols)
    for row in cur.execute("SELECT * FROM tsettings LIMIT 5"):
        desc = []
        for c, v in zip(cols, row):
            if isinstance(v, (bytes, bytearray)):
                desc.append(f"{c}=<{len(v)}B {v[:8].hex()}...>")
            else:
                desc.append(f"{c}={v!r}")
        print("  " + " | ".join(desc))
except Exception as e:
    print("err:", e)

con.close()
