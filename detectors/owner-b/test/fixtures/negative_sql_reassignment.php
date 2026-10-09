<?php
/**
 * Negative Regression Fixture 3: SQL-variable reassignment before DB execution
 * $sql is reassigned to another query before reaching the DB call.
 */
function fetchAccountInfo($userId, $cache, $db) {
    $sql = "SELECT * FROM users WHERE id = " . (int)$userId;
    if ($cached = $cache->get("user:" . $userId)) {
        return $cached;
    }
    // Reassigned to a completely different query before execution
    $sql = "SELECT id, balance FROM accounts WHERE user_id = " . (int)$userId;
    return $db->query($sql);
}
