<?php
/**
 * Negative Fixture 3: SQL string escapes to external helper
 * The query is used for auditing prior to the cache check.
 */
function fetchWithAudit($userId, $db, $cache) {
    $sql = "SELECT * FROM secrets WHERE user_id = " . (int)$userId;
    audit_log_query($sql);
    if ($data = $cache->get("secret:" . $userId)) {
        return $data;
    }
    return $db->query($sql);
}
