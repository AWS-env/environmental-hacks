# Detection Spec: DB-34 (Unnecessary Query Construction)

**Taxonomy Key:** `DB-34`  
**Layer:** Database access (`database`)  
**Owner:** Owner B (Data & external I/O)  
**Rule:** `R1` (In-process code efficiency / avoid unnecessary computation)  
**Reference:** Shao et al., *Database-Access Performance Antipatterns in Database-Backed Web Applications* (ICSME 2020), Section IV.C (AP-34) & Figure 11.

---

## 1. Specification (Owner to Fill Fields)

### Detection Signal
Eager direct-SQL query string construction before a cache lookup, where a feasible cache-hit path bypasses transmission or execution of that SQL query to the database, resulting in wasted query-construction CPU cycles.

### Detection Tool
AST-based static analyzer (`php-parser`) implemented under `detectors/owner-b/db-34-detector.js`.
- **Supported Language:** PHP (direct database access)
- **Supported Database APIs:** PDO (`$pdo->query`, `$pdo->prepare`), mysqli (`$mysqli->query`, `mysqli_query`), Joomla JDatabase (`$db->setQuery` as setup + `$db->loadObjectList` as execution), WordPress (`$wpdb->query`, `$wpdb->get_results`), procedural (`pg_query`, `mysql_query`).
- **Supported Cache APIs:** PSR-6 / PSR-16 cache interfaces (`$cache->get`, `$cache->fetch`, `$cache->getItem`), Memcached/Redis client lookups (`$memcached->get`, `$redis->get`), Joomla (`$cache->get`), WordPress (`wp_cache_get`), APCu (`apcu_fetch`), and procedural `cache_get`.

### Telemetry Needed
Source code text and file path/commit metadata only.
- **Client IAM Role:** None required (`No — internal scanner role`).
- **Runtime Telemetry:** None needed for static detection. Pure read-only static analysis without executing target application code.

### Report Output Fields
Emits structured findings matching `detectors/owner-b/types.d.ts`:
- `check_id`: `"DB-34"`
- `rule_id`: `"R1"`
- `file_path`: Target file path
- `line_number`: Line of eager query construction
- `locations`:
  - `construction`: Line, column, and snippet of SQL string assembly
  - `cache_check`: Line, column, and snippet of cache lookup
  - `db_setup` *(optional)*: Line, column, and snippet of query setup call (e.g. Joomla `$db->setQuery($query)`)
  - `db_execution`: Line, column, and snippet of database execution call (e.g. `$db->loadObjectList()` or `$pdo->query($sql)`)
- `bypass_explanation`: Clear narrative describing how cache-hit returns before query execution
- `affected_resource`: `"CPU"`
- `supported_language`: `"PHP (direct database access)"`
- `supported_apis`: List of detected APIs
- `confidence`: `"High"` (when AST confirms exact variable flow)
- `recommendation`: `"Check cache first; construct SQL only on the miss path."`
- `reference`: Shao et al. (ICSME 2020) citation

### False-Positive Risk & Controls
- **Cache Receiver Identity:** The receiver of `.get()` is verified to ensure it is a cache instance (`$cache`, `$userCache`, `$memcached`, `$redis`, `Cache::get`, etc.). Non-cache receivers like `$request->get()`, `$input->get()`, or `$params->get()` are explicitly rejected.
- **Cache Branch Polarity:** Distinguishes between cache-hit test conditions (`if ($val = $cache->get())`, `if ($cached !== false)`) and cache-miss test conditions (`if (!$cached)`, `if ($cached === false)`). Early returns on cache-miss conditions do not bypass execution on cache-hit and produce no finding.
- **Variable Reassignment:** Verifies variable identity. If the SQL variable is reassigned to another value before reaching the database execution call, findings are suppressed.
- **Helper Escaping:** If the SQL string is passed into logging, auditing, or custom helper functions prior to the cache check, query construction was not solely for the database call and findings are suppressed.
- **Lazy ORM Query Builders:** Query builder objects (e.g. `Post::where(...)`) do not construct immediate SQL strings. The detector explicitly excludes ORM query builder patterns.

### Detectable (H/M/L)
**Medium** — High confidence for direct SQL strings within bounded local function/method scopes. Cross-procedural delegation, dynamic eval, or complex macros remain outside static bounds.

### Measurable (H/M/L)
**Low** — Avoided CPU cycles and associated energy savings cannot be precisely calculated from static code analysis alone without runtime measurement of query assembly frequency and cache hit rates.

---

## 2. Acceptance Criteria Checklist

- [x] Detector implemented under `detectors/owner-b/`
- [x] Emits an evidence-backed finding with exact line numbers and bypass explanation
- [x] Unit test for the detector covering positive and negative fixtures
- [x] Detection spec fields above filled and recorded beside the detector
