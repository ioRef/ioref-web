"""The file-backed catalogue: parsing, rendering, and the URLs it serves.

SimpleTestCase throughout, because there is no database. The parsing tests
build a content directory in a temporary folder so they can assert on exact
inputs; the view tests run against the real content/, because the real files
are the thing most likely to contain a case nobody thought to write a fixture
for.

Category is fetched live in views.py, not stored on Part, so those tests mock
stock.client rather than needing inventory reachable. See stock/tests.py for
the client-level coverage of list_categories/list_groups_by_category.
"""

import io
import re
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from catalog import content
from stock.client import InventoryUnavailable

FENCED = """Wire it up, then:

```cpp
void setup() {
  Serial.begin(9600);
}
```
"""

FIGURE = (
    '<figure class="image" style="text-align:center">'
    '<img src="/images/parts/pot.gif" alt="A potentiometer">'
    "<figcaption><em>Image from Sparkfun</em></figcaption></figure>"
)


def write_content(root, parts, categories=None, part_sets=None):
    root = Path(root)
    (root / "parts").mkdir(parents=True, exist_ok=True)
    (root / "categories.yml").write_text(
        categories or "categories:\n- slug: input\n  title: Input\n",
        encoding="utf-8",
    )
    (root / "part-sets.yml").write_text(
        part_sets or "part_sets:\n- slug: starter\n  title: Starter\n", encoding="utf-8"
    )
    for name, text in parts.items():
        (root / "parts" / f"{name}.md").write_text(text, encoding="utf-8")
    return root


