<?php
/**
 * Negative Fixture 1: Optimized pattern - cache checked first
 * SQL is only constructed on miss. No waste occurs.
 */
function getUserProfileOptimized($userId, $pdo, $cache) {
    $cachedProfile = $cache->get("user:" . $userId);
    if ($cachedProfile !== false) {
        return $cachedProfile;
    }
    $sql = "SELECT id, username, email FROM users WHERE id = " . (int)$userId;
    $stmt = $pdo->query($sql);
    return $stmt;
}
