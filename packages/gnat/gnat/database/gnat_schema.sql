
CREATE TABLE attributes
(
    attribute_id INTEGER PRIMARY KEY NOT NULL, 
    attribute_name TEXT,
    machine_name TEXT
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