class ParsingTests(SimpleTestCase):
    def load(self, parts, **kwargs):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = write_content(self.tmp.name, parts, **kwargs)
        with override_settings(CONTENT_DIR=root):
            return content.load()

    def test_sections_come_back_in_display_order_not_file_order(self):
        """The jump menu is built from this order, so it cannot follow the file.

        An author who appends a section to the end of a file should not thereby
        move it to the end of the page.
        """
        cat = self.load(
            {
                "p": "---\ntitle: P\ngroup: p\n---\n\n"
                "## Resources\n\nLinks.\n\n## About\n\nA thing.\n"
            }
        )
        self.assertEqual(
            [s.label for s in cat.by_slug["p"].sections], ["About", "Resources"]
        )

    def test_empty_sections_are_omitted(self):
        """Live card 0390 has no About section; the jump menu must skip it."""
        cat = self.load(
            {
                "p": "---\ntitle: P\ngroup: p\n---\n\n"
                "## About\n\n## What it is\n\nA variable resistor.\n"
            }
        )
        self.assertEqual([s.label for s in cat.by_slug["p"].sections], ["What it is"])

    def test_fenced_code_survives_rendering(self):
        """22 parts carry fenced blocks with language hints.

        This is why the content is markdown and why the sanitiser has to allow
        <pre> and <code>: rich text would have eaten them on first save.
        """
        cat = self.load(
            {"p": f"---\ntitle: P\ngroup: p\n---\n\n## Getting started\n\n{FENCED}"}
        )
        html = str(cat.by_slug["p"].sections[0].body_html)
        self.assertIn("<code", html)
        self.assertIn("Serial.begin(9600)", html)

    def test_figure_markup_survives_rendering(self):
        """59 diagrams are inline <figure> HTML. Narrowing the allowed tags
        deletes them silently, and only on the pages that use them."""
        cat = self.load(
            {"p": f"---\ntitle: P\ngroup: p\n---\n\n## How it works\n\n{FIGURE}\n"}
        )
        html = str(cat.by_slug["p"].sections[0].body_html)
        self.assertIn("<figure", html)
        self.assertIn("<figcaption", html)
        self.assertIn("/images/parts/pot.gif", html)

    def test_script_tags_are_stripped(self):
        cat = self.load(
            {
                "p": "---\ntitle: P\ngroup: p\n---\n\n"
                "## About\n\n<script>alert(1)</script>Safe.\n"
            }
        )
        self.assertNotIn("<script", str(cat.by_slug["p"].sections[0].body_html))

    def test_url_is_flat_and_keyed_on_slug(self):
        """No category prefix: parsing a guide must not need inventory."""
        cat = self.load({"p": "---\ntitle: P\ngroup: p\n---\n\n## About\n\nx\n"})
        self.assertEqual(cat.by_slug["p"].url, "/parts/p/")

    def test_a_group_appears_exactly_once(self):
        """A guide covers a whole group. Two files claiming one group is the
        bug check_groups exists to catch at the inventory end -- a second
        local file for the same group must not paper over it."""
        with self.assertRaises(ValueError) as caught:
            self.load(
                {
                    "a": "---\ntitle: A\ngroup: shared\n---\n\n## About\n\nx\n",
                    "b": "---\ntitle: B\ngroup: shared\n---\n\n## About\n\nx\n",
                }
            )
        self.assertIn("shared", str(caught.exception))

    def test_a_page_needs_a_group_or_inline_parts(self):
        """Otherwise there is nothing to show a stock table for -- the mistake
        this guards against is a copy-pasted file with the group line deleted
        and no parts: added back."""
        with self.assertRaises(ValueError):
            self.load({"p": "---\ntitle: P\n---\n\n## About\n\nx\n"})

    def test_inline_parts_stand_in_for_a_group(self):
        """A part inventory has not yet grouped still needs a page."""
        cat = self.load(
            {"p": "---\ntitle: P\nparts:\n- number: '0574'\n---\n\n## About\n\nx\n"}
        )
        self.assertEqual(cat.by_slug["p"].part_numbers, ["0574"])
        self.assertEqual(cat.by_slug["p"].group, "")

    def test_part_numbers_stay_strings(self):
        """0386 is an identifier, not the integer 386."""
        cat = self.load(
            {"p": "---\ntitle: P\nparts:\n- number: '0386'\n---\n\n## About\n\nx\n"}
        )
        self.assertEqual(cat.by_slug["p"].part_numbers, ["0386"])

    def test_unknown_section_heading_is_an_error_not_a_silent_drop(self):
        """A typo in a heading would otherwise delete the section from the page
        with nothing to show for it."""
        with self.assertRaises(ValueError) as caught:
            self.load({"p": "---\ntitle: P\ngroup: p\n---\n\n## Waht it is\n\nx\n"})
        self.assertIn("Waht it is", str(caught.exception))

    def test_dangling_related_slug_is_dropped_not_fatal(self):
        """A rename elsewhere should not 500 an unrelated page."""
        cat = self.load(
            {"p": "---\ntitle: P\ngroup: p\nrelated:\n- gone\n---\n\n## About\n\nx\n"}
        )
        self.assertEqual(cat.by_slug["p"].related_parts, [])

    def test_deeper_headings_do_not_split_the_file(self):
        """### inside a section belongs to the author, not to the parser --
        this is how the breadboard/wire/pushbutton merges are structured."""
        cat = self.load(
            {
                "p": "---\ntitle: P\ngroup: p\n---\n\n"
                "## About\n\nIntro.\n\n### A detail\n\nMore.\n"
            }
        )
        sections = cat.by_slug["p"].sections
        self.assertEqual(len(sections), 1)
        self.assertIn("A detail", sections[0].body)

    def test_repr_terminates(self):
        """Part.related_parts can point back at pages that point back at it.
        A generated repr that walks the cycle runs until the process is
        killed, which is how this was found: an unrelated error page tried
        to repr a view's locals."""
        cat = self.load(
            {
                "a": "---\ntitle: A\ngroup: a\nrelated:\n- b\n---\n\n## About\n\nx\n",
                "b": "---\ntitle: B\ngroup: b\nrelated:\n- a\n---\n\n## About\n\nx\n",
            }
        )
        self.assertIn("slug='a'", repr(cat.by_slug["a"]))

    def test_load_categories_is_the_fixed_five(self):
        cat_yml = "categories:\n- slug: input\n  title: Input\n- slug: power\n  title: Power\n"
        self.load(
            {"p": "---\ntitle: P\ngroup: p\n---\n\n## About\n\nx\n"}, categories=cat_yml
        )
        with override_settings(CONTENT_DIR=self.tmp.name):
            cats = content.load_categories()
        self.assertEqual([c.slug for c in cats], ["input", "power"])
        self.assertEqual(cats[0].url, "/categories/input/")


