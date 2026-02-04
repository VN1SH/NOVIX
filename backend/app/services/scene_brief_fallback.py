from typing import Optional

from app.schemas.draft import SceneBrief
from app.services.scene_brief import _fallback_brief_from_content
from app.utils.chapter_id import normalize_chapter_id


def _build_fallback_scene_brief(chapter: str, content: str, title: Optional[str] = None) -> SceneBrief:
    canonical = normalize_chapter_id(chapter) or str(chapter)
    brief_text = _fallback_brief_from_content(content)
    return SceneBrief(
        chapter=canonical,
        title=title or canonical,
        goal=brief_text,
        characters=[],
        timeline_context={},
        world_constraints=[],
        facts=[],
        style_reminder="",
        forbidden=[],
    )


async def ensure_scene_brief_for_draft(
    draft_storage,
    project_id: str,
    chapter: str,
    content: str,
    title: Optional[str] = None,
) -> SceneBrief:
    scene_brief = _build_fallback_scene_brief(chapter=chapter, content=content, title=title)
    await draft_storage.save_scene_brief(project_id, chapter, scene_brief)
    return scene_brief
