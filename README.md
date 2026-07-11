# Janus

A Postfix greylisting policy daemon. Janus defers mail from unknown senders and lets it through once they retry — typical spam bots never do. Senders with an established delivery history get an immediate pass via the auto-whitelist (AWL).

Reimplementation of [Helm](https://github.com/mattiasa/helm) in modern Python, managed with [uv](https://docs.astral.sh/uv/).

The name comes from the [Roman god Janus](https://en.wikipedia.org/wiki/Janus), patron of gateways, doorways, and passages. He is depicted with two faces — one looking forward, one back — watching over every threshold. A mail gateway that decides what passes through and what is turned away seemed a fitting domain for his guardianship.

---

## Requirements

- Python 3.11+
- uv
- A database: SQLite for local use, PostgreSQL or MySQL for production

---

## Installation

```sh
git clone …
cd janus

# Local / development (SQLite)
uv sync --extra sqlite

# Production with PostgreSQL
uv sync --extra postgres

# Production with MySQL
uv sync --extra mysql
```

The `janus` command is available via `uv run janus …` or, after activating the virtualenv, directly as `janus`.

---

## Configuration

Copy the example and edit it:

```sh
cp configuration/janus.toml.example janus.toml
```

### Full reference

```toml
# TCP port Postfix connects to (required)
server_port = 1717

# Interface to bind — use 0.0.0.0 to accept from other hosts
bind_address = "127.0.0.1"

# Seconds a new triplet must wait before its first retry is accepted
delay = 60

# Seconds applied instead of 'delay' when the sender IP is in an RBL
rbl_delay = 3600

# Message sent to Postfix when deferring; @SECONDS@ is replaced with the remaining wait
gl_message = "Temporarily blocked for @SECONDS@ seconds."

# Entries not seen for this many days are removed by the garbage collector
gc_days = 5

# How often (seconds) the garbage collector runs after its initial 10-minute warm-up
gc_interval = 60

# Database URL — the scheme selects the backend and driver:
#   sqlite+aiosqlite:///path/to/greylist.db
#   postgresql+asyncpg://user:pass@host/dbname
#   mysql+aiomysql://user:pass@host/dbname
db_url = "sqlite+aiosqlite:///greylist.db"

# Unix domain socket used by the management commands (stop, gc, statistics)
control_socket = "/var/run/janus/janus.sock"

# DNS-based blocklists — each entry is a zone name (trailing dot optional)
rbls = [
    "bl.spamcop.net.",
    "zen.spamhaus.org.",
]
```

### Postfix integration

In `main.cf`, add Janus as a policy service:

```
smtpd_recipient_restrictions =
    …
    check_policy_service inet:127.0.0.1:1717
    …
```

And in `master.cf`:

```
policy  unix  -       n       n       -       0       spawn
    user=nobody argv=/path/to/janus /etc/janus.toml start
```

Or run Janus as a standalone daemon and point Postfix at its address.

---

## Usage

### Set up the database

Run once before starting the daemon for the first time:

```sh
uv run janus janus.toml create-database
```

### Start

```sh
uv run janus janus.toml start
```

The daemon runs in the foreground. Use your init system (systemd, runit, etc.) to manage it as a service.

### Stop

```sh
uv run janus janus.toml stop
```

Sends a shutdown request via the control socket. The daemon drains active connections and exits cleanly.

### Statistics

```sh
uv run janus janus.toml statistics
```

```
gauge/clients: 3
string/version: janus-0.1.0
counter/requests: 84201
counter/first_insert: 12045
counter/admitted_match: 9318
counter/admitted_awl: 2604
counter/first_reject: 11993
counter/update: 72156
gauge/requests_per_second: 4.2
```

### Trigger garbage collection

```sh
uv run janus janus.toml gc
```

Runs the GC immediately rather than waiting for the next scheduled interval.

### Reset the database

```sh
uv run janus janus.toml reset-database
```

Deletes all greylisting records. The daemon does not need to be stopped first.

---

## How greylisting works

Each SMTP delivery attempt is identified by a **triplet**: the sending IP address, the envelope sender, and the envelope recipient.

```
New triplet?
│
├─ Yes → INSERT, respond defer_if_permit   (first_reject)
│
└─ No  → Has delay elapsed since first_seen?
          │
          ├─ No  → respond defer_if_permit  (first_reject)
          │
          └─ Yes → UPDATE connection_count++
                   │
                   connection_count ≥ 1?
                   │
                   ├─ Yes → respond dunno   (admitted_match)
                   │
                   └─ No  → IP in an RBL?
                             │
                             ├─ Yes → respond defer_if_permit
                             │
                             └─ No  → AWL check:
                                       does this IP have any other
                                       entry older than `delay` with
                                       connection_count ≥ 1?
                                       │
                                       ├─ Yes → respond dunno  (admitted_awl)
                                       └─ No  → respond defer_if_permit
```

### Auto-whitelist (AWL)

Once a sending IP has successfully delivered mail to any recipient (its triplet has `connection_count ≥ 1`), subsequent messages from that IP to *new* recipients pass immediately without waiting for the delay. This avoids repeated delays for large senders with many envelope combinations (mailing list managers, ticketing systems, etc.).

RBL-listed IPs are excluded from the AWL regardless of their history.

### RBL checking

Janus performs a standard DNS A-record lookup of the reversed IP address under each configured RBL zone. A successful lookup means the IP is listed. DNS failures (NXDOMAIN, timeout, no answer) are treated as not listed — Janus fails open.

Senders whose IP is listed in an RBL are subject to `rbl_delay` instead of `delay`.

---

## Logging

Janus logs to syslog (facility `mail`) on Linux and macOS, falling back to stderr if no syslog socket is available.

Key log lines (all at WARNING level so they appear in typical mail logs):

```
janus: helm pass from=<sender@example.com> to=<user@domain.com> ip=1.2.3.4
janus: helm awl from=<sender@example.com> to=<user@domain.com> ip=1.2.3.4
janus: helm blocked from=<sender@example.com> to=<user@domain.com> ip=1.2.3.4 delay remaining=47
```

---

## Development

```sh
uv sync --group test

# Run the full test suite
uv run pytest

# Run with output
uv run pytest -v
```

Tests use an in-memory SQLite database. No external services are required.

---

## Database schema

```sql
CREATE TABLE greylist (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    sender           VARCHAR(255) NOT NULL,
    recipient        VARCHAR(255) NOT NULL,
    ip               VARCHAR(48)  NOT NULL,
    first_seen       REAL NOT NULL,
    last_seen        REAL NOT NULL,
    connection_count INTEGER NOT NULL DEFAULT 0,
    UNIQUE (ip, sender, recipient)
);
```

Timestamps are stored as Unix epoch floats, making the schema identical across SQLite, PostgreSQL, and MySQL without any timezone handling.

---

## License

BSD 3-Clause — see [LICENSE](LICENSE).
