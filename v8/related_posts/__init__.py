import blinker
from nikola.plugin_categories import SignalHandler

from .similarity import find_related


class RelatedPosts(SignalHandler):
    name = "related_posts"

    def set_site(self, site):
        super().set_site(site)
        blinker.signal("taxonomies_classified").connect(self._calculate_relations)
        blinker.signal("render_post").connect(self._inject_related_context)

    def _calculate_relations(self, site):
        if site is not self.site:
            return

        all_posts = self.site.posts
        count = self.site.config.get("RELATED_POSTS_COUNT", 5)

        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise ValueError("RELATED_POSTS_COUNT must be a non-negative integer")

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

        if post and context is not None:
            related = getattr(post, "related_posts", {})
            context["related_posts"] = related.get(lang, [])
