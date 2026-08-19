"""
Phase 0 regression tests — frontend truth-in-labeling.

Covers the four things Phase 0 was asked to guarantee:
  1. no auto-launch of the scripted investigation on page load
  2. correct provenance labels, and no fabricated metrics presented as results
  3. /dashboard route behaviour (trailing slash must not embed the demo page)
  4. one configurable backend URL, no port drift

Two kinds of check are used:
  * Static assertions over the frontend sources. The landing page is a single
    4100-line HTML file with an inline script, so text-level assertions are the
    practical regression guard.
  * Behavioural assertions: the pure helpers in App.jsx (isDashboardPath,
    resolveProvenance) are extracted and executed with node, so the routing and
    provenance logic is actually run rather than merely pattern-matched. These
    tests skip if node is unavailable.

Run:
    cd sentinel/backend && python3 -m unittest tests.test_phase0_frontend -v
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
_FRONTEND = os.path.abspath(os.path.join(_BACKEND_ROOT, "..", "frontend"))
_REPO = os.path.abspath(os.path.join(_BACKEND_ROOT, "..", ".."))

APP_JSX = os.path.join(_FRONTEND, "src", "App.jsx")
HEADER_NAV = os.path.join(_FRONTEND, "src", "components", "HeaderNav.jsx")
CLIENT_JS = os.path.join(_FRONTEND, "src", "api", "client.js")
SELECTORS_JS = os.path.join(_FRONTEND, "src", "state", "selectors.js")
# Phase 3: the generated data contract the frontend imports its vocabulary from.
CONTRACT_JS = os.path.join(_REPO, "contracts", "frontend", "contract.js")
INDEX_HTML = os.path.join(_FRONTEND, "index.html")
PUBLIC_LANDING = os.path.join(_FRONTEND, "public", "landing.html")
DASHBOARD_LANDING = os.path.join(_FRONTEND, "dashboard", "landing.html")
GENERATE_CONFIG = os.path.join(_FRONTEND, "scripts", "generate-config.js")
ENV_EXAMPLE = os.path.join(_FRONTEND, ".env.example")
VERCEL_JSON = os.path.join(_FRONTEND, "vercel.json")

# Every copy of the landing page that could be served.
LANDING_COPIES = [INDEX_HTML, PUBLIC_LANDING, DASHBOARD_LANDING]

# The single backend URL default that all config points must agree on.
EXPECTED_DEFAULT_BACKEND_URL = "http://localhost:8000"

NODE = shutil.which("node")


def read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def strip_comments(src: str) -> str:
    """Remove HTML, //, and /* */ comments.

    Phase 0 deliberately leaves comments naming the strings it removed (e.g.
    'previously read "ECSS Standardized Commands"'). Those notes are useful, so
    every content assertion runs against comment-stripped source — otherwise the
    documentation of a fix would trip the test guarding it.
    """
    src = re.sub(r"<!--.*?-->", "", src, flags=re.DOTALL)
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    src = re.sub(r"^[ \t]*//.*$", "", src, flags=re.MULTILINE)
    return src


def run_node(script: str) -> str:
    """Execute a JS snippet with node and return its stdout."""
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as fh:
        fh.write(script)
        tmp = fh.name
    try:
        proc = subprocess.run(
            [NODE, tmp], capture_output=True, text=True, timeout=30, check=False
        )
        if proc.returncode != 0:
            raise AssertionError(f"node failed: {proc.stderr.strip()}")
        return proc.stdout
    finally:
        os.unlink(tmp)


def extract_block(source: str, opening: str) -> str:
    """Return the source text of a brace-balanced block starting at `opening`."""
    start = source.index(opening)
    depth = 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start : i + 1]
    raise AssertionError(f"unbalanced block for {opening!r}")


# ═══════════════════════════════════════════════════════════════════
# 1. NO AUTO-LAUNCH
# ═══════════════════════════════════════════════════════════════════

class TestNoAutoLaunch(unittest.TestCase):
    """The scripted investigation must never start without user action."""

    def test_landing_copies_exist(self):
        for path in LANDING_COPIES:
            self.assertTrue(os.path.isfile(path), msg=path)

    def test_no_copy_auto_launches_on_load(self):
        # The original defect was:
        #   setTimeout(() => { loadPresetJSON(); launchInvestigation(); }, 1200);
        pattern = re.compile(r"launchInvestigation\s*\(\s*\)\s*;?\s*\}\s*,\s*\d+\s*\)")
        for path in LANDING_COPIES:
            with self.subTest(file=os.path.basename(path)):
                self.assertIsNone(
                    pattern.search(read(path)),
                    msg="launchInvestigation() must not be called from a load timer",
                )

    def test_launch_is_only_reachable_from_an_explicit_control(self):
        for path in LANDING_COPIES:
            src = strip_comments(read(path))
            # Skip the declaration itself so only call sites remain.
            call_sites = [
                m.start()
                for m in re.finditer(r"launchInvestigation\s*\(", src)
                if not src[max(0, m.start() - 12) : m.start()].rstrip().endswith("function")
            ]
            with self.subTest(file=os.path.basename(path)):
                self.assertGreater(len(call_sites), 0, msg="button handler should remain")
                for pos in call_sites:
                    context = src[max(0, pos - 200) : pos]
                    self.assertTrue(
                        "onclick" in context,
                        msg=(
                            "launchInvestigation() must only be called from an onclick "
                            f"handler; found a call at offset {pos} in "
                            f"{os.path.basename(path)} with no onclick in scope"
                        ),
                    )

    def test_load_timer_still_populates_the_input(self):
        # Removing the auto-run must not remove the preset JSON convenience.
        for path in LANDING_COPIES:
            with self.subTest(file=os.path.basename(path)):
                self.assertIn("loadPresetJSON();", read(path))


# ═══════════════════════════════════════════════════════════════════
# 2. PROVENANCE LABELS AND WITHDRAWN METRICS
# ═══════════════════════════════════════════════════════════════════

class TestProvenanceLabelsInFrontend(unittest.TestCase):

    def setUp(self):
        self.app = read(APP_JSX)
        self.contract = read(CONTRACT_JS)
        self.header_nav = read(HEADER_NAV)

    def test_app_defines_the_provenance_vocabulary(self):
        # The vocabulary moved into the generated contract in Phase 3; the
        # operator console imports it rather than re-typing the codes.
        for code in ["REAL", "SYNTHETIC", "SYNTHETIC_FROM_REAL_METADATA", "UNKNOWN"]:
            with self.subTest(code=code):
                self.assertIn(code, self.contract)
        self.assertIn("PROVENANCE_LABELS", self.header_nav)

    def test_required_display_wording_present(self):
        """The labels themselves moved into the generated contract in Phase 3.

        HeaderNav.jsx imports PROVENANCE_LABELS rather than re-typing the
        strings, so the wording is asserted where it is now defined. Checking
        App.jsx for these literals would now pass only if someone had
        re-introduced the duplication Phase 3 removed.
        """
        for wording in ("SYNTHETIC FROM REAL METADATA", "SYNTHETIC DATA",
                        "REAL ESA TELEMETRY", "PROVENANCE UNKNOWN"):
            with self.subTest(wording=wording):
                self.assertIn(wording, self.contract)
        # And the operator console must actually consume them.
        self.assertIn("PROVENANCE_LABELS", self.header_nav)
        self.assertIn('from "../generated/contract"', self.header_nav)

    def test_scenarios_declare_provenance_not_a_handwritten_source_type(self):
        """Phase 3 deleted App.jsx's LOCAL_PRESET_SCENARIOS copy.

        The property this test guards — a scenario declares a provenance CODE and
        the label is derived from it — is now a backend property, so it is
        asserted against the served catalogue. The frontend half of the property
        is that App.jsx contains no hand-written provenance label at all.
        """
        from app.api.scenarios import get_all_scenarios
        from app.api.provenance import display_label

        self.assertNotIn('"source_type": "Synthetic Safe Mode"', self.app)
        self.assertNotIn('"source_type": "Real ESA Telemetry"', self.app)
        self.assertNotIn("LOCAL_PRESET_SCENARIOS", self.app)

        scenarios = get_all_scenarios()
        self.assertTrue(scenarios)
        for scenario in scenarios:
            sid = scenario.get("scenario_id")
            with self.subTest(scenario_id=sid):
                self.assertIn("provenance", scenario)
                self.assertEqual(
                    scenario["source_type"],
                    display_label(scenario["provenance"]),
                    "source_type must be derived from provenance, not authored",
                )

    def test_only_one_scenario_is_real(self):
        """Only ESA-ADB id_109 has numeric telemetry read from the dataset."""
        from app.api.scenarios import get_all_scenarios
        from app.api.provenance import Provenance

        real = [s for s in get_all_scenarios()
                if s.get("provenance") == Provenance.REAL.value]
        self.assertEqual(
            len(real), 1,
            f"expected exactly 1 REAL scenario, got "
            f"{[s.get('scenario_id') for s in real]}",
        )

    def test_badge_no_longer_infers_real_from_a_substring(self):
        # The defect: source_type.includes("ESA") -> "REAL ESA TELEMETRY".
        self.assertNotIn('.includes("ESA")', self.app)

    def test_simulation_indicator_present_for_non_real_data(self):
        # The SOURCE pill in HeaderNav renders the backend-provided
        # simulation_live_status — the indicator is served, not inferred.
        self.assertIn("simulation_live_status", self.header_nav)
        # And the generated contract still exposes the real-provenance gate.
        self.assertIn("isRealProvenance", self.contract)
        self.assertNotIn('.includes("ESA")', self.app)

    @unittest.skipIf(NODE is None, "node not available")
    def test_provenance_resolution_behaviour(self):
        """Execute the real resolver rather than pattern-matching it.

        Phase 3 moved the vocabulary and normalizeProvenance() into the
        generated contract; the operator console resolves labels through
        HeaderNav.jsx's provenanceLabel(). contract.js only declares top-level
        `export const`/`export function`, so stripping the keyword yields
        runnable script; provenanceLabel is a plain function declaration.
        """
        parts = [
            read(CONTRACT_JS).replace("export const ", "const ")
                             .replace("export function ", "function "),
            extract_block(read(HEADER_NAV), "function provenanceLabel"),
        ]
        cases = [
            # (scenario, expected label, expected isRealTelemetry)
            ({"provenance": "REAL"}, "REAL ESA TELEMETRY", True),
            ({"provenance": "SYNTHETIC"}, "SYNTHETIC DATA", False),
            (
                {"provenance": "SYNTHETIC_FROM_REAL_METADATA"},
                "SYNTHETIC FROM REAL METADATA",
                False,
            ),
            # Nothing below may resolve to REAL.
            ({"provenance": "UNKNOWN"}, "PROVENANCE UNKNOWN", False),
            ({}, "PROVENANCE UNKNOWN", False),
            ({"provenance": "real"}, "PROVENANCE UNKNOWN", False),
            ({"provenance": "Real ESA Telemetry"}, "PROVENANCE UNKNOWN", False),
            # The old defect: source_type containing "ESA" must not imply real.
            (
                {"source_type": "Real ESA Telemetry", "provenance": "SYNTHETIC_FROM_REAL_METADATA"},
                "SYNTHETIC FROM REAL METADATA",
                False,
            ),
            (None, "PROVENANCE UNKNOWN", False),
        ]
        script = "\n".join(parts) + "\nconsole.log(JSON.stringify(["
        script += ",".join(
            "[provenanceLabel(%s), isRealProvenance(%s && (%s.provenance || %s.source_type))]"
            % (
                json.dumps(scenario),
                json.dumps(scenario),
                json.dumps(scenario),
                json.dumps(scenario),
            )
            for scenario, _, _ in cases
        )
        script += "]));"
        got = json.loads(run_node(script))
        for (scenario, want_label, want_real), (label, real) in zip(cases, got):
            with self.subTest(scenario=scenario):
                self.assertEqual(label, want_label)
                self.assertEqual(real, want_real)

    def test_landing_pages_carry_a_persistent_simulation_banner(self):
        for path in LANDING_COPIES:
            src = read(path)
            with self.subTest(file=os.path.basename(path)):
                self.assertIn('id="sim-banner"', src)
                self.assertIn("Scripted demonstration", src)

    def test_connection_notice_does_not_auto_hide(self):
        # showConnIndicator used to hide the "mock data" notice after 5s.
        for path in LANDING_COPIES:
            src = read(path)
            block = extract_block(src, "function showConnIndicator")
            with self.subTest(file=os.path.basename(path)):
                self.assertNotIn(
                    "setTimeout",
                    block,
                    msg="provenance notice must not disappear while content is on screen",
                )


class TestFabricatedMetricsWithdrawn(unittest.TestCase):
    """Unevidenced figures must read NOT EVALUATED."""

    # Substrings that asserted a measured result. Comments are stripped before
    # matching so the explanatory notes left in the source do not trip these.
    FORBIDDEN = [
        "99.999%",
        "99.9%",
        "87% Match",
        "Match Accuracy",
        "8.2 seconds",
        "8.2s",
        "92% Success",
        "88% Success",
        "95% Success",
        "91% Success",
        "SENTINEL Projection",
        "AUTO RECOVERY PERMITTED",
    ]

    def test_no_fabricated_metric_strings_remain(self):
        for path in LANDING_COPIES:
            body = strip_comments(read(path))
            for needle in self.FORBIDDEN:
                with self.subTest(file=os.path.basename(path), needle=needle):
                    self.assertFalse(
                        needle in body,
                        msg=f"{os.path.basename(path)} still presents {needle!r} as a result",
                    )

    def test_not_evaluated_is_used_instead(self):
        for path in LANDING_COPIES:
            with self.subTest(file=os.path.basename(path)):
                self.assertGreaterEqual(read(path).count("NOT EVALUATED"), 10)

    def test_no_random_jitter_on_financial_counter(self):
        for path in LANDING_COPIES:
            body = strip_comments(read(path))
            with self.subTest(file=os.path.basename(path)):
                self.assertFalse(
                    re.search(r"Math\.random\(\)\s*\*\s*0\.15", body),
                    msg="random jitter must not be injected into a financial figure",
                )

    def test_confidence_and_evidence_counters_are_not_fabricated(self):
        # INTEL_PRESETS previously carried confidence/evidence/ragMatch literals.
        for path in LANDING_COPIES:
            body = strip_comments(read(path))
            for key in ["confidence:", "evidence:", "ragMatch:"]:
                with self.subTest(file=os.path.basename(path), key=key):
                    self.assertFalse(
                        key in body,
                        msg=f"INTEL_PRESETS must not carry a hardcoded {key!r}",
                    )

    def test_dashboard_labels_are_accurate(self):
        app = strip_comments(read(APP_JSX))
        for needle in [
            "ECSS Standardized Commands",
            "Z-Score Monitoring Active",
            "SSE Streaming Active",
            "AUTO RECOVERY PERMITTED",
        ]:
            with self.subTest(needle=needle):
                self.assertFalse(needle in app, msg=f"App.jsx still renders {needle!r}")


class TestFakeStatusIndicatorsRemoved(unittest.TestCase):
    """No element may assert a link, health figure or mission time we don't have."""

    FORBIDDEN = [
        "1283 DAYS",           # frozen mission time, never referenced by JS
        "● CONNECTED",         # link status with no backend behind it
        "● ONLINE",            # agent status with no backend behind it
        "SAFE MODE ACTIVE",    # asserted spacecraft state
        "AI AGENT: ONLINE",
        "RECOVERY: IN PROGRESS",
        "ESA Compliance Monitor",
    ]

    def test_no_fake_status_strings(self):
        for path in LANDING_COPIES:
            body = strip_comments(read(path))
            for needle in self.FORBIDDEN:
                with self.subTest(file=os.path.basename(path), needle=needle):
                    self.assertFalse(
                        needle in body,
                        msg=f"{os.path.basename(path)} still asserts {needle!r}",
                    )

    def test_ribbon_health_is_not_written_from_a_hardcoded_sequence(self):
        for path in LANDING_COPIES:
            body = strip_comments(read(path))
            with self.subTest(file=os.path.basename(path)):
                self.assertFalse(
                    "healthPcts" in body,
                    msg="spacecraft health must not come from a hardcoded sequence",
                )

    def test_backend_indicator_is_the_only_writer_of_link_state(self):
        for path in LANDING_COPIES:
            src = read(path)
            with self.subTest(file=os.path.basename(path)):
                self.assertIn("function setBackendIndicator", src)
                ribbon = strip_comments(extract_block(src, "function setRibbonStatus"))
                self.assertFalse(
                    "rb-signal" in ribbon,
                    msg="setRibbonStatus must not write the backend link field",
                )
                self.assertFalse(
                    "CONNECTED" in ribbon,
                    msg="setRibbonStatus must not assert a connection state",
                )
                self.assertFalse(
                    "rb-health" in ribbon,
                    msg="setRibbonStatus must not write a spacecraft health figure",
                )

    def test_demo_replay_is_labelled(self):
        for path in LANDING_COPIES:
            with self.subTest(file=os.path.basename(path)):
                self.assertIn("DEMO / REPLAY", read(path))


