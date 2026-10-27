<p align="center">
  <img src="https://raw.githubusercontent.com/ioref/.github/main/branding/wordmark.svg" alt="ioRef" width="360">
</p>

<p align="center">
  ioRef is a set of tools for exploring, connecting, and coding<br>
  with components used in creative physical computing.
</p>

---

# ioref-web

The public ioref.org site: maker cards, categories, and part sets. Django,
with no CMS and no database.

The guides are markdown files in `content/`. Editing the site means editing a
file and committing it.

Replaces **maker-cards** (`guides.ioref.org`, Express + Handlebars + Directus).
Visually it is the same site. `static/css/main.css` is carried over verbatim,
and the templates are ports of the original Handlebars ones.

Stock lives in **ioref-inventory**, a separate repository with its own database.
This site reads it over HTTP with a read-scoped API key and joins on
`part_number`.

## Quick start

```bash
uv sync
cp .env.example .env          # set INVENTORY_API_KEY
uv run python manage.py runserver
```

No migrate, no superuser, no seed step: there is no database and no login, and
the content is checked in, so a fresh clone runs.

For the inventory integration to do anything, ioref-inventory must be running
and `INVENTORY_API_KEY` must hold a **read**-scoped key generated in its admin.
Without one the site still works — stock blocks are simply omitted.

## Editing a card

Every card is one file, `content/parts/<group-slug>.md`, keyed by the
inventory group it documents rather than by one part number:

```markdown
---
title: Resistor
description: A component that limits or regulates the flow of current in a circuit.
group: resistor
---

## What it is

A resistor limits or regulates the flow of electrical current in a circuit.
```

`group:` names an inventory group (`GET /api/v1/groups/`) and drives the
stock table: every part inventory currently files under `resistor` shows up,
live, whatever values happen to be stocked. It is a mistake for two files to
name the same group — `catalog/content.py` refuses to load a second one.

A part inventory has not yet grouped can still get a page. Use `parts:`
instead of `group:` and list its numbers by hand:

```yaml
parts:
- number: '0286'
```

A page needs one or the other. Neither is an error at load time.

The `##` headings are a fixed set of seven, in a fixed display order: About,
What it is, When to use it, How it works, How to use it, Getting started,
Resources. A heading outside that set is an error at startup rather than a
section that silently disappears from the page. Omit any you do not need. A
group covering more than one distinct product (`breadboard`, `wire`) uses `###`
subsections within a heading rather than being split back into separate pages
— see those two files for the pattern.

Images referenced as `/images/parts/<file>` are files under
`public/images/parts/`, which is served at the root of the URL space rather
than under `/static/`. Restart the server after editing; content is read once
at startup.

`content/categories.yml` lists the five fixed category slugs shown on the
home page and nothing else — no groups, no subcategories. Which groups sit
under which category is inventory's live data, fetched by `/category/<slug>/`
on every request; `/c/<slug>/` is a short alias that redirects there. See
CLAUDE.md. `content/part-sets.yml` still lists the part sets, unaffected by
any of this.

## URLs

```
/parts/<group-slug>/      a guide
/category/<slug>/         live browse, /c/<slug>/ redirects here
/part-sets/, /search/
/inventory/                compatibility redirect to inventory.ioref.org
/<token>/                 printed cards: ioref.org/resistor, ioref.org/0496
```

The last one exists because a deck of physical reference cards already has
URLs printed on it with no prefix at all — a bare group slug or a bare part
number. They cannot be reprinted, so `catalog/views.resolve_legacy` resolves
whichever one it is and redirects to the real page: a local guide slug first
(no inventory involved), then a live part-number lookup that redirects to the
canonical `/parts/<part-number>/` page.

## Structure

```
content/parts/*.md          one file per group, or per ungrouped part
content/categories.yml      the five fixed category slugs, for the home page only
content/part-sets.yml
public/images/parts/        media the prose references by path
public/videos/parts/
```

