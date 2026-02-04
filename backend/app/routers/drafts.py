"""
Drafts Router / 草稿路由
Draft and summary management endpoints / 草稿与摘要管理接口
"""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.schemas.draft import ChapterSummary
from app.services.scene_brief_fallback import ensure_scene_brief_for_draft
from app.storage import DraftStorage
from app.utils.version import increment_version
from app.utils.chapter_id import normalize_chapter_id

router = APIRouter(prefix="/projects/{project_id}/drafts", tags=["drafts"])
draft_storage = DraftStorage()


@router.get("")
async def list_chapters(project_id: str) -> List[str]:
    """List all chapters / 列出所有章节"""
    return await draft_storage.list_chapters(project_id)


@router.get("/summaries", response_model=List[ChapterSummary])
async def list_chapter_summaries(project_id: str, volume_id: Optional[str] = None):
    """List chapter summaries / 列出章节摘要"""
    return await draft_storage.list_chapter_summaries(project_id, volume_id=volume_id)


@router.get("/{chapter}/scene-brief")
async def get_scene_brief(project_id: str, chapter: str):
    """Get scene brief / 获取场景简报"""
    brief = await draft_storage.get_scene_brief(project_id, chapter)
    if not brief:
        raise HTTPException(status_code=404, detail="Scene brief not found")
    return brief


@router.get("/{chapter}/versions")
async def list_draft_versions(project_id: str, chapter: str) -> List[str]:
    """List all draft versions for a chapter / 列出章节草稿版本"""
    return await draft_storage.list_draft_versions(project_id, chapter)


@router.get("/{chapter}/{version}")
async def get_draft(project_id: str, chapter: str, version: str):
    """Get a specific draft version / 获取指定草稿版本"""
    draft = await draft_storage.get_draft(project_id, chapter, version)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    return draft


@router.get("/{chapter}/review")
async def get_review(project_id: str, chapter: str):
    """Get review result / 获取审稿结果"""
    review = await draft_storage.get_review(project_id, chapter)
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    return review


@router.get("/{chapter}/final")
async def get_final_draft(project_id: str, chapter: str):
    """Get final draft / 获取成稿"""
    final = await draft_storage.get_final_draft(project_id, chapter)
    if not final:
        raise HTTPException(status_code=404, detail="Final draft not found")
    return {"content": final, "word_count": len(final)}


@router.get("/{chapter}/summary")
async def get_chapter_summary(project_id: str, chapter: str):
    """Get chapter summary / 获取章节摘要"""
    summary = await draft_storage.get_chapter_summary(project_id, chapter)
    if not summary:
        raise HTTPException(status_code=404, detail="Summary not found")
    return summary


@router.post("/{chapter}/summary")
async def save_chapter_summary(project_id: str, chapter: str, summary: ChapterSummary):
    """Save chapter summary / 保存章节摘要"""
    await draft_storage.save_chapter_summary(project_id, summary)
    return {"success": True, "message": "Summary saved"}


@router.delete("/{chapter}")
async def delete_chapter(project_id: str, chapter: str):
    """Delete chapter artifacts / 删除章节相关内容"""
    deleted = await draft_storage.delete_chapter(project_id, chapter)
    if not deleted:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return {"success": True}


class UpdateContentRequest(BaseModel):
    content: str
    title: Optional[str] = None


@router.put("/{chapter}/content")
async def update_draft_content(project_id: str, chapter: str, body: UpdateContentRequest):
    """
    Update draft content manually / 手动更新草稿内容
    """
    versions = await draft_storage.list_draft_versions(project_id, chapter)
    next_version = "v1" if not versions else increment_version(versions[-1])

    draft = await draft_storage.save_draft(
        project_id=project_id,
        chapter=chapter,
        version=next_version,
        content=body.content,
        word_count=len(body.content),
    )

    canonical = normalize_chapter_id(chapter) or draft.chapter or chapter
    resolved_title = body.title
    if body.title is not None:
        summary = await draft_storage.get_chapter_summary(project_id, canonical)
        if summary:
            summary.title = body.title
            summary.word_count = len(body.content)
        else:
            summary = ChapterSummary(
                chapter=canonical,
                title=body.title,
                word_count=len(body.content),
            )
        await draft_storage.save_chapter_summary(project_id, summary)
        resolved_title = summary.title

    scene_brief = await draft_storage.get_scene_brief(project_id, canonical)
    if not scene_brief:
        await ensure_scene_brief_for_draft(
            draft_storage=draft_storage,
            project_id=project_id,
            chapter=canonical,
            content=body.content,
            title=resolved_title,
        )

    return {
        "success": True,
        "version": next_version,
        "message": "Content saved",
        "chapter": canonical,
        "title": body.title,
    }