# ═══════════════════════════════════════════════════════════════════
# 3. DASHBOARD ROUTE BEHAVIOUR
# ═══════════════════════════════════════════════════════════════════

class TestDashboardRoute(unittest.TestCase):

    def setUp(self):
        self.app = read(APP_JSX)

    def test_strict_path_comparison_removed(self):
        self.assertNotIn('currentPath !== "/dashboard"', self.app)
        self.assertIn("isDashboardPath", self.app)

    def test_iframe_points_at_the_non_autolaunching_copy(self):
        # "/landing.html" did not exist at the served root, so the catch-all
        # served index.html — the auto-launching build — inside the dashboard.
        self.assertNotIn('src="/landing.html"', self.app)
        self.assertIn('src="/public/landing.html"', self.app)

    def test_vercel_resolves_landing_html_explicitly(self):
        routes = json.loads(read(VERCEL_JSON))["routes"]
        srcs = [r["src"] for r in routes]
        self.assertIn("/landing.html", srcs)
        self.assertIn("/public/landing.html", srcs)
        catch_all = srcs.index("/(.*)")
        # Both must be matched before the catch-all, or they fall through again.
        self.assertLess(srcs.index("/landing.html"), catch_all)
        self.assertLess(srcs.index("/public/landing.html"), catch_all)
        for route in routes:
            if route["src"] in ("/landing.html", "/public/landing.html"):
                self.assertEqual(route["dest"], "/public/landing.html")

    @unittest.skipIf(NODE is None, "node not available")
    def test_is_dashboard_path_behaviour(self):
        fn = extract_block(self.app, "function isDashboardPath")
        cases = {
            "/dashboard": True,
            "/dashboard/": True,      # the trailing-slash defect
            "/dashboard//": True,
            "/DASHBOARD": True,
            "/Dashboard/": True,
            "/": False,
            "": False,
            "/landing.html": False,
            "/dashboard/extra": False,
            "/notdashboard": False,
        }
        script = fn + "\nconsole.log(JSON.stringify({" + ",".join(
            f"{json.dumps(k)}: isDashboardPath({json.dumps(k)})" for k in cases
        ) + "}));"
        got = json.loads(run_node(script))
        for path, expected in cases.items():
            with self.subTest(path=path):
                self.assertEqual(got[path], expected)

    @unittest.skipIf(NODE is None, "node not available")
    def test_is_dashboard_path_rejects_non_strings(self):
        fn = extract_block(self.app, "function isDashboardPath")
        script = fn + "\nconsole.log(JSON.stringify([isDashboardPath(null), isDashboardPath(undefined), isDashboardPath(7)]));"
        self.assertEqual(json.loads(run_node(script)), [False, False, False])


