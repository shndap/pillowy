import os

import psycopg


data = [
    ("Night", "22:00:00", [("Lithium", 2), ("Biperiden", 1), ("Haloperidol", 1), ("Ciprofloxacin (Ciprox)", 1)]),
    ("Morning", "10:00:00", [("Asentra", 2), ("Ciprofloxacin (Ciprox)", 1), ("Naproxen", 1)]),
    ("Evening", "18:00:00", [("Naproxen", 1)]),
    ("Morning", "02:00:00", [("Naproxen", 1)]),
]

with psycopg.connect(os.environ["SUPABASE_DB_URL"], prepare_threshold=None) as db:
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO users (telegram_id, first_name, timezone)
               VALUES (%s, %s, %s)
               ON CONFLICT (telegram_id) DO UPDATE SET first_name = EXCLUDED.first_name
               RETURNING id""",
            (5049923715, "My Love", "Asia/Tehran"),
        )
        user_id = cur.fetchone()[0]
        medication_ids = {}
        for _, _, items in data:
            for name, _ in items:
                if name in medication_ids:
                    continue
                cur.execute(
                    "SELECT id FROM medications WHERE user_id = %s AND name = %s AND active = true ORDER BY id LIMIT 1",
                    (user_id, name),
                )
                row = cur.fetchone()
                if row:
                    medication_ids[name] = row[0]
                else:
                    cur.execute(
                        "INSERT INTO medications (user_id, name, inventory, active) VALUES (%s, %s, %s, true) RETURNING id",
                        (user_id, name, 0),
                    )
                    medication_ids[name] = cur.fetchone()[0]
        for period, at, items in data:
            for name, quantity in items:
                medication_id = medication_ids[name]
                cur.execute(
                    "SELECT id FROM medication_schedules WHERE medication_id = %s AND period = %s AND \"at\" = %s AND enabled = true",
                    (medication_id, period, at),
                )
                row = cur.fetchone()
                if row:
                    cur.execute("UPDATE medication_schedules SET quantity = %s, reminder_enabled = true WHERE id = %s", (quantity, row[0]))
                else:
                    cur.execute(
                        "INSERT INTO medication_schedules (medication_id, period, \"at\", quantity, reminder_enabled, enabled) VALUES (%s, %s, %s, %s, true, true)",
                        (medication_id, period, at, quantity),
                    )
    print(f"seeded user={user_id} medications={len(medication_ids)} routines={len(data)}")
