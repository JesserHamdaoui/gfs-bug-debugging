# Shipping a database state instead of a pg_dump

**Backend Engineering · Debugging** | May 2025 | 8 min read  
_By Jesser Hamdaoui — Guepard Community Building Team_

---

> **TL;DR:** I reproduced a double-charge race condition and attached the exact database state to the GitHub issue as a single commit hash. Reviewers checked it out with two commands. No dump, no PII scrubbing, no stale snapshot.

---

There's a particular kind of debugging session that ends with you staring at code that looks completely correct — because the bug isn't in the code you're reading. It's in the data it was reading when things went wrong. I hit that wall again last month, and it finally pushed me to change how I write bug reports.

## Installing GFS

GFS is a single binary written in Rust. The only requirement is Docker — it manages the database container for you, no separate database installation needed.

```bash
curl -fsSL https://gfs.guepard.run/install | bash
```

Verify it works:

```bash
gfs providers   # lists postgres 13–18, mysql 8.x and their supported versions
```

> **Note:** GFS is under active development (currently v0.1.4) and not yet recommended for production use. Merge and remote push/pull are still on the roadmap.

## The endpoint

The app is a small Flask billing service. Users sign up, authenticate, and click Subscribe, which inserts a monthly invoice. The guard is straightforward:

```python
# app/routes.py · /subscribe

@app.route("/subscribe", methods=["POST"])
def subscribe():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id FROM invoices WHERE user_id = %s AND period = %s",
                (user["id"], period)
            )
            if cur.fetchone():
                return jsonify({"error": "Invoice already exists"}), 409

            time.sleep(0.5)  # simulate network latency

            cur.execute(
                "INSERT INTO invoices (user_id, period, price) VALUES (%s, %s, %s)",
                (user["id"], period, price)
            )
    return jsonify({"ok": True})
```

In single-user tests: works. In production: users are charged twice. Logs show two `200 OK` responses within milliseconds of each other, both inserting an invoice for the same `(user_id, period)`.

This is a textbook **TOCTOU — time-of-check / time-of-use** race condition. Two concurrent requests both run the `SELECT`, both see no existing invoice, both run the `INSERT`. Postgres writes both rows, because there's no unique constraint. The application-level guard isn't atomic.

## The race, visualised

| t (ms) | Request A                                      | Request B                     |
| ------ | ---------------------------------------------- | ----------------------------- |
| 0      | `SELECT` — sees 0 rows                         | —                             |
| 10     | —                                              | `SELECT` — sees 0 rows        |
| 500    | **`INSERT` invoice → 200 OK**                  | —                             |
| 510    | —                                              | **`INSERT` invoice → 200 OK** |
| —      | **User charged twice. Two rows, same period.** |                               |

## Where the usual tools fall short

Sentry, Rookout, Dynatrace — excellent at the code layer. They'll capture the stack trace, the request payload, local variable values at the moment of the exception. What they can't easily give you is the _state of every row that the request touched_, and the state of every row it didn't touch but could have.

For a race condition that lives in a 500 ms gap between two rows that briefly didn't exist simultaneously, that's the whole problem.

The traditional workaround is `pg_dump`. You dump the schema, scrub PII, `scp` it to a teammate, they restore locally. That workflow is slow, it leaks data, the dump is stale from the moment it's taken, and "scrub the PII" is doing a lot of unpaid labour in that sentence. Worse, it still can't capture the _moment_ — only the aftermath.

## Committing the database state

GFS treats a running database the way Git treats a working tree. You `gfs commit` snapshots the current state, `gfs checkout <hash>` restores it, `gfs checkout -b <name>` creates a branch. Under the hood, GFS manages an isolated Docker container per repo, so checkouts are instant and can't clobber each other.

After spinning up the app and writing seed data:

```bash
$ gfs init --database-provider postgres --database-version 17 --port 55000
$ gfs config user.name "jess"

$ psql "$(gfs status | grep -oE 'postgresql://[^ ]+')" -f app/seed.sql
$ gfs commit -m "seed: one user, no invoices"
```

Then I fired two parallel `curl` calls at `/subscribe` for the same user. The `invoices` table ended up with two rows for the same `(user_id, period)`. At that point:

```bash
$ gfs commit -m "repro: duplicate invoices from concurrent subscribe"
# → a3f91b2c
```

I copied the commit hash into the GitHub issue. The reproduction section read, in full:

```bash
$ gfs checkout a3f91b2c
$ flask --app app/main.py run -p 8080
```

Two commands. No dump, no scrubbing, no Slack DM. A teammate ran them and saw the same two duplicate rows. The bug stopped being something to _describe_ and became something they could _observe_.

## The fix

The fix uses Postgres advisory locks — a per-connection lock keyed on an arbitrary integer, released automatically when the transaction ends. We take one keyed on `(user_id, period)` before the check, so the second request blocks until the first commits, then sees the existing row and returns `409`.

```python
# acquire a transaction-scoped advisory lock on (user_id, period)
cur.execute(
    "SELECT pg_advisory_xact_lock(%s)",
    (hash(f"{user['id']}-{period}"),)
)

# now the check-then-insert is atomic
cur.execute(
    "SELECT id FROM invoices WHERE user_id = %s AND period = %s",
    (user["id"], period)
)
if cur.fetchone():
    return jsonify({"error": "Invoice already exists"}), 409
```

A unique index on `(user_id, period)` would have been defensible too — arguably better for defence in depth. I went with the advisory lock because it produces a clean user-facing error and avoids a schema migration during a hotfix window. In a less pressured context I'd add both.

The part worth calling out is the **verification step**. I checked out the broken commit, applied the patch, restarted the app, and re-ran the parallel `curl`. One request returned `200`, the other `409`. The fix worked not because the tests passed in some abstract environment, but because it worked against the exact state that broke production.

## Why this changes the workflow

The thing I keep coming back to is friction. A commit hash is small, shareable, and unambiguous. It carries the entire data context. Reviewers stop arguing about whether a bug is reproducible because reproducing it is a copy-paste away.

There's a wider point here. The industry has spent fifteen years building code-level observability — Sentry, Rookout, Datadog — all on the assumption that code is the interesting variable and data is just the input. For a large class of bugs, especially concurrency issues and data-shape edge cases, that assumption is backwards. The data _is_ the interesting variable.

I'm not suggesting you tear out your observability stack. But the next time you reach for `pg_dump` to attach to a ticket, there's a faster way.

---

**→ [github.com/Guepard-Corp/gfs](https://github.com/Guepard-Corp/gfs)**
