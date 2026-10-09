<?php
/**
 * Negative Regression Fixture 1: Cache-miss early return
 * The early return occurs on cache MISS, so DB execution is not bypassed on cache hit.
 */
function getCustomerData($id, $cache, $db) {
    $sql = "SELECT * FROM customers WHERE id = " . (int)$id;
    $cached = $cache->get("cust:" . $id);
    if ($cached === false) {
        // Cache miss: returns early. No cache-hit bypass occurred.
        return null;
    }
    return $db->query($sql);
}
