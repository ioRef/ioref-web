"""Guide pages, rendered from the markdown files in content/.

Guides live at /parts/<group-slug>/ regardless of category, because parsing
one never talks to inventory (see content.py). Category only exists as a
live, request-time concept, fetched by the /categories/<slug>/ browse view
below -- the site's shape does not depend on inventory being reachable at
startup, only that one page does.
"""

from types import SimpleNamespace
from urllib.parse import urlencode, urlparse

from django.conf import settings
from django.http import (
    Http404,
    HttpResponseBadRequest,
    HttpResponseForbidden,
    HttpResponseNotAllowed,
)
from django.shortcuts import redirect, render
from django.urls import reverse

from stock.client import (
    InventoryUnavailable,
    get_part,
    list_parts,
    list_groups_by_category,
    list_ungrouped_parts_by_category,
)
from stock.client import (
    report_stock as file_stock_report,
)

from .content import get_catalogue, load_categories


def home(request):
    categories = load_categories()
    by_slug = {c.slug: c for c in categories}

    # maker-cards laid these out two-then-three, and the widths in main.css
    # assume that split. Driven from slugs rather than file order so that
    # reordering categories.yml cannot break the layout.
    def row(*slugs):
        return [by_slug[slug] for slug in slugs if slug in by_slug]

    return render(
        request,
        "catalog/home_page.html",
        {
            "categories_top": row("input", "output"),
            "categories_bottom": row("power", "connector", "controller"),
        },
    )


def category(request, category_slug):
    """Every group inventory files under this category, live.

    Not built from content/: a group with no guide still belongs here, so a
    purchasing question ("what's under Power") is answerable without anyone
    having written a word about half of it. Guided groups link to their guide;
    the rest link to the corresponding filtered view in ioref-inventory.
    """
    categories = load_categories()
    page = next((c for c in categories if c.slug == category_slug), None)
    if page is None:
        raise Http404(f"No category {category_slug}")

    context = {
        "page": page,
        "all_categories": categories,
        "active_category_slug": category_slug,
    }

    catalogue = get_catalogue()
    try:
        groups = list_groups_by_category(category_slug)
        ungrouped_parts = list_ungrouped_parts_by_category(category_slug)
    except InventoryUnavailable:
        context["unavailable"] = True
        return render(request, "catalog/category_page.html", context, status=503)

    context["unavailable"] = False
    context["groups"] = [
        {
            "slug": g["slug"],
            "name": g["name"],
            "part_count": g.get("part_count", 0),
            "guide": catalogue.by_group.get(g["slug"]),
            "url": (
                catalogue.by_group[g["slug"]].url
                if g["slug"] in catalogue.by_group
                else f"https://inventory.ioref.org/?group={g['slug']}"
            ),
        }
        for g in sorted(groups, key=lambda g: g["name"])
    ]
    context["ungrouped_parts"] = [
        {
            **part,
            "guide": catalogue.by_part_number.get(part["part_number"]),
            "url": (
                catalogue.by_part_number[part["part_number"]].url
                if part["part_number"] in catalogue.by_part_number
                else f"/parts/{part['part_number']}/"
            ),
        }
        for part in sorted(ungrouped_parts, key=lambda part: part["short_name"])
    ]
    return render(request, "catalog/category_page.html", context)


def category_alias(request, category_slug):
    """/c/<slug>/ -> /categories/<slug>/. A short alias, not a second page --
    it never renders anything itself, so category_page.html has exactly one
    caller. Permanent redirect: the alias is a deliberate, stable shorthand,
    not a typo to leave open for search engines to index twice."""
    return redirect("category", category_slug=category_slug, permanent=True)


def resolve_legacy(request, token):
    """The printed cards' bare URLs: ioref.org/resistor, ioref.org/0496.

    They predate this site and cannot be reprinted, so whatever they name has
    to resolve from here, at the root, with no prefix to hint which case it
    is. Tried in order:

    1. `token` is a guide's own slug (a group name, "resistor") -- resolved
       locally, no inventory involved.
    2. `token` is a part number inventory knows about -- redirect to its
       canonical /parts/ URL. That page includes its guide when one exists
       and otherwise renders the stock information on its own.
    3. None of the above: 404.
    """
    catalogue = get_catalogue()

    guide = catalogue.by_slug.get(token)
    if guide is not None:
        return redirect(guide.url)

    return _redirect_part_number(token)


def _redirect_part_number(part_number):
    """Redirect a bare part number to its canonical parts URL."""
    try:
        part = get_part(part_number)
    except InventoryUnavailable:
        part = None

    if part is None:
        raise Http404(f"No part numbered {part_number}")

    return redirect("part", slug=part_number)


def part(request, slug):
    catalogue = get_catalogue()
    page = catalogue.by_slug.get(slug)
    if page is not None:
        return _render_part(request, page, _variants(page))

    try:
        stock = get_part(slug)
    except InventoryUnavailable:
        stock = None

    if stock is None:
        raise Http404(f"No guide or part named {slug}")

    page = _guide_for_inventory_part(stock, catalogue)
    if page is None:
        page = _stock_only_page(stock)

    # A group guide documents every part in its group, so it cannot name one.
    part_title = None
    if page.group and page.group == (stock.get("group") or {}).get("slug"):
        part_title = stock.get("short_name") or stock["part_number"]

    return _render_part(request, page, [_variant(stock)], part_title=part_title)


