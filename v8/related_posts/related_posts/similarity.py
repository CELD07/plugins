"""Related-post recommendations: Unicode n-grams, TF-IDF and candidate indexing."""

from collections import Counter, defaultdict
import gzip
import hashlib
import heapq
import json
import math
from pathlib import Path
import unicodedata


# These defaults can be tuned without adding language-specific stopword lists.
BODY_LIMIT = 20_000
NGRAM_SIZE = 3
MIN_SCORE = 0.15
EXACT_SEARCH_LIMIT = 150
CANDIDATE_LIMIT = 200
BODY_INDEX_FEATURES = 96
MAX_BODY_DF_RATIO = 0.20
CACHE_VERSION = 3


def normalize(text):
    return unicodedata.normalize("NFKC", text).casefold().strip()


def grams(text):
    """Language-independent Unicode character n-grams (with repetitions)."""
    cleaned = "".join(
        ch if ch.isalnum() or unicodedata.category(ch).startswith("M") else " "
        for ch in normalize(text)
    )
    cleaned = " ".join(cleaned.split())
    if not cleaned:
        return []
    if len(cleaned) < NGRAM_SIZE:
        return [cleaned]
    return [cleaned[i:i + NGRAM_SIZE]
            for i in range(len(cleaned) - NGRAM_SIZE + 1)]


def jaccard(a, b):
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def cosine(a, b):
    """Dot product of already normalized sparse vectors."""
    if len(a) > len(b):
        a, b = b, a
    return sum(value * b.get(term, 0.0) for term, value in a.items())


def weighted_jaccard(a, b, idf):
    union = a | b
    if not union:
        return 0.0
    return (sum(idf[t] for t in a & b) /
            sum(idf[t] for t in union))


def _cache_filename(cache_dir, lang):
    if cache_dir is None:
        return None
    safe_lang = hashlib.sha256(lang.encode("utf-8")).hexdigest()[:12]
    return Path(cache_dir) / "related_posts" / f"{safe_lang}.json.gz"


def _load_cache(filename):
    if filename is None or not filename.is_file():
        return {}
    try:
        with gzip.open(filename, "rt", encoding="utf-8") as stream:
            data = json.load(stream)
        if data.get("version") == CACHE_VERSION:
            return data.get("posts", {})
    except (OSError, ValueError, TypeError, EOFError):
        pass  # Treat corrupted/outdated cache as a cache miss.
    return {}


def _save_cache(filename, items):
    if filename is None:
        return
    filename.parent.mkdir(parents=True, exist_ok=True)
    temp = filename.with_suffix(".tmp")
    try:
        with gzip.open(temp, "wt", encoding="utf-8", compresslevel=3) as out:
            json.dump({"version": CACHE_VERSION, "posts": items}, out,
                      ensure_ascii=False, separators=(",", ":"))
        temp.replace(filename)
    finally:
        if temp.exists():
            temp.unlink()


def _body_counts(post, lang, old):
    """Use the source text to invalidate a persisted body profile safely."""
    source = post.source(lang)
    title = post.title(lang)
    tags = tuple(sorted(normalize(tag) for tag in post.tags_for_language(lang)))
    # Include known fragment dependencies (such as included source files).
    dependencies = []
    for path in sorted(set(post.fragment_deps(lang))):
        try:
            stat = Path(path).stat()
            dependencies.append((path, stat.st_size, stat.st_mtime_ns))
        except OSError:
            dependencies.append((path, None, None))

    signature = hashlib.sha256(
        repr((source, title, tags, dependencies, BODY_LIMIT, NGRAM_SIZE))
        .encode("utf-8")
    ).hexdigest()

    if old and old.get("signature") == signature:
        return Counter(old["body"]), old

    # post.text() can read a stale fragment; compile the changed source first.
    post.compile(lang)
    body = post.text(lang=lang, strip_html=True, show_read_more_link=False)
    counts = Counter(grams(body[:BODY_LIMIT]))
    return counts, {"signature": signature, "body": dict(counts)}


def build_profiles(posts, lang, cache_dir=None):
    """Return normalized features, and persist body token counts per source."""
    filename = _cache_filename(cache_dir, lang)
    old = _load_cache(filename)
    new = {}
    raw = []
    body_df = Counter()
    tag_df = Counter()

    for post in posts:
        key = post.translated_source_path(lang)
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
    body_idf = {term: 1.0 + math.log((n + 1) / (df + 1))
                for term, df in body_df.items()}
    tag_idf = {tag: 1.0 + math.log((n + 1) / (df + 1))
               for tag, df in tag_df.items()}
    profiles = []

    for post, tags, title, counts in raw:
        vector = {term: (1 + math.log(freq)) * body_idf[term]
                  for term, freq in counts.items()}
        norm = math.sqrt(sum(weight ** 2 for weight in vector.values()))
        if norm:
            vector = {term: weight / norm for term, weight in vector.items()}
        profiles.append((post, tags, title, vector))

    return profiles, tag_idf, body_df, body_idf


