from django.conf import settings
from django.urls import include, path, re_path
from django.views.generic import RedirectView

urlpatterns = [
    # Compatibility only. Inventory browsing now belongs to ioref-inventory.
    path("inventory/", include("stock.urls")),
    # Short link to the CRIT form. Hardcoded on purpose: edit the URL here and
    # redeploy to change it. Temporary (302), so browsers don't cache a target
    # that may change. Must sit above catalog, whose <slug:token>/ route would
    # otherwise take /crit/ as a legacy card URL.
    re_path(
        r"^crit/?$",
        RedirectView.as_view(
            url="https://docs.google.com/forms/d/e/1FAIpQLSdPNK2jsuhqpYFYns5phurYsDBdpiZswZcszGvXif2wwnATJQ/viewform?usp=publish-editor",
            permanent=False,
        ),
        name="crit",
    ),
    # Must come last: catalog puts category slugs at the root of the path, so
    # its patterns would otherwise swallow everything above.
    path("", include("catalog.urls")),
]


if settings.DEBUG:
    from django.contrib.staticfiles.urls import staticfiles_urlpatterns

    urlpatterns += staticfiles_urlpatterns()
