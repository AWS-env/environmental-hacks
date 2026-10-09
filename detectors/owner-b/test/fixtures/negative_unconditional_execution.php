<?php
/**
 * Negative Fixture 2: Unconditional DB execution
 * The query is always sent to the database.
 */
function recordMetric($userId, $db, $cache) {
    $sql = "INSERT INTO metrics (user_id, ts) VALUES (" . (int)$userId . ", NOW())";
    $db->query($sql);
    $cache->set("metric:" . $userId, true);
    return true;
}
