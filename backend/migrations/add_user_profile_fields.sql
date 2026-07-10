-- Migration: Create user_profiles table for avatar and nickname
-- Date: 2026-06-05
-- Description: 使用独立表存储用户个人信息，不修改users原表
-- Run this manually: docker exec -i baseagent-mysql mysql -u root -p baseagent < migrations/add_user_profile_fields.sql

USE baseagent;

CREATE TABLE IF NOT EXISTS user_profiles (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL UNIQUE COMMENT '关联users表ID',
    avatar VARCHAR(255) NULL COMMENT '用户头像URL',
    nickname VARCHAR(50) NULL COMMENT '用户昵称',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;