// One shared graph defines labels, directed edges, particle routes, and layout.
export const PIPELINE_NODES = [
  { id: 'browser', title: 'User Browser', detail: 'HTTPS · dashboard + API requests', x: .035, y: .35 },
  { id: 'edge', title: 'CloudFront', detail: 'Global edge · optional AWS WAF', x: .17, y: .35 },
  { id: 'hosting', title: 'Amplify Hosting', detail: 'Next.js · SSR / static · eu-north-1', x: .31, y: .04 },
  { id: 'api', title: 'API Gateway HTTP API', detail: 'Cognito JWT · GitHub webhook route', x: .31, y: .44 },
  { id: 'webhook', title: 'Webhook Receiver', detail: 'Ingest Lambda · enqueue scans', x: .45, y: .22 },
  { id: 'dashboardApi', title: 'Dashboard API', detail: 'Ingest Lambda · presign + report readback', x: .45, y: .46 },
  { id: 'oauth', title: 'OAuth Connect', detail: 'Ingest Lambda · connect GitHub app', x: .45, y: .70 },
  { id: 'queue', title: 'SQS scan-q', detail: 'Scan queue · dead-letter queue', x: .59, y: .22 },
  { id: 'artifacts', title: 'S3 Artifacts', detail: 'Presigned upload · read-only after upload', x: .59, y: .46 },
  { id: 'secrets', title: 'Secrets Manager', detail: 'Store GitHub app key', x: .59, y: .70 },
  { id: 'scheduler', title: 'EventBridge Scheduler', detail: 'Nightly / weekly scan trigger', x: .73, y: .04 },
  { id: 'workflow', title: 'Step Functions Express', detail: 'Ingest → detect → merge → score → report → notify', x: .73, y: .38 },
  { id: 'static', title: 'Static Scan', detail: 'Lambda · Semgrep / tree-sitter + config', x: .90, y: .04 },
  { id: 'parser', title: 'Artifact Parser', detail: 'Lambda · S3 JSON / logs · parse only', x: .90, y: .26 },
  { id: 'telemetry', title: 'Telemetry Reader', detail: 'Lambda · STS AssumeRole → client project · read only', x: .90, y: .48 },
  { id: 'estimate', title: 'Estimate Engine', detail: 'Lambda · input-key cache · materiality gate + range', x: .90, y: .70 },
  { id: 'hub', title: 'EventBridge Bus', detail: 'findings-hub', x: .73, y: .90 },
  { id: 'findings', title: 'DynamoDB Findings', detail: 'Hub findings · dashboard readback', x: .59, y: .90 },
  { id: 'history', title: 'DynamoDB Estimates', detail: 'Estimate history · dashboard readback', x: .45, y: .90 },
  { id: 'alerts', title: 'SNS', detail: 'Budget / threshold alerts', x: .31, y: .90 },
  { id: 'notifications', title: 'Notifications', detail: 'Email / Slack / webhook', x: .17, y: .90 },
] as const;
export const PIPELINE_EDGES = [
  ['browser', 'edge'], ['edge', 'hosting'], ['hosting', 'edge'], ['edge', 'browser'],
  ['edge', 'api'], ['api', 'webhook'], ['api', 'dashboardApi'], ['api', 'oauth'],
  ['webhook', 'queue'], ['dashboardApi', 'artifacts'], ['oauth', 'secrets'],
  ['queue', 'workflow'], ['artifacts', 'workflow'], ['scheduler', 'workflow'],
  ['workflow', 'static'], ['workflow', 'parser'], ['workflow', 'telemetry'], ['workflow', 'estimate'],
  ['static', 'hub'], ['parser', 'hub'], ['telemetry', 'hub'], ['estimate', 'hub'],
  ['hub', 'findings'], ['hub', 'history'], ['history', 'alerts'], ['alerts', 'notifications'],
  ['findings', 'dashboardApi'], ['history', 'dashboardApi'], ['dashboardApi', 'edge'],
] as const;
export const PIPELINE_ROUTE_LABELS = [
  'Dashboard delivery', 'Static analysis', 'Artifact analysis', 'Telemetry analysis',
  'Estimate + report readback', 'GitHub app connection', 'Scheduled scan + alerts',
];
export const PIPELINE_ROUTES = [
  ['browser', 'edge', 'hosting', 'edge', 'browser'],
  ['browser', 'edge', 'api', 'webhook', 'queue', 'workflow', 'static', 'hub', 'findings', 'dashboardApi', 'edge', 'browser'],
  ['browser', 'edge', 'api', 'dashboardApi', 'artifacts', 'workflow', 'parser', 'hub', 'findings', 'dashboardApi', 'edge', 'browser'],
  ['browser', 'edge', 'api', 'webhook', 'queue', 'workflow', 'telemetry', 'hub', 'findings', 'dashboardApi', 'edge', 'browser'],
  ['browser', 'edge', 'api', 'webhook', 'queue', 'workflow', 'estimate', 'hub', 'history', 'dashboardApi', 'edge', 'browser'],
  ['browser', 'edge', 'api', 'oauth', 'secrets'],
  ['scheduler', 'workflow', 'estimate', 'hub', 'history', 'alerts', 'notifications'],
];
export const PIPELINE_STEPS = PIPELINE_ROUTES.flatMap((route, routeIndex) => route.map((id, index) => ({
  id, next: route[index + 1] || id, routeIndex,
})));
export const MORPH_SECONDS = 2.4;
export const STEP_SECONDS = 4.2;
// Separate simulated requests may overlap; every transfer follows a graph edge.
export function pipelineActivity(age: number) {
  let offset = 0;
  return PIPELINE_ROUTES.map((route, routeIndex) => {
    const startDelay = routeIndex * 1.35;
    const durations = route.map((_, index) => 2.8 + (Math.sin((index + 1) * 13.7 + routeIndex * 7.3) * .5 + .5) * 1.8);
    const cycle = durations.reduce((sum, duration) => sum + duration, 0);
    let remaining = Math.max(0, age - startDelay) % (cycle + 2.5);
    const resting = remaining >= cycle;
    if (resting) remaining = 0;
    let index = 0;
    while (index < route.length - 1 && remaining >= durations[index]) remaining -= durations[index++];
    const step = offset + index;
    offset += route.length;
    return { step, stage: PIPELINE_STEPS[step], phase: age < startDelay || resting ? -1 : remaining / durations[index] * STEP_SECONDS };
  });
}
export function pipelineLayout(width: number, height: number) {
  const compact = width < 900;
  const left = compact ? 88 : 112, right = compact ? 20 : 44;
  const top = compact ? 205 : 235, bottom = compact ? 155 : 190;
  const boardWidth = width - left - right, boardHeight = Math.max(height - top - bottom, 350);
  const size = compact ? Math.min(boardWidth / 11, 26) : Math.min(boardWidth / 28, boardHeight / 12, 46);
  const nodes = PIPELINE_NODES.map((node, i) => ({ ...node,
    px: left + boardWidth * (compact ? ((i % 3) + .5) / 3 : node.x),
    py: top + boardHeight * (compact ? (Math.floor(i / 3) + .25) / 7 : node.y),
  }));
  return { nodes, size, compact };
}
export function nodeIndex(id: string) { return PIPELINE_NODES.findIndex(node => node.id === id); }