class RealContentTests(SimpleTestCase):
    """Against the real content/, not a fixture."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.catalogue = content.reload()

    def test_every_file_loads(self):
        expected = len(list((Path(settings.CONTENT_DIR) / "parts").glob("*.md")))
        self.assertEqual(len(self.catalogue.parts), expected)

    def test_every_part_has_a_group_or_inline_numbers(self):
        orphans = [
            p.slug for p in self.catalogue.parts if not p.group and not p.stocked
        ]
        self.assertEqual(orphans, [])

    def test_declared_groups_are_unique(self):
        groups = [p.group for p in self.catalogue.parts if p.group]
        self.assertEqual(len(groups), len(set(groups)))

    def test_every_section_heading_is_recognised(self):
        labels = {s.label for p in self.catalogue.parts for s in p.sections}
        self.assertTrue(labels <= set(content.LABEL_TO_ANCHOR))

    def test_search_ranks_title_matches_first(self):
        results = self.catalogue.search("resistor")
        self.assertTrue(results)
        self.assertIn("resistor", results[0].title.lower())

    def test_every_media_path_in_the_prose_exists_on_disk(self):
        """The diagrams are referenced by path, not by a database row.

        Nothing resolves these at runtime any more, so a missing or mis-cased
        file is a broken image on a live page and nothing else notices. The
        filenames contain spaces and mixed case, hence the wide character
        class and the exact-case check.
        """
        pattern = re.compile(r'/(images|videos)/parts/([^"\'<>)\]]+)')
        root = Path(settings.WHITENOISE_ROOT)
        missing = []
        for path in sorted((Path(settings.CONTENT_DIR) / "parts").glob("*.md")):
            for kind, name in pattern.findall(path.read_text(encoding="utf-8")):
                target = root / kind / "parts" / name.strip()
                if not target.exists():
                    missing.append(f"{path.name}: /{kind}/parts/{name.strip()}")
        self.assertEqual(missing, [])

    def test_frontmatter_images_exist_on_disk(self):
        for part in self.catalogue.parts:
            if part.image:
                target = (
                    Path(settings.WHITENOISE_ROOT) / "images" / "parts" / part.image
                )
                self.assertTrue(target.exists(), f"{part.slug}: {part.image}")


class ViewTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.catalogue = content.reload()

    def test_home(self):
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_every_guide_url_resolves(self):
        bad = [
            p.url
            for p in self.catalogue.parts
            if self.client.get(p.url).status_code != 200
        ]
        self.assertEqual(bad, [])

    @patch("catalog.views.get_part", return_value=None)
    def test_unknown_part_404s(self, mock_get):
        self.assertEqual(self.client.get("/parts/nonsense/").status_code, 404)

    @patch("catalog.views.get_part")
    def test_part_number_renders_its_group_guide_with_only_that_part(self, mock_get):
        mock_get.return_value = {
            "part_number": "0390",
            "group": {"slug": "potentiometers"},
            "description": "10k potentiometer",
            "location": "A1",
        }

        response = self.client.get("/parts/0390")
        self.assertRedirects(
            response, "/parts/0390/", status_code=301, fetch_redirect_response=False
        )

        response = self.client.get("/parts/0390/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Potentiometer")
        self.assertContains(response, "0390")
        self.assertContains(response, "A1")
        self.assertNotContains(response, "Full stock history →")

    @patch("catalog.views.get_part")
    def test_group_guide_title_is_the_part_not_the_guide(self, mock_get):
        mock_get.return_value = {
            "part_number": "2032",
            "group": {"slug": "batteries", "name": "Batteries"},
            "short_name": "CR2032 coin cell battery",
        }

        response = self.client.get("/parts/2032/")

        self.assertContains(response, "<title>CR2032 coin cell battery")
        self.assertContains(
            response, '<div class="part_name">CR2032 coin cell battery</div>'
        )

    @patch("catalog.views.get_part")
    def test_schemeless_purchase_link_renders_as_https(self, mock_get):
        mock_get.return_value = {
            "part_number": "1829",
            "group": {"slug": "fasteners", "name": "Fasteners"},
            "short_name": "Shoulder screw",
            "latest_price": {
                "supplier": "McMaster-Carr",
                "purchase_link": "mcmaster.com/#92095A182",
                "price": "0.08380",
                "currency": "USD",
            },
        }

        response = self.client.get("/parts/1829/")

        self.assertContains(response, 'href="https://mcmaster.com/#92095A182"')
        self.assertNotContains(response, 'href="/parts/1829/mcmaster.com')

    @patch("catalog.views.get_part")
    def test_unsafe_purchase_link_hides_the_row(self, mock_get):
        mock_get.return_value = {
            "part_number": "1829",
            "group": {"slug": "fasteners", "name": "Fasteners"},
            "short_name": "Shoulder screw",
            "latest_price": {"supplier": "Shelf", "purchase_link": "shelf 3"},
        }

        response = self.client.get("/parts/1829/")

        self.assertNotContains(response, "Purchase link")

    @patch("stock.client.list_by_group")
    def test_group_stock_table_links_each_variant_to_its_guide_view(self, mock_list):
        mock_list.return_value = [
            {
                "part_number": "0368",
                "group": {"slug": "accelerometers"},
                "short_name": "ADXL335 accelerometer",
                "location": "A1",
            },
            {
                "part_number": "1368",
                "group": {"slug": "accelerometers"},
                "short_name": "Another accelerometer",
                "location": "A2",
            },
        ]

        response = self.client.get("/parts/accelerometers/")

        self.assertContains(response, 'href="/parts/0368/"')
        self.assertNotContains(response, 'href="/inventory/0368/"')

    @patch("catalog.views.get_part")
    def test_part_number_without_guide_renders_stock_only_page(self, mock_get):
        mock_get.return_value = {
            "part_number": "0046",
            "group": {"slug": "fasteners"},
            "short_name": "M3 bolt",
            "location": "A4",
        }

        response = self.client.get("/parts/0046/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "M3 bolt")
        self.assertContains(response, "0046")
        self.assertContains(response, "A4")
        self.assertNotContains(response, "Full stock history")

    @patch("catalog.views.get_part")
    def test_ungrouped_part_can_use_an_explicit_guide(self, mock_get):
        mock_get.return_value = {
            "part_number": "0574",
            "group": None,
            "description": "PIR sensor",
            "location": "B2",
        }

        response = self.client.get("/parts/0574/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Passive Infrared Sensor")
        self.assertContains(response, "0574")

    def test_part_sets(self):
        self.assertEqual(self.client.get("/part-sets/").status_code, 200)
        for part_set in self.catalogue.part_sets:
            self.assertEqual(self.client.get(part_set.url).status_code, 200)

    def test_search_page(self):
        response = self.client.get("/search/", {"query": "resistor"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "esistor")

    def test_named_routes_are_not_swallowed_by_a_catch_all(self):
        self.assertEqual(self.client.get("/search/").status_code, 200)
        self.assertEqual(self.client.get("/part-sets/").status_code, 200)


@patch("catalog.views.list_ungrouped_parts_by_category", return_value=[])
@patch("catalog.views.list_groups_by_category")
class CategoryViewTests(SimpleTestCase):
    """/categories/<slug>/ is live: it never reads content/ to decide what
    belongs under a category, only to decide whether a group already has a
    guide."""

    def test_unknown_category_404s_without_touching_inventory(
        self, mock_list, mock_list_ungrouped
    ):
        response = self.client.get("/categories/nachos/")
        self.assertEqual(response.status_code, 404)
        mock_list.assert_not_called()
        mock_list_ungrouped.assert_not_called()

    def test_guided_group_links_to_its_guide(self, mock_list, _):
        mock_list.return_value = [
            {"slug": "resistor", "name": "Resistors", "part_count": 33}
        ]
        response = self.client.get("/categories/power/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "/parts/resistor/")

    def test_unguided_group_links_to_inventory(self, mock_list, _):
        mock_list.return_value = [
            {"slug": "fasteners", "name": "Fasteners", "part_count": 190}
        ]
        response = self.client.get("/categories/power/")
        self.assertContains(response, "https://inventory.ioref.org/?group=fasteners")
        self.assertContains(response, "no guide yet")

    def test_empty_category_is_not_an_outage(self, mock_list, _):
        mock_list.return_value = []
        response = self.client.get("/categories/power/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "No parts are filed")

    def test_outage_is_503_not_an_empty_category(self, mock_list, _):
        """An unreachable inventory must not read the same as a category with
        nothing in it."""
        mock_list.side_effect = InventoryUnavailable("refused")
        response = self.client.get("/categories/power/")
        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "unreachable", status_code=503)

    def test_ungrouped_part_links_to_its_guide(self, mock_list, mock_list_ungrouped):
        mock_list.return_value = []
        mock_list_ungrouped.return_value = [
            {"part_number": "0286", "short_name": "Soil moisture sensor"}
        ]
        response = self.client.get("/categories/input/")
        self.assertContains(response, "/parts/soil-moisture-sensor/")


class CategoryAliasTests(SimpleTestCase):
    """/c/<slug>/ redirects rather than rendering -- category_page.html must
    have exactly one caller, or the two would drift."""

    def test_redirects_to_the_canonical_path(self):
        response = self.client.get("/c/power/")
        self.assertRedirects(
            response,
            "/categories/power/",
            status_code=301,
            fetch_redirect_response=False,
        )

    def test_does_not_validate_the_slug_itself(self):
        """Validation is category()'s job; the alias only rewrites the path,
        so an unknown slug still redirects and 404s one hop later, not here."""
        response = self.client.get("/c/nachos/")
        self.assertEqual(response.status_code, 301)


class ResolveLegacyTests(SimpleTestCase):
    """The printed cards: ioref.org/<group-slug> and ioref.org/<part-number>,
    both bare, both predating this site, neither reprintable."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.catalogue = content.reload()

    def test_a_guide_slug_resolves_without_touching_inventory(self):
        with patch("catalog.views.get_part") as mock_get:
            response = self.client.get("/resistor/")
            mock_get.assert_not_called()
        self.assertRedirects(
            response, "/parts/resistor/", fetch_redirect_response=False
        )

    @patch("catalog.views.get_part")
    def test_a_part_number_in_a_guided_group_redirects_to_that_guide(self, mock_get):
        mock_get.return_value = {"part_number": "0001", "group": {"slug": "resistor"}}
        response = self.client.get("/0001/")
        self.assertRedirects(response, "/parts/0001/", fetch_redirect_response=False)

    @patch("catalog.views.get_part")
    def test_a_part_number_with_no_guide_uses_the_canonical_parts_url(self, mock_get):
        mock_get.return_value = {"part_number": "0046", "group": {"slug": "fasteners"}}
        response = self.client.get("/0046/")
        self.assertRedirects(response, "/parts/0046/", fetch_redirect_response=False)

    @patch("catalog.views.get_part")
    def test_an_ungrouped_part_with_an_explicit_guide_uses_it(self, mock_get):
        mock_get.return_value = {"part_number": "0574", "group": None}
        response = self.client.get("/0574/")
        self.assertRedirects(response, "/parts/0574/", fetch_redirect_response=False)

    @patch("catalog.views.get_part")
    def test_unknown_token_404s(self, mock_get):
        mock_get.return_value = None
        self.assertEqual(self.client.get("/totallybogus/").status_code, 404)

    @patch("catalog.views.get_part")
    def test_outage_on_an_unrecognised_token_is_404_not_503(self, mock_get):
        """A token that is not a local guide slug was never going to resolve
        without inventory, so there is nothing an outage-specific page would
        add -- unlike /categories/<slug>/, which has real content to withhold."""
        mock_get.side_effect = InventoryUnavailable("refused")
        self.assertEqual(self.client.get("/totallybogus/").status_code, 404)


