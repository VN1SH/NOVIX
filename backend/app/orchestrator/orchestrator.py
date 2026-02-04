"""
Orchestrator
Coordinates the multi-agent writing workflow.
"""

from enum import Enum
import asyncio
import os
from typing import Any, Callable, Dict, List, Optional, Tuple

from app.llm_gateway import get_gateway
from app.storage import CardStorage, CanonStorage, DraftStorage
from app.agents import ArchivistAgent, WriterAgent, EditorAgent
from app.context_engine.select_engine import ContextSelectEngine
from app.context_engine.trace_collector import trace_collector
from app.orchestrator.storage_adapter import UnifiedStorageAdapter
from app.utils.chapter_id import ChapterIDValidator
from app.utils.logger import get_logger
from app.schemas.canon import Fact, TimelineEvent, CharacterState
from app.schemas.draft import ChapterSummary, CardProposal
from app.schemas.card import CharacterCard, WorldCard, StyleCard

logger = get_logger(__name__)


class SessionStatus(str, Enum):
    """Session status values."""

    IDLE = "idle"
    GENERATING_BRIEF = "generating_brief"
    WRITING_DRAFT = "writing_draft"
    EDITING = "editing"
    WAITING_FEEDBACK = "waiting_feedback"
    WAITING_USER_INPUT = "waiting_user_input"
    COMPLETED = "completed"
    ERROR = "error"


