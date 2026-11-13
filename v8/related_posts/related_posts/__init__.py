
from html import escape

import blinker
from nikola.plugin_categories import SignalHandler

from .similarity import find_related


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

        all_posts = self.site.posts
        count = self.site.config.get("RELATED_POSTS_COUNT", 5)

        if (
            not isinstance(count, int)
            or isinstance(count, bool)
            or count < 0
        ):
            raise ValueError(
                "RELATED_POSTS_COUNT must be a non-negative integer"
            )

        languages = self.site.config["TRANSLATIONS"]

        for post in all_posts:
            post.related_posts = {}

            for lang in languages:
                if not post.is_translation_available(lang):
                    continue

                related = find_related(
                    post,
                    all_posts,
                    count=count,
                    lang=lang
                )

                post.related_posts[lang] = related

    def _inject_related_context(self, event):
        if event.get("site") is not self.site:
            return

        post = event.get("post")
        context = event.get("context")
        lang = event.get("lang")

        if post is None or context is None:
            return

        related = getattr(post, "related_posts", {})
        related = related.get(lang, [])

        context["related_posts"] = related

        event["deps_dict"]["related_posts"] = [
            (p.title(lang), p.permalink(lang))
            for p in related
        ]

    def _render_related_html(self, site=None, context=None):
        """Render the related posts section in the page footer."""
        if not context:
            return ""

        post = context.get("post")

        if post is None or not post.is_post:
            return ""

        related = context.get("related_posts", [])

        if not related:
            return ""

        lang = context["lang"]

        items = []

        for other in related:
            title = escape(other.title(lang))
            url = escape(other.permalink(lang), quote=True)

            items.append(
                f'<li><a href="{url}">{title}</a></li>'
            )

        return (
            '<section class="related-posts">'
            '<h2>Publicaciones relacionadas</h2>'
            '<ul>'
            + "\n".join(items)
            + '</ul>'
            '</section>'
        )
