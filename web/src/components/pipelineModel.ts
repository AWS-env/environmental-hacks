// Shared pixel layout keeps GPU particles, connectors, and labels aligned.
export const PIPELINE_NODES = [
  { id: "github", title: "GitHub", detail: "Repository · push webhook", x: .06, y: .47 },
  { id: "api", title: "API Gateway", detail: "Read-only intake", x: .205, y: .47 },
  { id: "queue", title: "SQS", detail: "Scan queue", x: .35, y: .47 },
  { id: "workflow", title: "Step Functions", detail: "Ingest → detect → score → report", x: .495, y: .47 },
  { id: "hub", title: "Findings Hub", detail: "EventBridge → DynamoDB", x: .64, y: .47 },
  { id: "estimate", title: "Estimate Engine", detail: "Cached keys · model version · materiality · range + provenance", x: .785, y: .47 },
  { id: "dashboard", title: "Dashboard", detail: "Recommendations + reports", x: .93, y: .47 },
  { id: "ci", title: "Client CI", detail: "Optional profiler / EXPLAIN artifact", x: .06, y: .14 },
  { id: "aws", title: "Client AWS", detail: "Read-only metrics · logs · traces", x: .205, y: .14 },
  { id: "static", title: "Static Scan", detail: "Lambda · Semgrep / tree-sitter", x: .35, y: .80 },
  { id: "parser", title: "Artifact Parser", detail: "Lambda · S3 artifacts", x: .495, y: .80 },
  { id: "telemetry", title: "Telemetry Analyzer", detail: "Lambda · CloudWatch / X-Ray", x: .64, y: .80 },
  { id: "scheduler", title: "Scheduler", detail: "Nightly / weekly · changed inputs", x: .785, y: .80 },
  { id: "alerts", title: "SNS", detail: "Budget alerts", x: .93, y: .80 },
] as const;
export const PIPELINE_EDGES = [
  ["github", "api"], ["ci", "api"], ["aws", "api"], ["api", "queue"],
  ["queue", "workflow"], ["workflow", "static"], ["workflow", "parser"],
  ["workflow", "telemetry"], ["static", "hub"], ["parser", "hub"],
  ["telemetry", "hub"], ["hub", "estimate"], ["hub", "dashboard"],
  ["estimate", "dashboard"], ["estimate", "scheduler"], ["scheduler", "estimate"], ["estimate", "alerts"],
] as const;
export const PIPELINE_ROUTES = [
  ["github", "api", "queue", "workflow", "static", "hub", "estimate", "dashboard"],
  ["ci", "api", "queue", "workflow", "parser", "hub", "estimate", "dashboard"],
  ["aws", "api", "queue", "workflow", "telemetry", "hub", "estimate", "scheduler", "estimate", "alerts"],
];
export const PIPELINE_STEPS = PIPELINE_ROUTES.flatMap((route, routeIndex) => route.map((id, index) => ({
  id, next: route[index + 1] || id, routeIndex,
})));
export const MORPH_SECONDS = 2.4;
export const STEP_SECONDS = 4.2;
export function pipelineLayout(width: number, height: number) {
  const compact = width < 900;
  const left = compact ? 88 : 112, right = compact ? 20 : 44;
  const top = compact ? 205 : 225, bottom = compact ? 120 : 170;
  const boardWidth = width - left - right, boardHeight = Math.max(height - top - bottom, 370);
  const size = compact ? Math.min(boardWidth / 6, 38) : Math.min(boardWidth / 18, 68);
  const nodes = PIPELINE_NODES.map((node, i) => ({ ...node,
    px: left + boardWidth * (compact ? ((i % 3) + .5) / 3 : node.x),
    py: top + boardHeight * (compact ? (Math.floor(i / 3) + .3) / 5 : node.y),
  }));
  return { nodes, size, compact };
}
export function nodeIndex(id: string) { return PIPELINE_NODES.findIndex(node => node.id === id); }
