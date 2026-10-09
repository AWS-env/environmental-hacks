<?php
/**
 * Negative Fixture 5: Unrelated comments and UI labels
 */
// SELECT * FROM users WHERE active = 1 (this is a comment)
function buildDropdown($cache) {
    $label = "SELECT a country from the dropdown";
    if ($cached = $cache->get("countries")) {
        return $cached;
    }
    $options = ["USA", "Canada", "UK"];
    return $label . ": " . implode(", ", $options);
}
