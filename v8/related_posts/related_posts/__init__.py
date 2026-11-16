"""Nikola plugin to calculate and render related posts."""

from collections import defaultdict
from html import escape
import logging

import blinker
from nikola.plugin_categories import SignalHandler

from .similarity import find_all_related


LOGGER = logging.getLogger(__name__)


class RelatedPosts(SignalHandler):
    name = "related_posts"

    def set_site(self, site):
        super().set_site(site)
        blinker.signal("taxonomies_classified").connect(self._calculate_relations)
        blinker.signal("render_post").connect(self._inject_related_context)
        site.template_hooks["page_footer"].append(self._render_related_html, True)

    @staticmethod
    def _references(posts, lang):
        """Index manual targets by slug, permalink and source path.

        Duplicate slugs are intentionally retained so ambiguous manual
        references can be reported instead of choosing an arbitrary post.
        """
        references = defaultdict(list)
        for post in posts:
            keys = (
                post.meta("slug", lang),
                post.permalink(lang),
                str(post.source_path),
            )
            for key in set(keys):
                if key:
                    references[str(key)].append(post)
        return references

    def _manual_recommendations(self, post, manual, references, lang, count):
        """Resolve manual references, keeping order and removing duplicates."""
        if isinstance(manual, (tuple, list)):
            values = [str(value).strip() for value in manual]
        else:
            values = [value.strip() for value in str(manual).split(",")]

        values = list(dict.fromkeys(value for value in values if value))
        selected = []
        for value in values:
            matches = references.get(value, [])
            if not matches:
                LOGGER.warning(
                    "related_manual: reference '%s' not found (lang=%s) in '%s'",
                    value, lang, post.source_path,
                )
                continue
            if len(matches) > 1:
                LOGGER.warning(
                    "related_manual: ambiguous reference '%s' (lang=%s) in '%s'; "
                    "use the full permalink or source path",
                    value, lang, post.source_path,
                )
                continue
            other = matches[0]
            if other is post:
                LOGGER.warning(
                    "related_manual: '%s' references itself (lang=%s)",
                    post.source_path, lang,
                )
                continue
            if other not in selected:
                selected.append(other)
                if len(selected) == count:
                    break
        return selected

    def _calculate_relations(self, site):
        if site is not self.site:
            return

        count = site.config.get("RELATED_POSTS_COUNT", 5)
        if type(count) is not int or count < 0:
            raise ValueError("RELATED_POSTS_COUNT must be a non-negative integer")

        for post in site.posts:
            post.related_posts = {}

        show_untranslated = site.config.get("SHOW_UNTRANSLATED_POSTS", True)
        for lang in site.config["TRANSLATIONS"]:
            related = find_all_related(
                site.posts,
                count=count,
                lang=lang,
                cache_dir=site.config.get("CACHE_FOLDER"),
                include_untranslated=show_untranslated,
            )
            references = self._references(related, lang)

            for post, recommendations in related.items():
                manual = post.meta("related_manual", lang)
                if isinstance(manual, str) and manual.strip().casefold() == "none":
                    post.related_posts[lang] = []
                elif manual and count:
                    post.related_posts[lang] = self._manual_recommendations(
                        post, manual, references, lang, count
                    )
                else:
                    post.related_posts[lang] = recommendations

    def _inject_related_context(self, event):
        if event.get("site") is not self.site:
            return

        post = event.get("post")
        context = event.get("context")
        lang = event.get("lang")
        if post is None or context is None:
            return

        related = getattr(post, "related_posts", {}).get(lang, [])
        context["related_posts"] = related
        deps_dict = event.get("deps_dict")
        if deps_dict is not None:
            deps_dict["related_posts"] = [
                (other.title(lang), other.permalink(lang)) for other in related
            ]
            deps_dict["related_posts_title"] = (
                self.site.config.get("RELATED_POSTS_TITLES", {}).get(
                    lang, "Related posts"
                )
            )

    def _render_related_html(self, site=None, context=None):
        """Render related links in the footer of blog posts only."""
        if context is None:
            return ""
        post = context.get("post")
        if post is None or not post.is_post:
            return ""

        lang = context.get("lang")
        related = context.get("related_posts", [])
        if not related:
            return ""

        links = "".join(
            '<li><a href="{}">{}</a></li>'.format(
                escape(other.permalink(lang), quote=True),
                escape(other.title(lang)),
            )
            for other in related
        )
        heading = self.site.config.get("RELATED_POSTS_TITLES", {}).get(
            lang, "Related posts"
        )
        return (
            '<section class="related-posts">'
            f'<h2>{escape(heading)}</h2>'
            f'<ul>{links}</ul>'
            '</section>'
        )