def _stock_only_page(stock):
    """Supply the page shape used by the guide template without guide copy."""
    return SimpleNamespace(
        title=stock.get("short_name") or stock["part_number"],
        description="",
        group="",
        part_sets=[],
        sections=[],
        image=None,
        related_parts=[],
    )


def _guide_for_inventory_part(stock, catalogue):
    group = stock.get("group")
    if group:
        guide = catalogue.by_group.get(group["slug"])
        if guide is not None:
            return guide

    number = stock["part_number"]
    return next((page for page in catalogue.parts if number in page.part_numbers), None)


def _variant(stock):
    description = (stock.get("description") or "").strip()
    return {
        "number": stock["part_number"],
        "label": description or stock.get("short_name", ""),
        "note": "",
        "stock": stock,
    }


def _reporter(request):
    return (request.META.get(settings.REPORT_IDENTITY_HEADER) or "").strip()


def _sign_in_url(request):
    if _reporter(request) or not settings.REPORT_SIGN_IN_URL:
        return ""
    return f"{settings.REPORT_SIGN_IN_URL}?{urlencode({'target': request.get_full_path()})}"


def _render_part(request, page, variants, part_title=None):
    # Only a page for one part can be discontinued as a whole. A multi-part
    # page names the discontinued parts in its stocked table instead.
    only = variants[0]["stock"] if len(variants) == 1 else None
    discontinued = bool(only) and only.get("status") == "discontinued"
    return render(
        request,
        "catalog/component_page.html",
        {
            "page": page,
            "part_title": part_title,
            "variants": variants,
            "related_parts": page.related_parts,
            "discontinued": discontinued,
            "can_report": bool(_reporter(request)),
            "sign_in_url": _sign_in_url(request),
            "reported": request.GET.get("reported", ""),
        },
    )


REPORT_KINDS = ("low", "out")


def report_stock(request, slug):
    """A signed-in person says a part is running low or out. Recorded in inventory."""
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    reporter = _reporter(request)
    if not reporter:
        return HttpResponseForbidden(
            "Sign in with your university account to report stock."
        )
    origin = request.headers.get("Origin")
    if origin and urlparse(origin).netloc != request.get_host():
        return HttpResponseForbidden("Cross-site report refused.")
    kind = request.POST.get("kind", "")
    if kind not in REPORT_KINDS:
        return HttpResponseBadRequest("Unknown report.")
    filed = file_stock_report(slug, kind, reporter)
    flag = kind if filed else "error"
    return redirect(f"{reverse('part', kwargs={'slug': slug})}?reported={flag}")


def _variants(page):
    """What the lab stocks under this component heading.

    Two sources, and they are additive: a page can read a whole inventory group
    and still name a stray part that inventory files elsewhere.
    """
    from stock.client import get_stock_many, list_by_group

    variants = []
    if page.group:
        for entry in list_by_group(page.group):
            variants.append(_variant(entry))

    seen = {v["number"] for v in variants}
    inline = [p for p in page.stocked if p["number"] not in seen]
    if inline:
        # One request for the lot, not one each: a group can run to 30+ parts.
        stock = get_stock_many([p["number"] for p in inline])
        variants += [
            {
                "number": p["number"],
                "label": p.get("label", ""),
                "note": p.get("note", ""),
                "stock": stock.get(p["number"]),
            }
            for p in inline
        ]

    return variants


def part_set_index(request):
    catalogue = get_catalogue()
    return render(
        request,
        "catalog/part_set_index_page.html",
        {"page": {"title": "Part Sets"}, "part_sets": catalogue.part_sets},
    )


def part_set(request, slug):
    catalogue = get_catalogue()
    page = catalogue.part_sets_by_slug.get(slug)
    if page is None:
        raise Http404(f"No part set {slug}")

    return render(
        request,
        "catalog/part_set_page.html",
        {
            "page": page,
            "set_parts": [p for p in page.parts if not p.hidden],
        },
    )


PART_RESULT_LIMIT = 50


def search(request):
    catalogue = get_catalogue()
    query = request.GET.get("query", "")
    include_discontinued = request.GET.get("include_discontinued") == "1"
    parts, part_total, parts_unavailable = [], 0, False
    if query.strip():
        try:
            data = list_parts(
                search=query.strip(),
                include_discontinued=include_discontinued,
                limit=PART_RESULT_LIMIT,
            )
        except InventoryUnavailable:
            parts_unavailable = True
        else:
            parts = data.get("results", [])
            part_total = data.get("count", len(parts))
    return render(
        request,
        "catalog/search.html",
        {
            "search_query": query,
            "guide_results": catalogue.search(query),
            "part_results": parts,
            "part_total": part_total,
            "parts_unavailable": parts_unavailable,
            "include_discontinued": include_discontinued,
        },
    )
