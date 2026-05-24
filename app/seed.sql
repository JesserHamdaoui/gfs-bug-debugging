-- PostgreSQL
CREATE TABLE IF NOT EXISTS users (
    id         SERIAL PRIMARY KEY,
    email      TEXT NOT NULL UNIQUE,
    password   TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS invoices (
    id         SERIAL PRIMARY KEY,
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    period     DATE NOT NULL,
    price      NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT NOW()
);
