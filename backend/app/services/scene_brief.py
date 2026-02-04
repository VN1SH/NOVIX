from datetime import datetime

def _fallback_brief_from_content(content: str) -> str:
    if not content:
        return "No content."
    text = " ".join(content.strip().split())
    return text[:800]

def ensure_scene_brief(db, SceneBrief, scene_id: str, content: str):
    brief_text = _fallback_brief_from_content(content)
    now = datetime.utcnow()

    brief = db.query(SceneBrief).filter(SceneBrief.scene_id == scene_id).first()
    if brief is None:
        brief = SceneBrief(
            scene_id=scene_id,
            content=brief_text,
            created_at=now,
        )
        if hasattr(brief, "updated_at"):
            brief.updated_at = now
        if hasattr(brief, "source"):
            brief.source = "manual_save"
        db.add(brief)
    else:
        brief.content = brief_text
        if hasattr(brief, "updated_at"):
            brief.updated_at = now

    return brief
