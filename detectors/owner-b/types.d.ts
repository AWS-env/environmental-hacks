/**
 * Types and schema for Owner B detectors (Data & external I/O).
 * Check: DB-34 (Unnecessary query construction)
 */

export interface SourceLocation {
  line: number;
  column?: number;
  snippet?: string;
}

export interface FindingLocations {
  construction: SourceLocation;
  cache_check: SourceLocation;
  db_setup?: SourceLocation;
  db_execution: SourceLocation;
}

export interface FindingEvidence {
  query_variable: string;
  sql_preview: string;
  cache_api: string;
  setup_api?: string;
  setup_line?: number;
  db_api: string;
  construction_line: number;
  cache_check_line: number;
  db_execution_line: number;
}

export interface Finding {
  check_id: 'DB-34';
  rule_id: 'R1';
  file_path: string;
  line_number: number;
  column_number?: number;
  locations: FindingLocations;
  bypass_explanation: string;
  affected_resource: 'CPU';
  supported_language: string;
  supported_apis: string[];
  confidence: 'High' | 'Medium' | 'Low';
  limitations: string;
  recommendation: string;
  reference: string;
  evidence: FindingEvidence;
}

export interface ScanResult {
  file_path: string;
  scanned: boolean;
  findings: Finding[];
  error?: string;
}

export type ScanSourceFn = (sourceText: string, filePath?: string) => Finding[];
export type ScanFileFn = (filePath: string) => Finding[];
