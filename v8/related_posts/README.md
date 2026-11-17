# Related Posts

A Nikola plugin that automatically recommends related posts based on their tags, titles, and content. It also allows authors to manually select related posts or disable recommendations for individual posts.

## Features

- **Automatic recommendations:** Finds related posts using a combination of tag similarity, title similarity, and content similarity.
- **Multilingual support:** Calculates automatic recommendations independently for each language.
- **Manual recommendations:** Allows authors to specify exactly which posts should appear in the related posts section.
- **Disable recommendations:** Authors can disable the section for individual posts.
- **Duplicate filtering:** Avoids recommending multiple posts with nearly identical content.
- **Caching:** Stores processed content profiles to speed up subsequent builds.
- **Configurable:** Allows users to control the maximum number of recommendations and section titles.

## Installation

Install the plugin using Nikola:

```bash
nikola plugin -i related_posts
```

Alternatively, clone or download the plugin into your site's `plugins` directory:

```text
your_site/
├── conf.py
├── posts/
├── pages/
└── plugins/
    └── related_posts/
        ├── __init__.py
        ├── similarity.py
        └── related_posts.plugin
```

Then rebuild your site:

```bash
nikola build
```

## Configuration

The plugin works with its default settings, but you can customize its behavior in `conf.py`.

### Number of related posts

Set `RELATED_POSTS_COUNT` to control the maximum number of recommendations displayed per post.

```python
RELATED_POSTS_COUNT = 5
```

The default value is `5`. Set it to `0` to disable automatic recommendations.

The value must be a non-negative integer.

### Section titles

You can customize the heading displayed above the related posts section using `RELATED_POSTS_TITLES`.

This setting is a dictionary mapping language codes to titles:

```python
RELATED_POSTS_TITLES = {
    "en": "Related posts",
    "es": "Artículos relacionados",
    "de": "Ähnliche Beiträge",
}
```

If a language is not specified, the default heading is `Related posts`.

## Manual Recommendations

Authors can override automatic recommendations for individual posts using the `related_manual` metadata field.

The field accepts a comma-separated list of post slugs.

For example:

```yaml
title: Introduction to Python
slug: introduction-to-python
related_manual: python-basics, advanced-python, python-projects
```

When this field is specified, the plugin displays the selected posts instead of automatically generated recommendations.

Manual recommendations allow authors to choose any eligible post, including posts written in a different language.

### Disable recommendations for a post

To disable the related posts section for an individual post, set:

```yaml
related_manual: none
```

This overrides automatic recommendations and displays no related posts for that post.

### Missing or invalid slugs

If a specified slug cannot be found, the plugin logs a warning and ignores that entry.

If a post references itself, the reference is ignored.

Duplicate slugs are removed while preserving their original order.

Manual recommendations are limited by `RELATED_POSTS_COUNT`.

## How It Works

The plugin calculates similarity using three complementary signals:

- **Tags:** Weighted Jaccard similarity, taking into account how common each tag is across the site.
- **Titles:** Character n-gram similarity, allowing comparisons across different languages without language-specific tokenizers.
- **Content:** TF-IDF weighted character n-grams combined with cosine similarity.

The final similarity score is calculated as:

\[
S(A,B) = 0.4S_{\text{tags}} + 0.2S_{\text{title}} + 0.4S_{\text{content}}
\]

Posts with scores below the minimum similarity threshold are excluded.

The plugin also uses inverted indexes to reduce the number of candidate pairs that need to be compared and caches processed content profiles between builds.

Automatic recommendations are calculated independently for each language and only consider posts available in that language.

## Requirements

- Nikola 8.x
- Python 3

The similarity algorithm uses Python's standard library and does not require additional third-party Python packages.

## Template Integration

The plugin automatically renders a related posts section at the bottom of blog posts using Nikola's `page_footer` template hook.

It also makes the following variable available in the post rendering context:

```python
related_posts
```

This variable contains the list of related post objects for the current language.

Themes can use this variable to implement their own related posts section if they prefer custom rendering.

## License

MIT License.

## Issues and Contributions

Bug reports, feature requests, and contributions are welcome.

Please report issues through the project's GitHub repository.