class CheckGroupsTests(SimpleTestCase):
    """The guard against a slug that inventory has renamed under us.

    Inventory answers an unknown group with HTTP 200 and an empty result set,
    which reads exactly like a group whose parts were all retired. The
    resistor page rendered an empty table for two days on the back of that.
    """

    def run_command(self, groups, results_by_group=None, **kwargs):
        """Runs against a small, fixed catalogue naming `groups`, rather than
        the real content/ -- the command iterates every group get_catalogue()
        returns, and pinning that set is what makes "all resolve" a
        meaningful assertion regardless of how many guides content/ holds.

        `results_by_group` is what list_by_group() returns per slug; a slug
        with nothing there simulates the group inventory has never heard of.
        """
        results_by_group = results_by_group or {}
        out = io.StringIO()
        fake_catalogue = SimpleNamespace(
            parts=[SimpleNamespace(slug=g, group=g) for g in groups]
        )
        with (
            patch(
                "catalog.management.commands.check_groups.get_catalogue",
                return_value=fake_catalogue,
            ),
            patch(
                "stock.client.list_by_group",
                side_effect=lambda g: results_by_group.get(g, []),
            ),
            patch("stock.client.list_parts", return_value={"results": [], "count": 0}),
        ):
            call_command("check_groups", stdout=out, stderr=out, **kwargs)
        return out.getvalue()

    def test_reports_each_group_that_resolves(self):
        groups = ["resistor", "potentiometers"]
        output = self.run_command(
            groups, {"resistor": [{}] * 33, "potentiometers": [{}] * 26}
        )
        self.assertIn("all resolve", output)
        self.assertNotIn("EMPTY", output)

    def test_flags_a_group_that_matches_nothing(self):
        output = self.run_command(
            ["resistor", "potentiometers"], {"potentiometers": [{}] * 26}
        )
        self.assertIn("EMPTY", output)
        self.assertIn("resistor", output)

    def test_strict_exits_non_zero_so_a_deploy_can_gate_on_it(self):
        with self.assertRaises(CommandError):
            self.run_command(["resistor"], strict=True)

    def test_an_outage_is_an_error_not_a_report_of_every_group_broken(self):
        """Reporting a down service as 'all your slugs are stale' would send
        someone editing front matter that is perfectly correct."""
        with patch(
            "stock.client.list_parts", side_effect=InventoryUnavailable("refused")
        ):
            with self.assertRaises(CommandError) as caught:
                call_command("check_groups", stdout=io.StringIO())
        self.assertIn("unreachable", str(caught.exception))


