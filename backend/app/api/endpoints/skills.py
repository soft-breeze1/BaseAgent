"""
Skills API Endpoints (v11.1 — Progressive Disclosure)
======================================================
提供两个端点供前端 "Skill 管理" 页面使用：
  GET /api/v1/skills    → 返回技能元数据列表 + 关键文件结构
  POST /api/v1/skills/sync → 触发全量扫描，更新元数据

底层使用 progressive_disclosure 模块（非旧的 services.skill_manager）。
"""

import os
import logging
from fastapi import APIRouter

from app.progressive_disclosure import create_progressive_disclosure_system

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/skills", tags=["skills"])

# 创建 Progressive Disclosure 系统实例
_pd_system = create_progressive_disclosure_system()
_pd_skill_manager = _pd_system["skill_manager"]

# 只展示的关键文件/文件夹
_KEY_FILES = {"SKILL.md", "references", "scripts"}


def _get_skill_file_tree(folder_name: str) -> list:
    """
    扫描技能文件夹，只返回关键文件和文件夹。
    """
    skills_dir = _pd_skill_manager.skills_dir
    skill_dir = os.path.join(skills_dir, folder_name)
    if not skill_dir or not os.path.isdir(skill_dir):
        return []
    try:
        entries = set(os.listdir(skill_dir))
        result = []
        if "SKILL.md" in entries:
            result.append("SKILL.md")
        if "references" in entries:
            result.append("references/")
        if "scripts" in entries:
            result.append("scripts/")
        return result
    except Exception as e:
        logger.warning(f"扫描技能文件夹结构失败: {skill_dir}: {e}")
        return []


@router.get("", response_model=dict)
async def list_skills():
    """
    获取所有已发现的技能列表（仅元数据 + 文件结构，不含正文）
    """
    skills_meta = _pd_skill_manager.get_active_skills_metadata()
    items = []
    for s in skills_meta:
        items.append({
            "name": s["folder_name"],
            "display_name": s.get("display_name", s["name"]),
            "files": _get_skill_file_tree(s["folder_name"]),
        })
    return {"total": len(items), "items": items}


@router.post("/sync", response_model=dict)
async def scan_skills_directory():
    """
    触发全量扫描 skills/ 目录，更新内存缓存中的技能列表。
    只解析 YAML Frontmatter，不会读取 SKILL.md 正文全文。
    """
    skills_meta = _pd_skill_manager.get_active_skills_metadata()
    return {"scanned": len(skills_meta), "errors": 0}


# 向后兼容：旧前端调用 POST /skills/scan
@router.post("/scan", response_model=dict, include_in_schema=False)
async def scan_skills_directory_legacy():
    """旧版端点别名，兼容未更新前端"""
    return await scan_skills_directory()