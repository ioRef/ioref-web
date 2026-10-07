# ioref-web

Django with no CMS and no database. The public ioref.org site, replacing
maker-cards. Initial implementation August 2026.

The guides are 49 markdown files in `content/`, read once at startup. Editing
the site means editing a file and committing it. Stock arrives over
ioref-inventory's API. There is nothing else.

`README.md` covers usage. This document covers rationale, constraints, and
outstanding work.

## System context

ioref.org currently runs as three applications and is being rebuilt as two.

| Current | Replacement | Status |
|---|---|---|
| **Directus** (`admin.ioref.org`, MySQL `phys_comp_prod`) — CMS and identity provider | retired | in production |
| **maker-cards** (`guides.ioref.org`, Express + Handlebars) | **ioref-web** — this repository | scaffolded |
| **IDeATe-Inventory** (Express, port 3000) | **ioref-inventory** — separate repository | scaffolded |

The applications being replaced are the authoritative reference. Check them out
alongside this repository:

- **maker-cards** — what this replaces. `public/css/main.scss` is the source of
  truth for visual design; `views/` are the templates these were ported from.
- **ioref-inventory** — the stock application. Read its `CLAUDE.md` too; the API
  contract is defined there.
- **cms** — the Directus installation.
- `ioref-schema.yaml` — Directus schema snapshot; the migration reference.
- **physcomp-drawio-library** — Python; generates drawio shape libraries. Shares
  the five-category taxonomy and is a candidate for the LTI work.

## Design decisions

**Guide content and stock are separate applications.** The Directus `parts`
collection was a 42-column table combining maker-card documentation with stock
keeping. Splitting it is what allows ioref-inventory to be deployed by another
organisation with its own parts and no interest in CMU's write-ups. Do not add
stock fields to models here, and do not add guide content to inventory.

**There is no database.** `DATABASES = {}`, and no `django.contrib.auth`,
`sessions`, `messages` or `admin`. Guide content is files, stock lives in
ioref-inventory, and there are no accounts because there is nothing to log in
to. Wagtail was here first and was carrying a page tree, a revision history, an
image library and a user system for a site whose entire content is 100 KB of
markdown edited by one person who would rather use git.

The empty `DATABASES` is the load-bearing part: it means anything that wants a
model has to argue for reintroducing a database, rather than adding one field
and quietly bringing back migrations.

**A guide documents an inventory group, not a part number.** `resistor.md`
answers "what is a resistor" once; its stock table is every value inventory
currently stocks under the `resistor` group -- live, not a list someone typed.
`group:` in front matter is the join key, and `catalog/content.py` refuses to
load two files that claim the same group.

This replaced an earlier, part-number-keyed scheme that produced 31 separate
"resistor" pages differing only by value, because the Directus import ran 1:1.
Merging those down surfaced 21 slugs that had silently lost a decimal point or
a micro sign in the process (`22f-electrolytic-capacitor` for both 2.2uF and
22uF); collapsing to one page per group made the whole class of collision
impossible rather than fixing each instance. Six groups (`breadboard`, `wire`,
`leds`, `pushbuttons`, `h-bridge-motor-drivers`, `microcontroller-boards`)
still carry more than one product's worth of prose, kept as `###` subsections
within the fixed section headings rather than split back into separate pages.

Two pages have no group at all: `soil-moisture-sensor` and
`passive-infrared-sensor` document parts inventory has not yet grouped. They
fall back to a hand-typed `parts:` list, the mechanism every guide used before
groups existed. `content/content.py:load()` requires a page to have one or the
other; a page with neither raises at startup rather than rendering with an
empty stock table and no explanation.