class CheckCategoriesTests(SimpleTestCase):
    """The mirror check: do the five hardcoded categories still exist on the
    inventory side, or has categories.yml drifted from what it names?"""

    def run_command(self, live_slugs, **kwargs):
        out = io.StringIO()
        live = [{"slug": s, "name": s.title()} for s in live_slugs]
        with patch("stock.client.list_categories", return_value=live):
            call_command("check_categories", stdout=out, stderr=out, **kwargs)
        return out.getvalue()

    def test_all_five_matching_is_a_clean_pass(self):
        output = self.run_command(
            {"input", "output", "power", "connector", "controller"}
        )
        self.assertIn("All hardcoded categories exist", output)
        self.assertNotIn("MISSING", output)

    def test_a_category_inventory_does_not_have_is_flagged(self):
        output = self.run_command(set())
        self.assertIn("MISSING", output)
        self.assertIn("power", output)

    def test_strict_exits_non_zero_on_drift(self):
        with self.assertRaises(CommandError):
            self.run_command(set(), strict=True)

    def test_an_outage_is_an_error_not_five_missing_categories(self):
        with patch(
            "stock.client.list_categories", side_effect=InventoryUnavailable("refused")
        ):
            with self.assertRaises(CommandError) as caught:
                call_command("check_categories", stdout=io.StringIO())
        self.assertIn("unreachable", str(caught.exception))


