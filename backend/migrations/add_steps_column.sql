-- Migration: Add steps column to chat_messages table
-- Date: 2026-05-30
-- Description: Stores thinking steps/process for each assistant message
-- Run this manually: docker exec -i baseagent-mysql mysql -u root -p baseagent < migrations/add_steps_column.sql

USE baseagent;

ALTER TABLE chat_messages 
ADD COLUMN steps TEXT NULL COMMENT 'JSON serialized thinking steps' 
AFTER content;