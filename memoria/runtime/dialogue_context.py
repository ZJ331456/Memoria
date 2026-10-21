"""Assemble bounded dialogue, skills and episodic context without a second Claim Reader."""

from __future__ import annotations
import re
from ..prompting import PromptSection
from ..skills import SkillMatch


def assemble_dialogue(
    runtime,
    context,
    tracer,
    *,
    history,
    compaction,
    interrupt_note,
    turn_kind,
    force_skill,
    memory_budget,
    memory_dependencies,
):
    self = runtime
    session_id, user_text = context.session_id, context.user_text
    episodes = (
        self.episodes.search(user_text, self.layers.episode_top_k)
        if self.episodes and self.layers and len(user_text.strip()) >= 4 and turn_kind == "chat"
        else []
    )
    context.metadata["episode_retrieval"] = {
        "matched": len(episodes),
        "injected": 0,
        "ids": [],
    }

    extra_sections: list[PromptSection] = []
    formation = getattr(self, "formation", None)
    if formation:
        core = formation.core()
        if core:
            extra_sections.append(
                PromptSection("Core Memory", "\n".join(line for line, _ in core), 31)
            )
            memory_dependencies.update(
                {"memory:" + m["id"]: self.store.memory_dependency(m) for _, m in core}
            )
        context.metadata["memory_formation"] = {"core_count": len(core), "inferred_patterns": True}
    if episodes:
        lines = []
        for episode in episodes:
            source = f"user={episode['user_message_id']}"
            if episode.get("assistant_message_id"):
                source += f", assistant={episode['assistant_message_id']}"
            lines.append(
                f"- [episode:{episode['id']}] 任务执行记录，不是已验证的用户事实 | source({source}) | "
                f"outcome={episode['outcome']} | task={episode['task'][:200]} | "
                f"result={episode['result'][:300]}"
            )
        extra_sections.append(PromptSection("Past Task Episodes", "\n".join(lines), 32))
    if turn_kind == "drift":
        extra_sections.append(
            PromptSection(
                "Drift Mode",
                "这是空闲 Drift 轮次：遵守技能与写权限白名单，完成后给出简洁审计摘要。",
                12,
            )
        )
    if self.markdown and self.markdown.enabled:
        self_text = self.markdown.self_excerpt()
        if self_text.strip():
            extra_sections.append(PromptSection("Self", self_text, 15))
        context.metadata["markdown_layer"] = self.markdown.status()
    catalog_text = ""
    if self.skills:
        if force_skill:
            record = self.skills.get(force_skill)
            if record:
                matches = [SkillMatch(record, 100.0, "drift_forced")]
            else:
                matches = self.skills.select(force_skill)
            catalog_text, active_text = self.skills.render_sections(matches)
        else:
            matches = self.skills.select(user_text)
            catalog_text, active_text = self.skills.render_sections(matches)
        if active_text:
            extra_sections.append(PromptSection("Active Skills", active_text, 45))
        context.metadata["skills"] = {
            "matched": [
                {
                    "name": item.skill.name,
                    "revision": item.skill.revision,
                    "score": item.score,
                    "reason": item.reason,
                }
                for item in matches
            ],
            "catalog_size": len(self.skills.list()),
            "loaded": [],
        }
    tool_catalog = self.tool_presentation.catalog_prompt()
    if self.layers and self.layers.enabled:
        catalog_cap = self.layers.catalog_chars
        if catalog_text and tool_catalog:
            skills_cap = catalog_cap - min(len(tool_catalog), catalog_cap // 2)
            catalog_text = catalog_text[:skills_cap]
            tool_catalog = tool_catalog[: catalog_cap - len(catalog_text)]
        else:
            catalog_text = catalog_text[:catalog_cap]
            tool_catalog = tool_catalog[:catalog_cap]
    if catalog_text:
        extra_sections.append(PromptSection("Available Skills", catalog_text, 40))
    if tool_catalog:
        extra_sections.append(PromptSection("Tool Search", tool_catalog, 35))
    context.metadata["tool_search"] = self.tool_presentation.public_status()

    with tracer.timings.measure("prompt.assemble"):
        assembled = self.prompt_assembler.assemble(
            self.settings.system_prompt,
            memories=context.memories,
            compaction_summary=compaction.summary if compaction else "",
            interrupt_note=interrupt_note,
            extra_sections=extra_sections,
        )
    context.messages = assembled.as_messages()
    context.messages += [
        {"role": message["role"], "content": message["content"]} for message in history
    ]
    context.metadata["prompt_sections"] = [section.name for section in assembled.sections]
    episode_content = next(
        (section.content for section in assembled.sections if section.name == "Past Task Episodes"),
        "",
    )
    injected_episode_ids = [
        item["id"]
        for item in episodes
        if f"[episode:{item['id']}]" in episode_content
        and item["user_message_id"] in episode_content
    ]
    context.metadata["episode_retrieval"]["injected"] = len(injected_episode_ids)
    context.metadata["episode_retrieval"]["ids"] = injected_episode_ids
    for episode in episodes:
        if episode["id"] in injected_episode_ids:
            for source_id in (episode["user_message_id"], episode.get("assistant_message_id")):
                source = self.store.message_source(source_id) if source_id else None
                if source:
                    memory_dependencies["message:" + source_id] = self.store.message_dependency(
                        source
                    )
    if self.skills:
        active_content = next(
            (section.content for section in assembled.sections if section.name == "Active Skills"),
            "",
        )
        context.metadata["skills"]["injected"] = [
            {"name": item.skill.name, "revision": item.skill.revision}
            for item in matches
            if re.search(r"(?m)^### skill:" + re.escape(item.skill.name) + r" \(", active_content)
        ]
    context.metadata["memory_layers"] = {
        "enabled": bool(self.layers and self.layers.enabled),
        "section_stats": assembled.section_stats,
        "frame_chars": len(assembled.context_frame["content"]) if assembled.context_frame else 0,
    }
    if memory_budget:
        memory_budget.account_initial(context.metadata["memory_layers"]["frame_chars"])
        context.metadata["memory_tool_budget"] = memory_budget.public_dict()
    if interrupt_note:
        self.store.clear_interrupt_note(session_id)