class SearchColumnsTests(SimpleTestCase):
    PART = {
        "part_number": "0347",
        "short_name": "keyboard switch",
        "description": "switch, keyboard, momentary",
        "status": "active",
    }
    RETIRED = {
        "part_number": "0348",
        "short_name": "old keyboard",
        "description": "",
        "status": "discontinued",
    }

    @patch("catalog.views.list_parts", return_value={"results": [PART], "count": 1})
    def test_parts_column_lists_matches_and_links_to_part_pages(self, _):
        response = self.client.get("/search/", {"query": "keyboard"})
        self.assertContains(response, "<h2>ioRef Guides</h2>")
        self.assertContains(response, "<h2>Phys Comp Lab Parts</h2>")
        self.assertContains(response, 'href="/parts/0347/"')
        self.assertContains(response, '<span class="part-number">#0347</span>')
        self.assertNotContains(response, "No results for Phys Comp Lab Parts")

    @patch("catalog.views.list_parts", return_value={"results": [], "count": 0})
    def test_empty_columns_say_so_and_both_still_print(self, _):
        response = self.client.get("/search/", {"query": "zzqqxx"})
        self.assertContains(response, "No results for ioRef Guides")
        self.assertContains(response, "No results for Phys Comp Lab Parts")

    @patch("catalog.views.list_parts", return_value={"results": [RETIRED], "count": 1})
    def test_discontinued_parts_are_struck_through(self, _):
        response = self.client.get("/search/", {"query": "keyboard"})
        self.assertContains(response, 'class="discontinued"')

    @patch("catalog.views.list_parts", side_effect=InventoryUnavailable())
    def test_inventory_outage_is_not_shown_as_no_results(self, _):
        response = self.client.get("/search/", {"query": "keyboard"})
        self.assertContains(response, "Parts search is unavailable right now.")
        self.assertNotContains(response, "No results for Phys Comp Lab Parts")

    @patch("catalog.views.list_parts", return_value={"results": [PART] * 50, "count": 120})
    def test_a_long_part_list_says_how_many_more_there_are(self, _):
        response = self.client.get("/search/", {"query": "keyboard"})
        self.assertContains(response, "Showing the first 50 of 120 parts.")

    @patch("catalog.views.list_parts")
    def test_blank_query_asks_inventory_nothing(self, mock_list):
        self.client.get("/search/", {"query": "  "})
        mock_list.assert_not_called()


