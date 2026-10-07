"""The guide content, read from markdown files rather than a database.

The whole catalogue is 40 files and well under 100 KB of prose, so it is
parsed once and held in memory. There is no database behind any of this:
editing the site means editing a file and committing it.

A guide is keyed by inventory group, not by part number. "What is a
resistor" is one page that draws its stock table live from inventory's
`resistor` group, whatever values happen to be stocked -- not one page per
value. See CLAUDE.md for how that came about.

Category is deliberately absent from this module. It used to be local
front matter; it is now a fact inventory holds about the group
(`Group.category`), fetched live by catalog/views.py only where it is
needed -- the /categories/<slug>/ browse page. Parsing a guide file here never talks
to inventory, so the guides render even when it is down.
"""

import threading
from dataclasses import dataclass, field
from pathlib import Path

import markdown as markdown_lib
import nh3
import yaml
from django.conf import settings
from django.utils.safestring import mark_safe

# Matches the old WAGTAILMARKDOWN configuration this replaced.
ALLOWED_TAGS = set(nh3.ALLOWED_TAGS) | {
    "figure",
    "figcaption",
    "img",
    "video",
    "source",
    "pre",
    "code",
}
ALLOWED_ATTRIBUTES = {
    **nh3.ALLOWED_ATTRIBUTES,
    "figure": {"class", "style"},
    "figcaption": {"class", "style"},
    "img": {"src", "alt", "title", "width", "height", "class", "style"},
    "video": {"src", "controls", "width", "height", "class"},
    "source": {"src", "type"},
    "code": {"class"},
    "pre": {"class"},
    "a": {"href", "title", "rel", "target"},
}

MARKDOWN_EXTENSIONS = ["fenced_code", "codehilite", "tables", "nl2br"]
MARKDOWN_CONFIGS = {"codehilite": {"use_pygments": False}}

# The seven documentation sections, in the order they render. The labels are
# also the headings in the markdown files, so this is what maps a file back
# onto a section, and what the side-menu jump links are built from.
DOC_SECTIONS = (
    ("docs_about", "About"),
    ("docs_what_it_is", "What it is"),
    ("docs_when_to_use_it", "When to use it"),
    ("docs_how_it_works", "How it works"),
    ("docs_how_to_use_it", "How to use it"),
    ("docs_getting_started", "Getting started"),
    ("docs_resources", "Resources"),
)
LABEL_TO_ANCHOR = {label: anchor for anchor, label in DOC_SECTIONS}
SECTION_ORDER = {label: i for i, (_, label) in enumerate(DOC_SECTIONS)}


def render_markdown(text):
    html = markdown_lib.markdown(
        text,
        extensions=MARKDOWN_EXTENSIONS,
        extension_configs=MARKDOWN_CONFIGS,
    )
    return mark_safe(
        nh3.clean(
            html,
            tags=ALLOWED_TAGS,
            attributes=ALLOWED_ATTRIBUTES,
            # nh3 rewrites rel on every link by default and refuses to also
            # take rel as an allowed attribute. The prose sets its own on
            # outbound links, so hand it over rather than have nh3 impose one.
            link_rel=None,
        )
    )


@dataclass
class Section:
    anchor: str
    label: str
    body: str

    @property
    def body_html(self):
        return self._html

    def render(self):
        self._html = render_markdown(self.body)


# eq=False, and repr=False on the back-reference.
#
# Part.related_parts can point back at pages that point back at it, forming a
# cycle. The generated __repr__ and __eq__ both walk fields recursively, so
# either one follows the cycle until the process dies -- not theoretical, an
# unrelated exception under DEBUG=True once made Django repr a view's local
# variables while rendering a traceback page, and the process was OOM-killed
# with no traceback to explain it. eq=False also restores hashability: there
# is exactly one object per slug in a loaded catalogue, so identity is what
# equality should mean anyway.
@dataclass(eq=False)
class PartSet:
    slug: str
    title: str
    description: str = ""
    image: str = ""
    parts: list = field(default_factory=list, repr=False)

    @property
    def url(self):
        return f"/part-sets/{self.slug}/"

    @property
    def image_url(self):
        return f"/images/parts/{self.image}" if self.image else ""

    @property
    def id(self):
        return self.slug


