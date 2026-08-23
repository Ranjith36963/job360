import json
import os

import psycopg

dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
conn = psycopg.connect(dsn)
cur = conn.cursor()
cur.execute("""
    SELECT u.id, u.email, p.updated_at, p.preferences
    FROM user_profiles p JOIN users u ON u.id = p.user_id
    ORDER BY u.id
""")
for uid, email, updated_at, pref_json in cur.fetchall():
    prefs = json.loads(pref_json) if pref_json else {}
    add_skills = prefs.get("additional_skills") or []
    titles = prefs.get("target_job_titles") or []
    print(f"user_id={uid[:8]} email={email!r} updated_at={updated_at} n_additional_skills={len(add_skills)} n_titles={len(titles)}")
cur.execute("SELECT COUNT(*) FROM user_profile_versions")
print("total versions:", cur.fetchone()[0])
cur.execute("""
    SELECT user_id, COUNT(*), MIN(created_at), MAX(created_at)
    FROM user_profile_versions GROUP BY user_id
""")
for row in cur.fetchall():
    print("versions:", row)
conn.close()