class Orchestrator:
    """Coordinates the multi-agent writing workflow."""

    def __init__(self, data_dir: str = "../data", progress_callback: Optional[Callable] = None):
        self.card_storage = CardStorage(data_dir)
        self.canon_storage = CanonStorage(data_dir)
        self.draft_storage = DraftStorage(data_dir)

        self.gateway = get_gateway()

        self.archivist = ArchivistAgent(self.gateway, self.card_storage, self.canon_storage, self.draft_storage)
        self.writer = WriterAgent(self.gateway, self.card_storage, self.canon_storage, self.draft_storage)
        self.editor = EditorAgent(self.gateway, self.card_storage, self.canon_storage, self.draft_storage)

        self.storage_adapter = UnifiedStorageAdapter(self.card_storage, self.canon_storage, self.draft_storage)
        self.select_engine = ContextSelectEngine()

        self.progress_callback = progress_callback
        self.current_status = SessionStatus.IDLE
        self.current_project_id: Optional[str] = None
        self.current_chapter: Optional[str] = None
        self.iteration_counts: Dict[Tuple[str, str], int] = {}
        max_iterations_env = os.getenv("NOVIX_MAX_ITERATIONS")
        self.max_iterations = int(max_iterations_env) if max_iterations_env and max_iterations_env.isdigit() else 10
        self._stream_task: Optional[asyncio.Task] = None

    async def start_session(
        self,
        project_id: str,
        chapter: str,
        chapter_title: str,
        chapter_goal: str,
        target_word_count: int = 3000,
        character_names: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Start a new writing session."""
        self.current_project_id = project_id
        self.current_chapter = chapter
        self.iteration_counts[self._get_iteration_key(project_id, chapter)] = 0

        try:
            try:
                await trace_collector.start_agent_trace("archivist", f"{project_id}:{chapter}")
            except Exception as exc:
                logger.warning(f"Trace start failed: {exc}")

            await self._update_status(SessionStatus.GENERATING_BRIEF, "Archivist is preparing the scene brief...")

            archivist_result = await self.archivist.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "chapter_title": chapter_title,
                    "chapter_goal": chapter_goal,
                    "characters": character_names or [],
                },
            )

            if not archivist_result.get("success"):
                try:
                    await trace_collector.end_agent_trace("archivist", status="failed")
                except Exception as exc:
                    logger.warning(f"Trace end failed: {exc}")
                return await self._handle_error("Scene brief generation failed")

            scene_brief = archivist_result["scene_brief"]
            try:
                await trace_collector.end_agent_trace("archivist", status="completed")
            except Exception as exc:
                logger.warning(f"Trace end failed: {exc}")

            context_bundle = await self._prepare_writer_context(
                project_id=project_id,
                chapter=chapter,
                chapter_goal=chapter_goal,
                scene_brief=scene_brief,
                character_names=character_names,
            )
            writer_context = context_bundle["writer_context"]
            critical_items = context_bundle["critical_items"]
            dynamic_items = context_bundle["dynamic_items"]

            questions = await self.writer.generate_questions(
                context_package=writer_context.get("context_package"),
                scene_brief=scene_brief,
                chapter_goal=chapter_goal,
            )
            if questions:
                await self._update_status(SessionStatus.WAITING_USER_INPUT, "Waiting for user input...")
                return {
                    "success": True,
                    "status": SessionStatus.WAITING_USER_INPUT,
                    "questions": questions,
                    "scene_brief": scene_brief,
                }

            try:
                summary_text = str(getattr(scene_brief, "summary", scene_brief))[:100]
                await trace_collector.record_handoff(
                    "archivist",
                    "writer",
                    f"Scene brief prepared: {summary_text}...",
                )
                await trace_collector.start_agent_trace("writer", f"{project_id}:{chapter}")
                await trace_collector.record_context_select(
                    "writer",
                    selected_count=len(critical_items) + len(dynamic_items),
                    total_candidates=100,
                    token_usage=sum(len(str(i.content)) for i in critical_items + dynamic_items),
                )
            except Exception as exc:
                logger.warning(f"Trace writer setup failed: {exc}")

            result = await self._run_writing_flow(
                project_id=project_id,
                chapter=chapter,
                writer_context=writer_context,
                target_word_count=target_word_count,
            )
            if result.get("success"):
                result["scene_brief"] = scene_brief
            return result

        except Exception as exc:
            return await self._handle_error(f"Session error: {exc}")

    async def answer_questions(
        self,
        project_id: str,
        chapter: str,
        chapter_title: str,
        chapter_goal: str,
        target_word_count: int,
        answers: List[Dict[str, str]],
        character_names: Optional[list] = None,
    ) -> Dict[str, Any]:
        """Continue session after user answers pre-writing questions."""
        self.current_project_id = project_id
        self.current_chapter = chapter
        self.iteration_counts[self._get_iteration_key(project_id, chapter)] = 0

        scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
        if not scene_brief:
            archivist_result = await self.archivist.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "chapter_title": chapter_title,
                    "chapter_goal": chapter_goal,
                    "characters": character_names or [],
                },
            )
            if not archivist_result.get("success"):
                return await self._handle_error("Scene brief generation failed")
            scene_brief = archivist_result["scene_brief"]

        context_bundle = await self._prepare_writer_context(
            project_id=project_id,
            chapter=chapter,
            chapter_goal=chapter_goal,
            scene_brief=scene_brief,
            character_names=character_names,
        )
        writer_context = context_bundle["writer_context"]
        writer_context["user_answers"] = answers

        result = await self._run_writing_flow(
            project_id=project_id,
            chapter=chapter,
            writer_context=writer_context,
            target_word_count=target_word_count,
        )
        if result.get("success"):
            result["scene_brief"] = scene_brief
        return result

    async def process_feedback(
        self,
        project_id: str,
        chapter: str,
        feedback: str,
        action: str = "revise",
        rejected_entities: list = None,
    ) -> Dict[str, Any]:
        """Process user feedback."""
        if action == "confirm":
            return await self._finalize_chapter(project_id, chapter)

        self.current_project_id = project_id
        self.current_chapter = chapter
        iteration_key = self._get_iteration_key(project_id, chapter)
        self.iteration_counts[iteration_key] = self.iteration_counts.get(iteration_key, 0) + 1
        iteration_count = self.iteration_counts[iteration_key]
        if iteration_count >= self.max_iterations:
            logger.debug(
                "Maximum revision iterations reached (key=%s, current=%s, max=%s, func=process_feedback)",
                iteration_key,
                iteration_count,
                self.max_iterations,
            )
            return {
                "success": False,
                "error": "Maximum iterations reached",
                "message": "Maximum revision iterations reached.",
            }

        try:
            versions = await self.draft_storage.list_draft_versions(project_id, chapter)
            latest_version = versions[-1] if versions else "v1"
            latest_draft = await self.draft_storage.get_draft(project_id, chapter, latest_version)
            draft_length = len(latest_draft.content) if latest_draft and latest_draft.content else 0

            if draft_length <= 500:
                await self._update_status(SessionStatus.WRITING_DRAFT, "Writer is refining based on feedback...")
                scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
                if not scene_brief:
                    return await self._handle_error("Scene brief not found for rewrite")

                context_bundle = await self._prepare_writer_context(
                    project_id=project_id,
                    chapter=chapter,
                    chapter_goal=scene_brief.goal,
                    scene_brief=scene_brief,
                    character_names=None,
                )
                writer_context = context_bundle["writer_context"]
                writer_context["user_feedback"] = feedback

                writer_result = await self.writer.execute(
                    project_id=project_id,
                    chapter=chapter,
                    context=writer_context,
                )
                if not writer_result.get("success"):
                    return await self._handle_error("Rewrite failed")

                draft = writer_result["draft"]
                await self._update_status(SessionStatus.WAITING_FEEDBACK, "Waiting for user feedback...")
                proposals = await self._detect_proposals(project_id, draft)

                return {
                    "success": True,
                    "status": SessionStatus.WAITING_FEEDBACK,
                    "draft": draft,
                    "version": draft.version,
                    "iteration": iteration_count,
                    "proposals": proposals,
                }

            await self._update_status(SessionStatus.EDITING, "Revising based on feedback...")

            editor_result = await self.editor.execute(
                project_id=project_id,
                chapter=chapter,
                context={
                    "draft_version": latest_version,
                    "user_feedback": feedback,
                    "rejected_entities": rejected_entities or [],
                },
            )

            if not editor_result.get("success"):
                return await self._handle_error("Revision failed")

            await self._update_status(SessionStatus.WAITING_FEEDBACK, "Waiting for user feedback...")

            proposals = await self._detect_proposals(project_id, editor_result["draft"])

            return {
                "success": True,
                "status": SessionStatus.WAITING_FEEDBACK,
                "draft": editor_result["draft"],
                "version": editor_result.get("version", latest_version),
                "iteration": iteration_count,
                "proposals": proposals,
            }

        except Exception as exc:
            return await self._handle_error(f"Feedback processing error: {exc}")

    async def _finalize_chapter(self, project_id: str, chapter: str) -> Dict[str, Any]:
        """Finalize chapter and save final draft."""
        try:
            versions = await self.draft_storage.list_draft_versions(project_id, chapter)
            if not versions:
                return await self._handle_error("No draft found to finalize")

            latest_version = versions[-1]
            draft = await self.draft_storage.get_draft(project_id, chapter, latest_version)
            if not draft:
                return await self._handle_error("No draft content found to finalize")

            await self.draft_storage.save_final_draft(project_id=project_id, chapter=chapter, content=draft.content)

            await self._analyze_content(project_id, chapter, draft.content)

            await self._update_status(SessionStatus.COMPLETED, "Chapter completed.")

            return {
                "success": True,
                "status": SessionStatus.COMPLETED,
                "message": "Chapter finalized successfully",
                "final_draft": draft,
            }

        except Exception as exc:
            return await self._handle_error(f"Finalization error: {exc}")

    async def _prepare_writer_context(
        self,
        project_id: str,
        chapter: str,
        chapter_goal: str,
        scene_brief: Any,
        character_names: Optional[list],
    ) -> Dict[str, Any]:
        """Prepare context for writer and return trace info."""
        critical_items = await self.select_engine.deterministic_select(
            project_id, "writer", self.storage_adapter
        )

        query = f"{scene_brief.title} {scene_brief.goal}" if scene_brief else chapter_goal
        dynamic_items = await self.select_engine.retrieval_select(
            project_id=project_id,
            query=query,
            item_types=["character", "world", "fact"],
            storage=self.storage_adapter,
            top_k=10,
        )

        style_card = next((item.content for item in critical_items if item.type.value == "style_card"), None)

        character_cards = []
        world_cards = []
        facts = []

        for item in dynamic_items:
            if item.type.value == "character":
                name = item.id.replace("char_", "")
                card = await self.card_storage.get_character_card(project_id, name)
                if card:
                    character_cards.append(card)
            elif item.type.value == "world":
                name = item.id.replace("world_", "")
                card = await self.card_storage.get_world_card(project_id, name)
                if card:
                    world_cards.append(card)
            elif item.type.value == "fact":
                facts.append(item.content)

        timeline = await self.canon_storage.get_all_timeline_events(project_id)
        character_states = await self.canon_storage.get_all_character_states(project_id)

        context_package = await self.draft_storage.get_context_for_writing(project_id, chapter)

        base_tokens = 2000
        base_tokens += sum(len(str(c)) // 2 for c in critical_items)
        base_tokens += sum(len(str(i.content)) // 2 for i in dynamic_items)

        safe_limit = 12000
        context_budget = max(0, safe_limit - base_tokens)
        trimmed_context, trim_stats = self._trim_context_package(context_package, context_budget)
        if trim_stats["trimmed"]:
            try:
                await trace_collector.record_context_compress(
                    "archivist",
                    before_tokens=trim_stats["before"],
                    after_tokens=trim_stats["after"],
                    method="drop_low_priority_context",
                )
            except Exception as exc:
                logger.warning(f"Trace compress failed: {exc}")
        context_package = trimmed_context

        if character_names:
            for name in character_names:
                if not any(getattr(c, "name", None) == name for c in character_cards):
                    card = await self.card_storage.get_character_card(project_id, name)
                    if card:
                        character_cards.append(card)

        writer_context = {
            "scene_brief": scene_brief,
            "chapter_goal": chapter_goal,
            "style_card": style_card,
            "character_cards": character_cards,
            "world_cards": world_cards,
            "facts": facts,
            "timeline": timeline,
            "character_states": character_states,
            "context_package": context_package,
        }

        return {
            "writer_context": writer_context,
            "critical_items": critical_items,
            "dynamic_items": dynamic_items,
        }

    async def _run_writing_flow(
        self,
        project_id: str,
        chapter: str,
        writer_context: Dict[str, Any],
        target_word_count: int,
    ) -> Dict[str, Any]:
        """Run writer flow and wait for user feedback."""
        await self._update_status(SessionStatus.WRITING_DRAFT, "Writer is drafting...")

        writer_payload = dict(writer_context)
        writer_payload["target_word_count"] = target_word_count

        if self._stream_task:
            self._stream_task.cancel()
            self._stream_task = None

        try:
            self._stream_task = asyncio.create_task(
                self._stream_writer_output(project_id, chapter, writer_payload)
            )
            await self._stream_task
            self._stream_task = None
        except asyncio.CancelledError:
            return await self._handle_error("Stream cancelled")
        except Exception as exc:
            return await self._handle_error(f"Draft generation failed: {exc}")

        versions = await self.draft_storage.list_draft_versions(project_id, chapter)
        latest_version = versions[-1] if versions else "v1"
        draft = await self.draft_storage.get_draft(project_id, chapter, latest_version)
        if not draft:
            return await self._handle_error("Draft generation failed")

        await self._update_status(SessionStatus.WAITING_FEEDBACK, "Waiting for user feedback...")
        draft_text = draft.content if hasattr(draft, "content") else str(draft)
        proposals = await self._detect_proposals(project_id, draft_text)

        return {
            "success": True,
            "status": SessionStatus.WAITING_FEEDBACK,
            "draft_v1": draft,
            "iteration": self._get_iteration_count(project_id, chapter),
            "proposals": proposals,
        }

    async def _stream_writer_output(
        self,
        project_id: str,
        chapter: str,
        writer_payload: Dict[str, Any],
    ) -> None:
        """Stream writer output to client while persisting the final draft."""
        if self.progress_callback:
            await self.progress_callback({
                "type": "stream_start",
                "project_id": project_id,
                "chapter": chapter,
            })

        chunks: List[str] = []
        async for chunk in self.writer.execute_stream_draft(
            project_id=project_id,
            chapter=chapter,
            context=writer_payload,
        ):
            if not chunk:
                continue
            if self._stream_task and self._stream_task.cancelled():
                break
            chunks.append(chunk)
            if self.progress_callback:
                await self.progress_callback({
                    "type": "token",
                    "project_id": project_id,
                    "chapter": chapter,
                    "content": chunk,
                })

        final_text = "".join(chunks).strip()
        if not final_text:
            raise RuntimeError("Empty draft result")

        draft = await self.draft_storage.save_draft(
            project_id=project_id,
            chapter=chapter,
            version="v1",
            content=final_text,
            word_count=len(final_text),
            pending_confirmations=[],
        )

        proposals = await self._detect_proposals(project_id, final_text)

        if self.progress_callback:
            try:
                draft_payload = draft.model_dump(mode="json")
            except Exception:
                draft_payload = {
                    "chapter": getattr(draft, "chapter", chapter),
                    "version": getattr(draft, "version", "v1"),
                    "content": getattr(draft, "content", final_text),
                    "word_count": getattr(draft, "word_count", len(final_text)),
                }
            await self.progress_callback({
                "type": "stream_end",
                "project_id": project_id,
                "chapter": chapter,
                "draft": draft_payload,
                "proposals": proposals,
            })

    async def _update_status(self, status: SessionStatus, message: str) -> None:
        """Update session status and notify callback."""
        self.current_status = status

        if self.progress_callback:
            await self.progress_callback(
                {
                    "status": status.value,
                    "message": message,
                    "project_id": self.current_project_id,
                    "chapter": self.current_chapter,
                    "iteration": self._get_iteration_count(self.current_project_id, self.current_chapter),
                }
            )

    async def _handle_error(self, error_message: str) -> Dict[str, Any]:
        """Handle error and update status."""
        self.current_status = SessionStatus.ERROR

        if self.progress_callback:
            await self.progress_callback(
                {
                    "status": SessionStatus.ERROR.value,
                    "message": error_message,
                    "project_id": self.current_project_id,
                    "chapter": self.current_chapter,
                }
            )

        return {"success": False, "status": SessionStatus.ERROR, "error": error_message}

    def get_status(self) -> Dict[str, Any]:
        """Get current session status."""
        return {
            "status": self.current_status.value,
            "project_id": self.current_project_id,
            "chapter": self.current_chapter,
            "iteration": self._get_iteration_count(self.current_project_id, self.current_chapter),
        }

    def _get_iteration_key(self, project_id: Optional[str], chapter: Optional[str]) -> Tuple[str, str]:
        """Build the iteration key for tracking revisions."""
        return (project_id or "", chapter or "")

    def _get_iteration_count(self, project_id: Optional[str], chapter: Optional[str]) -> int:
        """Return current iteration count for a project/chapter pair."""
        iteration_key = self._get_iteration_key(project_id, chapter)
        return self.iteration_counts.get(iteration_key, 0)

    async def analyze_chapter(
        self,
        project_id: str,
        chapter: str,
        content: Optional[str] = None,
        chapter_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Manually trigger analysis for a chapter (no persistence)."""
        try:
            draft_content = content or ""
            if not draft_content:
                versions = await self.draft_storage.list_draft_versions(project_id, chapter)
                if not versions:
                    return {"success": False, "error": "No draft found"}

                latest = versions[-1]
                draft = await self.draft_storage.get_draft(project_id, chapter, latest)
                if not draft:
                    return {"success": False, "error": "Draft content missing"}
                draft_content = draft.content

            self.current_project_id = project_id
            self.current_chapter = chapter
            await self._update_status(SessionStatus.GENERATING_BRIEF, "Analyzing content...")

            analysis = await self._build_analysis(
                project_id=project_id,
                chapter=chapter,
                content=draft_content,
                chapter_title=chapter_title,
            )

            await self._update_status(SessionStatus.IDLE, "Analysis completed.")
            return {"success": True, "analysis": analysis}
        except Exception as exc:
            return await self._handle_error(f"Analysis failed: {exc}")

    async def _build_analysis(
        self,
        project_id: str,
        chapter: str,
        content: str,
        chapter_title: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Build analysis payload (summary, facts, proposals) without persisting."""
        scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
        title = chapter_title or (scene_brief.title if scene_brief and scene_brief.title else chapter)

        summary = await self.archivist.generate_chapter_summary(
            project_id=project_id,
            chapter=chapter,
            chapter_title=title,
            final_draft=content,
        )
        volume_id = summary.volume_id or ChapterIDValidator.extract_volume_id(chapter) or "V1"
        summary_data = summary.model_dump()
        summary_data["chapter"] = chapter
        summary_data["volume_id"] = volume_id
        summary_data["word_count"] = len(content)
        if not summary_data.get("title"):
            summary_data["title"] = title
        summary = ChapterSummary(**summary_data)

        canon_updates = await self.archivist.extract_canon_updates(
            project_id=project_id,
            chapter=chapter,
            final_draft=content,
        )

        facts = canon_updates.get("facts", []) or []
        if len(facts) > 5:
            facts = facts[:5]

        proposals = await self._detect_proposals(project_id, content)

        return {
            "summary": summary.model_dump(),
            "facts": [fact.model_dump() for fact in facts],
            "timeline_events": [event.model_dump() for event in canon_updates.get("timeline_events", []) or []],
            "character_states": [state.model_dump() for state in canon_updates.get("character_states", []) or []],
            "proposals": proposals or [],
        }

    async def analyze_sync(self, project_id: str, chapters: List[str]) -> Dict[str, Any]:
        """Batch analyze and overwrite summaries/facts/cards for selected chapters."""
        results = []
        for chapter in chapters:
            try:
                versions = await self.draft_storage.list_draft_versions(project_id, chapter)
                if not versions:
                    results.append({"chapter": chapter, "success": False, "error": "No draft found"})
                    continue
                latest = versions[-1]
                draft = await self.draft_storage.get_draft(project_id, chapter, latest)
                if not draft:
                    results.append({"chapter": chapter, "success": False, "error": "Draft content missing"})
                    continue
                analysis = await self._build_analysis(
                    project_id=project_id,
                    chapter=chapter,
                    content=draft.content,
                    chapter_title=None,
                )
                save_result = await self.save_analysis(
                    project_id=project_id,
                    chapter=chapter,
                    analysis=analysis,
                    overwrite=True,
                )
                results.append({"chapter": chapter, **save_result})
            except Exception as exc:
                results.append({"chapter": chapter, "success": False, "error": str(exc)})

        return {"success": True, "results": results}

    async def analyze_batch(self, project_id: str, chapters: List[str]) -> Dict[str, Any]:
        """Batch analyze chapters and return analysis payload."""
        results = []
        for chapter in chapters:
            try:
                versions = await self.draft_storage.list_draft_versions(project_id, chapter)
                if not versions:
                    results.append({"chapter": chapter, "success": False, "error": "No draft found"})
                    continue
                latest = versions[-1]
                draft = await self.draft_storage.get_draft(project_id, chapter, latest)
                if not draft:
                    results.append({"chapter": chapter, "success": False, "error": "Draft content missing"})
                    continue
                analysis = await self._build_analysis(
                    project_id=project_id,
                    chapter=chapter,
                    content=draft.content,
                    chapter_title=None,
                )
                results.append({"chapter": chapter, "success": True, "analysis": analysis})
            except Exception as exc:
                results.append({"chapter": chapter, "success": False, "error": str(exc)})

        return {"success": True, "results": results}

    async def save_analysis_batch(
        self,
        project_id: str,
        items: List[Dict[str, Any]],
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Persist analysis payload batch."""
        results = []
        for item in items:
            chapter = item.get("chapter")
            analysis = item.get("analysis", {}) if isinstance(item, dict) else {}
            if not chapter:
                results.append({"chapter": "", "success": False, "error": "Missing chapter"})
                continue
            try:
                result = await self.save_analysis(
                    project_id=project_id,
                    chapter=chapter,
                    analysis=analysis,
                    overwrite=overwrite,
                )
                results.append({"chapter": chapter, **result})
            except Exception as exc:
                results.append({"chapter": chapter, "success": False, "error": str(exc)})
        return {"success": True, "results": results}

    async def save_analysis(
        self,
        project_id: str,
        chapter: str,
        analysis: Dict[str, Any],
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Persist analysis output (summary, facts, cards)."""
        try:
            summary_data = analysis.get("summary", {}) or {}
            summary_data["chapter"] = self._normalize_chapter_id(
                summary_data.get("chapter") or chapter
            )
            summary = ChapterSummary(**summary_data)
            summary.new_facts = []
            if not summary.volume_id:
                summary.volume_id = ChapterIDValidator.extract_volume_id(summary.chapter) or "V1"
            if not summary.title:
                summary.title = chapter

            await self.draft_storage.save_chapter_summary(project_id, summary)

            volume_summaries = await self.draft_storage.list_chapter_summaries(
                project_id,
                volume_id=summary.volume_id,
            )
            volume_summary = await self.archivist.generate_volume_summary(
                project_id=project_id,
                volume_id=summary.volume_id,
                chapter_summaries=volume_summaries,
            )
            await self.draft_storage.volume_storage.save_volume_summary(project_id, volume_summary)

            facts_saved = 0
            timeline_saved = 0
            states_saved = 0

            if overwrite:
                await self.canon_storage.normalize_fact_records(project_id)
                await self.canon_storage.delete_facts_by_chapter(project_id, summary.chapter)

            existing_facts = await self.canon_storage.get_all_facts_raw(project_id)
            existing_ids = {item.get("id") for item in existing_facts if item.get("id")}
            next_fact_index = len(existing_facts) + 1

            facts_input = analysis.get("facts", []) or []
            if len(facts_input) > 5:
                facts_input = facts_input[:5]

            for item in facts_input:
                fact_data = item if isinstance(item, dict) else {}
                fact_data = {**fact_data}
                if not fact_data.get("statement") and not fact_data.get("content"):
                    continue
                fact_data["statement"] = fact_data.get("statement") or fact_data.get("content") or ""
                fact_data["source"] = fact_data.get("source") or summary.chapter
                fact_data["introduced_in"] = fact_data.get("introduced_in") or summary.chapter
                if not fact_data.get("id") or fact_data.get("id") in existing_ids:
                    fact_data["id"] = f"F{next_fact_index:04d}"
                    next_fact_index += 1
                existing_ids.add(fact_data["id"])
                await self.canon_storage.add_fact(project_id, Fact(**fact_data))
                facts_saved += 1

            for item in analysis.get("timeline_events", []) or []:
                event_data = item if isinstance(item, dict) else {}
                event_data = {**event_data, "source": event_data.get("source") or chapter}
                await self.canon_storage.add_timeline_event(project_id, TimelineEvent(**event_data))
                timeline_saved += 1

            for item in analysis.get("character_states", []) or []:
                state_data = item if isinstance(item, dict) else {}
                if not state_data.get("character"):
                    continue
                state_data = {**state_data, "last_seen": state_data.get("last_seen") or chapter}
                await self.canon_storage.update_character_state(project_id, CharacterState(**state_data))
                states_saved += 1

            cards_created = await self._create_cards_from_proposals(
                project_id=project_id,
                proposals=analysis.get("proposals", []) or [],
                overwrite=overwrite,
            )

            return {
                "success": True,
                "stats": {
                    "facts_saved": facts_saved,
                    "timeline_saved": timeline_saved,
                    "states_saved": states_saved,
                    "cards_created": cards_created,
                },
            }
        except Exception as exc:
            return await self._handle_error(f"Analysis save failed: {exc}")

    async def _analyze_content(self, project_id: str, chapter: str, content: str):
        """Run post-draft analysis (summaries + canon updates)."""
        try:
            normalized_chapter = self._normalize_chapter_id(chapter)
            scene_brief = await self.draft_storage.get_scene_brief(project_id, chapter)
            chapter_title = scene_brief.title if scene_brief and scene_brief.title else chapter

            summary = await self.archivist.generate_chapter_summary(
                project_id=project_id,
                chapter=normalized_chapter,
                chapter_title=chapter_title,
                final_draft=content,
            )
            summary.chapter = normalized_chapter
            await self.draft_storage.save_chapter_summary(project_id, summary)

            volume_id = ChapterIDValidator.extract_volume_id(normalized_chapter) or "V1"
            volume_summaries = await self.draft_storage.list_chapter_summaries(project_id, volume_id=volume_id)
            volume_summary = await self.archivist.generate_volume_summary(
                project_id=project_id,
                volume_id=volume_id,
                chapter_summaries=volume_summaries,
            )
            await self.draft_storage.volume_storage.save_volume_summary(project_id, volume_summary)
        except Exception as exc:
            logger.warning(f"Failed to generate summaries: {exc}")

        try:
            canon_updates = await self.archivist.extract_canon_updates(
                project_id=project_id,
                chapter=normalized_chapter,
                final_draft=content,
            )

            for fact in canon_updates.get("facts", []) or []:
                await self.canon_storage.add_fact(project_id, fact)

            for event in canon_updates.get("timeline_events", []) or []:
                await self.canon_storage.add_timeline_event(project_id, event)

            for state in canon_updates.get("character_states", []) or []:
                await self.canon_storage.update_character_state(project_id, state)

            try:
                report = await self.canon_storage.detect_conflicts(
                    project_id=project_id,
                    chapter=chapter,
                    new_facts=canon_updates.get("facts", []) or [],
                    new_timeline_events=canon_updates.get("timeline_events", []) or [],
                    new_character_states=canon_updates.get("character_states", []) or [],
                )
                await self.draft_storage.save_conflict_report(
                    project_id=project_id,
                    chapter=chapter,
                    report=report,
                )
            except Exception as exc:
                logger.warning(f"Failed to detect conflicts: {exc}")
        except Exception as exc:
            logger.warning(f"Failed to update canon: {exc}")

    async def _detect_proposals(self, project_id: str, content: Any) -> List[Dict]:
        """Detect setting proposals from content."""
        try:
            if hasattr(content, "content"):
                content_text = content.content
            else:
                content_text = str(content)

            chars = await self.card_storage.list_character_cards(project_id)
            worlds = await self.card_storage.list_world_cards(project_id)
            existing = chars + worlds

            proposals = await self.archivist.detect_setting_changes(content_text, existing)
            return [p.model_dump() for p in proposals]
        except Exception as exc:
            logger.warning(f"Proposal detection failed: {exc}")
            return []

    async def _create_cards_from_proposals(
        self,
        project_id: str,
        proposals: List[Dict[str, Any]],
        overwrite: bool = False,
    ) -> int:
        """Create cards from proposals. Returns created count."""
        created = 0
        for item in proposals:
            try:
                proposal = CardProposal(**(item or {}))
            except Exception:
                continue

            name = (proposal.name or "").strip()
            if not name:
                continue

            ptype = (proposal.type or "").lower()
            if ptype == "character":
                existing = await self.card_storage.get_character_card(project_id, name)
                if existing and not overwrite:
                    continue
                card = CharacterCard(
                    name=name,
                    description=self._merge_card_description(
                        proposal.description,
                        proposal.rationale,
                    ),
                )
                await self.card_storage.save_character_card(project_id, card)
                created += 1
                continue

            if ptype == "world":
                existing = await self.card_storage.get_world_card(project_id, name)
                if existing and not overwrite:
                    continue
                card = WorldCard(
                    name=name,
                    description=self._merge_card_description(
                        proposal.description,
                        proposal.rationale,
                    ),
                )
                await self.card_storage.save_world_card(project_id, card)
                created += 1
                continue

        return created

    def _normalize_chapter_id(self, chapter_id: str) -> str:
        if not chapter_id:
            return chapter_id
        normalized = str(chapter_id).strip().upper()
        if not normalized:
            return chapter_id
        if normalized.startswith("CH"):
            normalized = "C" + normalized[2:]
        if ChapterIDValidator.validate(normalized):
            if normalized.startswith("C"):
                return f"V1{normalized}"
            return normalized
        return str(chapter_id).strip()

    def _estimate_context_tokens(self, context_package: Dict[str, Any]) -> int:
        """Estimate tokens for context package only."""
        total = 0
        for key in ["full_facts", "summary_with_events", "summary_only", "title_only", "volume_summaries"]:
            for item in context_package.get(key, []) or []:
                total += len(str(item)) // 2
        return total

    def _trim_context_package(
        self,
        context_package: Dict[str, Any],
        max_tokens: int,
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Trim low-priority context to fit within max_tokens."""
        trimmed = dict(context_package or {})
        for key in ["full_facts", "summary_with_events", "summary_only", "title_only", "volume_summaries"]:
            trimmed[key] = list(trimmed.get(key, []) or [])

        before = self._estimate_context_tokens(trimmed)
        if before <= max_tokens:
            return trimmed, {"trimmed": False, "before": before, "after": before}

        if max_tokens <= 0:
            for key in ["summary_with_events", "summary_only", "title_only", "volume_summaries"]:
                trimmed[key] = []
            return trimmed, {"trimmed": True, "before": before, "after": self._estimate_context_tokens(trimmed)}

        removal_order = ["title_only", "volume_summaries", "summary_only", "summary_with_events"]
        while self._estimate_context_tokens(trimmed) > max_tokens:
            removed_any = False
            for key in removal_order:
                if trimmed[key]:
                    trimmed[key].pop(0)
                    removed_any = True
                    if self._estimate_context_tokens(trimmed) <= max_tokens:
                        break
            if not removed_any:
                break

        after = self._estimate_context_tokens(trimmed)
        return trimmed, {"trimmed": True, "before": before, "after": after}

    def _merge_card_description(self, description: str, rationale: str) -> str:
        description_text = (description or "").strip()
        rationale_text = (rationale or "").strip()
        if description_text and rationale_text:
            return f"{description_text}\n理由: {rationale_text}"
        return description_text or rationale_text

    async def extract_style_profile(self, project_id: str, sample_text: str) -> StyleCard:
        """Extract writing style guidance from sample text."""
        style_text = await self.archivist.extract_style_profile(sample_text)
        return StyleCard(style=style_text)