# ═══════════════════════════════════════════════════════════════════
# 4. BACKEND URL CONFIGURATION
# ═══════════════════════════════════════════════════════════════════

class TestBackendUrlConfiguration(unittest.TestCase):
    """One configurable URL, one default, no drift."""

    def _defaults_found(self) -> dict[str, str]:
        found: dict[str, str] = {}
        for label, path, pattern in [
            ("client.js", CLIENT_JS, r'DEFAULT_BACKEND_URL\s*=\s*"([^"]+)"'),
            ("index.html", INDEX_HTML, r"SENTINEL_DEFAULT_BACKEND_URL\s*=\s*'([^']+)'"),
            ("public/landing.html", PUBLIC_LANDING, r"SENTINEL_DEFAULT_BACKEND_URL\s*=\s*'([^']+)'"),
            ("dashboard/landing.html", DASHBOARD_LANDING, r"SENTINEL_DEFAULT_BACKEND_URL\s*=\s*'([^']+)'"),
            ("generate-config.js", GENERATE_CONFIG, r"DEFAULT_BACKEND_URL\s*=\s*'([^']+)'"),
            ("*.env.example", ENV_EXAMPLE, r"REACT_APP_BACKEND_URL=(\S+)"),
        ]:
            m = re.search(pattern, read(path))
            self.assertIsNotNone(m, msg=f"no default backend URL found in {label}")
            found[label] = m.group(1)
        return found

    def test_all_defaults_agree(self):
        found = self._defaults_found()
        self.assertEqual(
            set(found.values()),
            {EXPECTED_DEFAULT_BACKEND_URL},
            msg=f"backend URL default drifted across config points: {found}",
        )

    def test_no_stale_ports_anywhere(self):
        stale = ["localhost:8001", "localhost:8005"]
        targets = LANDING_COPIES + [
            APP_JSX,
            GENERATE_CONFIG,
            ENV_EXAMPLE,
            os.path.join(_FRONTEND, "config.js"),
            os.path.join(_FRONTEND, "public", "config.js"),
        ]
        for path in targets:
            if not os.path.isfile(path):
                continue
            body = read(path)
            for needle in stale:
                with self.subTest(file=os.path.relpath(path, _FRONTEND), needle=needle):
                    self.assertNotIn(needle, body)

    def test_runtime_config_is_preferred_over_the_default(self):
        for path in LANDING_COPIES:
            src = read(path)
            with self.subTest(file=os.path.basename(path)):
                self.assertIn("window.SENTINEL_BACKEND_URL", src)
        self.assertIn("window.SENTINEL_BACKEND_URL", read(CLIENT_JS))

    @unittest.skipIf(NODE is None, "node not available")
    def test_generate_config_emits_the_expected_default(self):
        out_dir = tempfile.mkdtemp()
        try:
            # Run the real script in a copy of scripts/ so we never touch the
            # working tree's generated config.js.
            work = os.path.join(out_dir, "frontend")
            os.makedirs(os.path.join(work, "scripts"))
            os.makedirs(os.path.join(work, "public"))
            shutil.copy(GENERATE_CONFIG, os.path.join(work, "scripts"))
            proc = subprocess.run(
                [NODE, os.path.join(work, "scripts", "generate-config.js")],
                capture_output=True,
                text=True,
                timeout=30,
                env={k: v for k, v in os.environ.items() if k != "REACT_APP_BACKEND_URL"},
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            generated = read(os.path.join(work, "config.js"))
            self.assertIn(
                f'window.SENTINEL_BACKEND_URL = "{EXPECTED_DEFAULT_BACKEND_URL}"',
                generated,
            )
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)

    @unittest.skipIf(NODE is None, "node not available")
    def test_generate_config_honours_the_env_override(self):
        out_dir = tempfile.mkdtemp()
        try:
            work = os.path.join(out_dir, "frontend")
            os.makedirs(os.path.join(work, "scripts"))
            os.makedirs(os.path.join(work, "public"))
            shutil.copy(GENERATE_CONFIG, os.path.join(work, "scripts"))
            env = dict(os.environ)
            env["REACT_APP_BACKEND_URL"] = "https://sentinel-api.example.org"
            proc = subprocess.run(
                [NODE, os.path.join(work, "scripts", "generate-config.js")],
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, msg=proc.stderr)
            self.assertIn(
                'window.SENTINEL_BACKEND_URL = "https://sentinel-api.example.org"',
                read(os.path.join(work, "config.js")),
            )
        finally:
            shutil.rmtree(out_dir, ignore_errors=True)


