"""Nikola plugin to calculate and display related posts."""

from html import escape

import blinker
from nikola.plugin_categories import SignalHandler

from .similarity import find_all_related


class RelatedPosts(SignalHandler):
    name = "related_posts"

    def set_site(self, site):
        super().set_site(site)
        blinker.signal("taxonomies_classified").connect(
            self._calculate_relations
        )
        blinker.signal("render_post").connect(
            self._inject_related_context
        )
        site.template_hooks["page_footer"].append(
            self._render_related_html, True
        )

    def _calculate_relations(self, site):
        if site is not self.site:
            return

        count = self.site.config.get("RELATED_POSTS_COUNT", 5)
        if type(count) is not int or count < 0:
            raise ValueError("RELATED_POSTS_COUNT must be a non-negative integer")

        for post in self.site.posts:
            post.related_posts = {}

        for lang in self.site.config["TRANSLATIONS"]:
            related = find_all_related(
                self.site.posts,
                count=count,
                lang=lang,
                cache_dir=self.site.config["CACHE_FOLDER"],
            )
            for post, recommendations in related.items():
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
        event["deps_dict"]["related_posts"] = [
            (other.title(lang), other.permalink(lang)) for other in related
        ]

    def _render_related_html(self, site=None, context=None):
        """Render a related-posts section at the bottom of blog posts."""
        if not context:
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
            ) for other in related
        )
        return (
            '<section class="related-posts">'
            f'<h2>{escape(self.site.config.get("RELATED_POSTS_TITLES", {}).get(lang, "Related posts"))}</h2>'
            f'<ul>{links}</ul>'
            '</section>'
        )
