import sqlite3, sys
con = sqlite3.connect(sys.argv[1] if len(sys.argv) > 2 else "data/radares.sqlite")
for q in sys.argv[-1].split(";"):
    if q.strip():
        print("--", q.strip()[:90])
        for r in con.execute(q):
            print(r)
