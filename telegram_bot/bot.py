"""
telegram_bot/bot.py — Telegram bot interface for MMT-OS UAT automation

Commands:
    /start   — welcome message + list commands
    /run     — start a UAT run (uses ./apks/candidate.apk or prompts upload)
    /status  — current run status
    /report  — send latest report
    /list    — list recent runs with pass rates
    /cases   — list registered use cases for a feature
    /help    — show all commands

APK uploads: send a .apk document; bot saves to ./apks/candidate.apk.
UAT runs execute in a background thread so the bot event loop stays responsive.
"""
from __future__ import annotations

import io
import json
import logging
import os
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from telegram import Document, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths / env
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parent.parent
_APKS_DIR = _REPO_ROOT / "apks"
_CANDIDATE_APK = _APKS_DIR / "candidate.apk"
_REPORTS_DIR = _REPO_ROOT / "reports"

_DEFAULT_ACCOUNTS_FILE = os.getenv("UAT_ACCOUNTS_FILE", str(_REPO_ROOT / "accounts.json"))
_DEFAULT_FEATURE = os.getenv("UAT_FEATURE", "search")

# ---------------------------------------------------------------------------
# RunTracker — in-memory state
# ---------------------------------------------------------------------------


class RunTracker:
    """Thread-safe store of UAT run state."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: dict[str, dict] = {}   # run_id -> {status, result, error, start_time, chat_id}
        self._latest_run_id: Optional[str] = None

    def start(self, run_id: str, chat_id: int) -> None:
        with self._lock:
            self._runs[run_id] = {
                "status": "running",
                "result": None,
                "error": None,
                "start_time": time.time(),
                "chat_id": chat_id,
            }
            self._latest_run_id = run_id

    def complete(self, run_id: str, result: dict) -> None:
        with self._lock:
            if run_id in self._runs:
                self._runs[run_id]["status"] = "completed"
                self._runs[run_id]["result"] = result

    def fail(self, run_id: str, error: str) -> None:
        with self._lock:
            if run_id in self._runs:
                self._runs[run_id]["status"] = "failed"
                self._runs[run_id]["error"] = error

    def get(self, run_id: str) -> Optional[dict]:
        with self._lock:
            return self._runs.get(run_id)

    def latest(self) -> Optional[dict]:
        with self._lock:
            if self._latest_run_id:
                return self._runs.get(self._latest_run_id)
            return None

    def latest_run_id(self) -> Optional[str]:
        with self._lock:
            return self._latest_run_id

    def all_runs(self) -> list[dict]:
        with self._lock:
            return [
                {"run_id": rid, **info}
                for rid, info in self._runs.items()
            ]

    def is_running(self) -> bool:
        with self._lock:
            return any(
                info["status"] == "running" for info in self._runs.values()
            )


_tracker = RunTracker()

# ---------------------------------------------------------------------------
# Figma UAT state — tracks chats awaiting a Figma URL after APK upload
# ---------------------------------------------------------------------------

# {chat_id: {"apk_path": str, "state": "waiting_figma_url"}}
_pending_figma: dict[int, dict] = {}

# ---------------------------------------------------------------------------
# Background UAT runner
# ---------------------------------------------------------------------------


def _run_uat_in_background(
    run_id: str,
    feature: str,
    accounts_file: str,
    chat_id: int,
    app,          # The Application instance — used to queue send_message callbacks
) -> None:
    """Execute UAT in a worker thread and notify the user when done."""
    try:
        # Lazy import to avoid circular deps and slow startup
        from agent.orchestrator import Orchestrator  # noqa: PLC0415

        # Load accounts from JSON file
        accounts_path = Path(accounts_file)
        if accounts_path.exists():
            with open(accounts_path) as f:
                accounts = json.load(f)
        else:
            accounts = []

        orchestrator = Orchestrator(
            candidate_apk=str(_CANDIDATE_APK),
            feature_description=feature,
            accounts=accounts,
            run_id=run_id,
        )
        result = orchestrator.run()
        _tracker.complete(run_id, result)

        # Build notification message
        passed = result.get("passed", 0)
        failed = result.get("failed", 0)
        total = result.get("total", passed + failed)
        pass_rate = f"{round(passed / total * 100)}%" if total else "N/A"

        msg = (
            f"*UAT Run Complete* ✓\n"
            f"Run ID: `{run_id}`\n"
            f"Feature: {feature}\n"
            f"Result: {passed}/{total} passed ({pass_rate})\n\n"
            f"Use /report to fetch the full report."
        )

        # Also try to generate the report file
        try:
            report_path = orchestrator.generate_report()
            if report_path:
                msg += f"\nReport saved: `{Path(report_path).name}`"
        except Exception as rep_err:
            logger.warning(f"[bot] generate_report failed: {rep_err}")

    except Exception as exc:
        _tracker.fail(run_id, str(exc))
        msg = (
            f"*UAT Run Failed*\n"
            f"Run ID: `{run_id}`\n"
            f"Error: {exc}"
        )
        logger.exception(f"[bot] UAT run {run_id} raised an exception")

    # Queue the notification back onto the event loop
    import asyncio  # noqa: PLC0415

    async def _notify() -> None:
        await app.bot.send_message(
            chat_id=chat_id,
            text=msg,
            parse_mode=ParseMode.MARKDOWN,
        )

    asyncio.run_coroutine_threadsafe(_notify(), app.loop)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "*MMT-OS UAT Bot*\n\n"
        "I run automated UAT on MakeMyTrip Android builds and report results.\n\n"
        "*Commands:*\n"
        "/run `<feature>` — start a standard UAT run\n"
        "/run_figma `<figma_url>` — start Figma-first UAT\n"
        "/status — show current run status\n"
        "/report — get the latest report\n"
        "/list — list recent runs\n"
        "/cases `<feature>` — list use cases for a feature\n"
        "/help — show this message\n\n"
        "To upload an APK, simply send a `.apk` file as a document.\n"
        "After uploading an APK, send a Figma URL to run design compliance UAT."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await cmd_start(update, context)


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat_id = update.effective_chat.id

    if _tracker.is_running():
        await update.message.reply_text(
            "A UAT run is already in progress. Use /status to check it."
        )
        return

    # Feature from args or env default
    feature = " ".join(context.args).strip() if context.args else _DEFAULT_FEATURE

    # Check APK exists
    if not _CANDIDATE_APK.exists():
        await update.message.reply_text(
            f"No candidate APK found at `{_CANDIDATE_APK}`.\n"
            "Please upload a `.apk` file as a document first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    run_id = f"run_{uuid.uuid4().hex[:8]}"
    _tracker.start(run_id, chat_id)

    await update.message.reply_text(
        f"*Starting UAT run*\n"
        f"Run ID: `{run_id}`\n"
        f"Feature: {feature}\n\n"
        "I'll notify you when it's done.",
        parse_mode=ParseMode.MARKDOWN,
    )

    thread = threading.Thread(
        target=_run_uat_in_background,
        args=(run_id, feature, _DEFAULT_ACCOUNTS_FILE, chat_id, context.application),
        daemon=True,
        name=f"uat-{run_id}",
    )
    thread.start()


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    latest = _tracker.latest()

    if not latest:
        await update.message.reply_text("No UAT runs recorded yet. Use /run to start one.")
        return

    run_id = _tracker.latest_run_id()
    status = latest["status"]
    elapsed = round(time.time() - latest["start_time"])

    if status == "running":
        msg = (
            f"*Run in progress*\n"
            f"Run ID: `{run_id}`\n"
            f"Elapsed: {elapsed}s"
        )
    elif status == "completed":
        result = latest.get("result") or {}
        passed = result.get("passed", 0)
        total = result.get("total", 0)
        pass_rate = f"{round(passed / total * 100)}%" if total else "N/A"
        msg = (
            f"*Last run completed*\n"
            f"Run ID: `{run_id}`\n"
            f"Pass rate: {pass_rate} ({passed}/{total})\n"
            f"Duration: {elapsed}s"
        )
    else:  # failed
        error = latest.get("error", "unknown error")
        msg = (
            f"*Last run failed*\n"
            f"Run ID: `{run_id}`\n"
            f"Error: {error}"
        )

    await update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN)


async def cmd_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    run_id = _tracker.latest_run_id()

    if not run_id:
        await update.message.reply_text("No runs yet. Use /run to start one.")
        return

    # Look for markdown report file first
    report_path = _REPORTS_DIR / f"uat_report_{run_id}.md"
    summary_path = _REPORTS_DIR / f"run_summary_{run_id}.json"

    if report_path.exists():
        content = report_path.read_text(encoding="utf-8")
        if len(content) <= 4000:
            await update.message.reply_text(
                f"```\n{content}\n```", parse_mode=ParseMode.MARKDOWN
            )
        else:
            # Send truncated preview + full file as document
            preview = content[:3800]
            await update.message.reply_text(
                f"*Report preview (truncated):*\n```\n{preview}\n...\n```",
                parse_mode=ParseMode.MARKDOWN,
            )
            with open(report_path, "rb") as fh:
                await update.message.reply_document(
                    document=fh,
                    filename=report_path.name,
                    caption=f"Full report for run `{run_id}`",
                    parse_mode=ParseMode.MARKDOWN,
                )

    elif summary_path.exists():
        # Fall back to JSON summary
        data = json.loads(summary_path.read_text(encoding="utf-8"))
        content = json.dumps(data, indent=2)
        if len(content) <= 4000:
            await update.message.reply_text(
                f"*Run Summary (`{run_id}`):*\n```json\n{content}\n```",
                parse_mode=ParseMode.MARKDOWN,
            )
        else:
            await update.message.reply_text(
                f"Summary too large. Key stats:\n"
                f"Passed: {data.get('passed', 'N/A')}\n"
                f"Failed: {data.get('failed', 'N/A')}\n"
                f"Total: {data.get('total', 'N/A')}",
            )
    else:
        latest = _tracker.latest()
        if latest and latest["status"] == "running":
            await update.message.reply_text("Run still in progress. Check back with /report once done.")
        else:
            await update.message.reply_text(
                f"No report file found for run `{run_id}`.\n"
                "The orchestrator may not have saved one yet.",
                parse_mode=ParseMode.MARKDOWN,
            )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runs = _tracker.all_runs()
    if not runs:
        await update.message.reply_text("No runs recorded this session.")
        return

    lines = ["*Recent UAT Runs:*\n"]
    for run in sorted(runs, key=lambda r: r["start_time"], reverse=True)[:10]:
        rid = run["run_id"]
        status = run["status"]
        result = run.get("result") or {}
        passed = result.get("passed", 0)
        total = result.get("total", 0)
        pass_rate = f"{round(passed / total * 100)}%" if total else "—"

        status_icon = {"running": "🔄", "completed": "✅", "failed": "❌"}.get(status, "?")
        lines.append(f"{status_icon} `{rid}` — {status} | pass {pass_rate}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_cases(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    feature = " ".join(context.args).strip() if context.args else _DEFAULT_FEATURE

    # Attempt to load use cases from the project's use-case registry
    use_cases_path = _REPO_ROOT / "config" / "use_cases.json"
    if not use_cases_path.exists():
        await update.message.reply_text(
            f"No use-case registry found at `{use_cases_path}`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    try:
        registry = json.loads(use_cases_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        await update.message.reply_text(f"Could not parse use-case registry: {exc}")
        return

    # Registry can be a dict keyed by feature, or a list
    if isinstance(registry, dict):
        cases = registry.get(feature, registry.get(feature.lower(), []))
    elif isinstance(registry, list):
        cases = [c for c in registry if c.get("feature", "").lower() == feature.lower()]
    else:
        cases = []

    if not cases:
        await update.message.reply_text(
            f"No use cases found for feature `{feature}`.\n"
            "Check /list for available features.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    lines = [f"*Use cases for `{feature}`:*\n"]
    for i, case in enumerate(cases, 1):
        if isinstance(case, dict):
            name = case.get("name", case.get("id", f"case_{i}"))
            desc = case.get("description", "")
            lines.append(f"{i}. *{name}*" + (f" — {desc}" if desc else ""))
        else:
            lines.append(f"{i}. {case}")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# APK upload handler
# ---------------------------------------------------------------------------


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Accept .apk file uploads and save to ./apks/candidate.apk."""
    doc: Document = update.message.document
    if not doc:
        return

    filename = doc.file_name or ""
    if not filename.lower().endswith(".apk"):
        await update.message.reply_text(
            "Only `.apk` files are accepted. Please send a valid APK.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        f"Downloading `{filename}` ({doc.file_size // 1024} KB)...",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        _APKS_DIR.mkdir(parents=True, exist_ok=True)
        tg_file = await context.bot.get_file(doc.file_id)
        await tg_file.download_to_drive(str(_CANDIDATE_APK))

        await update.message.reply_text(
            f"APK saved as `candidate.apk`.\n"
            f"Use /run to start a UAT run.",
            parse_mode=ParseMode.MARKDOWN,
        )
    except Exception as exc:
        logger.exception("[bot] APK download failed")
        await update.message.reply_text(f"Failed to save APK: {exc}")
        return

    # Offer Figma-first UAT mode
    chat_id = update.effective_chat.id
    _pending_figma[chat_id] = {
        "apk_path": str(_CANDIDATE_APK),
        "state": "waiting_figma_url",
    }
    await update.message.reply_text(
        f"*APK saved!* To test against a Figma design:\n\n"
        f"• Send me the Figma file URL (figma.com/...)\n"
        f"• Or use /run `<feature>` for standard UAT",
        parse_mode=ParseMode.MARKDOWN,
    )


# ---------------------------------------------------------------------------
# Text message handler — Figma URL detection
# ---------------------------------------------------------------------------


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Detect Figma URLs in plain text messages and trigger Figma UAT if pending."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Check if this looks like a Figma URL
    if "figma.com" not in text.lower():
        return  # not a Figma URL — ignore (let other handlers deal with it)

    pending = _pending_figma.get(chat_id)
    if not pending or pending.get("state") != "waiting_figma_url":
        await update.message.reply_text(
            "Figma URL detected, but no APK is pending for this chat.\n"
            "Please upload an APK first, then send the Figma URL.",
        )
        return

    # Clear pending state and trigger Figma UAT
    apk_path = pending["apk_path"]
    del _pending_figma[chat_id]

    await _start_figma_uat(
        update=update,
        context=context,
        figma_url=text,
        apk_path=apk_path,
        chat_id=chat_id,
    )


# ---------------------------------------------------------------------------
# /run_figma command handler
# ---------------------------------------------------------------------------


async def cmd_run_figma(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/run_figma <figma_url> — Run Figma-first UAT with the current candidate APK."""
    chat_id = update.effective_chat.id

    if not context.args:
        await update.message.reply_text(
            "Usage: /run_figma `<figma_url>`\n"
            "Example: /run_figma https://www.figma.com/design/ABC123/MyApp",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    figma_url = context.args[0].strip()
    if "figma.com" not in figma_url.lower():
        await update.message.reply_text(
            "That doesn't look like a valid Figma URL. "
            "Expected: https://www.figma.com/design/<FILE_ID>/name"
        )
        return

    if not _CANDIDATE_APK.exists():
        await update.message.reply_text(
            f"No candidate APK found at `{_CANDIDATE_APK}`.\n"
            "Please upload a `.apk` file as a document first.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    # Clear any lingering pending state
    _pending_figma.pop(chat_id, None)

    await _start_figma_uat(
        update=update,
        context=context,
        figma_url=figma_url,
        apk_path=str(_CANDIDATE_APK),
        chat_id=chat_id,
    )


async def _start_figma_uat(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    figma_url: str,
    apk_path: str,
    chat_id: int,
) -> None:
    """Shared helper: validate URL, show confirmation, then kick off background run."""
    if _tracker.is_running():
        await update.message.reply_text(
            "A UAT run is already in progress. Use /status to check it."
        )
        return

    run_id = f"figma_{uuid.uuid4().hex[:8]}"
    _tracker.start(run_id, chat_id)

    await update.message.reply_text(
        f"*Starting Figma UAT*\n"
        f"Run ID: `{run_id}`\n"
        f"Figma URL: {figma_url}\n\n"
        "Parsing Figma file... I'll update you with progress.",
        parse_mode=ParseMode.MARKDOWN,
    )

    thread = threading.Thread(
        target=_run_figma_uat_in_background,
        args=(run_id, figma_url, apk_path, chat_id, context.application),
        daemon=True,
        name=f"figma-uat-{run_id}",
    )
    thread.start()


# ---------------------------------------------------------------------------
# Background Figma UAT runner
# ---------------------------------------------------------------------------


def _run_figma_uat_in_background(
    run_id: str,
    figma_url: str,
    apk_path: str,
    chat_id: int,
    app,
) -> None:
    """Execute Figma-first UAT in a worker thread and notify when done."""
    import asyncio  # noqa: PLC0415

    async def _send(text: str) -> None:
        await app.bot.send_message(
            chat_id=chat_id,
            text=text,
            parse_mode=ParseMode.MARKDOWN,
        )

    def _notify(text: str) -> None:
        asyncio.run_coroutine_threadsafe(_send(text), app.loop)

    try:
        from agent.orchestrator import Orchestrator  # noqa: PLC0415

        result = Orchestrator.run_figma_uat(
            figma_url=figma_url,
            apk_path=apk_path,
            accounts=[],
            run_id=run_id,
            notify=_notify,
        )
        _tracker.complete(run_id, result)

        compliance_pct = round(result.get("compliance_rate", 0) * 100, 1)
        verdict = result.get("overall_verdict", "UNKNOWN")
        tested = result.get("tested_screens", 0)
        compliant = result.get("compliant", 0)
        report_path = result.get("report_path", "")

        msg = (
            f"*Figma UAT Complete*\n"
            f"Run ID: `{run_id}`\n"
            f"Verdict: *{verdict}*\n"
            f"Compliance: {compliance_pct}% ({compliant}/{tested} screens)\n"
        )
        if report_path:
            msg += f"Report: `{Path(report_path).name}`\n"
        msg += "\nUse /report to fetch the full report."

    except Exception as exc:
        _tracker.fail(run_id, str(exc))
        msg = (
            f"*Figma UAT Failed*\n"
            f"Run ID: `{run_id}`\n"
            f"Error: {exc}"
        )
        logger.exception(f"[bot] Figma UAT run {run_id} raised an exception")

    _notify(msg)


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
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("run_figma", cmd_run_figma))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("cases", cmd_cases))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    # Must be registered AFTER document handler so APK uploads are handled first
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("[bot] Starting polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)