# ═══════════════════════════════════════════════════════════════════
# SOURCE INTEGRITY
# ═══════════════════════════════════════════════════════════════════

class TestFrontendSourceIntegrity(unittest.TestCase):
    """Guard against a broken edit shipping silently."""

    @unittest.skipIf(NODE is None, "node not available")
    def test_landing_inline_scripts_parse(self):
        for path in LANDING_COPIES:
            src = read(path)
            blocks = re.findall(
                r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", src, re.IGNORECASE
            )
            with self.subTest(file=os.path.basename(path)):
                self.assertGreater(len(blocks), 0, msg="expected an inline script")
                for i, code in enumerate(blocks):
                    script = (
                        "const vm=require('vm');"
                        f"new vm.Script({json.dumps(code)});"
                        "console.log('ok');"
                    )
                    self.assertEqual(run_node(script).strip(), "ok", msg=f"block {i}")

    def test_landing_copies_stay_in_sync_except_video_playback(self):
        # index.html scrubs the hero video on scroll; the landing copies loop it.
        # Any OTHER divergence means a truth-in-labeling fix was applied to one
        # copy but not the others.
        allowed = {"video.pause", "video.currentTime", "video.loop", "video.muted",
                   "video.play", "targetTime", "Play video continuously",
                   "Pause video", "Set currentTime directly"}
        index_lines = read(INDEX_HTML).splitlines()
        for path in [PUBLIC_LANDING, DASHBOARD_LANDING]:
            other = read(path).splitlines()
            diff = set(index_lines).symmetric_difference(other)
            unexplained = [
                line.strip()
                for line in diff
                if line.strip() and not any(token in line for token in allowed)
            ]
            with self.subTest(file=os.path.basename(path)):
                self.assertEqual(
                    unexplained, [], msg="unexpected divergence between landing copies"
                )

    def test_dashboard_landing_matches_its_source(self):
        # dashboard/ is committed CRA build output; landing.html there is a copy
        # of public/landing.html and must not drift from it.
        self.assertEqual(read(DASHBOARD_LANDING), read(PUBLIC_LANDING))


if __name__ == "__main__":
    unittest.main(verbosity=2)
