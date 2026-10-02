# 360-review — the weekly look back
<!-- doc: REFERENCE — a /run 360 recipe the connected assistant reads; served by routes/recipes.py -->

1. **Read the numbers:** `stats` (counts by CV version and by role) and
   `export_history` for the full record.
2. **Say what the record shows** — only what the counts prove. A rate over
   two or three applications is not a pattern; say so.
3. **Suggest one or two changes** for next week (titles, places, CV angle)
   and ask the user which to keep.
4. **Save what the user agrees with** as a `lesson` with `record_event` on the
   application it came from, so the next run starts from it.
