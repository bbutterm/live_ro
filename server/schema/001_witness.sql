-- Initial migration ONLY; fail if schema already exists. Rollback: remove NPC include, then DROP DATABASE residents (only after preserving required events).
CREATE DATABASE residents CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE TABLE residents.registry (
 char_id INT PRIMARY KEY,
 resident_id VARCHAR(24) NOT NULL UNIQUE,
 enabled TINYINT NOT NULL DEFAULT 1
);
CREATE TABLE residents.resident_events (
 id BIGINT AUTO_INCREMENT PRIMARY KEY,
 ts DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
 char_id INT NOT NULL,
 kind VARCHAR(16) NOT NULL,
 map VARCHAR(16), x SMALLINT, y SMALLINT,
 a1 INT, a2 INT,
 INDEX(char_id,id)
);
GRANT SELECT ON residents.registry TO 'ro_residents_srv'@'127.0.0.1';
GRANT INSERT ON residents.resident_events TO 'ro_residents_srv'@'127.0.0.1';