class DiscontinuedPartTests(SimpleTestCase):
    """Status on the part page, and the discontinued toggle in search."""

    def part(self, status, **extra):
        return {
            "part_number": "0999",
            "short_name": "LED strip",
            "description": "RGB LED strip, 1 m",
            "status": status,
            "unit": "each",
            **extra,
        }

    @patch("catalog.views.get_part")
    def test_active_part_shows_status_in_plain_text(self, mock_get):
        mock_get.return_value = self.part("active")
        response = self.client.get("/parts/0999/")
        self.assertContains(response, "Status:")
        self.assertContains(response, "<div class=\"value\">Active</div>")
        self.assertContains(response, '<span class="part-number">#0999</span>')
        self.assertNotContains(response, "status_discontinued")
        self.assertNotContains(response, "(Discontinued part)")

    @patch("catalog.views.get_part")
    def test_status_sits_between_description_and_availability(self, mock_get):
        mock_get.return_value = self.part("active")
        content = self.client.get("/parts/0999/").content.decode()
        self.assertLess(content.index("Description:"), content.index("Status:"))
        self.assertLess(content.index("Status:"), content.index("Available in the lab:"))

    @patch("catalog.views.get_part")
    def test_to_discontinue_is_red_with_help_text_after(self, mock_get):
        mock_get.return_value = self.part("to_discontinue")
        response = self.client.get("/parts/0999/")
        self.assertContains(
            response,
            '<span class="status_discontinued">To Discontinue</span>: will not be restocked once inventory is empty',
        )
        self.assertNotContains(response, "(Discontinued part)")

    @patch("catalog.views.get_part")
    def test_discontinued_part_is_flagged_in_title_and_status(self, mock_get):
        mock_get.return_value = self.part("discontinued")
        response = self.client.get("/parts/0999/")
        self.assertContains(
            response, '<span class="status_discontinued">(Discontinued part)</span>'
        )
        self.assertContains(
            response, '<span class="status_discontinued">Discontinued</span>'
        )

    @patch("catalog.views.get_part")
    def test_other_status_text_is_printed_as_is(self, mock_get):
        mock_get.return_value = self.part("on backorder")
        response = self.client.get("/parts/0999/")
        self.assertContains(response, "<div class=\"value\">on backorder</div>")
        self.assertNotContains(response, "status_discontinued")

    @patch("catalog.views.list_parts", return_value={"results": [], "count": 0})
    def test_search_hides_discontinued_parts_by_default(self, mock_list):
        response = self.client.get("/search/", {"query": "led"})
        mock_list.assert_called_once()
        self.assertFalse(mock_list.call_args.kwargs["include_discontinued"])
        self.assertContains(response, 'name="include_discontinued"')
        self.assertNotContains(response, "checked")

    @patch("catalog.views.list_parts", return_value={"results": [], "count": 0})
    def test_search_toggle_includes_discontinued_parts_when_checked(self, mock_list):
        response = self.client.get(
            "/search/", {"query": "led", "include_discontinued": "1"}
        )
        self.assertTrue(mock_list.call_args.kwargs["include_discontinued"])
        self.assertContains(response, "checked")

    @patch("stock.client._get", return_value={"results": [], "count": 0})
    def test_client_asks_inventory_to_exclude_discontinued(self, mock_get):
        from stock.client import list_parts

        list_parts(search="led", include_discontinued=False)
        self.assertEqual(mock_get.call_args.args[1]["include_discontinued"], "0")


