"""
run_details_uat.py — Hotel Details Page UAT: 10.7.0 vs 11.3.0

Fully autonomous: launches MMT, navigates to hotel details, captures all sections.
No manual steps required after running.

Usage:
  .venv/bin/python3 run_details_uat.py --phase baseline   # capture v10.7.0
  .venv/bin/python3 run_details_uat.py --phase candidate  # capture v11.3.0
  .venv/bin/python3 run_details_uat.py --phase report     # generate diff report
  .venv/bin/python3 run_details_uat.py --phase all        # full end-to-end run

Optional:
  --hotel "Taj Mahal Palace"   Hotel name to search (default: uses HOTEL_SEARCH_QUERY)
"""
import os, sys, re, json, time, argparse
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent))
from dotenv import load_dotenv
load_dotenv()

from tools.android_device import AndroidDevice
from utils.claude_client import ask, ask_with_tools

RUN_ID        = "details_uat_10_7_vs_11_3"
EVIDENCE_BASE = Path(".tmp/evidence") / RUN_ID
BASELINE_DIR  = EVIDENCE_BASE / "baseline"
CANDIDATE_DIR = EVIDENCE_BASE / "candidate"
REPORT_DIR    = Path("reports")

PACKAGE            = "com.makemytrip"
HOTEL_SEARCH_QUERY = "Taj Palace New Delhi"   # default hotel to navigate to

# UI signals that confirm we are on the hotel details page
DETAILS_PAGE_SIGNALS = [
    "SELECT ROOM", "selectRoom", "Check-In", "Check-Out",
    "About This Property", "Similar Properties", "Property Rules",
    "Room Types", "Amenities", "Reviews", "Policies",
    "Travel Dates", "Guests/", "per night",
]

# UI signals that confirm we are past the hero gallery (real content visible)
GALLERY_CLEARED_SIGNALS = [
    "About This Property", "Amenities", "Room Types",
    "Similar Properties", "Property Rules", "Location",
    "Reviews", "Policies", "Travel Dates & Guests",
    "Check-In", "Check-Out",
]

# ──────────────────────────────────────────────
# Screen state helpers
# ──────────────────────────────────────────────

def get_screen_state(device: AndroidDevice) -> dict:
    """
    Returns a dict describing the current screen state:
      - package: current foreground app
      - on_mmt: bool
      - on_details_page: bool
      - gallery_cleared: bool  (content sections visible, not just gallery)
      - visible_texts: list of key text snippets from UI tree
    """
    try:
        pkg = device.get_current_package()
    except Exception:
        pkg = "unknown"

    on_mmt = pkg == PACKAGE

    visible_texts = []
    on_details = False
    gallery_cleared = False

    if on_mmt:
        try:
            xml = device.get_ui_tree()
            visible_texts = re.findall(r'text="([^"]{2,50})"', xml)[:40]
            text_blob = " ".join(visible_texts)
            on_details    = any(s in text_blob for s in DETAILS_PAGE_SIGNALS)
            gallery_cleared = any(s in text_blob for s in GALLERY_CLEARED_SIGNALS)
        except Exception:
            pass

    return {
        "package": pkg,
        "on_mmt": on_mmt,
        "on_details_page": on_details,
        "gallery_cleared": gallery_cleared,
        "visible_texts": visible_texts[:20],
    }


def launch_mmt(device: AndroidDevice) -> bool:
    """Force-launch MMT. Returns True if app comes to foreground within 5s."""
    print("  Launching MakeMyTrip...")
    device.d.app_start(PACKAGE)
    for _ in range(10):
        time.sleep(0.5)
        if device.get_current_package() == PACKAGE:
            print("  MMT is in foreground.")
            return True
    print("  WARNING: MMT did not come to foreground after launch.")
    return False


