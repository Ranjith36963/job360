-- 0087_ask_questions: one ask per job carries every open form question (owner, 2026-10-24).
--
-- kind      - what the ask is: '' (older asks), 'question', or a blocker the user must clear
--             themselves ('captcha', 'login', 'upload', 'safety'). Closed set in asks.py.
-- questions - JSON list of {key, question, answer, source, choices?, remembered_on?} the assistant
--             filled in, each with where its answer came from; '' = a plain one-question ask.
-- answers   - JSON object {key: answer} the user approved; '' until answered.
ALTER TABLE application_asks ADD COLUMN IF NOT EXISTS kind TEXT NOT NULL DEFAULT '';
ALTER TABLE application_asks ADD COLUMN IF NOT EXISTS questions TEXT NOT NULL DEFAULT '';
ALTER TABLE application_asks ADD COLUMN IF NOT EXISTS answers TEXT NOT NULL DEFAULT '';
