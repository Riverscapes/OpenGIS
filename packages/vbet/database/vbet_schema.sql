CREATE TABLE inputs (
    input_id    INTEGER PRIMARY KEY UNIQUE NOT NULL,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    metadata    TEXT
);

CREATE TABLE transform_types (
    type_id     INTEGER PRIMARY KEY UNIQUE NOT NULL,
    name        TEXT UNIQUE NOT NULL,
    description TEXT,
    metadata    TEXT
);

CREATE TABLE transforms (
    transform_id    INTEGER PRIMARY KEY UNIQUE NOT NULL,
    type_id         INTEGER NOT NULL,
    input_id        INTEGER NOT NULL,
    name            TEXT UNIQUE NOT NULL,
    description     TEXT,
    metadata        TEXT,

    CONSTRAINT fk_transforms_type_id FOREIGN KEY (type_id) REFERENCES transform_types(type_id),
    CONSTRAINT fk_transforms_input_id FOREIGN KEY (input_id) REFERENCES inputs(input_id) ON DELETE CASCADE
);

CREATE TABLE inflections (
    inflection_id   INTEGER PRIMARY KEY UNIQUE NOT NULL,
    transform_id    INTEGER NOT NULL,
    input_value     REAL NOT NULL,
    output_value    REAL NOT NULL,

    CONSTRAINT fk_inflections_transform_id FOREIGN KEY (transform_id) REFERENCES transforms(transform_id) ON DELETE CASCADE,
    CONSTRAINT ck_inflections_output_value CHECK (output_value >= 0 AND output_value <= 1)
);

CREATE TABLE scenarios (
    scenario_id     INTEGER PRIMARY KEY UNIQUE NOT NULL,
    scenario_name   TEXT,
    machine_code    TEXT,
    scenario_description TEXT
);

CREATE TABLE scenario_inputs (
    scenario_input_id   INTEGER PRIMARY KEY UNIQUE NOT NULL,
    scenario_id         INTEGER NOT NULL,
    input_id            INTEGER NOT NULL,
    weight              REAL,

    CONSTRAINT fk_scenario_id FOREIGN KEY (scenario_id) REFERENCES scenarios(scenario_id) ON DELETE CASCADE,
    CONSTRAINT fk_input_id FOREIGN KEY (input_id) REFERENCES inputs(input_id) ON DELETE CASCADE
);

CREATE TABLE input_zones (
    zone_id             INTEGER PRIMARY KEY UNIQUE NOT NULL,
    scenario_input_id   INTEGER NOT NULL,
    transform_id        INTEGER NOT NULL,
    min_da              REAL,
    max_da              REAL,

    CONSTRAINT fk_scenario_input_id FOREIGN KEY (scenario_input_id) REFERENCES scenario_inputs(scenario_input_id) ON DELETE CASCADE,
    CONSTRAINT fk_transform_id FOREIGN KEY (transform_id) REFERENCES transforms(transform_id) ON DELETE CASCADE
);

INSERT INTO gpkg_contents (table_name, data_type) VALUES ('inputs', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type) VALUES ('transform_types', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type) VALUES ('transforms', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type) VALUES ('inflections', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type) VALUES ('scenarios', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type) VALUES ('scenario_inputs', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type) VALUES ('input_zones', 'attributes');
