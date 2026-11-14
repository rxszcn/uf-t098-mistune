"""Formatting markers and code spans share one scope, not two.

A delimiter written between backticks is literal text: it closes nothing. These
tests pin that rule for every formatting plugin that scans for a closer, and
they keep the neighbouring contexts (links, autolinks, HTML, math) unchanged.
"""

from unittest import TestCase

from mistune import create_markdown


def render(text, plugins=("strikethrough",)):
    return create_markdown(plugins=list(plugins))(text).strip()


class TestFormattingAcrossCodeSpan(TestCase):
    def test_strikethrough_wraps_a_code_span(self):
        self.assertEqual(
            render("~~foo `bar` baz~~"),
            "<p><del>foo <code>bar</code> baz</del></p>",
        )

    def test_marker_inside_code_span_does_not_close_strikethrough(self):
        self.assertEqual(
            render("~~foo `a~~b` bar~~"),
            "<p><del>foo <code>a~~b</code> bar</del></p>",
        )

    def test_strikethrough_around_a_span_holding_only_the_marker(self):
        self.assertEqual(
            render("~~`~~`~~"),
            "<p><del><code>~~</code></del></p>",
        )

    def test_double_backtick_span_keeps_the_marker_literal(self):
        self.assertEqual(
            render("~~a ``b~~c`` d~~"),
            "<p><del>a <code>b~~c</code> d</del></p>",
        )

    def test_adjacent_spans_inside_strikethrough(self):
        self.assertEqual(
            render("~~`a` and `b~~c`~~"),
            "<p><del><code>a</code> and <code>b~~c</code></del></p>",
        )

    def test_strikethrough_and_code_span_next_to_each_other(self):
        self.assertEqual(
            render("~~a~~ `~~b~~`"),
            "<p><del>a</del> <code>~~b~~</code></p>",
        )

    def test_code_span_alone_is_untouched(self):
        self.assertEqual(render("`a~~b`"), "<p><code>a~~b</code></p>")

    def test_unclosed_code_span_leaves_marker_visible(self):
        # With no closing backtick nothing is a code span, so the first ``~~``
        # still closes the strikethrough and the stray backtick stays literal.
        self.assertEqual(render("~~a `b~~ c"), "<p><del>a `b</del> c</p>")


class TestFormattingPluginFamily(TestCase):
    """Every formatting plugin scans for a closer and must share the rule."""

    def test_mark_ignores_marker_inside_code_span(self):
        self.assertEqual(
            render("==mark `a==b` me==", ["mark"]),
            "<p><mark>mark <code>a==b</code> me</mark></p>",
        )

    def test_insert_ignores_marker_inside_code_span(self):
        self.assertEqual(
            render("^^insert `a^^b` me^^", ["insert"]),
            "<p><ins>insert <code>a^^b</code> me</ins></p>",
        )

    def test_mark_around_a_span_holding_only_the_marker(self):
        self.assertEqual(render("==`==`==", ["mark"]), "<p><mark><code>==</code></mark></p>")

    def test_insert_around_a_span_holding_only_the_marker(self):
        self.assertEqual(render("^^`^^`^^", ["insert"]), "<p><ins><code>^^</code></ins></p>")


class TestNestedFormattingAndCode(TestCase):
    def test_strikethrough_inside_mark_with_code_marker(self):
        self.assertEqual(
            render("==a ~~b `c==d` e~~ f==", ["strikethrough", "mark"]),
            "<p><mark>a <del>b <code>c==d</code> e</del> f</mark></p>",
        )

    def test_strikethrough_outside_mark_inside_code(self):
        self.assertEqual(
            render("~~a ==b `c~~d` e== f~~", ["strikethrough", "mark"]),
            "<p><del>a <mark>b <code>c~~d</code> e</mark> f</del></p>",
        )

    def test_mark_then_strikethrough_each_see_their_own_marker(self):
        self.assertEqual(
            render("~~a~~ ==b== `~~c~~ ==d==`", ["strikethrough", "mark"]),
            "<p><del>a</del> <mark>b</mark> <code>~~c~~ ==d==</code></p>",
        )

    def test_code_span_nested_inside_link_inside_strikethrough(self):
        self.assertEqual(
            render("~~[x `a~~b` y](/u) z~~"),
            '<p><del><a href="/u">x <code>a~~b</code> y</a> z</del></p>',
        )


class TestNeighbouringContextsUnchanged(TestCase):
    """Backticks owned by other constructs never open a code span."""

    def test_backtick_in_link_destination_does_not_shadow_marker(self):
        self.assertEqual(
            render("[x](/a`b) and ~~c~~ d`"),
            '<p><a href="/a%60b">x</a> and <del>c</del> d`</p>',
        )

    def test_backtick_in_autolink_does_not_shadow_marker(self):
        self.assertEqual(
            render("<http://a`b> and ~~del~~ y`"),
            '<p><a href="http://a%60b">http://a`b</a> and <del>del</del> y`</p>',
        )

    def test_backtick_in_html_tag_does_not_shadow_marker(self):
        md = create_markdown(escape=False, plugins=["strikethrough"])
        self.assertEqual(
            md('<a title="`">x</a> and ~~del~~ y`').strip(),
            '<p><a title="`">x</a> and <del>del</del> y`</p>',
        )

    def test_backtick_in_inline_math_does_not_shadow_marker(self):
        self.assertEqual(
            render("~~a $x`y$ b~~ c`", ["strikethrough", "math"]),
            '<p><del>a <span class="math">\\(x`y\\)</span> b</del> c`</p>',
        )

    def test_escaped_backtick_does_not_open_a_span(self):
        self.assertEqual(
            render(r"~~a \` b~~ c`"),
            "<p><del>a ` b</del> c`</p>",
        )

    def test_unmatched_backtick_before_formatting_is_literal(self):
        self.assertEqual(render("`a ~~b~~"), "<p>`a <del>b</del></p>")
