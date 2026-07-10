-- Add aborted column to chat_message table
ALTER TABLE chat_messages ADD COLUMN aborted TINYINT(1) DEFAULT 0 NULL COMMENT '用户终止/被中断';