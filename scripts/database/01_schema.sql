CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    value_type TEXT NOT NULL DEFAULT 'string',
    description TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by TEXT
);

CREATE TABLE IF NOT EXISTS chart_settings (
    metric TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    color_hex TEXT NOT NULL,
    visible_default BOOLEAN NOT NULL DEFAULT true,
    unit TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS measures_raw (
    id BIGSERIAL PRIMARY KEY,
    ts TIMESTAMPTZ NOT NULL,
    prod_power_w DOUBLE PRECISION,
    prod_energy_wh DOUBLE PRECISION,
    prod_ret_energy_wh DOUBLE PRECISION,
    prod_voltage_v DOUBLE PRECISION,
    prod_current_a DOUBLE PRECISION,
    prod_freq_hz DOUBLE PRECISION,
    prod_pf DOUBLE PRECISION,
    cons_power_w DOUBLE PRECISION,
    cons_energy_wh DOUBLE PRECISION,
    cons_ret_energy_wh DOUBLE PRECISION,
    cons_voltage_v DOUBLE PRECISION,
    cons_current_a DOUBLE PRECISION,
    cons_freq_hz DOUBLE PRECISION,
    cons_pf DOUBLE PRECISION,
    shelly_unixtime BIGINT,
    shelly_uptime BIGINT,
    wifi_rssi INTEGER,
    source TEXT NOT NULL DEFAULT 'shelly'
);
CREATE INDEX IF NOT EXISTS idx_measures_raw_ts ON measures_raw(ts);

CREATE TABLE IF NOT EXISTS energy_daily (
    day DATE PRIMARY KEY,
    production_wh DOUBLE PRECISION,
    consumption_wh DOUBLE PRECISION,
    grid_import_wh DOUBLE PRECISION,
    feed_in_wh DOUBLE PRECISION,
    self_consumption_wh DOUBLE PRECISION,
    source TEXT NOT NULL,
    quality TEXT NOT NULL,
    samples INTEGER DEFAULT 0,
    is_anomaly BOOLEAN NOT NULL DEFAULT false,
    anomaly_note TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS tariffs (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    valid_from DATE NOT NULL,
    valid_to DATE,
    import_eur_kwh DOUBLE PRECISION NOT NULL DEFAULT 0.30,
    feed_in_eur_kwh DOUBLE PRECISION NOT NULL DEFAULT 0.10,
    fixed_monthly_eur DOUBLE PRECISION NOT NULL DEFAULT 0,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS reports (
    id BIGSERIAL PRIMARY KEY,
    report_type TEXT NOT NULL,
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    file_pdf TEXT,
    file_xlsx TEXT,
    status TEXT NOT NULL DEFAULT 'created',
    message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alert_rules (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    rule_type TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT true,
    threshold_value DOUBLE PRECISION,
    severity TEXT NOT NULL DEFAULT 'medium',
    cooldown_minutes INTEGER NOT NULL DEFAULT 60,
    params JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS alert_events (
    id BIGSERIAL PRIMARY KEY,
    rule_id BIGINT REFERENCES alert_rules(id),
    status TEXT NOT NULL DEFAULT 'open',
    severity TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS users_local (
    id BIGSERIAL PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'readonly',
    active BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    actor TEXT,
    action TEXT NOT NULL,
    object_type TEXT,
    object_key TEXT,
    details JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO chart_settings(metric,label,color_hex,unit,sort_order) VALUES
('production_wh','Produzione','#2ecc71','Wh',10),
('consumption_wh','Consumi','#e74c3c','Wh',20),
('grid_import_wh','Prelievi','#3498db','Wh',30),
('feed_in_wh','Immissioni','#f1c40f','Wh',40),
('self_consumption_wh','Autoconsumo','#9b59b6','Wh',50)
ON CONFLICT(metric) DO NOTHING;

INSERT INTO tariffs(name,valid_from,import_eur_kwh,feed_in_eur_kwh,fixed_monthly_eur,notes)
VALUES('Default','2026-01-01',0.30,0.10,0,'Valori iniziali da aggiornare in configurazione')
ON CONFLICT DO NOTHING;