**A guide documents a group, not a part number.** `resistor.md` answers "what
is a resistor" once, and its stock table is every value inventory currently
stocks under the `resistor` group — not a hand-typed list. This replaced an
earlier scheme with one file per part number, which produced 31 separate
"resistor" pages differing only by value.

Category slugs are load-bearing: `main.css` colours boxes by
`category-<slug>`, and the same five categories key the drawio shape library.

## The split with ioref-inventory

| Here | ioref-inventory |
|---|---|
| Guide prose, images, signal type | Counts, backstock, locations |
| Seven `docs_*` sections | Prices, suppliers, purchase links |
| Part sets | Groups, and which category each belongs to |
| Related guides | Stock and price history |
| Which group a guide documents | The parts themselves |

They join on group slug (or, for the rare ungrouped part, `part_number`), not
a foreign key — separate databases by design, so integrity is a convention
checked by `manage.py check_groups`, not enforced by a database constraint.

Two fixes over the Directus schema:

- **Part sets are many-to-many.** Directus had a single FK on `parts`, so a part
  could belong to one set only — wrong, since a resistor appears in many kits.
- **Guide content is separated from stock**, and both are separated from
  category, which now lives in inventory as `Group.category` rather than as
  front matter here — see CLAUDE.md for why.

## Inventory integration

`stock/client.py` is the only channel. Everything fails soft, but two different
ways, deliberately:

- `get_stock()` / `get_stock_many()` return `None` / `{}` when inventory is
  unreachable. A component page is mostly documentation and reads fine without
  stock counts.
- `list_parts()` raises `InventoryUnavailable`. The browse view must be able to
  distinguish "nothing matched your filter" from "inventory is down" — rendering
  an outage as an empty catalogue would be worse than an error.

`get_stock_many()` fetches every part under a component in a single request via
the API's `part_number__in` filter — the capacitor page covers 33 of them.

Results are cached for `INVENTORY_CACHE_SECONDS` (default 120). 404s are cached
too, so a part with a guide but no stock record is not re-requested on every
pageview; 500s are not, since they are transient.

## Styling

`static/css/main.css` is an unmodified copy from
`maker-cards/public/css/main.scss`. Do not edit it — put changes in
`static/css/site.css`. To resync:

```bash
npx sass static/css/main.scss static/css/main.css
```

## Favicon

`static/favicon.svg` is the only favicon file meant to be edited by hand.
`favicon.ico`, `favicon-16x16.png`, `favicon-32x32.png` and
`apple-touch-icon.png` are all derived from it — commit them anyway, since
whitenoise serves `static/` as-is and there is no build step at request time
to generate them from. To resync after editing the SVG:

```bash
uv run python tools/generate_favicons.py
```

Needs Inkscape on `PATH` to rasterise the SVG; Pillow (a dev dependency,
not a production one) does the resizing and `.ico` packing from there.

The header search is a plain GET, matched as a substring across titles and
prose with title hits ranked first. The catalogue fits in memory, so there is
no index. The original ran a live autocomplete against Directus via jQuery,
bootstrap-autocomplete and a Handlebars template; that machinery went with
Directus.

## Where the content came from

Directus → Wagtail → files. The middle step is gone: `content/` is the source
of truth now and is edited directly.

Of 1,511 source rows in Directus about 130 carried guide content, and 40 of
those survive as files in `content/parts/` today. The rest were stock rows
with no prose of their own, or value variants of a group that now has one
page (31 resistor rows became one), or ungrouped items nobody wrote up; all
of it remains browsable in ioref-inventory. The seven `docs_*` fields were
markdown in Directus and are markdown here, byte for byte.

The Wagtail-era importer and the exporter that produced `content/` are in git
history rather than the tree, since neither can run without the models they
were written against.

## Tests

```bash
uv run python manage.py test
```

Two commands are separate from the test suite, because both need a running
inventory:

```bash
uv run python manage.py check_groups       # every group: in content/ still matches parts
uv run python manage.py check_categories   # the five home-page categories still exist
```

Inventory answers an unknown group or category with an empty list rather than
a 404, so a rename on that side empties a page silently rather than erroring.
Run these after inventory changes how it names either.
