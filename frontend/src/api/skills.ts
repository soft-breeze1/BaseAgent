// Skills API (v9.0 - Progressive Disclosure)
// 简化后的 API：只返回 name + display_name + files（文件夹文件列表）

import client from './client'

export interface SkillItem {
  name: string           // 文件夹名，如 "slide-craft-skill-main"
  display_name: string   // YAML name，如 "slidecraft"
  files: string[]        // 文件列表，如 ["SKILL.md", "references/", "scripts/"]
}

export interface SkillListResponse {
  total: number
  items: SkillItem[]
}

export const skillApi = {
  // 获取技能列表（仅元数据，不含正文）
  list() {
    return client.get<SkillListResponse>('/skills')
  },

  // 同步 skills/ 目录（全量扫描，更新内存缓存）
  sync() {
    return client.post<{ scanned: number; errors: number }>('/skills/sync')
  },
}