"""Read-only prod audit: trace cv_projects / linkedin_projects / linkedin_languages /
linkedin_courses for all profiles. NEVER writes. Run via:
railway run -s Postgres python scripts/_audit_pillar1_lens1.py
"""
import hashlib
import json
import os

import psycopg

EXTRACTOR_VERSION = "6"


def _input_hash(raw):
    if not isinstance(raw, str):
        raw = json.dumps(raw, sort_keys=True, default=str)
    payload = f"v{EXTRACTOR_VERSION}\x00{raw}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def main():
    url = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not url:
        print("NO DATABASE URL IN ENV")
        return
    with psycopg.connect(url) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT user_id, cv_data, updated_at FROM user_profiles ORDER BY user_id")
            rows = cur.fetchall()
    print(f"=== {len(rows)} profile rows ===")
    for user_id, cv_data_raw, updated_at in rows:
        cv = json.loads(cv_data_raw) if isinstance(cv_data_raw, str) else cv_data_raw
        print(f"\n--- user {user_id} (updated_at={updated_at}) ---")
        cv_raw_text = cv.get("raw_text", "") or ""
        li_raw_text = cv.get("linkedin_raw_text", "") or ""
        hashes = cv.get("llm_input_hashes", {}) or {}
        print("stored llm_input_hashes keys:", list(hashes.keys()))

        # cv hash freshness
        if cv_raw_text:
            expect_cv_hash = _input_hash(cv_raw_text)
            stored_cv_hash = hashes.get("cv")
            print(f"cv hash matches v6? {stored_cv_hash == expect_cv_hash}  "
                  f"(stored={str(stored_cv_hash)[:12]}, expect_v6={expect_cv_hash[:12]})")
        else:
            print("cv_raw_text EMPTY")

        if li_raw_text:
            expect_li_hash = _input_hash(li_raw_text)
            stored_li_hash = hashes.get("linkedin")
            print(f"linkedin hash matches v6? {stored_li_hash == expect_li_hash}  "
                  f"(stored={str(stored_li_hash)[:12]}, expect_v6={expect_li_hash[:12]})")
        else:
            print("linkedin_raw_text EMPTY")

        print("cv_projects len:", len(cv.get("cv_projects") or []))
        print("linkedin_projects len:", len(cv.get("linkedin_projects") or []))
        print("linkedin_languages len:", len(cv.get("linkedin_languages") or []))
        print("linkedin_courses len:", len(cv.get("linkedin_courses") or []))

        # Heading-line check (STRUCTURAL, not just substring) in CV raw text for "Projects"
        for label, text in (("CV", cv_raw_text), ("LinkedIn", li_raw_text)):
            lines = text.splitlines()
            heading_hits = [
                (i, ln.strip()) for i, ln in enumerate(lines)
                if ln.strip().lower() in {"projects", "languages", "courses"}
            ]
            print(f"{label} exact-heading-line hits (projects/languages/courses): {heading_hits[:10]}")

        print("cv_experience_level:", repr(cv.get("cv_experience_level", "")))
        print("cv_right_to_work:", repr(cv.get("cv_right_to_work", "")))


if __name__ == "__main__":
    main()