class StockReportTests(SimpleTestCase):
    STOCK = {
        "part_number": "0006",
        "short_name": "soldering sponge",
        "group": {"slug": "fasteners", "name": "Fasteners"},
        "location": "Soldering bench",
    }

    @patch("catalog.views.get_part")
    def test_buttons_show_only_to_a_signed_in_person(self, mock_get):
        mock_get.return_value = dict(self.STOCK)
        self.assertNotContains(self.client.get("/parts/0006/"), "Report out of stock")
        response = self.client.get("/parts/0006/", HTTP_EPPN="rzachari@andrew.cmu.edu")
        self.assertContains(response, "Report out of stock")
        self.assertContains(response, 'action="/parts/0006/report/"')

    @patch("catalog.views.get_part")
    def test_a_signed_out_person_is_offered_sign_in_back_to_this_page(self, mock_get):
        mock_get.return_value = dict(self.STOCK)
        response = self.client.get("/parts/0006/")
        self.assertContains(
            response, 'href="/Shibboleth.sso/Login?target=%2Fparts%2F0006%2F"'
        )
        signed_in = self.client.get("/parts/0006/", HTTP_EPPN="rzachari@andrew.cmu.edu")
        self.assertNotContains(signed_in, "Shibboleth.sso")

    def test_anonymous_report_is_refused(self):
        response = self.client.post("/parts/0006/report/", {"kind": "out"})
        self.assertEqual(response.status_code, 403)

    @patch("catalog.views.file_stock_report", return_value=True)
    def test_signed_in_report_is_filed_and_the_person_is_thanked(self, mock_file):
        response = self.client.post(
            "/parts/0006/report/", {"kind": "out"}, HTTP_EPPN="rzachari@andrew.cmu.edu"
        )
        mock_file.assert_called_once_with("0006", "out", "rzachari@andrew.cmu.edu")
        self.assertRedirects(
            response, "/parts/0006/?reported=out", fetch_redirect_response=False
        )

    @patch("catalog.views.file_stock_report", return_value=False)
    def test_a_failed_report_says_so(self, mock_file):
        response = self.client.post(
            "/parts/0006/report/", {"kind": "low"}, HTTP_EPPN="x@andrew.cmu.edu"
        )
        self.assertRedirects(
            response, "/parts/0006/?reported=error", fetch_redirect_response=False
        )

    @patch("catalog.views.file_stock_report")
    def test_unknown_kind_and_cross_site_posts_are_refused(self, mock_file):
        self.assertEqual(
            self.client.post(
                "/parts/0006/report/", {"kind": "maybe"}, HTTP_EPPN="x@andrew.cmu.edu"
            ).status_code,
            400,
        )
        self.assertEqual(
            self.client.post(
                "/parts/0006/report/",
                {"kind": "out"},
                HTTP_EPPN="x@andrew.cmu.edu",
                HTTP_ORIGIN="https://evil.example",
            ).status_code,
            403,
        )
        mock_file.assert_not_called()

    def test_get_is_not_allowed(self):
        self.assertEqual(
            self.client.get(
                "/parts/0006/report/", HTTP_EPPN="x@andrew.cmu.edu"
            ).status_code,
            405,
        )

    @patch("stock.client._client", side_effect=httpx.ConnectError("down"))
    def test_inventory_outage_means_not_filed(self, _):
        from stock.client import report_stock

        self.assertFalse(report_stock("0006", "out", "x@andrew.cmu.edu"))
