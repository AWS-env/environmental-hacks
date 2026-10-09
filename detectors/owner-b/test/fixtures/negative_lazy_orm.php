<?php
/**
 * Negative Fixture 4: Lazy ORM query builder
 * ORM query builders are lazy specifications, not direct SQL strings.
 */
function getPostsWithOrm($cache) {
    $query = Post::where('status', 'published')->orderBy('created_at', 'desc');
    if ($cached = $cache->get('published_posts')) {
        return $cached;
    }
    return $query->get();
}
