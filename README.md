# gfs-bug-debugging

A small Flask **billing app** with a deliberate **race condition** in its `/subscribe` endpoint, used as a realistic case study for reproducing and debugging production bugs that depend on a specific database state.

Submission for **Project 3 — Reproducing & Debugging Production Bugs** of the Guepard Community Builder Technical Challenge.

- `main` — app with the race condition intact
- `fix/billing-race-condition` — app with the fix applied

For the narrative write-up, see [BLOG.md](BLOG.md).

---

## The bug

`POST /subscribe` creates a monthly invoice for the logged-in user. The endpoint guards against duplicates with a `SELECT` before the `INSERT`:

```python
cur.execute("SELECT id FROM invoices WHERE user_id = %s AND period = %s",
            (user["id"], period))
if cur.fetchone():
    return jsonify({"error": "Invoice already exists"}), 409

time.sleep(0.5)   # simulates network/processing latency

cur.execute("INSERT INTO invoices (user_id, period, price) VALUES (%s, %s, %s)",
            (user["id"], period, price))
```

The guard is non-atomic. Two concurrent requests both pass the `SELECT`, both run the `INSERT`, and the user ends up with two invoices for the same period — a **time-of-check / time-of-use race condition**.

It only reproduces under a very specific data state: a logged-in user, no invoice yet for the current period, two requests within ~500 ms. That specificity is exactly what makes it a good case study — the bug lives in the data, not just the code.

---

## What this repo demonstrates

1. A realistic, data-dependent bug that resists log-only debugging.
2. A reproduction workflow built around **database commit hashes** instead of database dumps — the bug state is captured, shared, and restored with a single identifier.
3. A non-trivial fix (`pg_advisory_xact_lock`) verified against the captured broken state.
4. A before/after comparison of the database under concurrent load.

[GFS](https://github.com/Guepard-Corp/gfs) is the tool that makes step 2 possible; it's used throughout but is not the subject of this project.

---

## Project layout

```
.
├── app/
│   ├── main.py           # Flask app: signup, signin, /subscribe, /invoices
│   ├── seed.sql          # Schema: users + invoices
│   └── templates/        # Jinja templates
├── BLOG.md               # ~1000-word write-up
├── requirements.txt      # flask, psycopg2-binary
└── README.md
```

The interesting file is [app/main.py](app/main.py). Everything else exists to make the bug fire reliably.

---

## Prerequisites

- Python 3.10+
- Docker (the database container runs in Docker)
- GFS CLI — install: `curl -fsSL https://gfs.guepard.run/install | bash`
- `curl` for triggering the race

---

## Run the project locally

### 1. Clone and install

```bash
git clone https://github.com/JesserHamdaoui/gfs-bug-debugging.git
cd gfs-bug-debugging
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Start a Postgres instance and load the schema

```bash
gfs init --database-provider postgres --database-version 17 --port 55000
gfs config user.name  "your-name"
gfs config user.email "you@example.com"
gfs query --file app/seed.sql
gfs commit -m "seed: schema only, no data"
```

You now have an isolated Postgres on `localhost:55000` with the `users` and `invoices` tables.

### 3. Start the Flask app

```bash
flask --app app/main.py run -p 8080
```

Open http://localhost:8080, sign up, land on `/billing`.

---

## Trigger the bug

The endpoint includes a deliberate `time.sleep(0.5)` between the check and the insert so the race fires reliably from a single laptop.

Grab the `session` cookie from your browser dev tools, then fire two requests in parallel:

```bash
COOKIE="session=<your-session-cookie>"

curl -s -X POST http://localhost:8080/subscribe \
  -H "Content-Type: application/json" -H "Cookie: $COOKIE" \
  -d '{"plan":"pro"}' &
curl -s -X POST http://localhost:8080/subscribe \
  -H "Content-Type: application/json" -H "Cookie: $COOKIE" \
  -d '{"plan":"pro"}' &
wait
```

Check the result:

```bash
gfs query --sql "SELECT user_id, period, COUNT(*) FROM invoices GROUP BY 1,2 HAVING COUNT(*) > 1;"
```

At least one `(user_id, period)` pair returns count `2`. The user has been double-charged.

---

## Reproduce the bug from a shared identifier

Snapshot the broken database so anyone can reproduce it:

```bash
gfs commit -m "repro: duplicate invoices from concurrent /subscribe"
gfs log
```

Copy the resulting commit hash into a bug report. **Sample issue:**

```markdown
**Title:** Duplicate invoices created when /subscribe is called concurrently

**Environment**

- App commit: 2478ba0 (main)
- Database state: <paste-hash-here>

**Reproduce**

1. gfs checkout <paste-hash-here>
2. flask --app app/main.py run -p 8080
3. Fire two parallel POSTs to /subscribe with the same session cookie.
4. gfs query --sql "SELECT user_id, period, COUNT(\*) FROM invoices GROUP BY 1,2;"
5. Observe: two rows for the same (user_id, period).

**Expected:** second request returns HTTP 409.
**Actual:** both return HTTP 200; two invoice rows written.
```

Anyone with the repo checks out the hash and is staring at the exact same data state. No dump, no PII scrub.

---

## Fix and verify

Switch the app to the fix branch:

```bash
git checkout fix/billing-race-condition
```

The fix takes a Postgres advisory transaction lock keyed on `(user_id, period)` inside the transaction, serializing concurrent attempts on the same key:

```python
cur.execute("SELECT pg_advisory_xact_lock(%s)",
            (hash(f"{user['id']}-{period}"),))
```

A unique index on `(user_id, period)` would be a reasonable alternative — better defence in depth, but it requires a schema migration. The advisory lock keeps the user-facing 409 response clean and fits a hotfix.

Verify the fix against the **exact** broken state captured earlier:

```bash
gfs checkout <bug-commit-hash>
flask --app app/main.py run -p 8080
# Re-fire the two parallel curls from above
```

Expected outcome: one `200 OK`, one `409 Conflict`, one row in `invoices` for that `(user_id, period)`.

---

## Before / after

|                                           | Before (`main`)                                  | After (`fix/billing-race-condition`)              |
| ----------------------------------------- | ------------------------------------------------ | ------------------------------------------------- |
| Two concurrent `/subscribe` (same period) | Two `200 OK`, two invoice rows                   | One `200 OK`, one `409 Conflict`, one invoice row |
| `COUNT(*)` for `(user_id, period)`        | `2`                                              | `1`                                               |
| Guard mechanism                           | Non-atomic application-level `SELECT` → `INSERT` | `pg_advisory_xact_lock` inside the transaction    |

---

## Branches

| Branch                       | Purpose                                 |
| ---------------------------- | --------------------------------------- |
| `main`                       | App with the race condition bug intact  |
| `fix/billing-race-condition` | Fix applied via `pg_advisory_xact_lock` |

---

## Links

- **Blog post:** [BLOG.md](BLOG.md) (also published on Dev.to — link in repo description)
- **Video demo:** soon
- **GFS:** https://github.com/Guepard-Corp/gfs