**Category is not a fact a guide file carries.** It used to be `category:`/
`subcategory:` front matter, validated against a local `categories.yml` tree.
It is now `Group.category` in inventory (see "Category lives in inventory,
live" below), which is why the local file only lists five fixed slugs and
nothing under them any more.

**Part sets are many-to-many.** Directus modelled this as a single FK on
`parts`, restricting a part to one set. A resistor belongs in many kits.

**The seven documentation sections are markdown.** maker-cards ran every one of
them through `marked()` (`routes/parts.js:91`), and the content is written
accordingly: 22 parts carry fenced code blocks with language hints (`cpp` x14),
and 59 diagrams are inline `<figure>` HTML.

They are `##` headings in the file, and the heading text is what maps a section
back onto its anchor. The set is fixed, not free-form: the side-menu jump links
are built from it by name, so an unrecognised heading raises at load rather than
vanishing from the page. See `catalog/content.py:DOC_SECTIONS`.

The nh3 allowances in `catalog/content.py` are what let `<figure>`,
`<figcaption>` and `<pre>` through. Narrowing them silently deletes the
diagrams, and only on the pages that use them.

`nh3.clean` is called with `link_rel=None`. It rewrites `rel` on every link by
default and refuses to also accept `rel` as an allowed attribute, so the two
settings are mutually exclusive; the prose sets its own on outbound links.

**`/images/parts/<file>` is a real path, served from `public/`.** The guide
markdown references its diagrams that way because maker-cards resolved them
against Directus at request time (`routes/file-redirect.js`). Exporting the
media under the names the prose already uses means a file server answers
directly, and the alias table that used to translate them is gone along with
the view that read it. Not rewriting the markdown was the point of storing it.

`public/` is served at the root of the URL space by `WHITENOISE_ROOT`, and is
deliberately **not** in `STATICFILES_DIRS`. Under `STATIC_URL` these would be
`/static/images/parts/<file>`, which is not what the prose asks for, and
`collectstatic` would copy 51 MB into `staticfiles/` as a second set whose
names the manifest storage then hashes. Files existing on disk is not the same
as files answering on a URL; `catalog/tests.py` checks the paths and the smoke
test in the Commands section checks the URLs.

**`main.css` is carried over verbatim.** The site must look unchanged. It is a
straight copy of maker-cards' compiled stylesheet; additions go in `site.css`
so the copy can be resynced without merging.

**There is no sign-in, because there is nothing to sign in to.** An earlier
revision put the Wagtail admin behind CMU Shibboleth, with a custom user model,
a trusted-header backend and a vhost that cleared the identity headers. All of
it went when the CMS did; recover it from git history if a web editor ever comes
back. ioref-inventory still uses that arrangement for staff count entry, and is
unaffected.

**Media is committed to the repository.** 98 files, 51 MB in `public/`,
unoptimised straight out of Directus. Only files the prose actually references
are kept; both the original export and the group merge left orphans behind
(Wagtail's whole alias table on the way in, then a size-variant's own photo
becoming redundant once four breadboards became one guide), and each pass
deleted what it found. Deliberate, on the grounds that the alternative is
running something to serve them. Note that git keeps every future revision
forever, so replacing an image is not free.

## Implementation constraints

**Never give the content objects a generated `__repr__` or `__eq__`.** `Part`
can reach another `Part` through `related_parts`, which can point back. Both
generated methods walk fields recursively, so either one follows a cycle until
the process dies. This is not hypothetical: an unrelated exception under
`DEBUG=True` made Django render a traceback page, which reprs the view's local
variables, which exhausted memory and got the process OOM-killed with no
traceback to say why. Hence `@dataclass(eq=False)` and `repr=False` on every
list/relation field in `catalog/content.py`, and a test that asserts `repr()`
terminates.

**`group:` means "this page documents the whole group".** It does not mean
"this part happens to be in that group", which is what the Directus import
originally wrote and what made three switch pages each render a byte-identical
table of every switch in the lab. `catalog/content.py:load()` now enforces the
stronger half of this mechanically: it raises if two files declare the same
group. It cannot catch a file naming the *wrong* group -- that takes a live
check, which is what the paragraph below is for.

**An unknown group is silent.** Inventory answers a group it has never heard
of with HTTP 200 and an empty result set, which is byte-identical to a group
whose parts were all retired. When inventory re-derived its groups from part
names, `resistors` became `resistor`, and the resistor page rendered an empty
stock table for two days without logging anything. Three more slugs were found
the same way while merging the value-keyed pages down to one-per-group:
`thermistor`/`thermistors`, `breadboard-power-supply`/`power-supplies`,
`pancake-vibration-motor`/`vibration-motors` -- each a file whose own name and
declared group happened to be identical and both wrong, which is exactly the
shape a human proofreading a list of "does this look right" will not catch.
What did catch them was resolving each file's group from its own part number
against inventory's live data and comparing, which is mechanical and does not
get tired. `list_by_group` also warns on an empty result at runtime, and
`manage.py check_groups` is the deliberate version to run after any rename;
`--strict` exits non-zero for a deploy gate. It is a command rather than a
test because it needs a running inventory, and a suite that fails when a
service is down is a suite people learn to ignore.

**A guide's URL never depends on its category.** Every guide lives at
`/parts/<group-slug>/`, full stop -- `catalog/content.py` never calls
inventory, so parsing a guide and building its URL cannot fail because
inventory is down. Category only exists as a live, request-scoped concept,
fetched by `views.category` for the `/category/<slug>/` browse page and
nowhere else. This replaced an earlier scheme that nested guides under
`/<category>/[<subcategory>/]<slug>/`, which needed a resolver to tell a
subcategory from a part at the same URL depth and made the site's whole
shape depend on `category:` front matter that inventory now owns instead.

**Category lives in inventory, live.** `Group.category` is inventory's fact,
not a mapping file here -- see its own docstring: "the person who creates a
group is the person who knows which category it belongs to, and making them
edit a second repository to say so is how a taxonomy goes stale."
`/category/<slug>/` calls `list_groups_by_category()` on every request and
shows every group inventory returns, whether or not a guide exists for it; a
guided group links to its guide, an unguided one links to
`/inventory/?group=<slug>`. This is a browse view in the same sense
`/inventory/` is one: `InventoryUnavailable` renders as a 503, not an empty
category, for the same reason `list_parts()` raises rather than returning
`[]`.

**`/c/<slug>/` is a redirect, not a second view.** `category_page.html` has
exactly one caller. `category_alias` rewrites the path and hands off; it does
not know or care whether the slug is real, because `category()` already
handles that one hop later. A 301: the shorthand is deliberate and stable,
not a typo search engines should index twice.

**The printed cards resolve at the root, ahead of everything else.** They
predate this rebuild and carry bare URLs with no prefix -- `ioref.org/resistor`
(a group), `ioref.org/0496` (a part number) -- so `<slug:token>/` in
`catalog/urls.py` catches anything not claimed by a named route above it and
`views.resolve_legacy` works out which case it is: a local guide slug first
(free, no inventory involved), then a live part-number lookup, preferring the
part's group's guide over `/inventory/<number>/` if one exists. Ordering in
`urls.py` is what makes this safe -- see the note there. `stock/views.py`'s
`_guide_for` is the same resolution, reused: it used to only check a guide's
inline `parts:` list, which went from covering every guide to covering two of
them the day guides became group-keyed, and nothing caught it. Both places
now check the live group first.

**The five categories themselves are hardcoded, not fetched.** `content/
categories.yml` lists `input`, `output`, `power`, `connector`, `controller`
and nothing else -- no groups, no subcategories. The home page must render
even when inventory is unreachable, and `main.css` colours boxes by these
exact five slugs, which essentially never change. What changes constantly is
*which groups sit under Power today*, and that is never cached here; it is
asked for fresh every time `/c/power/` is visited.

**Subcategory does not exist any more.** It was local front matter
(`acceleration`, `light`, `movement`...) validated against a tree in
`categories.yml`. Inventory has no equivalent concept -- only `Group.category`,
one level -- so there is nothing left to source a second tier from. Inventory's
`tags` (`light`, `movement`, `sound`...) cover similar ground and are already
returned per-part by the API; a subcategory-like filter on the `/c/<slug>/`
page could be rebuilt from those later, but nothing does today.

**The client functions fail differently on purpose.** `get_stock()` and
`get_stock_many()` return `None` / `{}`; `list_parts()` raises
`InventoryUnavailable`. A component page without stock is still a useful page —
it is mostly documentation — but a browse view that renders an outage as an
empty catalogue is actively misleading. Preserve the distinction.

**Component pages fetch stock in one request, not one per part.** The ceramic
capacitor page covers 33 part numbers; `get_stock_many()` uses the API's
`part_number__in` filter so rendering it costs a single call. Reverting to a
loop over `get_stock()` would be 33 round trips per pageview.

**404s are cached, 500s are not.** A part with a guide but no stock record is a
permanent condition and should not be re-requested every pageview; a 500 is
transient and caching it would extend the outage.

**Images are served full size.** Wagtail generated a 200x200 rendition for
every tile; there is no rendition machinery now, so `.img_part_tile` scales the
original in CSS and the browser downloads all 403 KB of it. The tiles look
right and the bytes are wrong. A build-time resize step is the fix if it ever
matters.

**Never write a multi-line `{# … #}` comment.** Django's `{# #}` syntax is
single-line only. Spanning one across lines does not produce a comment — the
text is emitted into the page as literal content. It raises no error and no
warning, so it is caught only by looking at the rendered output. Use
`{% comment %} … {% endcomment %}` for anything longer than one line. Every
comment in this repository longer than a line uses the tag form for this
reason — including one that had to be found and fixed after shipping,
in `side_category_menu.html` during the /category/ rework below, caught only
by loading the page and seeing the comment text rendered on it.

## Design language

`maker-cards/public/css/main.scss` is authoritative.

- Typeface Nunito Sans. Info pages: 24px bold titles, 22px bold section
  headings, bold labels.
- Neutral greys, no blue cast: `#f2f2f2`, `#c4c4c4`, `#4f4f4f`, off-black
  `#1d1d1d`, off-white `#fdfdfd`.
- Category colours: input `#14B04D`, output `#00A0C4`, controller `#4C265B`,
  connector `#636466`, power `#DD1B50`.
- The `stock_low` crimson matches the badge in the inventory admin, so both
  interfaces agree on what "low" looks like.

## Commits

Conventional Commits, e.g. `feat(api): add part_number__in filter`.

Types in use: `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, `perf`,
`build`, `ci`. Scope is the app or area (`catalog`, `stock`, `content`,
`deploy`). Breaking changes take a `!` before the colon and a
`BREAKING CHANGE:` footer.

List items in commit bodies use `*`, not `-`. Commits made with Claude end with a
`Co-Authored-By:` trailer.

The subject is imperative and lower-case, under about 72 characters. Where a
change encodes a decision, put the reasoning in the body -- this project has
several constraints that look arbitrary without it.

## Commands

```bash
uv sync
uv run python manage.py runserver
uv run python manage.py test
uv run python manage.py check_groups      # after inventory renames a group
uv run python manage.py check_categories  # after inventory's Category rows change
```

No `migrate`, no `createsuperuser`, no seed command. There is no database and
no login, and the content is checked in, so a fresh clone runs.

Run both applications for the cross-application join: inventory on 8765, this
on 8766, with `INVENTORY_API_URL` pointing at the former. The part numbers in
`content/` are the real ones, which ioref-inventory's `seed_demo` covers a
subset of, so most parts will show stock as unavailable against a demo
inventory. That is the honest rendering of a part inventory has not heard of.

Content is read once at startup. `runserver` reloads on `.py` changes but not
on edits under `content/`, so restart it after editing a card, or call
`catalog.content.reload()`.

Media is the thing the test suite can only half check, because a file existing
on disk and a file answering on a URL are different claims. Against a running
server:

```bash
python - <<'EOF'
import re, pathlib, urllib.parse, urllib.request
pat = re.compile(r'/(images|videos)/parts/([^"\'<>)\]]+)')
for f in sorted(pathlib.Path("content/parts").glob("*.md")):
    for kind, name in pat.findall(f.read_text()):
        url = urllib.parse.quote(f"/{kind}/parts/{name.strip()}")
        try:
            code = urllib.request.urlopen(f"http://127.0.0.1:8000{url}").status
        except Exception as e:
            print(getattr(e, "code", e), url, f.name)
EOF
```

## Outstanding work

**Every category page reads "No groups are filed" right now.** Inventory's
`Category` table exists (`Group.category` shipped in ioref-inventory's
`187b5c7`) but is unpopulated: `manage.py check_categories` confirms zero of
the five hardcoded slugs currently match anything live. This is not a bug
here -- `/c/power/` is correctly reporting what inventory has -- it is
inventory-side population work, assigning each of its ~126 groups a category
in the admin. Once that starts happening the pages fill in with no code
change on this side.

**Search has no autocomplete.** `Catalogue.search` is a substring scan over the
catalogue, ranking title matches first. It returns a superset of what Wagtail's
database backend did. The legacy site had live autocomplete against Directus; if
that is wanted, it is a JSON index and a little JavaScript, not a search
backend.

**Image bytes.** See the note under implementation constraints. A build step
that writes resized copies alongside the originals would fix both the tile
bandwidth and some of the repository weight.

**LTI.** Under consideration for the drawio library or part sets, launched from
Canvas. It lands here, not on inventory. LTI 1.3 is OIDC-based and launches in
an iframe, so it needs `SameSite=None` cookies — maker-cards already established
that pattern for its Directus session cookie.

**`.env` currently holds a development API key** generated against the local
inventory instance. Production needs its own read-scoped key.
