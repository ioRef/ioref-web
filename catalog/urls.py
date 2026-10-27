"""Public URLs.

A guide is always /parts/<group-slug>/ regardless of category, and a category
listing is always /categories/<slug>/ -- /c/<slug>/ is kept as a short alias,
redirecting to the canonical path rather than duplicating the view.

The trailing <slug:token>/ pattern exists for the printed cards, which predate
this scheme and carry bare URLs like ioref.org/resistor and ioref.org/0496 --
a group slug and a part number, with no prefix at all. It must be last: it
matches any single path segment, so anything above it (search/, part-sets/,
parts/<slug>/, categories/<slug>/, c/<slug>/) has to get first refusal or the
resolver would swallow every one of them. See views.resolve_legacy.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.home, name="home"),
    path("search/", views.search, name="search"),
    path("part-sets/", views.part_set_index, name="part_set_index"),
    path("part-sets/<slug:slug>/", views.part_set, name="part_set"),
    path("parts/<slug:slug>/", views.part, name="part"),
    path("parts/<slug:slug>/report/", views.report_stock, name="report_stock"),
    path("categories/<slug:category_slug>/", views.category, name="category"),
    path("c/<slug:category_slug>/", views.category_alias, name="category_alias"),
    path("<slug:token>/", views.resolve_legacy, name="resolve_legacy"),
]
