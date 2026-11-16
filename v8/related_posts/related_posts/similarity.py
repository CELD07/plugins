"""Recommend related Nikola posts using Unicode n-grams and TF-IDF."""

from collections import Counter, defaultdict
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path
import unicodedata


BODY_LIMIT = 20_000
NGRAM_SIZE = 3
MIN_SCORE = 0.15
EXACT_SEARCH_LIMIT = 150
CANDIDATE_LIMIT = 200
BODY_INDEX_FEATURES = 96
MAX_BODY_DF_RATIO = 0.20
CACHE_VERSION = 4  # Invalidate caches created before dependency tracking was fixed.


def normalize(text):
    return unicodedata.normalize("NFKC", str(text)).casefold().strip()


def grams(text):
    """Return Unicode character n-grams, preserving their frequencies."""
    cleaned = "".join(
        ch if ch.isalnum() or unicodedata.category(ch).startswith("M") else " "
        for ch in normalize(text)
    )
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return []
    if len(cleaned) < NGRAM_SIZE:
        return [cleaned]
    return [
        cleaned[i:i + NGRAM_SIZE]
        for i in range(len(cleaned) - NGRAM_SIZE + 1)
    ]


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cosine(a, b):
    """Dot product of two previously normalized sparse vectors."""
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(term, 0.0) for term, value in a.items())


def weighted_jaccard(a, b, idf):
    union = a | b
    if not union:
        return 0.0
    return sum(idf[t] for t in a & b) / sum(idf[t] for t in union)


def _cache_filename(cache_dir, lang):
    if cache_dir is None:
        return None
    safe_lang = hashlib.sha256(lang.encode("utf-8")).hexdigest()[:12]
    return Path(cache_dir) / "related_posts" / f"{safe_lang}.json.gz"


def _valid_entry(entry):
    if not isinstance(entry, dict):
        return False
    if not isinstance(entry.get("signature"), str):
        return False
    if not isinstance(entry.get("dependencies"), list):
        return False
    if not all(isinstance(path, str) for path in entry["dependencies"]):
        return False
    body = entry.get("body")
    if not isinstance(body, dict):
        return False
    return all(
        isinstance(term, str) and type(freq) is int and freq > 0
        for term, freq in body.items()
    )