def navigate_to_hotel_details(device: AndroidDevice, hotel_query: str) -> bool:
    """
    From MMT home screen, search for hotel_query and open the first result's details page.
    Returns True if successfully landed on a hotel details page.
    """
    print(f"  Navigating to hotel details for: '{hotel_query}'")
    time.sleep(2)  # let home screen settle

    # Step 1: tap the Hotels tab or search bar
    xml = device.get_ui_tree()

    # Try tapping "Hotels" tab first
    if "Hotels" in xml:
        device.tap_text("Hotels", exact=True)
        time.sleep(2)

    # Step 2: find and tap the search/destination field
    # MMT home has a search input for destination
    tapped = False
    for hint in ["Where do you want to go", "Enter City", "Search Hotels", "City, Property Name"]:
        if hint in xml or device.tap_text(hint):
            tapped = True
            time.sleep(1.5)
            break

    if not tapped:
        # Fallback: tap near top center where search bar typically is
        w, h = device.get_screen_size()
        device.tap(w // 2, int(h * 0.28))
        time.sleep(1.5)

    # Step 3: type hotel name
    device.d.send_keys(hotel_query, clear=True)
    time.sleep(2)

    # Step 4: tap first suggestion
    xml = device.get_ui_tree()
    texts = re.findall(r'text="([^"]{3,60})"', xml)
    for t in texts:
        if any(word.lower() in t.lower() for word in hotel_query.split()):
            if device.tap_text(t):
                time.sleep(2)
                break
    else:
        # Fallback: tap first non-empty result below search bar
        w, h = device.get_screen_size()
        device.tap(w // 2, int(h * 0.35))
        time.sleep(2)

    # Step 5: on search results page, tap first hotel card
    time.sleep(2)
    xml = device.get_ui_tree()
    # Look for price text (₹) as indicator we're on listing page
    if "₹" in xml or "per night" in xml.lower():
        # Tap the first hotel result — typically around 40% down the screen
        w, h = device.get_screen_size()
        device.tap(w // 2, int(h * 0.40))
        time.sleep(3)

    # Step 6: verify we landed on details page
    state = get_screen_state(device)
    if state["on_details_page"]:
        print("  Successfully on hotel details page.")
        return True

    print("  Could not confirm details page. Will attempt capture anyway.")
    return False


def ensure_on_details_page(device: AndroidDevice, hotel_query: str) -> bool:
    """
    Full entry-point: checks current state, launches app if needed,
    navigates to details if needed. Returns True if ready to capture.
    """
    state = get_screen_state(device)
    print(f"  Screen state: pkg={state['package']}, on_mmt={state['on_mmt']}, "
          f"on_details={state['on_details_page']}")

    if state["on_details_page"]:
        print("  Already on details page. Ready to capture.")
        return True

    if not state["on_mmt"]:
        ok = launch_mmt(device)
        if not ok:
            return False
        time.sleep(2)

    # Check again after launch
    state = get_screen_state(device)
    if state["on_details_page"]:
        return True

    # Need to navigate
    return navigate_to_hotel_details(device, hotel_query)


# ──────────────────────────────────────────────
# Agent system prompt + tools
# ──────────────────────────────────────────────

SYSTEM_EXPLORE = """You are a QA agent capturing every section of a MakeMyTrip hotel details page.

## Your workflow — follow this EXACTLY:

### Step 1: Verify you are on the details page
Call check_screen first. If on_details_page is False, call open_mmt_app, then check_screen again.
Do NOT take any screenshots until on_details_page is True.

### Step 2: Escape the hero gallery
The hero gallery auto-cycles photos every few seconds. If gallery_cleared is False:
  a. Call take_screenshot with label "hero_gallery" — ONE time only.
  b. Call scroll_fast immediately (do NOT call scroll_down here).
  c. Call check_screen to verify gallery_cleared is now True.
  d. If still False, call scroll_fast again. Repeat max 3 times.
Only proceed to Step 3 once gallery_cleared is True.

### Step 3: Capture each content section
For each section below, in order:
  1. Call take_screenshot with the section label.
  2. Call scroll_down to reveal the next section.
  3. Wait — do NOT screenshot immediately after scrolling. Let content load.
  4. Check if new content is visible before screenshotting.

Sections to capture (use these exact labels):
  hotel_name_rating_price → booking_cta → about_property → amenities →
  room_types → location_map → reviews → policies → similar_hotels → page_bottom

### Step 4: Detect page end
If two consecutive scroll_down calls show no new content, call finish.

### Rules:
- NEVER screenshot while the hero gallery image is the dominant element on screen.
- NEVER screenshot the home screen, app drawer, or any non-MMT screen.
- If you see the home screen or wrong app, call open_mmt_app immediately.
- Use check_screen any time you are unsure what is on screen.
- Each section should have exactly ONE screenshot. Do not duplicate.
"""

TOOLS = [
    {
        "name": "check_screen",
        "description": (
            "Check the current screen state. Returns: current app package, "
            "whether you are on the MMT hotel details page, whether the hero gallery "
            "has been scrolled past (gallery_cleared), and key visible text elements. "
            "Always call this before taking your first screenshot, and after scroll_fast."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "open_mmt_app",
        "description": (
            "Force-launch the MakeMyTrip app and bring it to the foreground. "
            "Call this if check_screen shows on_mmt is False or you see a wrong screen."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "take_screenshot",
        "description": (
            "Capture a screenshot of the current screen and label it with the section name. "
            "Only call this when you have confirmed you are on the details page AND "
            "the hero gallery is cleared (or you are capturing the hero gallery itself)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "label": {
                    "type": "string",
                    "description": "Section label e.g. 'hotel_name_rating_price', 'amenities', 'reviews'"
                }
            },
            "required": ["label"]
        }
    },
    {
        "name": "scroll_down",
        "description": "Scroll down one step to reveal the next content section. Content takes ~1s to settle.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "scroll_fast",
        "description": (
            "Burst-scroll to escape the hero gallery. Fires multiple rapid safe swipes "
            "in the middle of the screen to jump past the gallery without triggering "
            "Android home gestures. Use ONLY to escape the gallery — not for regular scrolling."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "times": {"type": "integer", "description": "Number of burst scrolls, default 5"}
            },
            "required": []
        }
    },
    {
        "name": "scroll_up",
        "description": "Scroll back up if you need to re-capture a section.",
        "input_schema": {"type": "object", "properties": {}, "required": []}
    },
    {
        "name": "finish",
        "description": "Call when all sections have been captured and page bottom is reached.",
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {"type": "string", "description": "Summary of what was captured"}
            },
            "required": ["summary"]
        }
    }
]


