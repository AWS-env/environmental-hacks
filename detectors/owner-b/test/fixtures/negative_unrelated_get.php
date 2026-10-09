<?php
/**
 * Negative Regression Fixture 2: Unrelated object's get() method
 * $request->get() is an HTTP request parameter lookup, not a cache lookup.
 */
function handleHttpRequest($request, $db) {
    $sql = "SELECT * FROM products WHERE status = 'active'";
    if ($page = $request->get('page')) {
        return "Page: " . $page;
    }
    return $db->query($sql);
}