def _load_cache(filename):
    """Read valid entries; ignore malformed or outdated cache data."""
    if filename is None or not filename.is_file():
        return {}
    try:
        with gzip.open(filename, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        if not isinstance(data, dict) or data.get("version") != CACHE_VERSION:
            return {}
        posts = data.get("posts")
        if not isinstance(posts, dict):
            return {}
        return {
            key: entry for key, entry in posts.items()
            if isinstance(key, str) and _valid_entry(entry)
        }
    except (OSError, ValueError, TypeError, EOFError):
        return {}


def _save_cache(filename, items):
    if filename is None:
        return
    filename.parent.mkdir(parents=True, exist_ok=True)
    temp = filename.with_suffix(".tmp")
    try:
        with gzip.open(temp, "wt", encoding="utf-8", compresslevel=3) as out:
            json.dump(
                {"version": CACHE_VERSION, "posts": items},
                out, ensure_ascii=False, separators=(",", ":"),
            )
        temp.replace(filename)
    finally:
        if temp.exists():
            temp.unlink()


def _dependencies(post, lang, saved=()):
    """Combine discovered dependencies with those persisted in the cache.

    Some Nikola compilers discover included-file dependencies only when the
    fragment is compiled. The old dependency list must be checked on a cache
    hit, even when Nikola hasn't loaded it yet in this process.
    """
    paths = set(saved)
    paths.update(str(path) for path in post.fragment_deps(lang))
    return sorted(path for path in paths if not path.startswith("####MAGIC####"))


def _dependency_states(paths):
    states = []
    for path in paths:
        try:
            info = Path(path).stat()
            states.append((path, info.st_size, info.st_mtime_ns))
        except OSError:
            states.append((path, None, None))
    return states


def _signature(post, lang, dependencies):
    """Fingerprint inputs that affect a post's indexed body profile."""
    tags = sorted(normalize(tag) for tag in post.tags_for_language(lang))
    material = (
        post.source(lang), post.title(lang), tags,
        _dependency_states(dependencies), BODY_LIMIT, NGRAM_SIZE,
    )
    return hashlib.sha256(repr(material).encode("utf-8")).hexdigest()


def _body_counts(post, lang, old):
    """Reuse compiled text only when source and dependencies are unchanged."""
    saved_paths = old["dependencies"] if _valid_entry(old) else ()
    paths = _dependencies(post, lang, saved_paths)
    signature = _signature(post, lang, paths)

    if _valid_entry(old) and old["signature"] == signature:
        return Counter(old["body"]), old

    # Compilers may discover more dependencies while compiling the fragment.
    post.compile(lang)
    body = post.text(lang=lang, strip_html=True, show_read_more_link=False)
    counts = Counter(grams(body[:BODY_LIMIT]))
    final_paths = _dependencies(post, lang)
    # Store the post-compilation set (not the pre-compilation set).
    entry = {
        "signature": _signature(post, lang, final_paths),
        "dependencies": final_paths,
        "body": dict(counts),
    }
    return counts, entry


def build_profiles(posts, lang, cache_dir=None):
    """Build feature profiles and cache compiled body n-gram counts."""
    filename = _cache_filename(cache_dir, lang)
    old = _load_cache(filename)
    new = {}
    raw = []
    body_df = Counter()
    tag_df = Counter()

    for post in posts:
        key = str(post.translated_source_path(lang))
        tags = frozenset(normalize(tag) for tag in post.tags_for_language(lang))
        title = frozenset(grams(post.title(lang)))
        body, entry = _body_counts(post, lang, old.get(key))
        new[key] = entry
        raw.append((post, tags, title, body))
        body_df.update(body.keys())
        tag_df.update(tags)

    if filename is not None and new != old:
        _save_cache(filename, new)

    n = len(raw)
    body_idf = {
        term: 1.0 + math.log((n + 1) / (df + 1))
        for term, df in body_df.items()
    }
    tag_idf = {
        tag: 1.0 + math.log((n + 1) / (df + 1))
        for tag, df in tag_df.items()
    }
    profiles = []

    for post, tags, title, counts in raw:
        vector = {
            term: (1.0 + math.log(freq)) * body_idf[term]
            for term, freq in counts.items()
        }
        norm = math.sqrt(sum(weight * weight for weight in vector.values()))
        if norm:
            vector = {term: weight / norm for term, weight in vector.items()}
        profiles.append((post, tags, title, vector))

    return profiles, tag_idf, body_df, body_idf


def _candidate_pairs(profiles, tag_idf, body_df, body_idf,
                     candidate_limit=CANDIDATE_LIMIT):
    """Find candidate pairs with capped high-frequency index postings."""
    n = len(profiles)
    if n <= EXACT_SEARCH_LIMIT:
        return {(i, j) for i in range(n) for j in range(i + 1, n)}
    if candidate_limit <= 0:
        return set()

    # Very common features produce mostly noise and can make index traversal
    # quadratic. Rare tags are still given greater influence than title grams.
    max_df = max(3, int(n * MAX_BODY_DF_RATIO))
    tag_df = Counter(tag for _, tags, _, _ in profiles for tag in tags)
    title_df = Counter(term for _, _, title, _ in profiles for term in title)
    by_tag = defaultdict(list)
    by_title = defaultdict(list)
    by_body = defaultdict(list)
    body_tokens = []

    for i, (_, tags, title, body) in enumerate(profiles):
        for tag in tags:
            if tag_df[tag] <= max_df:
                by_tag[tag].append(i)
        for term in title:
            if title_df[term] <= max_df:
                by_title[term].append(i)

        choices = heapq.nlargest(
            BODY_INDEX_FEATURES,
            (term for term in body if 2 <= body_df[term] <= max_df),
            key=lambda term: (body[term] * body_idf[term], term),
        )
        body_tokens.append(choices)
        for term in choices:
            by_body[term].append(i)

    pairs = set()
    for i, (_, tags, title, body) in enumerate(profiles):
        evidence = defaultdict(float)
        for tag in tags:
            for j in by_tag.get(tag, ()):
                if i != j:
                    evidence[j] += 3.0 * tag_idf[tag]
        for term in title:
            for j in by_title.get(term, ()):
                if i != j:
                    evidence[j] += 0.15
        for term in body_tokens[i]:
            for j in by_body[term]:
                if i != j:
                    evidence[j] += body_idf[term] * body[term]

        for j, _ in heapq.nlargest(
            candidate_limit,
            evidence.items(),
            key=lambda item: (item[1], -item[0]),
        ):
            pairs.add((min(i, j), max(i, j)))

        if not evidence:
            # When all features are very common, the capped indexes can have
            # no candidates. Still inspect a small deterministic neighborhood.
            # Scoring below will reject unrelated posts.
            radius = min(candidate_limit, 16)
            for j in range(max(0, i - radius), min(n, i + radius + 1)):
                if j != i:
                    pairs.add((min(i, j), max(i, j)))

    return pairs


def _score(a, b, tag_idf):
    _, tags_a, title_a, body_a = a
    _, tags_b, title_b, body_b = b
    return (
        0.4 * weighted_jaccard(tags_a, tags_b, tag_idf)
        + 0.2 * jaccard(title_a, title_b)
        + 0.4 * cosine(body_a, body_b)
    )


def _near_duplicate(a, b):
    """Avoid recommending several nearly identical posts together."""
    return (
        bool(a[3]) and bool(b[3])
        and cosine(a[3], b[3]) > 0.94
        and jaccard(a[2], b[2]) > 0.70
    )


def find_all_related(all_posts, count=5, lang=None, cache_dir=None,
                     candidate_limit=CANDIDATE_LIMIT,
                     include_untranslated=False):
    """Compute recommendations for each eligible post in the requested language."""
    all_posts = list(all_posts)
    if lang is None:
        if not all_posts:
            return {}
        lang = getattr(all_posts[0], "default_lang", None)
        if lang is None:
            lang = all_posts[0].config["DEFAULT_LANG"]

    posts = [
        post for post in all_posts
        if post.is_post and post.use_in_feeds
        and (include_untranslated or post.is_translation_available(lang))
    ]
    result = {post: [] for post in posts}
    if count <= 0 or len(posts) < 2:
        return result

    profiles, tag_idf, body_df, body_idf = build_profiles(posts, lang, cache_dir)
    scored = [[] for _ in posts]
    pairs = _candidate_pairs(
        profiles, tag_idf, body_df, body_idf, candidate_limit
    )

    for i, j in pairs:
        score = _score(profiles[i], profiles[j], tag_idf)
        if score >= MIN_SCORE:
            scored[i].append((score, j))
            scored[j].append((score, i))

    titles = [normalize(post.title(lang)) for post in posts]
    urls = [post.permalink(lang) for post in posts]
    for i, entries in enumerate(scored):
        # Consider all scored matches, not just count * 5: duplicate suppression
        # should not hide available alternative recommendations.
        ranked = sorted(entries, key=lambda item: (
            -item[0], titles[item[1]], urls[item[1]],
        ))
        selected = []
        for _, j in ranked:
            if any(_near_duplicate(profiles[j], profiles[k]) for k in selected):
                continue
            selected.append(j)
            if len(selected) == count:
                break
        result[posts[i]] = [posts[j] for j in selected]

    return result


def find_related(post, all_posts, count=5, lang=None, cache_dir=None):
    """Compatibility wrapper for the original single-post API."""
    if lang is None:
        lang = getattr(post, "default_lang", None)
        if lang is None:
            lang = post.config["DEFAULT_LANG"]
    return find_all_related(
        all_posts, count=count, lang=lang, cache_dir=cache_dir
    ).get(post, [])