# ──────────────────────────────────────────────
# Core exploration loop
# ──────────────────────────────────────────────

def explore_details_page(device: AndroidDevice, save_dir: Path, build_label: str,
                         hotel_query: str = HOTEL_SEARCH_QUERY) -> list[dict]:
    """
    Autonomous Claude-driven exploration of the MMT hotel details page.
    Handles app launch, navigation, gallery bypass, and section capture.
    """
    save_dir.mkdir(parents=True, exist_ok=True)
    captured = []
    done = False
    summary = ""

    # Pre-flight: ensure we are on the details page before handing off to Claude
    print(f"\n  Pre-flight check for {build_label}...")
    on_page = ensure_on_details_page(device, hotel_query)
    if not on_page:
        print("  WARNING: Could not confirm details page. Claude will attempt to recover.")

    initial_state = get_screen_state(device)
    print(f"  Initial state: {initial_state}")

    messages = [{
        "role": "user",
        "content": (
            f"You are capturing the MMT hotel details page for build {build_label}. "
            f"Current screen state: {json.dumps(initial_state)}. "
            f"Follow the workflow in your instructions exactly. "
            f"Start by calling check_screen, then proceed."
        )
    }]

    iteration = 0
    max_iterations = 60
    consecutive_no_new_content = 0

    print(f"  Starting exploration...")

    while not done and iteration < max_iterations:
        iteration += 1
        response = ask_with_tools(messages, TOOLS, system=SYSTEM_EXPLORE, max_tokens=1024)
        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print("  Agent returned end_turn without calling finish.")
            break

        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue

            name = block.name
            inp  = block.input

            # ── check_screen ──
            if name == "check_screen":
                state = get_screen_state(device)
                result = json.dumps(state)
                print(f"  [check_screen] {state['package']} | details={state['on_details_page']} | gallery_cleared={state['gallery_cleared']}")

            # ── open_mmt_app ──
            elif name == "open_mmt_app":
                launched = launch_mmt(device)
                time.sleep(2)
                state = get_screen_state(device)
                result = json.dumps({"launched": launched, **state})
                print(f"  [open_mmt_app] launched={launched}, on_mmt={state['on_mmt']}")

            # ── take_screenshot ──
            elif name == "take_screenshot":
                label = inp.get("label", f"section_{len(captured)+1}")
                safe  = "".join(c if c.isalnum() or c in "-_" else "_" for c in label)
                path  = save_dir / f"{len(captured)+1:02d}_{safe}.png"
                device.screenshot(save_path=str(path))
                captured.append({"label": label, "path": str(path)})
                print(f"    [{len(captured):02d}] {label}")
                result = f"screenshot saved: {path}"

            # ── scroll_down ──
            elif name == "scroll_down":
                prev_texts = set(get_screen_state(device)["visible_texts"])
                device.swipe("up")
                time.sleep(1.2)
                new_texts = set(get_screen_state(device)["visible_texts"])
                new_content = bool(new_texts - prev_texts)
                if not new_content:
                    consecutive_no_new_content += 1
                else:
                    consecutive_no_new_content = 0
                result = f"scrolled down. new_content_visible={new_content} (consecutive_empty={consecutive_no_new_content})"

            # ── scroll_fast ──
            elif name == "scroll_fast":
                times = inp.get("times", 5)
                w, h = device.get_screen_size()
                cx = w // 2
                # Safe mid-screen swipe: 65% → 30%, stays away from gesture zone
                y_start = int(h * 0.65)
                y_end   = int(h * 0.30)
                for _ in range(times):
                    device.swipe_coords(cx, y_start, cx, y_end, duration=0.25)
                    time.sleep(0.4)
                time.sleep(1.5)
                state = get_screen_state(device)
                result = (f"burst-scrolled {times}x. "
                          f"gallery_cleared={state['gallery_cleared']}, "
                          f"on_details={state['on_details_page']}")
                print(f"  [scroll_fast] {result}")

            # ── scroll_up ──
            elif name == "scroll_up":
                device.swipe("down")
                time.sleep(1.0)
                result = "scrolled up"

            # ── finish ──
            elif name == "finish":
                summary = inp.get("summary", "")
                done = True
                result = "exploration complete"
                print(f"  Agent finished. Captured {len(captured)} sections.")

            else:
                result = f"unknown tool: {name}"

            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": result
            })

        messages.append({"role": "user", "content": tool_results})

        # Safety: if page bottom confirmed by repeated empty scrolls, stop
        if consecutive_no_new_content >= 3 and not done:
            print("  3 consecutive empty scrolls — treating as page end.")
            done = True

    # Save manifest
    manifest = {
        "build": build_label,
        "run_id": RUN_ID,
        "hotel_query": hotel_query,
        "captured_at": datetime.now().isoformat(),
        "sections": captured,
        "summary": summary,
        "total_screenshots": len(captured)
    }
    with open(save_dir / "manifest.json", "w") as f:
        json.dump(manifest, f, indent=2)

    print(f"  Captured {len(captured)} sections. Saved to {save_dir}")
    return captured


