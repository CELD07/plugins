def tags_similarity(post_a, post_b, lang):
    tags_a = set(post_a.tags_for_language(lang))
    tags_b = set(post_b.tags_for_language(lang))

    if not (tags_a and tags_b):
        return 0.0

    return 2.0 * len(tags_a & tags_b) / (len(tags_a) + len(tags_b))


def find_related(post, all_posts, count=5, lang=None):
    scored = []

    for other in all_posts:
        if other.source_path == post.source_path:
            continue

        if lang and not other.is_translation_available(lang):
            continue

        score = tags_similarity(post, other, lang)

        if score > 0:
            scored.append((other, score))

    scored.sort(key=lambda x: (-x[1], x[0].title(lang).casefold()))

    return [other for other, _ in scored[:count]]
