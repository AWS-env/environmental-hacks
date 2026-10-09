<?php
/**
 * Positive Fixture 2: PDO direct query access
 */
function fetchUserProfile($userId, $pdo, $cache) {
    $sql = "SELECT id, username, email FROM users WHERE id = " . (int)$userId;
    $cachedProfile = $cache->get("user:" . $userId);
    if ($cachedProfile !== false) {
        return $cachedProfile;
    }
    $stmt = $pdo->query($sql);
    return $stmt;
}