# ──────────────────────────────────────────────
# Phase runners
# ──────────────────────────────────────────────

def run_baseline(device, hotel_query):
    print("\n=== PHASE 1: Baseline (10.7.0) ===")
    sections = explore_details_page(device, BASELINE_DIR, "10.7.0", hotel_query)
    print(f"\nBaseline captured: {len(sections)} screenshots")


def run_candidate(device, hotel_query):
    print("\n=== PHASE 2: Candidate (11.3.0) ===")
    sections = explore_details_page(device, CANDIDATE_DIR, "11.3.0", hotel_query)
    print(f"\nCandidate captured: {len(sections)} screenshots")


def run_report():
    print("\n=== PHASE 3: Generating Comparison Report ===")

    with open(BASELINE_DIR / "manifest.json") as f:
        baseline = json.load(f)
    with open(CANDIDATE_DIR / "manifest.json") as f:
        candidate = json.load(f)

    print(f"  Baseline:  {baseline['total_screenshots']} screenshots ({baseline['build']})")
    print(f"  Candidate: {candidate['total_screenshots']} screenshots ({candidate['build']})")

    from agent.diff_agent import DiffAgent

    print("\n  Running visual diff...")
    diff_agent = DiffAgent(
        baseline_dir=str(BASELINE_DIR),
        candidate_dir=str(CANDIDATE_DIR),
        feature_description="Hotel details page redesign — new layout launched in 11.3.0",
        run_id=RUN_ID
    )
    diff_results = diff_agent.analyze()

    print("  Assembling report with Claude...")
    sections_summary = "\n".join([
        f"- {c['label']}: {c.get('visual_diff', {}).get('assessment', 'not compared')}, "
        f"change_type={c.get('change_type','?')}, severity={c.get('severity','?')}"
        for c in diff_results.get("comparisons", [])
    ])

    report_prompt = f"""You are writing a UAT report for the MakeMyTrip hotel details page.

Baseline: MMT v10.7.0 (old details page)
Candidate: MMT v11.3.0 (new details page redesign)

Sections captured and compared:
{sections_summary}

Diff summary: {diff_results.get('summary', '')}

Write a structured UAT report in Markdown covering:
1. Executive Summary (2-3 sentences, verdict: PASS/FAIL/PASS WITH ISSUES)
2. Key Changes Detected (table: Section | Change Type | Severity | Description)
3. Regressions Found (if any)
4. New Elements Introduced
5. Missing Elements (present in old, absent in new)
6. Recommendations

Be specific and evidence-based."""

    report_md = ask(report_prompt, max_tokens=2000)

    device_label = baseline.get("build", "unknown")
    header = f"""# UAT Report — Hotel Details Page Redesign

**Run ID:** {RUN_ID}
**Date:** {datetime.now().strftime('%Y-%m-%d %H:%M')}
**Baseline:** MakeMyTrip v10.7.0
**Candidate:** MakeMyTrip v11.3.0
**Hotel (baseline):** {baseline.get('hotel_query', '-')}
**Hotel (candidate):** {candidate.get('hotel_query', '-')}
**Baseline screenshots:** {baseline['total_screenshots']}
**Candidate screenshots:** {candidate['total_screenshots']}

---

"""
    full_report = header + report_md

    full_report += "\n\n---\n\n## Appendix: Per-Section Visual Diff\n\n"
    full_report += "| Section | Diff % | Assessment | Change Type | Severity |\n"
    full_report += "|---------|--------|------------|-------------|----------|\n"
    for c in diff_results.get("comparisons", []):
        vd = c.get("visual_diff", {})
        full_report += (
            f"| {c.get('screen_label','-')} "
            f"| {vd.get('diff_percentage', 0):.1f}% "
            f"| {vd.get('assessment','-')} "
            f"| {c.get('change_type','-')} "
            f"| {c.get('severity','-')} |\n"
        )

    REPORT_DIR.mkdir(exist_ok=True)
    report_path = REPORT_DIR / f"uat_report_{RUN_ID}.md"
    with open(report_path, "w") as f:
        f.write(full_report)

    print(f"\n  Report saved: {report_path}")
    verdict_line = [l for l in report_md.split("\n") if "verdict" in l.lower() or "PASS" in l or "FAIL" in l]
    if verdict_line:
        print(f"\n  {verdict_line[0].strip()}")
    print(f"\n=== Done. Open {report_path} to review. ===\n")


