"""
模块 A：SkillManager — 基础底层读写
====================================
职责：负责与文件系统交互，作为 Single Source of Truth。

核心原则：
  1. 使用 `python-frontmatter` 库扫描 `.skills/` 下所有一级子文件夹中的 `SKILL.md`
  2. get_active_skills_metadata(): 仅提取并返回 YAML 头部信息，**绝对禁止**返回 Markdown 正文
  3. read_skill_content(folder_name): 根据传入的文件夹名，读取对应的 SKILL.md，**仅返回 Markdown 正文**
  4. 完善的容错拦截：目录不存在、YAML 格式错误、无 parameters 字段等情况优雅跳过

此模块不与任何 LLM 请求逻辑耦合，也不涉及 Schema 构建。
"""

import os
import logging
from pathlib import Path
from typing import Optional, List, Dict

import frontmatter  # python-frontmatter 库

logger = logging.getLogger(__name__)

# 默认技能目录：优先使用环境变量 SKILLS_DIR，未设置时使用 backend/skills/
_DEFAULT_SKILLS_DIR = os.getenv(
    "SKILLS_DIR",
    os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),  # backend/
        "skills",
    ),
)


class SkillManager:
    """
    技能管理器 — 纯文件系统交互，不包含任何业务语义。
    
    是四个模块中最底层的模块，其他三个模块都依赖此模块提供的数据。
    使用 python-frontmatter 库解析 SKILL.md 的 YAML Frontmatter。
    """

    def __init__(self, skills_dir: Optional[str] = None):
        """
        Args:
            skills_dir: 技能目录路径。为 None 时使用默认值。
        """
        self._skills_dir = skills_dir or _DEFAULT_SKILLS_DIR
        logger.info(f"SkillManager 初始化，技能目录: {self._skills_dir}")

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def get_active_skills_metadata(self) -> List[dict]:
        """
        扫描并返回所有有效的技能元数据（仅 YAML 头部，不含 Markdown 正文）。
        
        返回格式：
        [
            {
                "folder_name": "slide_crafter",      # 文件夹名
                "name": "slide_crafter",             # YAML 中的 name
                "display_name": "幻灯片制作助手",     # YAML 中的 display_name
                "description": "用于制作精美幻灯片",   # YAML 中的 description
                "parameters": {"type": "object", "properties": {}, "required": []},  # 参数 Schema
                "version": "1.0.0",                  # 版本
                "category": "general",               # 分类
            },
            ...
        ]
        
        容错处理：
          - 目录不存在 → 记录 warning，返回空列表
          - YAML 格式错误 → 记录 warning，跳过
          - 无 name/description 字段 → 记录 warning，跳过
          - 无 parameters 字段 → 自动补齐空 Schema
        """
        skills_dir = Path(self._skills_dir)
        if not skills_dir.exists() or not skills_dir.is_dir():
            logger.warning(f"技能目录不存在或不可读: {self._skills_dir}")
            return []

        results: List[dict] = []

        # 遍历 skills/ 下所有一级子文件夹
        for item in sorted(skills_dir.iterdir()):
            if not item.is_dir():
                continue  # 只处理文件夹

            folder_name = item.name
            skill_md_path = item / "SKILL.md"
            if not skill_md_path.exists():
                logger.debug(f"跳过文件夹（无 SKILL.md）: {folder_name}")
                continue

            # 使用 python-frontmatter 解析：同时获取 frontmatter 和正文内容
            metadata = self._parse_skill_file(skill_md_path, folder_name)
            if metadata:
                results.append(metadata)

        logger.info(f"技能扫描完成: 发现 {len(results)} 个有效技能（来源: {self._skills_dir}）")
        return results

    def read_skill_content(self, folder_name: str) -> Optional[str]:
        """
        根据文件夹名读取 SKILL.md 的 Markdown 正文。
        
        这是 Step 3（EXECUTOR 阶段）按需加载的核心方法：
          - 仅在 PLANNER 决定调用某个技能后才触发
          - 从磁盘完整读取 SKILL.md
          - 使用正则分离 YAML 头部，**仅返回正文**
        
        Args:
            folder_name: 技能文件夹名（如 "slide_crafter"）
        
        Returns:
            Markdown 正文文本（不含 Frontmatter），或 None（技能不存在/读取失败）
        """
        import re as _re
        skill_md_path = Path(self._skills_dir) / folder_name / "SKILL.md"
        if not skill_md_path.exists():
            logger.warning(f"SKILL.md 不存在: {skill_md_path}")
            return None

        try:
            with open(str(skill_md_path), "r", encoding="utf-8") as _f:
                _content = _f.read()
            # Remove YAML frontmatter (--- ... ---) and return the body
            _body = _re.sub(r'^\s*---\s*\n.*?\n---\s*\n?', '', _content, count=1, flags=_re.DOTALL)
            if _body and _body.strip():
                logger.info(f"已加载技能正文: {folder_name} ({len(_body)} chars)")
                return _body.strip()
            else:
                logger.warning(f"SKILL.md 正文为空: {skill_md_path}")
                return _content.strip() if _content.strip() else None
        except Exception as e:
            logger.warning(f"读取 SKILL.md 正文失败: {skill_md_path}: {e}", exc_info=True)
            return None

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _parse_skill_file(self, skill_md_path: Path, folder_name: str) -> Optional[dict]:
        """
        使用简单正则 + yaml 解析单个 SKILL.md 文件。
        
        返回 YAML 头部的关键字段，不包含正文。
        
        Args:
            skill_md_path: SKILL.md 的完整路径
            folder_name: 技能文件夹名
        
        Returns:
            dict 格式的元数据，或 None（解析失败）
        """
        import re as _re
        import yaml as _yaml
        meta = None
        try:
            with open(str(skill_md_path), "r", encoding="utf-8") as _f:
                _content = _f.read()
            # 匹配 --- 之间的 YAML frontmatter
            _m = _re.match(r'^\s*---\s*\n(.*?)\n---', _content, _re.DOTALL)
            if _m:
                _yaml_str = _m.group(1)
                meta = _yaml.safe_load(_yaml_str)
        except Exception as e:
            logger.warning(f"YAML 解析失败 [{folder_name}]: {skill_md_path}: {e}")
            return None

        if not isinstance(meta, dict) or not meta:
            logger.warning(f"SKILL.md 缺少有效 YAML Frontmatter [{folder_name}]: {skill_md_path}")
            return None

        # 提取必需字段: name 和 description
        name = meta.get("name", "") or meta.get("display_name", "") or folder_name
        description = meta.get("description", "")

        if not name:
            logger.warning(f"SKILL.md 缺少 name 字段，跳过 [{folder_name}]: {skill_md_path}")
            return None
        if not description:
            logger.warning(f"SKILL.md 缺少 description 字段，跳过 [{folder_name}]: {skill_md_path}")
            return None

        # 处理 parameters：如果 YAML 中没有，自动补齐空 Schema
        raw_params = meta.get("parameters", None)
        if raw_params is None:
            # OpenAI function calling 标准：空的 parameters 必须是合法的 object 类型
            parameters = {
                "type": "object",
                "properties": {},
                "required": [],
            }
        elif isinstance(raw_params, dict):
            # 验证 parameters 是否符合基本规范
            if "type" not in raw_params:
                raw_params["type"] = "object"
            if "properties" not in raw_params:
                raw_params["properties"] = {}
            if "required" not in raw_params:
                raw_params["required"] = []
            parameters = raw_params
        else:
            # parameters 存在但不是 dict 格式，回退到空 Schema
            logger.warning(f"parameters 格式异常 [{folder_name}]，已自动补齐空 Schema")
            parameters = {
                "type": "object",
                "properties": {},
                "required": [],
            }

        # 组装返回数据（仅 YAML 头部字段，不含正文）
        return {
            "folder_name": folder_name,
            "name": name,
            "display_name": meta.get("display_name", name),
            "description": description,
            "parameters": parameters,
            "version": str(meta.get("version", "1.0.0")),
            "category": meta.get("category", "general"),
            "author": meta.get("author", ""),
            "icon": str(meta.get("icon", "📋")),
            "requires_tools": meta.get("requires_tools", False),
        }

    # ------------------------------------------------------------------
    # v2.0: 增强匹配方法（增量，不修改任何原有逻辑）
    # ------------------------------------------------------------------

    def _extract_keywords(self, meta: dict) -> List[str]:
        """
        从技能元数据中提取关键词列表。
        优先使用 YAML 中的 keywords 字段，无则从 name/display_name/description 自动提取。
        """
        keywords = meta.get("keywords", None)
        if isinstance(keywords, list) and keywords:
            return [kw.strip().lower() for kw in keywords if kw and kw.strip()]

        # 自动提取兜底：从 name、display_name、description 中提取关键词
        texts = [
            meta.get("name", ""),
            meta.get("display_name", ""),
            meta.get("description", ""),
        ]
        import re as _re
        extracted = set()
        for t in texts:
            if not t:
                continue
            t_lower = t.lower()
            # 提取中文词组（2-6个汉字）
            chinese_words = _re.findall(r'[\u4e00-\u9fff]{2,6}', t_lower)
            for w in chinese_words:
                extracted.add(w)
            # 提取英文单词（含中划线）
            eng_words = _re.findall(r'[a-z][a-z0-9_-]{2,}', t_lower)
            for w in eng_words:
                extracted.add(w)
        return list(extracted)

    def match_skill(self, query: str, threshold: float = 0.6) -> Optional[str]:
        """
        多维度加权匹配：关键词命中（权重0.6）+ 文本相似度（权重0.4）。

        Args:
            query: 用户查询文本
            threshold: 匹配阈值（0.0-1.0），默认0.6

        Returns:
            匹配到的技能 folder_name，无匹配返回 None
        """
        import difflib as _difflib
        query_lower = query.lower().strip()
        if not query_lower:
            return None

        best_score: float = 0.0
        best_folder: Optional[str] = None

        for skill in self.get_active_skills_metadata():
            folder = skill.get("folder_name", "")
            name = skill.get("name", "").lower()
            display_name = skill.get("display_name", "").lower()
            description = skill.get("description", "").lower()

            # ── 维度1: 关键词命中（权重0.6）──
            keywords = self._extract_keywords(skill)
            kw_hits = sum(1 for kw in keywords if kw in query_lower)
            kw_score = min(kw_hits / max(len(keywords), 1), 1.0) * 0.6

            # ── 维度2: 文本相似度（权重0.4）──
            targets = [f for f in [folder.lower(), name, display_name, description] if f]
            sim_score = 0.0
            if targets:
                sim_scores = [_difflib.SequenceMatcher(None, query_lower, t).ratio() for t in targets]
                sim_score = max(sim_scores) * 0.4

            total_score = kw_score + sim_score

            # 特殊规则硬加分：CSDN 博客写作特殊匹配
            if "csdn" in folder.lower() and ("博客" in query_lower or "文章" in query_lower or "blog" in query_lower):
                total_score = max(total_score, 0.75)
            # 幻灯片制作特殊匹配
            if "slide" in folder.lower() and any(w in query_lower for w in ["幻灯片", "ppt", "slide", "presentation", "演示"]):
                total_score = max(total_score, 0.75)
            # 格式化/排版特殊匹配
            if "format" in folder.lower() and any(w in query_lower for w in ["格式", "排版", "美化", "format"]):
                total_score = max(total_score, 0.75)

            if total_score > best_score:
                best_score = total_score
                best_folder = folder

        logger.debug(f"match_skill query='{query}' best={best_folder} score={best_score:.3f}")
        return best_folder if best_score >= threshold else None

    def get_candidate_skills(self, query: str, top_n: int = 3, threshold: float = 0.3) -> List[dict]:
        """
        返回 TopN 候选 Skill 列表，用于 Agent 层引导 LLM。

        Args:
            query: 用户查询文本
            top_n: 返回前 N 个候选
            threshold: 最低匹配阈值

        Returns:
            [{"folder_name": ..., "name": ..., "display_name": ..., "description": ..., "score": ...}, ...]
        """
        import difflib as _difflib
        query_lower = query.lower().strip()
        if not query_lower:
            return []

        scored: List[dict] = []
        for skill in self.get_active_skills_metadata():
            # 复用 match_skill 的多维度打分逻辑
            keywords = self._extract_keywords(skill)
            kw_hits = sum(1 for kw in keywords if kw in query_lower)
            kw_score = min(kw_hits / max(len(keywords), 1), 1.0) * 0.6

            targets = [
                f for f in [
                    skill.get("folder_name", "").lower(),
                    skill.get("name", "").lower(),
                    skill.get("display_name", "").lower(),
                    skill.get("description", "").lower(),
                ] if f
            ]
            sim_score = 0.0
            if targets:
                sim_scores = [_difflib.SequenceMatcher(None, query_lower, t).ratio() for t in targets]
                sim_score = max(sim_scores) * 0.4

            total_score = kw_score + sim_score

            if total_score >= threshold:
                scored.append({
                    "folder_name": skill.get("folder_name", ""),
                    "name": skill.get("name", ""),
                    "display_name": skill.get("display_name", ""),
                    "description": skill.get("description", ""),
                    "score": round(total_score, 3),
                })

        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:top_n]

    @property
    def skills_dir(self) -> str:
        """当前技能目录路径（仅用于调试和日志）"""
        return self._skills_dir
