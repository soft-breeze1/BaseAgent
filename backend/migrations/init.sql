-- BaseAgent MySQL Initialization Script
-- This runs automatically on first MySQL container startup

CREATE DATABASE IF NOT EXISTS baseagent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE baseagent;

-- Tables are created by SQLAlchemy on backend startup,
-- but we ensure the database exists here.