"""Isolated regression tests; Nikola itself isn't required for these tests."""

import gzip
import importlib
import json
import logging
from pathlib import Path
import sys
import tempfile
import types
import unittest

# Provide minimal stand-ins for Nikola's plugin base and Blinker if they aren't
# installed. The tests exercise the plugin logic, not Nikola's integration.
try:
    import blinker  # noqa: F401
except ImportError:
    blinker = types.ModuleType('blinker')
    blinker.signal = lambda name: types.SimpleNamespace(connect=lambda fn: None)
    sys.modules['blinker'] = blinker

try:
    from nikola.plugin_categories import SignalHandler  # noqa: F401
except ImportError:
    nikola = types.ModuleType('nikola')
    categories = types.ModuleType('nikola.plugin_categories')
    class SignalHandler:
        def set_site(self, site):
            self.site = site
    categories.SignalHandler = SignalHandler
    nikola.plugin_categories = categories
    sys.modules['nikola'] = nikola
    sys.modules['nikola.plugin_categories'] = categories

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sim = importlib.import_module('related_posts.similarity')
RelatedPosts = importlib.import_module('related_posts').RelatedPosts


class FakePost:
    def __init__(self, name, root, slug=None, available=True, body='Very useful article'):
        self.name = name
        self.path = Path(root) / (name + '.md')
        self.path.write_text(body, encoding='utf8')
        self.source_path = str(self.path)
        self.is_post = True
        self.use_in_feeds = True
        self.available = available
        self.default_lang = 'en'
        self.config = {'DEFAULT_LANG': 'en'}
        self.slug = slug or name
        self.body = body
        self.extra_deps = []
        self.compile_deps = []
        self.compiles = 0
        self.manual = None

    def translated_source_path(self, lang):
        return str(self.path)

    def is_translation_available(self, lang):
        return self.available

    def source(self, lang):
        return self.path.read_text(encoding='utf8')

    def title(self, lang):
        return self.name

    def tags_for_language(self, lang):
        return ['python']

    def meta(self, field, lang):
        if field == 'slug':
            return self.slug
        if field == 'related_manual':
            return self.manual
        return None

    def permalink(self, lang):
        return '/post/' + self.name + '/'

    def fragment_deps(self, lang):
        return [str(self.path)] + self.extra_deps

    def compile(self, lang):
        self.compiles += 1
        self.extra_deps = list(self.compile_deps)

    def text(self, **kwargs):
        return self.source('en') + ''.join(Path(p).read_text() for p in self.extra_deps)


class RelatedPostsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def post(self, name, **kw):
        return FakePost(name, self.root, **kw)

    def test_invalid_cache_structure(self):
        path = self.root / 'bad.gz'
        for content in ([], {'version': sim.CACHE_VERSION, 'posts': []},
                        {'version': sim.CACHE_VERSION,
                         'posts': {'x': {'signature': 'abc', 'dependencies': [],
                                         'body': {'a': 'no'}}}}):
            with gzip.open(path, 'wt', encoding='utf-8') as out:
                json.dump(content, out)
            self.assertEqual(sim._load_cache(path), {})

    def test_cache_discovers_new_dependencies(self):
        dep = self.root / 'included.txt'
        dep.write_text('initially included')
        p = self.post('test')
        p.compile_deps = [str(dep)]
        sim.build_profiles([p], 'en', self.root)
        self.assertEqual(p.compiles, 1)
        # On the next build Nikola knows no fragment deps until compilation.
        p.extra_deps = []
        sim.build_profiles([p], 'en', self.root)
        self.assertEqual(p.compiles, 1, 'unchanged fragments should reuse cache')
        dep.write_text('A very different included section')
        p.extra_deps = []
        sim.build_profiles([p], 'en', self.root)
        self.assertEqual(p.compiles, 2, 'changes to included files must recompile')

    def test_manual_references_ambiguous_and_paths(self):
        a = self.post('a', slug='same')
        b = self.post('b', slug='same')
        current = self.post('current')
        p = RelatedPosts()
        refs = p._references([a, b, current], 'en')
        with self.assertLogs(level='WARNING') as logs:
            out = p._manual_recommendations(
                current, 'same,/post/a/,current,/post/b/', refs, 'en', 5
            )
        self.assertEqual(out, [a, b])
        self.assertTrue(any('ambiguous' in m for m in logs.output))
        self.assertTrue(any('itself' in m for m in logs.output))

    def test_show_untranslated_posts(self):
        a = self.post('one')
        b = self.post('two', available=False)
        visible = sim.find_all_related([a, b], count=0, lang='de',
                                       include_untranslated=True)
        hidden = sim.find_all_related([a, b], count=0, lang='de',
                                      include_untranslated=False)
        self.assertEqual(set(visible), {a, b})
        self.assertEqual(set(hidden), {a})

    def test_render_escape_and_dependencies(self):
        target = self.post('x&<tag>')
        current = self.post('current')
        current.related_posts = {'en': [target]}
        site = types.SimpleNamespace(config={
            'RELATED_POSTS_TITLES': {'en': 'Read <More> & Now'}
        })
        p = RelatedPosts()
        p.site = site
        event = {'site': site, 'post': current, 'lang': 'en', 'context': {},
                 'deps_dict': {}}
        p._inject_related_context(event)
        html = p._render_related_html(context={'post': current, 'lang': 'en',
                                                **event['context']})
        self.assertIn('Read &lt;More&gt; &amp; Now', html)
        self.assertIn('x&amp;&lt;tag&gt;', html)
        self.assertEqual(event['deps_dict']['related_posts_title'],
                         'Read <More> & Now')

    def test_candidate_fallback_for_very_common_terms(self):
        # Regression: 160 posts with identical features must not end up with
        # zero comparisons just because each feature exceeds the DF threshold.
        class P:
            pass
        profile = (P(), frozenset({'popular'}), frozenset({'same'}),
                   {'aaa': 1.0})
        profiles = [profile] * 160
        pairs = sim._candidate_pairs(
            profiles, {'popular': 1.0}, {'aaa': 160}, {'aaa': 1.0}
        )
        self.assertTrue(pairs)
        self.assertIn((0, 1), pairs)

    def test_algorithm_empty_and_default_lang(self):
        self.assertEqual(sim.find_all_related([]), {})
        a = self.post('a', body='similar words and other things')
        b = self.post('b', body='similar words and other things')
        result = sim.find_all_related([a, b], count=1, cache_dir=self.root)
        self.assertEqual(result[a], [b])
        self.assertEqual(result[b], [a])
        self.assertEqual(sim.find_related(a, [a, b], cache_dir=self.root), [b])

    def test_automatic_and_manual_choices(self):
        a = self.post('a')
        b = self.post('b')
        c = self.post('c')
        a.manual = 'b'
        b.manual = 'none'
        site = types.SimpleNamespace(
            config={'TRANSLATIONS': {'en': ''}, 'CACHE_FOLDER': str(self.root),
                    'RELATED_POSTS_COUNT': 2, 'SHOW_UNTRANSLATED_POSTS': True},
            posts=[a, b, c],
        )
        plugin = RelatedPosts()
        plugin.site = site
        plugin._calculate_relations(site)
        self.assertEqual(a.related_posts['en'], [b])
        self.assertEqual(b.related_posts['en'], [])
        self.assertIn('en', c.related_posts)


if __name__ == '__main__':
    unittest.main()