@dataclass(eq=False)
class Part:
    """A guide, keyed by the inventory group it documents.

    `group` names that group and drives the page's stock table (see
    catalog/views.py:_variants and stock/client.py:list_by_group). It is
    optional: `soil-moisture-sensor` and `passive-infrared-sensor` document
    parts inventory has not yet put in a group, and fall back to `stocked`,
    an explicit list of part numbers in front matter -- the mechanism every
    guide used before groups existed. A page needs one or the other, or it
    has nothing to show a stock table for.
    """

    slug: str
    title: str
    description: str = ""
    image: str = ""
    group: str = ""
    hidden: bool = False
    part_sets: list = field(default_factory=list, repr=False)
    related_slugs: list = field(default_factory=list, repr=False)
    stocked: list = field(default_factory=list, repr=False)
    sections: list = field(default_factory=list, repr=False)

    @property
    def url(self):
        return f"/parts/{self.slug}/"

    @property
    def id(self):
        return self.slug

    @property
    def part_numbers(self):
        return [p["number"] for p in self.stocked]

    @property
    def image_url(self):
        return f"/images/parts/{self.image}" if self.image else ""

    @property
    def related_parts(self):
        return self._related


class Catalogue:
    """Every guide, indexed the handful of ways the views ask for it."""

    def __init__(self, parts, part_sets):
        self.parts = parts
        self.part_sets = part_sets
        self.by_slug = {p.slug: p for p in parts}
        self.by_group = {p.group: p for p in parts if p.group}
        self.by_part_number = {
            number: page for page in parts for number in page.part_numbers
        }
        self.part_sets_by_slug = {s.slug: s for s in part_sets}

    def search(self, query):
        """Substring match over title and prose.

        The whole catalogue fits in memory many times over, so this is a loop
        rather than an index. Wagtail's database backend was not doing
        anything cleverer.
        """
        needle = (query or "").strip().lower()
        if not needle:
            return []
        hits = []
        for part in self.parts:
            if part.hidden:
                continue
            haystack = " ".join(
                [part.title, part.description] + [s.body for s in part.sections]
            ).lower()
            if needle in haystack:
                # Title matches first: searching "potentiometer" should not bury
                # the potentiometer under every page that mentions one.
                hits.append(
                    (0 if needle in part.title.lower() else 1, part.title, part)
                )
        return [p for _, _, p in sorted(hits, key=lambda h: (h[0], h[1]))]


@dataclass(eq=False)
class Category:
    """One of the five fixed macro categories shown on the home page.

    Unlike everything else in this module, these are not read from inventory.
    They are the five slugs `main.css` colours by, hardcoded here on purpose:
    the home page must render even when inventory is unreachable, and the set
    of five essentially never changes. What varies -- which groups sit under
    Power today -- is inventory's live data, fetched only when a
    /categories/<slug>/ page is actually visited. See catalog/views.py:category.
    """

    slug: str
    title: str

    @property
    def url(self):
        return f"/categories/{self.slug}/"


_lock = threading.Lock()
_catalogue = None


def content_dir():
    return Path(getattr(settings, "CONTENT_DIR", Path(settings.BASE_DIR) / "content"))


def get_catalogue():
    global _catalogue
    if _catalogue is None:
        with _lock:
            if _catalogue is None:
                _catalogue = load()
    return _catalogue


def reload():
    """Drop the cache. For tests, and for a development autoreloader."""
    global _catalogue
    with _lock:
        _catalogue = None
    return get_catalogue()


