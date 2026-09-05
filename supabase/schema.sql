-- Device telemetry.
--
-- Holds the live state of installed equipment: whether it is reporting, how
-- much battery it has left, and when it was last heard from. Rows here change
-- constantly, which is why this lives apart from the customer records.
--
-- Households are referenced by the identifier this project assigns rather than
-- by the CRM's own record id, so the two systems can be re-pointed or reloaded
-- independently without the link breaking.

CREATE TABLE IF NOT EXISTS devices (
    external_id          TEXT PRIMARY KEY,
    customer_external_id TEXT NOT NULL,
    name                 TEXT NOT NULL,
    device_type          TEXT NOT NULL
                         CHECK (device_type IN ('control_panel', 'door_sensor', 'window_sensor',
                                                'motion_sensor', 'camera', 'keypad')),
    status               TEXT NOT NULL
                         CHECK (status IN ('online', 'offline', 'low_battery')),
    battery_pct          INTEGER CHECK (battery_pct BETWEEN 0 AND 100),
    last_seen            TIMESTAMPTZ,
    -- Whether a power cycle brings this unit back. Equipment that has failed
    -- repeatedly does not, and that is the signal that a call should reach a
    -- person instead of another round of the same instructions.
    recovers_on_reset    BOOLEAN NOT NULL DEFAULT TRUE,
    notes                TEXT NOT NULL DEFAULT '',
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Every lookup during a call starts from the household.
CREATE INDEX IF NOT EXISTS devices_customer_idx ON devices (customer_external_id);

-- Reporting on what is currently broken.
CREATE INDEX IF NOT EXISTS devices_status_idx ON devices (status) WHERE status <> 'online';

-- Row level security is on and no policy is defined, on purpose: the key that
-- ships to a browser can therefore read nothing at all.
ALTER TABLE devices ENABLE ROW LEVEL SECURITY;

-- The backend role bypasses row level security, but still needs table
-- privileges to be granted explicitly. A table created through the dashboard
-- picks these up automatically; one created over a direct connection, as this
-- is, does not. The browser-facing roles are deliberately left with nothing.
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE devices TO service_role;

-- New tables in this schema inherit a default grant that includes TRUNCATE for
-- the browser-facing roles. Truncation ignores row level security, so leaving it
-- in place would let anyone holding the publishable key empty the table despite
-- the policy above. Strip those defaults back.
REVOKE ALL ON TABLE devices FROM anon, authenticated;

-- Read privilege is then handed back to the signed-in role, which is what the
-- project dashboard browses with. It grants no actual read access: row level
-- security is on with no policy defined, so every row is still filtered out.
-- The privilege only decides whether the query is refused outright or returns
-- nothing, and being refused is what leaves the table looking broken in the UI.
GRANT SELECT ON TABLE devices TO authenticated;

CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS devices_set_updated_at ON devices;
CREATE TRIGGER devices_set_updated_at
    BEFORE UPDATE ON devices
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();
