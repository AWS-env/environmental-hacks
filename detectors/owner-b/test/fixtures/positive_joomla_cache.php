<?php
/**
 * Positive Fixture 1: Joomla-style direct database access
 * Matches Shao et al. AP-34 (Figure 11)
 */
class ArticleModel {
    public function getArticle($id, $db, $cache) {
        $query = "SELECT a.*, c.title AS category FROM #__content AS a LEFT JOIN #__categories AS c ON a.catid = c.id WHERE a.id = " . (int)$id;
        $cacheKey = "article_" . (int)$id;
        if ($data = $cache->get($cacheKey)) {
            return $data;
        }
        $db->setQuery($query);
        $data = $db->loadObjectList();
        $cache->set($cacheKey, $data);
        return $data;
    }
}
