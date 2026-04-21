"""
telegram_bot/bot.py — Telegram bot interface for Prism Product Intelligence.

Commands:
    /start  — welcome message + list commands
    /help   — show all commands
    /new    — create a product and auto-start intelligence agents
    /intel  — Product OS intelligence subcommands (status, ask, competitors, ...)

UAT automation has been carved out of Prism into a sibling repo (Loupe); this
module intentionally only handles the intelligence surface.
"""
from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from dotenv import load_dotenv
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "*Prism — Product Intelligence Bot*\n\n"
        "Track competitors and research your industry — all from here.\n\n"
        "*Get started:*\n"
        "/new `Name — description` — create a new product and start research\n\n"
        "*Intelligence:*\n"
        "/intel status — check agent progress\n"
        "/intel competitors — see discovered competitors\n"
        "/intel ask `<question>` — query the knowledge base\n"
        "/intel run `<agent>` — run an agent manually\n"
        "/intel digest — get a daily summary\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


# ---------------------------------------------------------------------------
# Prism — /new command (create product + auto-start agents from phone)
# ---------------------------------------------------------------------------


async def cmd_new(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Create a new product and start intelligence agents.

    Usage: /new ProductName — description of the product
    Example: /new Swiggy — India's largest food delivery and quick commerce platform
    """
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "*Create a new product*\n\n"
            "Usage: `/new Name — description`\n\n"
            "Example:\n"
            "`/new Swiggy — India's largest food delivery and quick commerce platform`\n\n"
            "The agents will automatically start researching competitors and the industry.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    raw = " ".join(args)
    if " — " in raw:
        name, description = raw.split(" — ", 1)
    elif " - " in raw:
        name, description = raw.split(" - ", 1)
    else:
        name = raw
        description = ""

    name = name.strip()
    description = description.strip()

    if not name:
        await update.message.reply_text("Product name is required.")
        return

    await update.message.reply_text(f"Creating *{name}*...", parse_mode=ParseMode.MARKDOWN)

    try:
        import httpx

        # Create project via API
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                "http://localhost:8000/api/projects",
                json={
                    "name": name,
                    "description": description or None,
                    "enable_intelligence": True,
                },
            )
            r.raise_for_status()
            project = r.json()
            pid = project["id"]

        # Set as active project for /intel commands
        _intel_project[update.effective_chat.id] = pid

        # Start agents in background
        def _start():
            try:
                import sys
                from pathlib import Path
                _root = Path(__file__).resolve().parent.parent
                if str(_root) not in sys.path:
                    sys.path.insert(0, str(_root))
                from agent.product_os_orchestrator import get_orchestrator
                orch = get_orchestrator(pid)
                orch.run_agent_session("intel")
            except Exception as e:
                logger.error(f"[bot] Auto-start agents failed for project {pid}: {e}")

        thread = threading.Thread(target=_start, daemon=True)
        thread.start()

        msg = (
            f"*{name}* created (project #{pid})\n\n"
            f"Agents are starting up — they'll discover competitors and research the industry.\n\n"
            f"Use these commands:\n"
            f"`/intel status` — check progress\n"
            f"`/intel competitors` — see discovered competitors\n"
            f"`/intel ask <question>` — query the knowledge base\n"
            f"`/intel digest` — get a summary\n"
        )
        if description:
            msg += f"\nDescription: _{description}_"

        await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)

    except Exception as e:
        await update.message.reply_text(f"Failed to create product: {e}")


# ---------------------------------------------------------------------------
# Prism — /intel commands
# ---------------------------------------------------------------------------

# Track the active project for intel commands per chat
_intel_project: dict[int, int] = {}  # chat_id -> project_id


async def cmd_intel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route /intel subcommands: start, stop, status, run, ask, competitors, trends, digest."""
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "*Product OS Intelligence*\n\n"
            "/intel start — start autonomous agents\n"
            "/intel stop — stop agents\n"
            "/intel status — agent status & knowledge stats\n"
            "/intel run `<agent>` — run a single agent session\n"
            "/intel ask `<question>` — query the knowledge base\n"
            "/intel competitors — list discovered competitors\n"
            "/intel trends — latest industry trends\n"
            "/intel digest — generate daily digest\n"
            "/intel setproject `<id>` — set active project\n",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    subcmd = args[0].lower()
    rest = " ".join(args[1:])

    if subcmd == "setproject":
        await _intel_setproject(update, rest)
    elif subcmd == "start":
        await _intel_start(update)
    elif subcmd == "stop":
        await _intel_stop(update)
    elif subcmd == "status":
        await _intel_status(update)
    elif subcmd == "run":
        await _intel_run(update, rest, context)
    elif subcmd == "ask":
        await _intel_ask(update, rest)
    elif subcmd == "competitors":
        await _intel_competitors(update)
    elif subcmd == "trends":
        await _intel_trends(update)
    elif subcmd == "digest":
        await _intel_digest(update)
    else:
        await update.message.reply_text(f"Unknown subcommand: {subcmd}. Use /intel for help.")


async def _intel_setproject(update: Update, project_id_str: str) -> None:
    if not project_id_str.strip():
        await update.message.reply_text("Usage: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        pid = int(project_id_str.strip())
    except ValueError:
        await update.message.reply_text("Project ID must be a number.")
        return
    _intel_project[update.effective_chat.id] = pid
    await update.message.reply_text(f"Intel project set to {pid}")


def _get_intel_project(chat_id: int) -> int | None:
    return _intel_project.get(chat_id)


async def _intel_start(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        from agent.product_os_orchestrator import get_orchestrator
        orch = get_orchestrator(pid)
        orch.start_daemon()
        await update.message.reply_text(f"Product OS agents started for project {pid}")
    except Exception as e:
        await update.message.reply_text(f"Failed to start: {e}")


async def _intel_stop(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("No active intel project.")
        return
    try:
        from agent.product_os_orchestrator import get_orchestrator
        orch = get_orchestrator(pid)
        orch.stop_daemon()
        await update.message.reply_text("Product OS agents stopped.")
    except Exception as e:
        await update.message.reply_text(f"Failed to stop: {e}")


async def _intel_status(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        from agent.product_os_orchestrator import get_orchestrator
        orch = get_orchestrator(pid)
        status = orch.get_status()

        lines = [f"*Product OS Status* (project {pid})\n"]
        lines.append(f"Running: {'Yes' if status['is_running'] else 'No'}\n")

        for agent_type, info in status.get("agents", {}).items():
            last = info.get("last_session")
            pending = info.get("pending_work_items", 0)
            total = info.get("total_sessions", 0)
            last_str = last.get("completed_at", "never")[:19] if last else "never"
            lines.append(f"*{agent_type}*: {total} sessions, {pending} pending, last: {last_str}")

        kb = status.get("knowledge", {})
        lines.append(f"\nKnowledge: {kb.get('total_entities', 0)} entities, "
                      f"{kb.get('total_observations', 0)} observations, "
                      f"{kb.get('total_artifacts', 0)} artifacts")

        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _intel_run(update: Update, agent_type: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    agent_type = agent_type.strip()
    if not agent_type:
        await update.message.reply_text("Usage: /intel run `<agent_type>`\nTypes: intel (default), impact_analysis, ux_intel, competitive_intel, industry_research", parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text(f"Starting {agent_type} session for project {pid}...")

    chat_id = update.effective_chat.id
    app_ref = context.application

    def _run_in_thread():
        try:
            from agent.product_os_orchestrator import get_orchestrator
            orch = get_orchestrator(pid)
            result = orch.run_agent_session(agent_type)
            msg = (
                f"*{agent_type} session complete*\n"
                f"Items: {result.get('items_completed', 0)} done, {result.get('items_failed', 0)} failed\n"
                f"Knowledge added: {result.get('knowledge_added', 0)}\n"
                f"Duration: {result.get('elapsed_s', 0)}s"
            )
        except Exception as e:
            msg = f"Agent session failed: {e}"
            logger.exception(f"[intel] {agent_type} session error")

        import asyncio
        asyncio.run_coroutine_threadsafe(
            app_ref.bot.send_message(chat_id, msg, parse_mode=ParseMode.MARKDOWN),
            app_ref.loop if hasattr(app_ref, 'loop') else asyncio.get_event_loop(),
        )

    thread = threading.Thread(target=_run_in_thread, daemon=True)
    thread.start()


async def _intel_ask(update: Update, question: str) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    if not question.strip():
        await update.message.reply_text("Usage: /intel ask `<question>`", parse_mode=ParseMode.MARKDOWN)
        return

    await update.message.reply_text("Searching knowledge base...")
    try:
        from agent.knowledge_store import KnowledgeStore
        from webapp.api.db import SessionLocal
        db = SessionLocal()
        try:
            ks = KnowledgeStore(db, "query", pid)
            # Simple: search entities + observations, synthesize with Claude
            results = ks.semantic_search(question, top_k=10)
            entities = ks.find_entities(name_like=question.split()[0] if question.split() else None, limit=5)

            from utils.claude_client import ask as claude_ask
            context_parts = []
            for e in entities:
                context_parts.append(f"Entity: {e['name']} ({e['entity_type']}): {e.get('description', '')}")
            for r in results:
                context_parts.append(f"Knowledge: {r.get('text_chunk', '')[:200]}")

            if not context_parts:
                await update.message.reply_text("No relevant knowledge found yet. Run some agent sessions first.")
                return

            answer = claude_ask(
                f"Based on this knowledge, answer the question concisely:\n\n"
                f"Knowledge:\n" + "\n".join(context_parts) + f"\n\nQuestion: {question}",
                max_tokens=1024,
            )
            await update.message.reply_text(answer)
        finally:
            db.close()
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _intel_competitors(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        from agent.knowledge_store import KnowledgeStore
        from webapp.api.db import SessionLocal
        db = SessionLocal()
        try:
            ks = KnowledgeStore(db, "query", pid)
            competitors = ks.find_entities(entity_type="company", limit=20)
            if not competitors:
                await update.message.reply_text("No competitors discovered yet. Run: /intel run competitive\\_intel", parse_mode=ParseMode.MARKDOWN)
                return
            lines = ["*Discovered Competitors*\n"]
            for c in competitors:
                desc = (c.get("description") or "")[:80]
                lines.append(f"• *{c['name']}* — {desc}")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        finally:
            db.close()
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _intel_trends(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    try:
        from webapp.api.db import SessionLocal
        from webapp.api.models import KnowledgeObservation, KnowledgeEntity
        from datetime import datetime, timedelta
        db = SessionLocal()
        try:
            cutoff = datetime.utcnow() - timedelta(days=7)
            recent = (
                db.query(KnowledgeObservation)
                .join(KnowledgeEntity)
                .filter(
                    KnowledgeEntity.project_id == pid,
                    KnowledgeObservation.recorded_at >= cutoff,
                    KnowledgeObservation.observation_type.in_(["news", "regulatory", "metric"]),
                )
                .order_by(KnowledgeObservation.recorded_at.desc())
                .limit(10)
                .all()
            )
            if not recent:
                await update.message.reply_text("No recent trends found. Run: /intel run industry\\_research", parse_mode=ParseMode.MARKDOWN)
                return
            lines = ["*Recent Industry Trends*\n"]
            for obs in recent:
                date_str = obs.recorded_at.strftime("%m/%d")
                lines.append(f"• [{date_str}] {obs.content[:100]}")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        finally:
            db.close()
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


async def _intel_digest(update: Update) -> None:
    pid = _get_intel_project(update.effective_chat.id)
    if pid is None:
        await update.message.reply_text("Set a project first: /intel setproject `<id>`", parse_mode=ParseMode.MARKDOWN)
        return
    await update.message.reply_text("Generating digest...")
    try:
        from agent.product_os_orchestrator import get_orchestrator
        orch = get_orchestrator(pid)
        digest = orch.generate_daily_digest()
        await update.message.reply_text(digest)
    except Exception as e:
        await update.message.reply_text(f"Error generating digest: {e}")


# ---------------------------------------------------------------------------
# /purge — F3: one-shot "bad data → fresh run" command
# ---------------------------------------------------------------------------


async def cmd_purge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Usage: /purge <entity_id> [reason]

    Calls the same POST /purge endpoint the web UI uses. Tombstones the
    entity so the canonical blocks re-learning, cascade-deletes its
    observations + relations, and enqueues a fresh industry_research run.
    """
    import httpx
    args = context.args or []
    if not args:
        await update.message.reply_text(
            "*Purge a mis-tagged trend*\n\n"
            "Usage: `/purge <entity_id> [reason]`\n\n"
            "Example: `/purge 148 wrong industry — Swiggy is food delivery, not travel`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    try:
        entity_id = int(args[0])
    except (ValueError, TypeError):
        await update.message.reply_text("First argument must be an integer entity_id.")
        return
    reason = " ".join(args[1:]).strip() or "[purged via Telegram]"

    url = f"{_api_base()}/api/knowledge/entities/{entity_id}/purge"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.post(url, json={"signal": "dismissed", "reason": reason})
    except Exception as exc:
        await update.message.reply_text(f"Purge request failed: {exc}")
        return

    if resp.status_code == 404:
        await update.message.reply_text(f"No entity with id={entity_id}")
        return
    if resp.status_code != 200:
        await update.message.reply_text(f"Server returned {resp.status_code}: {resp.text[:200]}")
        return

    out = resp.json()
    await update.message.reply_text(
        f"✔ Purged entity {out['entity_id']}\n"
        f"• {out['observations_deleted']} observations deleted\n"
        f"• {out['relations_deleted']} relations deleted\n"
        f"• Enqueued industry_research work item #{out['work_item_enqueued']}\n"
        f"• Reason: {out['reason']}\n\n"
        f"The next research run will skip this canonical and try to fill the gap."
    )


# ---------------------------------------------------------------------------
# Research-digest callbacks (F1 — keep/dismiss/star buttons)
# ---------------------------------------------------------------------------
#
# Buttons are attached by telegram_bot/digest.py with callback_data of the
# form `sig:<signal>:<entity_id>`. When tapped, we POST to the FastAPI
# endpoint so the same server-side validation path runs.
#
# Dismiss can optionally collect a "why" — after ✖, we store the pending
# entity_id in context.user_data and the next plain text reply is written
# to dismissed_reason.

_VALID_SIGNALS = {"kept", "dismissed", "starred"}


def _api_base() -> str:
    return os.environ.get("PRISM_API_URL", "http://localhost:8100").rstrip("/")


async def cb_signal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle Keep/Dismiss/Star taps from a digest message."""
    import httpx
    query = update.callback_query
    if query is None or query.data is None:
        return
    await query.answer()  # acknowledge so the spinner goes away

    try:
        _, signal, entity_id_s = query.data.split(":", 2)
        entity_id = int(entity_id_s)
    except (ValueError, AttributeError):
        return
    if signal not in _VALID_SIGNALS:
        return

    url = f"{_api_base()}/api/knowledge/entities/{entity_id}/signal"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(url, json={"signal": signal})
        ok = resp.status_code == 200
    except Exception as exc:
        logger.warning("[bot] signal POST failed for entity=%d: %s", entity_id, exc)
        ok = False

    suffix = {"kept": "👍 Kept", "dismissed": "✖ Dismissed", "starred": "⭐ Starred"}[signal]
    status_line = suffix if ok else f"⚠️ {suffix} (save failed — check server)"

    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass
    try:
        await query.message.reply_text(status_line)
    except Exception:
        pass

    if signal == "dismissed" and ok:
        # Ask for a brief "why" — next plain text becomes dismissed_reason.
        context.user_data["awaiting_dismiss_reason"] = entity_id
        try:
            await query.message.reply_text(
                "Dismissed. Reply with a one-line reason if you want "
                "(e.g. 'wrong industry' / 'we already know this'), "
                "or ignore to skip."
            )
        except Exception:
            pass


async def on_text_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Capture an optional dismiss-reason reply right after a Dismiss tap."""
    import httpx
    pending = context.user_data.get("awaiting_dismiss_reason")
    if not pending:
        return  # unrelated text — ignore (avoids swallowing other flows)
    if update.message is None or not (update.message.text or "").strip():
        return
    reason = update.message.text.strip()[:500]

    # Re-post `dismissed` with the reason attached.
    url = f"{_api_base()}/api/knowledge/entities/{pending}/signal"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            await client.post(url, json={"signal": "dismissed", "reason": reason})
    except Exception as exc:
        logger.warning("[bot] dismiss-reason POST failed for entity=%d: %s", pending, exc)

    context.user_data.pop("awaiting_dismiss_reason", None)
    try:
        await update.message.reply_text("📝 Reason saved — it'll shape the next research run.")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# /prd — combined PRD synthesis (Prism market lens + Loupe build lens)
# ---------------------------------------------------------------------------
#
# Two flows:
#   1. `/prd <feature>` — direct generation. Sends "generating…" then delivers.
#   2. `/prd`            — inline keyboard of recent TestPlans ∪ starred trends
#                          (F1 from the UX-friction plan). User taps → generate.
# Plus one callback:
#   3. `prd:dd:<entity_id>` — [Deep-dive] button attached to digest trend cards
#                             (F2). Looks up the entity and generates a PRD for its name.


def _md2_escape(s: str) -> str:
    """Minimal MarkdownV2 escaper for the cover line we prepend."""
    if not s:
        return ""
    for ch in r"_*[]()~`>#+-=|{}.!\\":
        s = s.replace(ch, f"\\{ch}")
    return s


def _chunk_markdown(md: str, max_chars: int = 3800) -> list[str]:
    """Split a long Markdown doc into Telegram-safe chunks.

    Telegram caps single messages at ~4096 chars. We stay under with a
    margin to leave room for the optional header. Breaks on paragraph
    boundaries when possible so sections stay intact.
    """
    if len(md) <= max_chars:
        return [md]
    chunks: list[str] = []
    remaining = md
    while len(remaining) > max_chars:
        # Prefer splitting at a paragraph boundary
        split_at = remaining.rfind("\n\n", 0, max_chars)
        if split_at < int(max_chars * 0.6):
            # No good paragraph break — fall back to nearest newline
            split_at = remaining.rfind("\n", 0, max_chars)
        if split_at < int(max_chars * 0.3):
            split_at = max_chars
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:].lstrip()
    if remaining:
        chunks.append(remaining)
    return chunks


async def _deliver_prd(message, api_base: str, project_id: int, feature: str) -> None:
    """Call /api/prd/generate, edit status message, deliver Markdown chunks."""
    import httpx
    status = await message.reply_text(f"⏳ Generating PRD for *{_md2_escape(feature[:80])}*…",
                                      parse_mode=ParseMode.MARKDOWN_V2)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(
                f"{api_base}/api/prd/generate",
                json={"project_id": project_id, "feature_description": feature},
            )
    except Exception as exc:
        await status.edit_text(f"PRD request failed: {exc}")
        return

    if r.status_code != 200:
        await status.edit_text(f"Server returned {r.status_code}: {r.text[:300]}")
        return

    out = r.json()
    artifact_id = out.get("artifact_id")
    # Fetch the saved Markdown
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            art = await client.get(f"{api_base}/api/knowledge/artifacts/{artifact_id}")
        art.raise_for_status()
        content = art.json().get("content_md", "")
    except Exception as exc:
        await status.edit_text(f"PRD generated (id={artifact_id}) but fetch failed: {exc}")
        return

    # Delete the status message, send the PRD in chunks. First chunk gets a
    # short header so the user knows which feature this PRD is for.
    try:
        await status.delete()
    except Exception:
        pass
    header = (
        f"📝 PRD · {feature[:80]}\n"
        f"   artifact #{artifact_id}  ·  Prism entities: {out.get('prism_evidence_count')}  ·  "
        f"Loupe runs: {out.get('loupe_runs_matched') if out.get('loupe_evidence_available') else 'n/a'}\n"
        f"{'─' * 40}\n\n"
    )
    chunks = _chunk_markdown(header + content)
    for ch in chunks:
        try:
            await message.reply_text(ch)
        except Exception as exc:
            logger.warning("[bot] PRD chunk send failed: %s", exc)


async def cmd_prd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """`/prd [<feature description>]` — generate a combined PRD doc.

    With a feature arg → call the synthesizer directly.
    Without → show an inline keyboard of recent plans/starred trends (F1).
    """
    import httpx
    chat_id = update.effective_chat.id
    project_id = _get_intel_project(chat_id)
    if project_id is None:
        await update.message.reply_text(
            "Set an active project first. Use `/new <name>` or pick one via `/intel use <id>`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    args = context.args or []
    if args:
        feature = " ".join(args).strip()
        await _deliver_prd(update.message, _api_base(), project_id, feature)
        return

    # No arg → show picker (F1). Query candidates from the API.
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"{_api_base()}/api/prd/feature-candidates",
                                 params={"project_id": project_id})
        r.raise_for_status()
        candidates = r.json().get("candidates", [])
    except Exception as exc:
        await update.message.reply_text(
            f"Couldn't fetch recent features ({exc}).\n"
            "Try `/prd <feature description>` directly, e.g.\n"
            "`/prd hotel rebooking flow`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if not candidates:
        await update.message.reply_text(
            "No recent features yet — type a feature description:\n"
            "`/prd hotel rebooking flow`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Stash candidates in chat_data so callback data stays short (Telegram
    # callback_data is 64-byte capped; feature names would overflow).
    context.chat_data["prd_candidates"] = candidates
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    rows = []
    for i, c in enumerate(candidates):
        src_tag = "📋" if c["source"] == "plan" else "⭐"
        rows.append([InlineKeyboardButton(
            f"{src_tag} {c['label'][:60]}",
            callback_data=f"prd:gen:{i}",
        )])
    await update.message.reply_text(
        "Which feature should the PRD cover?",
        reply_markup=InlineKeyboardMarkup(rows),
    )


async def cb_prd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle both F1 picker taps (`prd:gen:<idx>`) and F2 digest deep-dive
    taps (`prd:dd:<entity_id>`)."""
    import httpx
    query = update.callback_query
    if query is None or not query.data:
        return
    await query.answer()
    chat_id = update.effective_chat.id
    project_id = _get_intel_project(chat_id)
    if project_id is None:
        try:
            await query.message.reply_text("No active project — run /new or /intel use <id> first.")
        except Exception:
            pass
        return

    parts = query.data.split(":", 2)
    if len(parts) < 3:
        return
    _, kind, token = parts

    feature: str | None = None
    if kind == "gen":
        # F1: index into stashed candidates
        try:
            idx = int(token)
        except ValueError:
            return
        candidates = context.chat_data.get("prd_candidates") or []
        if 0 <= idx < len(candidates):
            feature = candidates[idx]["label"]
    elif kind == "dd":
        # F2: look up entity by id and use its name
        try:
            entity_id = int(token)
        except ValueError:
            return
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{_api_base()}/api/knowledge/entities/{entity_id}")
            if r.status_code == 200:
                feature = (r.json() or {}).get("name")
        except Exception:
            pass

    if not feature:
        try:
            await query.message.reply_text("Couldn't resolve the feature — try `/prd <feature>`.")
        except Exception:
            pass
        return

    # Hide the picker keyboard so the user sees progress inline
    try:
        await query.edit_message_reply_markup(reply_markup=None)
    except Exception:
        pass

    await _deliver_prd(query.message, _api_base(), project_id, feature)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set.")

    logging.basicConfig(
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        level=logging.INFO,
    )

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    # Product OS intelligence commands
    app.add_handler(CommandHandler("new", cmd_new))
    app.add_handler(CommandHandler("intel", cmd_intel))
    app.add_handler(CommandHandler("purge", cmd_purge))
    app.add_handler(CommandHandler("prd", cmd_prd))
    # Research-digest buttons + optional reason replies
    app.add_handler(CallbackQueryHandler(cb_signal, pattern=r"^sig:"))
    app.add_handler(CallbackQueryHandler(cb_prd, pattern=r"^prd:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text_reply))

    logger.info("[bot] Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
