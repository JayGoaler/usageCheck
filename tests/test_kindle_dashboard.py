import re
import unittest
from html.parser import HTMLParser
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
HTML_PATH = ROOT / "web" / "static" / "index.html"


class ElementTreeParser(HTMLParser):
    VOID_TAGS = {
        "area", "base", "br", "embed", "hr", "img", "input",
        "link", "meta", "param", "source", "track", "wbr",
    }

    def __init__(self) -> None:
        super().__init__()
        self.stack: list[dict] = []
        self.elements: dict[str, dict] = {}
        self.ordered_elements: list[dict] = []

    def handle_starttag(self, tag, attrs) -> None:
        attributes = dict(attrs)
        element = {
            "tag": tag,
            "classes": set(attributes.get("class", "").split()),
            "parent": self.stack[-1] if self.stack else None,
        }
        self.ordered_elements.append(element)
        element_id = attributes.get("id")
        if element_id:
            self.elements[element_id] = element
        if tag not in self.VOID_TAGS:
            self.stack.append(element)

    def handle_endtag(self, tag) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index]["tag"] == tag:
                del self.stack[index:]
                break


class KindleDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.html = HTML_PATH.read_text(encoding="utf-8")
        style = re.search(r"<style>(.*?)</style>", cls.html, re.S | re.I)
        script = re.search(r"<script>(.*?)</script>", cls.html, re.S | re.I)
        if style is None or script is None:
            raise AssertionError("index.html must contain inline style and script")
        cls.css = style.group(1)
        cls.script = script.group(1)
        parser = ElementTreeParser()
        parser.feed(cls.html)
        cls.elements = parser.elements
        cls.ordered_elements = parser.ordered_elements

    def function_source(self, name: str) -> str:
        start = re.search(r"\bfunction\s+" + re.escape(name) + r"\s*\(", self.script)
        self.assertIsNotNone(start, name)
        opening = self.script.find("{", start.end())
        self.assertNotEqual(opening, -1, name)
        closing = self.matching_delimiter(self.script, opening, "{", "}")
        return self.script[start.start():closing + 1]

    def matching_delimiter(
        self,
        source: str,
        opening: int,
        open_char: str,
        close_char: str,
    ) -> int:
        depth = 1
        quote = None
        escaped = False
        line_comment = False
        block_comment = False
        index = opening + 1
        while index < len(source):
            char = source[index]
            following = source[index + 1] if index + 1 < len(source) else ""
            if line_comment:
                if char in "\r\n":
                    line_comment = False
            elif block_comment:
                if char == "*" and following == "/":
                    block_comment = False
                    index += 1
            elif quote:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == quote:
                    quote = None
            elif char in ("'", '"', "`"):
                quote = char
            elif char == "/" and following == "/":
                line_comment = True
                index += 1
            elif char == "/" and following == "*":
                block_comment = True
                index += 1
            elif char == open_char:
                depth += 1
            elif char == close_char:
                depth -= 1
            if depth == 0:
                return index
            index += 1
        self.fail("unclosed " + open_char + close_char + " block")

    def block_after(self, source: str, pattern: str) -> str:
        match = re.search(pattern, source, re.S)
        self.assertIsNotNone(match, pattern)
        opening = source.find("{", match.end())
        self.assertNotEqual(opening, -1, pattern)
        closing = self.matching_delimiter(source, opening, "{", "}")
        return source[opening + 1:closing]

    def callback_source(self, source: str, method: str, path: str) -> str:
        call = re.search(self.request_pattern(method, path), source)
        self.assertIsNotNone(call, path)
        callback = re.search(r"\bfunction\s*\(([^)]*)\)\s*\{", source[call.end():])
        self.assertIsNotNone(callback, path)
        absolute_start = call.end() + callback.start()
        opening = source.find("{", absolute_start)
        closing = self.matching_delimiter(source, opening, "{", "}")
        return source[absolute_start:closing + 1]

    def target_value_expression(self, source: str, element_id: str) -> str:
        call = re.search(
            r"\b(?:setText|setBar)\s*\(\s*['\"]"
            + re.escape(element_id)
            + r"['\"]\s*,",
            source,
        )
        if call is not None:
            opening = source.rfind("(", call.start(), call.end())
            closing = self.matching_delimiter(source, opening, "(", ")")
            arguments = source[call.end():closing]
            return arguments.strip()
        assignment = re.search(
            r"(?:byId|document\.getElementById)\s*\(\s*['\"]"
            + re.escape(element_id)
            + r"['\"]\s*\)[^=;]*=\s*([^;]+);",
            source,
            re.S,
        )
        self.assertIsNotNone(assignment, element_id)
        return assignment.group(1).strip()

    def aliases_derived_from(self, source: str, root_expression: str) -> set[str]:
        aliases: set[str] = set()
        assignments = list(re.finditer(
            r"(?:\bvar\s+)?\b([A-Za-z_$]\w*)\s*=\s*([^;]+);",
            source,
        ))
        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                name, expression = assignment.groups()
                if name in aliases:
                    continue
                derives_from_root = root_expression in expression
                derives_from_alias = any(
                    re.search(r"\b" + re.escape(alias) + r"\b", expression)
                    for alias in aliases
                )
                if derives_from_root or derives_from_alias:
                    aliases.add(name)
                    changed = True
        return aliases

    def metric_aliases(
        self,
        source: str,
        quota_root: str,
        property_name: str,
    ) -> tuple[set[str], set[str]]:
        quota_aliases = self.aliases_derived_from(source, quota_root)
        metric_aliases: set[str] = set()
        assignments = list(re.finditer(
            r"(?:\bvar\s+)?\b([A-Za-z_$]\w*)\s*=\s*([^;]+);",
            source,
        ))
        changed = True
        while changed:
            changed = False
            for assignment in assignments:
                name, expression = assignment.groups()
                if name in metric_aliases:
                    continue
                direct_property = quota_root + "." + property_name in expression
                aliased_property = any(
                    re.search(
                        r"\b" + re.escape(alias) + r"\s*\.\s*"
                        + re.escape(property_name) + r"\b",
                        expression,
                    )
                    for alias in quota_aliases
                )
                derived_metric = any(
                    re.search(r"\b" + re.escape(alias) + r"\b", expression)
                    for alias in metric_aliases
                )
                if direct_property or aliased_property or derived_metric:
                    metric_aliases.add(name)
                    changed = True
        return quota_aliases, metric_aliases

    def expression_uses_metric(
        self,
        expression: str,
        quota_root: str,
        quota_aliases: set[str],
        metric_aliases: set[str],
        property_name: str,
    ) -> bool:
        if quota_root + "." + property_name in expression:
            return True
        if any(
            re.search(
                r"\b" + re.escape(alias) + r"\s*\.\s*"
                + re.escape(property_name) + r"\b",
                expression,
            )
            for alias in quota_aliases
        ):
            return True
        return any(
            re.search(r"\b" + re.escape(alias) + r"\b", expression)
            for alias in metric_aliases
        )

    def expression_references_quota(
        self,
        expression: str,
        quota_root: str,
        quota_aliases: set[str],
    ) -> bool:
        if quota_root in expression:
            return True
        return any(
            re.search(r"\b" + re.escape(alias) + r"\b", expression)
            for alias in quota_aliases
        )

    def assert_callback_error_null(self, source: str, message_pattern: str) -> None:
        self.assertRegex(
            source,
            r"\bcallback\s*\(\s*new\s+Error\s*\(\s*"
            + message_pattern
            + r"[\s\S]*?\)\s*,\s*null\s*\)",
        )

    def assert_error_branch_sets_status(
        self,
        callback: str,
        message: str,
    ) -> None:
        signature = re.search(r"function\s*\(\s*([A-Za-z_$]\w*)", callback)
        self.assertIsNotNone(signature)
        error_name = signature.group(1)
        error_if = re.search(
            r"\bif\s*\(([^)]*\b" + re.escape(error_name) + r"\b[^)]*)\)\s*\{",
            callback,
        )
        self.assertIsNotNone(error_if, "callback must branch on its error argument")
        opening = callback.find("{", error_if.end() - 1)
        closing = self.matching_delimiter(callback, opening, "{", "}")
        branch = callback[opening + 1:closing]
        self.assertIn(message, branch)
        self.assertRegex(branch, r"\bsetStatus\s*\(")

    def css_rule(self, selector: str, source: str | None = None) -> str:
        source = self.css if source is None else source
        match = re.search(
            r"(?:^|})\s*" + re.escape(selector) + r"\s*\{([^}]*)\}",
            source,
            re.S | re.I,
        )
        self.assertIsNotNone(match, selector)
        return match.group(1)

    def landscape_css(self) -> str:
        match = re.search(
            r"@media[^{]*orientation\s*:\s*landscape[^{]*\{",
            self.css,
            re.I,
        )
        self.assertIsNotNone(match, "landscape media query")
        depth = 1
        index = match.end()
        while index < len(self.css) and depth:
            if self.css[index] == "{":
                depth += 1
            elif self.css[index] == "}":
                depth -= 1
            index += 1
        self.assertEqual(depth, 0)
        return self.css[match.end():index - 1]

    def request_pattern(self, method: str, path: str) -> str:
        return (
            r"\brequestJSON\s*\(\s*['\"]" + re.escape(method)
            + r"['\"]\s*,\s*['\"]" + re.escape(path) + r"['\"]\s*,"
        )

    def assert_descends_from(self, element_id: str, class_name: str) -> None:
        parent = self.elements[element_id]["parent"]
        while parent is not None:
            if class_name in parent["classes"]:
                return
            parent = parent["parent"]
        self.fail(element_id + " must descend from ." + class_name)

    def test_uses_xhr_without_modern_syntax(self) -> None:
        self.assertIn("XMLHttpRequest", self.script)
        forbidden = (
            r"\bconst\s+", r"\blet\s+", r"\basync\b", r"\bawait\s+",
            r"=>", r"\bfetch\s*\(", r"\?\?", r"\?\.", r"`",
        )
        for pattern in forbidden:
            with self.subTest(pattern=pattern):
                self.assertIsNone(re.search(pattern, self.script))

    def test_uses_real_same_origin_request_calls(self) -> None:
        for method, path in (
            ("GET", "/api/dashboard"),
            ("POST", "/api/collect/usage"),
            ("POST", "/api/collect/usage/login"),
        ):
            with self.subTest(method=method, path=path):
                self.assertRegex(self.script, self.request_pattern(method, path))
        self.assertIsNone(re.search(r"['\"]\s*https?://", self.script, re.I))
        self.assertNotIn("localhost", self.script)
        self.assertNotIn("127.0.0.1", self.script)

    def test_codex_has_two_stacked_quota_bar_elements(self) -> None:
        for element_id in (
            "codex-primary-pct", "codex-primary-bar", "codex-primary-reset",
            "codex-secondary-pct", "codex-secondary-bar", "codex-secondary-reset",
        ):
            self.assertEqual(self.html.count('id="' + element_id + '"'), 1)
        self.assertIn("\u0035 \u5c0f\u65f6\u989d\u5ea6", self.html)
        self.assertIn("\u0037 \u5929\u989d\u5ea6", self.html)

    def test_codex_quota_targets_use_corresponding_primary_secondary_data(self) -> None:
        render = self.function_source("renderCodex")
        signature = re.search(
            r"function\s+renderCodex\s*\(\s*([A-Za-z_$]\w*)",
            render,
        )
        self.assertIsNotNone(signature)
        parameter = signature.group(1)
        primary_root = parameter + ".primary"
        secondary_root = parameter + ".secondary"
        primary_quota, primary_remaining = self.metric_aliases(
            render, primary_root, "remainingPercent",
        )
        secondary_quota, secondary_remaining = self.metric_aliases(
            render, secondary_root, "remainingPercent",
        )
        _, primary_reset = self.metric_aliases(render, primary_root, "resetTime")
        _, secondary_reset = self.metric_aliases(render, secondary_root, "resetTime")
        for (
            target,
            expected_root,
            expected_quota,
            expected_metric,
            property_name,
            wrong_root,
            wrong_quota,
        ) in (
            (
                "codex-primary-pct", primary_root, primary_quota,
                primary_remaining, "remainingPercent",
                secondary_root, secondary_quota,
            ),
            (
                "codex-primary-bar", primary_root, primary_quota,
                primary_remaining, "remainingPercent",
                secondary_root, secondary_quota,
            ),
            (
                "codex-primary-reset", primary_root, primary_quota,
                primary_reset, "resetTime",
                secondary_root, secondary_quota,
            ),
            (
                "codex-secondary-pct", secondary_root, secondary_quota,
                secondary_remaining, "remainingPercent",
                primary_root, primary_quota,
            ),
            (
                "codex-secondary-bar", secondary_root, secondary_quota,
                secondary_remaining, "remainingPercent",
                primary_root, primary_quota,
            ),
            (
                "codex-secondary-reset", secondary_root, secondary_quota,
                secondary_reset, "resetTime",
                primary_root, primary_quota,
            ),
        ):
            value = self.target_value_expression(render, target)
            with self.subTest(target=target):
                self.assertTrue(
                    self.expression_uses_metric(
                        value,
                        expected_root,
                        expected_quota,
                        expected_metric,
                        property_name,
                    ),
                    target + " must use corresponding " + property_name,
                )
                self.assertFalse(
                    self.expression_references_quota(
                        value,
                        wrong_root,
                        wrong_quota,
                    ),
                    target + " must not reference the opposite quota",
                )

    def test_codex_quota_sections_stay_vertically_stacked_in_all_orientations(
        self,
    ) -> None:
        quotas = [
            element for element in self.ordered_elements
            if "quota" in element["classes"]
        ]
        self.assertEqual(len(quotas), 2)
        self.assertNotIn("quota-secondary", quotas[0]["classes"])
        self.assertIn("quota-secondary", quotas[1]["classes"])
        self.assertEqual(quotas[0]["tag"], "div")
        self.assertEqual(quotas[1]["tag"], "div")

        default_quota = self.css_rule(".quota")
        self.assertIsNone(
            re.search(
                r"\bdisplay\s*:\s*(?:flex|inline|inline-block)\b"
                r"|\bfloat\s*:",
                default_quota,
                re.I,
            ),
        )
        self.assertRegex(
            self.css_rule(".quota-secondary"),
            r"\bborder-top\s*:\s*(?!0(?:\D|$))",
        )

        landscape_quota = self.css_rule(
            ".codex-card .quota",
            self.landscape_css(),
        )
        self.assertIsNone(
            re.search(
                r"\bdisplay\s*:\s*(?:flex|inline|inline-block)\b"
                r"|\bfloat\s*:",
                landscape_quota,
                re.I,
            ),
        )

    def test_card_structure_is_portrait_single_column_landscape_one_plus_two(self) -> None:
        for element_id in ("codex-card", "deepseek-card", "vpn-card"):
            self.assertIn(element_id, self.elements)
        self.assertLess(
            self.html.index('id="codex-card"'),
            self.html.index('id="deepseek-card"'),
        )
        self.assert_descends_from("deepseek-card", "secondary-row")
        self.assert_descends_from("vpn-card", "secondary-row")
        parent = self.elements["codex-card"]["parent"]
        while parent is not None:
            self.assertNotIn("secondary-row", parent["classes"])
            parent = parent["parent"]
        self.assertRegex(self.css_rule(".card"), r"\bwidth\s*:\s*100%")
        self.assertRegex(self.css_rule(".secondary-card"), r"\bdisplay\s*:\s*block")
        landscape = self.landscape_css()
        secondary = self.css_rule(".secondary-card", landscape)
        self.assertRegex(secondary, r"\bdisplay\s*:\s*inline-block")
        self.assertRegex(secondary, r"\bwidth\s*:\s*(?:49|50)(?:\.0+)?%")

    def test_visuals_are_high_contrast_and_animation_free(self) -> None:
        body = self.css_rule("body")
        self.assertRegex(body, r"(?i)\bbackground\s*:\s*#f7f7f4\b")
        self.assertRegex(body, r"(?i)\bcolor\s*:\s*#17191c\b")
        for pattern in (
            r"\btransition\s*:", r"\banimation\s*:",
            r"@keyframes\b", r"\blinear-gradient\s*\(",
        ):
            self.assertIsNone(re.search(pattern, self.css, re.I), pattern)

    def test_fullscreen_selects_invokes_and_falls_back_visibly(self) -> None:
        source = self.function_source("enterFullscreen")
        names = (
            "requestFullscreen", "webkitRequestFullscreen",
            "mozRequestFullScreen", "msRequestFullscreen",
        )
        positions = [source.find(name) for name in names]
        self.assertTrue(all(position >= 0 for position in positions))
        self.assertEqual(positions, sorted(positions))
        selected = re.search(
            r"(?:var\s+)?(\w+)\s*=\s*root\.requestFullscreen\s*\|\|\s*"
            r"root\.webkitRequestFullscreen\s*\|\|\s*"
            r"root\.mozRequestFullScreen\s*\|\|\s*root\.msRequestFullscreen",
            source,
        )
        self.assertIsNotNone(selected)
        self.assertRegex(
            source,
            re.escape(selected.group(1)) + r"\s*\.\s*(?:call|apply)\s*\(\s*root",
        )
        catch_branch = self.block_after(source, r"\bcatch\s*\([^)]*\)")
        self.assertRegex(
            catch_branch,
            r"(?:byId|document\.getElementById)\s*\(\s*['\"]fullscreen-help"
            r"['\"]\s*\)\.style\.display\s*=\s*['\"]block['\"]",
        )
        request_if = re.search(
            r"\bif\s*\(\s*" + re.escape(selected.group(1)) + r"\s*\)\s*\{",
            source,
        )
        self.assertIsNotNone(request_if)
        request_opening = source.find("{", request_if.end() - 1)
        request_closing = self.matching_delimiter(source, request_opening, "{", "}")
        unsupported_branch = source[request_closing + 1:]
        self.assertRegex(
            unsupported_branch,
            r"(?:byId|document\.getElementById)\s*\(\s*['\"]fullscreen-help"
            r"['\"]\s*\)\.style\.display\s*=\s*['\"]block['\"]",
        )
        self.assertIn(
            "\u8bf7\u901a\u8fc7 Kindle \u6d4f\u89c8\u5668\u83dc\u5355"
            "\u8fdb\u5165\u5168\u5c4f\u6216\u9690\u85cf\u5de5\u5177\u680f",
            self.html,
        )

    def test_debug_mode_reports_viewport_and_user_agent(self) -> None:
        source = self.function_source("showDebugIfRequested")
        for marker in (
            "screen.width", "screen.height", "window.innerWidth",
            "window.innerHeight", "document.documentElement.clientWidth",
            "document.documentElement.clientHeight", "devicePixelRatio",
            "navigator.userAgent",
        ):
            self.assertIn(marker, source)
        self.assertRegex(source, r"indexOf\(['\"]debug=1['\"]\)")
        self.assertIn("debug-panel", source)

    def test_xhr_routes_http_parse_network_and_timeout_errors_to_callback(self) -> None:
        source = self.function_source("requestJSON")
        http_branch = self.block_after(
            source,
            r"\bif\s*\([^)]*status\s*<\s*200[^)]*status\s*>=\s*300[^)]*\)",
        )
        self.assert_callback_error_null(http_branch, r"['\"]HTTP\s+['\"]")
        parse_catch = self.block_after(source, r"\bcatch\s*\([^)]*\)")
        self.assert_callback_error_null(
            parse_catch,
            r"['\"]\u6570\u636e\u683c\u5f0f\u9519\u8bef['\"]",
        )
        onerror = self.block_after(source, r"\.onerror\s*=\s*function\s*\([^)]*\)")
        self.assert_callback_error_null(
            onerror,
            r"['\"]\u65e0\u6cd5\u8fde\u63a5\u670d\u52a1['\"]",
        )
        ontimeout = self.block_after(source, r"\.ontimeout\s*=\s*function\s*\([^)]*\)")
        self.assert_callback_error_null(
            ontimeout,
            r"['\"]\u8bf7\u6c42\u8d85\u65f6['\"]",
        )

    def test_request_failures_are_written_to_visible_status(self) -> None:
        self.assertIn('id="status-message"', self.html)
        status = self.function_source("setStatus")
        self.assertIn("status-message", status)
        for function, method, path, message in (
            ("fetchDashboard", "GET", "/api/dashboard", "\u8bfb\u53d6\u5931\u8d25"),
            ("refreshUsage", "POST", "/api/collect/usage", "\u7528\u91cf\u5237\u65b0\u5931\u8d25"),
            ("openLogin", "POST", "/api/collect/usage/login", "\u6253\u5f00\u767b\u5f55\u5931\u8d25"),
        ):
            source = self.function_source(function)
            self.assertRegex(source, self.request_pattern(method, path))
            callback = self.callback_source(source, method, path)
            self.assert_error_branch_sets_status(callback, message)


if __name__ == "__main__":
    unittest.main()
