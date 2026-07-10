-- Migration: Add ReAct-compatible columns to chat_messages
-- Adds tool_call_id and tool_calls columns for full ReAct loop support
-- Run: mysql -u baseagent -p baseagent < backend/migrations/add_react_columns.sql

ALTER TABLE chat_messages
    ADD COLUMN tool_call_id VARCHAR(100) NULL COMMENT 'For tool role: the tool_call id this result belongs to' AFTER content,
    ADD COLUMN tool_calls TEXT NULL COMMENT 'JSON serialized tool_call list (name, args, id) for assistant messages' AFTER tool_call_id;

-- Update the comment on role column to reflect all supported roles
ALTER TABLE chat_messages
    MODIFY COLUMN role VARCHAR(20) NOT NULL COMMENT 'user / assistant / tool / system';

-- Update the comment on route_used to reflect full ReAct route types
ALTER TABLE chat_messages
    MODIFY COLUMN route_used VARCHAR(50) NULL COMMENT 'rag / web_search / llm / tools / skill / skill_pending / error';