from django.test import SimpleTestCase

from catalog.templatetags.links import external_href


class ExternalHrefTests(SimpleTestCase):
    def test_schemeless_host_gets_https(self):
        self.assertEqual(
            external_href("mcmaster.com/#92095A182"),
            "https://mcmaster.com/#92095A182",
        )

    def test_host_with_port_gets_https(self):
        self.assertEqual(external_href("example.com:8080/x"), "https://example.com:8080/x")

    def test_protocol_relative_gets_https(self):
        self.assertEqual(external_href("//cdn.example.com/y"), "https://cdn.example.com/y")

    def test_full_urls_are_unchanged(self):
        for url in (
            "https://www.digikey.com/en/products/detail/x",
            "http://example.com/",
            "HTTPS://EXAMPLE.COM/",
            "mailto:sales@example.com",
            "tel:+14125551234",
        ):
            with self.subTest(url=url):
                self.assertEqual(external_href(url), url)

    def test_internal_paths_are_unchanged(self):
        for url in ("/parts/1829/", "#section", "?q=1"):
            with self.subTest(url=url):
                self.assertEqual(external_href(url), url)

    def test_unsafe_or_non_url_values_are_dropped(self):
        for value in ("javascript:alert(1)", "data:text/html,x", "shelf 3", "localhost"):
            with self.subTest(value=value):
                self.assertEqual(external_href(value), "")

    def test_empty_and_whitespace(self):
        self.assertEqual(external_href(""), "")
        self.assertEqual(external_href(None), "")
        self.assertEqual(external_href("   "), "")
        self.assertEqual(
            external_href("  mcmaster.com/x  "), "https://mcmaster.com/x"
        )
