-- Reset all knowledge documents to pending status for Qdrant re-indexing
-- This triggers re-processing of all documents on next Celery task run
-- Run this after switching from Chroma to Qdrant

UPDATE knowledge_document 
SET status = 'pending', 
    chunk_count = NULL, 
    error_message = NULL
WHERE status IN ('ready', 'error');