# ──────────────────────────────────────────────
# APK install helpers
# ──────────────────────────────────────────────

def install_old_apk(device):
    old_apk = ".tmp/builds/MakeMyTrip-v991-10.7.0.RC1-integration_main-standard_charles-debug-13074.apk"
    print(f"\nInstalling 10.7.0 (baseline)... this may take ~2 min")
    ret = os.system(f'adb install -r "{old_apk}"')
    if ret != 0:
        print("Install failed. Check the device for any permission prompts.")
        sys.exit(1)
    print("10.7.0 installed.")


def install_new_apk():
    new_apk = ".tmp/builds/MakeMyTrip-v1021-11.3.0.RC4-integration_main-standard_charles-release-17145.apk"
    print(f"\nInstalling 11.3.0 (candidate)...")
    ret = os.system(f'adb install -r "{new_apk}"')
    if ret != 0:
        print("Install failed.")
        sys.exit(1)
    print("11.3.0 installed.")


# ──────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hotel Details Page UAT: 10.7.0 vs 11.3.0")
    parser.add_argument("--phase", choices=["baseline", "candidate", "report", "all"],
                        default="all")
    parser.add_argument("--hotel", default=HOTEL_SEARCH_QUERY,
                        help="Hotel name to search and navigate to")
    args = parser.parse_args()

    device = AndroidDevice()
    print(f"Connected: {device.serial} ({device.get_device_info()['model']})")

    if args.phase == "baseline":
        install_old_apk(device)
        run_baseline(device, args.hotel)

    elif args.phase == "candidate":
        install_new_apk()
        run_candidate(device, args.hotel)

    elif args.phase == "report":
        run_report()

    elif args.phase == "all":
        install_old_apk(device)
        run_baseline(device, args.hotel)
        install_new_apk()
        run_candidate(device, args.hotel)
        run_report()