def load_categories():
    """The five home-page categories, in file order."""
    root = content_dir()
    data = yaml.safe_load((root / "categories.yml").read_text(encoding="utf-8"))
    return [Category(slug=c["slug"], title=c["title"]) for c in data["categories"]]


def _split_frontmatter(text, path):
    if not text.startswith("---"):
        raise ValueError(f"{path}: no frontmatter")
    _, _, rest = text.partition("---\n")
    front, sep, body = rest.partition("\n---")
    if not sep:
        raise ValueError(f"{path}: frontmatter is not closed")
    return yaml.safe_load(front) or {}, body.lstrip("\n")


def _parse_sections(body, path):
    sections = []
    current_label = None
    buffer = []

    def flush():
        if current_label is None:
            return
        text = "\n".join(buffer).strip()
        if not text:
            return
        if current_label not in LABEL_TO_ANCHOR:
            raise ValueError(
                f"{path}: unknown section heading '{current_label}'. "
                f"Expected one of: {', '.join(LABEL_TO_ANCHOR)}"
            )
        sections.append(Section(LABEL_TO_ANCHOR[current_label], current_label, text))

    for line in body.splitlines():
        # Only level two, and only at the start of a line. Deeper headings
        # inside a section are the author's own and must not split the file.
        if line.startswith("## "):
            flush()
            current_label = line[3:].strip()
            buffer = []
        else:
            buffer.append(line)
    flush()

    sections.sort(key=lambda s: SECTION_ORDER[s.label])
    return sections


def load():
    root = content_dir()

    set_data = yaml.safe_load((root / "part-sets.yml").read_text(encoding="utf-8"))
    part_sets = [
        PartSet(
            slug=s["slug"],
            title=s["title"],
            description=s.get("description", ""),
            image=s.get("image", ""),
        )
        for s in set_data.get("part_sets", [])
    ]
    by_set = {s.slug: s for s in part_sets}

    parts = []
    seen_groups = {}
    for path in sorted((root / "parts").glob("*.md")):
        front, body = _split_frontmatter(path.read_text(encoding="utf-8"), path)

        group = front.get("group", "")
        stocked = [dict(p) for p in front.get("parts", [])]
        if not group and not stocked:
            raise ValueError(
                f"{path}: no group and no parts: -- nothing to show a stock "
                "table for. Give it a group, or list its part numbers by hand."
            )
        if group in seen_groups:
            raise ValueError(
                f"{path}: group '{group}' is already documented by "
                f"{seen_groups[group]}.md. A guide covers a whole group; two "
                "files for one group is the bug check_groups exists to avoid "
                "at the inventory end, not something a second file should hide."
            )
        if group:
            seen_groups[group] = path.stem

        part = Part(
            slug=path.stem,
            title=front["title"],
            description=front.get("description", ""),
            image=front.get("image", ""),
            group=group,
            hidden=bool(front.get("hidden", False)),
            related_slugs=list(front.get("related", [])),
            stocked=stocked,
            sections=_parse_sections(body, path),
        )

        for slug in front.get("part_sets", []):
            part_set = by_set.get(slug)
            if part_set is None:
                raise ValueError(f"{path}: part set '{slug}' is not in part-sets.yml")
            part.part_sets.append(part_set)
            part_set.parts.append(part)

        parts.append(part)

    by_slug = {p.slug: p for p in parts}

    # Second pass: relations point at other parts, so they need every part
    # read before they can be resolved. A dangling slug is an editing mistake
    # worth dropping quietly rather than 500ing the page it appears on.
    for part in parts:
        part._related = [
            by_slug[s]
            for s in part.related_slugs
            if s in by_slug and not by_slug[s].hidden
        ]
        for section in part.sections:
            section.render()

    for part_set in part_sets:
        part_set.parts.sort(key=lambda p: p.title)
    parts.sort(key=lambda p: p.title)

    return Catalogue(parts, part_sets)
