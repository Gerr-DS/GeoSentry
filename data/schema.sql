CREATE TABLE IF NOT EXISTS social_threats (
    id            BIGSERIAL PRIMARY KEY,
    sumber        VARCHAR(20) NOT NULL,
    teks          TEXT NOT NULL,
    label         VARCHAR(20),
    probabilitas  REAL,
    geom          GEOMETRY(Point, 4326),
    lokasi_nama   VARCHAR(120),
    url           TEXT,
    referensi_urls JSONB DEFAULT '[]'::jsonb,
    status_tayang BOOLEAN DEFAULT FALSE,
    dikoreksi     BOOLEAN DEFAULT FALSE,
    dibuat_pada   TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_threats_geom   ON social_threats USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_threats_status ON social_threats (status_tayang);
CREATE INDEX IF NOT EXISTS idx_threats_label  ON social_threats (label);
CREATE INDEX IF NOT EXISTS idx_threats_id_asc ON social_threats (id);

CREATE TABLE IF NOT EXISTS gazetteer (
    id       BIGSERIAL PRIMARY KEY,
    nama     VARCHAR(120) NOT NULL UNIQUE,
    provinsi VARCHAR(120),
    lat      DOUBLE PRECISION NOT NULL,
    lon      DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_gazetteer_nama ON gazetteer (nama);
