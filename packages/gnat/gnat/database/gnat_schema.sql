
CREATE TABLE inputs
(
    input_id INTEGER PRIMARY KEY NOT NULL,
    input_name TEXT,
    is_intermeidate INTEGER
);

CREATE TABLE fields
(
    field_id INTEGER PRIMARY KEY NOT NULL,
    field_name TEXT
);

CREATE TABLE summary_methods
(
    method_id INTEGER PRIMARY KEY NOT NULL,
    method_name TEXT
);

CREATE TABLE attributes
(
    attribute_id INTEGER PRIMARY KEY NOT NULL, 
    attribute_name TEXT,
    dataset_id INTEGER,
    field_id INTEGER,
    method_id INTEGER,

    CONSTRAINT fk_inputs_input_id FOREIGN KEY (dataset_id) REFERENCES inputs (input_id),
    CONSTRAINT fk_inputs_input_id FOREIGN KEY (field_id) REFERENCES fields (field_id),
    CONSTRAINT fk_inputs_input_id FOREIGN KEY (method_id) REFERENCES summary_methods (method_id)
);

CREATE TABLE attribute_inputs
(
    attribute_id INTEGER,
    input_id INTEGER,

    CONSTRAINT pk_attribute_inputs PRIMARY KEY (attribute_id, input_id),
    CONSTRAINT fk_attribute_inputs_input_id FOREIGN KEY (input_id) REFERENCES inputs (input_id),
    CONSTRAINT fk_attribute_inputs_attribute_id FOREIGN KEY (attribute_id) REFERENCES attributes (attribute_id)
);

CREATE TABLE riverscape_attributes 
(
    riverscape_id INTEGER NOT NULL,
    attribute_id INTEGER NOT NULL,
    value TEXT,

    CONSTRAINT pk_riverscape_attribute PRIMARY KEY (riverscape_id, attribute_id),
    CONSTRAINT fk_riverscape_attribute_riverscape_id FOREIGN KEY (riverscape_id) REFERENCES riverscapes (riverscape_id) ON DELETE CASCADE,
    CONSTRAINT fk_riverscape_attribute_attribute_id FOREIGN KEY (attribute_id) REFERENCES attributes (attribute_id)
);

INSERT INTO gpkg_contents (table_name, data_type)
VALUES ('attributes', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type)
VALUES ('riverscape_attributes', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type)
VALUES ('attribute_inputs', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type)
VALUES ('inputs', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type)
VALUES ('fields', 'attributes');
INSERT INTO gpkg_contents (table_name, data_type)
VALUES ('summary_methods', 'attributes');

