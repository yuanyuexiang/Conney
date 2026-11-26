"""Freeze confirmed delivery workbooks and track per-recipient SMTP attempts."""

from alembic import op

revision = "0014_delivery_dispatch"
down_revision = "0013_drx_rule_versions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE outgoing_mailbox (
            id integer PRIMARY KEY CHECK (id = 1),
            address varchar(254) NOT NULL,
            host varchar(48) NOT NULL,
            encrypted_password bytea NOT NULL,
            domains text[] NOT NULL,
            test_recipient varchar(254),
            revision uuid NOT NULL,
            verified boolean NOT NULL DEFAULT false
        );
        CREATE TABLE delivery_recipient_groups (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            name varchar(64) NOT NULL UNIQUE,
            addresses text[] NOT NULL,
            active boolean NOT NULL,
            revision integer NOT NULL DEFAULT 1
        );
        CREATE TABLE delivery_releases (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id bigint NOT NULL REFERENCES delivery_drafts(run_id),
            version integer NOT NULL CHECK (version > 0),
            draft_revision integer NOT NULL,
            rule_version varchar(40) NOT NULL,
            output_version varchar(48) NOT NULL,
            ship_through date NOT NULL,
            document_no text,
            filename text NOT NULL,
            workbook bytea NOT NULL,
            checksum char(64) NOT NULL,
            confirmed_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            confirmed_by bigint NOT NULL REFERENCES relay_users(id),
            UNIQUE (run_id, version),
            UNIQUE (run_id, draft_revision)
        );
        CREATE TABLE delivery_dispatches (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            release_id bigint NOT NULL REFERENCES delivery_releases(id),
            request_id uuid NOT NULL UNIQUE,
            fingerprint char(64) NOT NULL,
            mailbox_revision uuid NOT NULL,
            sender varchar(254) NOT NULL,
            subject varchar(180) NOT NULL,
            body text NOT NULL,
            retry_of bigint REFERENCES delivery_dispatches(id),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            created_by bigint NOT NULL REFERENCES relay_users(id),
            UNIQUE (id, release_id)
        );
        CREATE INDEX delivery_dispatch_by_release ON delivery_dispatches (release_id, id DESC);
        CREATE TABLE delivery_dispatch_recipients (
            id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            dispatch_id bigint NOT NULL,
            release_id bigint NOT NULL,
            address varchar(254) NOT NULL,
            departments text[] NOT NULL,
            message_id text NOT NULL UNIQUE,
            state varchar(12) NOT NULL DEFAULT 'queued'
                CHECK (state IN ('queued', 'sending', 'accepted', 'failed', 'uncertain')),
            detail varchar(40),
            smtp_code integer,
            lease_until timestamptz,
            finished_at timestamptz,
            FOREIGN KEY (dispatch_id, release_id) REFERENCES delivery_dispatches(id, release_id),
            UNIQUE (dispatch_id, address)
        );
        CREATE UNIQUE INDEX delivery_dispatch_once_per_address
            ON delivery_dispatch_recipients (release_id, address) WHERE state <> 'failed';
        CREATE INDEX delivery_dispatch_queued ON delivery_dispatch_recipients (id)
            WHERE state = 'queued';
        CREATE INDEX delivery_dispatch_sending ON delivery_dispatch_recipients (lease_until)
            WHERE state = 'sending';
        ALTER TABLE worker_heartbeats DROP CONSTRAINT known_worker;
        ALTER TABLE worker_heartbeats ADD CONSTRAINT known_worker
            CHECK (worker IN ('mail', 'parser', 'delivery'));
    """)


def downgrade() -> None:
    op.execute("""
        DELETE FROM worker_heartbeats WHERE worker = 'delivery';
        ALTER TABLE worker_heartbeats DROP CONSTRAINT known_worker;
        ALTER TABLE worker_heartbeats ADD CONSTRAINT known_worker
            CHECK (worker IN ('mail', 'parser'));
        DROP TABLE delivery_dispatch_recipients;
        DROP TABLE delivery_dispatches;
        DROP TABLE delivery_releases;
        DROP TABLE delivery_recipient_groups;
        DROP TABLE outgoing_mailbox;
    """)