def _candidate_pairs(profiles, tag_idf, body_df, body_idf,
                     candidate_limit=CANDIDATE_LIMIT):
    """Find promising unordered pairs via inverted indexes."""
    n = len(profiles)
    if n <= EXACT_SEARCH_LIMIT:
        return {(i, j) for i in range(n) for j in range(i + 1, n)}

    by_tag = defaultdict(set)
    by_title = defaultdict(set)
    by_body = defaultdict(set)
    body_tokens = []
    max_df = max(3, int(n * MAX_BODY_DF_RATIO))

    for i, (_, tags, title, body) in enumerate(profiles):
        for tag in tags:
            by_tag[tag].add(i)
        for term in title:
            by_title[term].add(i)

        choices = heapq.nlargest(
            BODY_INDEX_FEATURES,
            (term for term in body if 2 <= body_df[term] <= max_df),
            key=lambda term: (body[term] * body_idf[term], term),
        )
        body_tokens.append(choices)
        for term in choices:
            by_body[term].add(i)

    pairs = set()
    for i, (_, tags, title, body) in enumerate(profiles):
        evidence = defaultdict(float)
        for tag in tags:
            for j in by_tag[tag]:
                if j != i:
                    evidence[j] += 3.0 * tag_idf[tag]
        for term in title:
            for j in by_title[term]:
                if j != i:
                    evidence[j] += 0.15
        for term in body_tokens[i]:
            for j in by_body[term]:
                if j != i:
                    evidence[j] += body_idf[term] * body[term]

        for j, _ in heapq.nlargest(candidate_limit, evidence.items(),
                                  key=lambda item: (item[1], -item[0])):
            pairs.add((min(i, j), max(i, j)))

    return pairs


def _score(a, b, tag_idf):
    _, tags_a, title_a, body_a = a
    _, tags_b, title_b, body_b = b
    return (0.4 * weighted_jaccard(tags_a, tags_b, tag_idf)
            + 0.2 * jaccard(title_a, title_b)
            + 0.4 * cosine(body_a, body_b))


def _near_duplicate(a, b):
    """Avoid listing several near-identical articles as recommendations."""
    return (bool(a[3]) and bool(b[3])
            and cosine(a[3], b[3]) > 0.94
            and jaccard(a[2], b[2]) > 0.70)


def find_all_related(all_posts, count=5, lang=None, cache_dir=None,
                     candidate_limit=CANDIDATE_LIMIT):
    """Build all recommendations in one pass; each unordered pair is scored once."""
    posts = [p for p in all_posts
             if p.is_post and p.use_in_feeds and p.is_translation_available(lang)]
    result = {p: [] for p in posts}
    if count <= 0 or len(posts) < 2:
        return result

    profiles, tag_idf, body_df, body_idf = build_profiles(posts, lang, cache_dir)
    scored = [[] for _ in posts]
    pairs = _candidate_pairs(profiles, tag_idf, body_df, body_idf,
                             candidate_limit)

    for i, j in pairs:
        score = _score(profiles[i], profiles[j], tag_idf)
        if score >= MIN_SCORE:
            scored[i].append((score, j))
            scored[j].append((score, i))

    for i, entries in enumerate(scored):
        # Limit the list to a small pool; nsmallest gives descending score
        # and deterministic title/URL tie-breaking without sorting everything.
        pool = heapq.nsmallest(
            count * 5,
            entries,
            key=lambda item: (
                -item[0], normalize(profiles[item[1]][0].title(lang)),
                profiles[item[1]][0].permalink(lang),
            ),
        )
        selected = []
        for _, j in pool:
            if any(_near_duplicate(profiles[j], profiles[k]) for k in selected):
                continue
            selected.append(j)
            if len(selected) == count:
                break
        result[posts[i]] = [posts[j] for j in selected]

    return result


def find_related(post, all_posts, count=5, lang=None, cache_dir=None):
    """Compatibility with the original single-post API."""
    if lang is None:
        lang = post.default_lang
    return find_all_related(all_posts, count, lang, cache_dir).get(post, [])
