-- Migration: Add progress tracking columns to knowledge_documents
-- Date: 2026-06-18

ALTER TABLE knowledge_documents
  ADD COLUMN progress INT DEFAULT 0,
  ADD COLUMN progress_message VARCHAR(500) DEFAULT NULL;
