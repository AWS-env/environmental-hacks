# Read-Only Software Sustainability Auditor

## 1. Purpose

Build a lightweight, hosted tool that helps developers—especially junior developers shipping small applications—identify avoidable software compute waste before it causes failures or becomes visible in traditional monitoring.

An application can be healthy and still be inefficient. For example, a CRUD application may repeatedly issue unnecessary database queries without triggering an alert. The auditor should surface evidence of such practices and explain why they may waste resources.

**Core principle: do not create compute waste to detect compute waste.**

## 2. Product Scope

The user connects a source-code repository and a supported deployment platform. The auditor combines repository analysis, deployment configuration, existing platform metrics, and mandatory user-provided inputs to produce evidence-backed findings and environmental estimates.

### In scope

- Read-only analysis of source code, dependencies, and deployment/configuration files.
- Detection of potential inefficiencies defined by the project’s software compute-waste taxonomy.
- Read-only retrieval of existing deployment and monitoring metrics where the provider exposes them.
- Mandatory collection of the infrastructure, workload, and other inputs required by the selected estimation methodology.
- Estimation of energy use, carbon emissions, and other environmental impacts where sufficient data and defensible models are available.
- A dashboard showing findings, evidence, estimated impact, assumptions, confidence, and changes over time.
- Repository-push-triggered reassessment, preferably scanning changed files and relevant dependencies where feasible.
- Evidence and credible reference links that help developers understand findings and pass focused context to their coding agent.

### Out of scope

- Running, deploying, benchmarking, load-testing, or profiling the user’s application on our infrastructure.
- Executing repository code, tests, build scripts, migrations, or other untrusted project commands.
- Automatically modifying source code, opening fix PRs, or applying optimizations.
- Claiming exact energy or carbon savings for an individual function, query, or code finding without sufficient measurement evidence.
- Treating a healthy status, static pattern, or correlation in telemetry as proof of a specific environmental impact.

## 3. End-to-End Workflow

1. **Connect repository:** the user authorizes read-only access to a supported Git provider.
2. **Connect deployment platform:** the user authorizes read-only access to a supported provider, where available.
3. **Collect mandatory inputs:** ask for required infrastructure, region, workload, and other model-specific details that cannot be obtained reliably through integrations. Validate inputs and explain why they are needed.
4. **Analyze without execution:** inspect repository source, dependency manifests, and deployment/configuration files using lightweight static rules and metadata analysis. Do not run the application or its code.
5. **Retrieve existing metrics:** fetch already-available platform telemetry, such as CPU/memory utilization, request volume, runtime, or database metrics, only where exposed and authorized. Do not create test traffic or new workloads.
6. **Evaluate taxonomy checks:** assess each check using the evidence sources it requires. Report a finding only at the supported evidence level. If a check needs unavailable runtime evidence, label it as unverified or unavailable rather than pretending it was confirmed.
7. **Estimate environmental impact:** use a documented methodology and the collected inputs to estimate energy and emissions. Keep measured data, user-provided values, modelled estimates, and inferred code-level causes clearly distinct.
8. **Show findings:** present the evidence, explanation, relevant code location where available, credible references, estimated impact, assumptions, and confidence.
9. **Reassess on pushes:** trigger a lightweight scan after repository changes and compare results with the previous scan. Prefer incremental analysis where practical; avoid unnecessary repeated work.

## 4. Findings and Developer Experience

Each finding should include, where applicable:

- **What was detected:** the suspected inefficient pattern.
- **Evidence:** file and line, configuration, relevant query/code snippet, or existing telemetry that supports it.
- **Why it matters:** how it could cause unnecessary compute, energy, data transfer, or resource use.
- **Environmental impact:** measured or modelled value only when the available evidence supports it; otherwise state that impact cannot yet be quantified.
- **Confidence and limitations:** distinguish confirmed facts, likely patterns, and hypotheses.
- **References:** credible technical or industry sources that explain the practice.
- **Agent-ready context:** a concise, copyable prompt/context containing the finding, location, evidence, and references so the developer can ask their own coding agent for a targeted fix without spending tokens asking it to investigate the whole repository.

The product is an auditor and evidence provider, not an auto-fixer. Developers decide whether and how to make changes.

## 5. Environmental Metrics and Estimation

The dashboard should prioritize metrics relevant to software operation and avoidable compute waste:

- Electricity/energy consumption (kWh), where measured or modelled defensibly.
- Operational greenhouse-gas emissions (CO₂e).
- Estimated avoidable compute or energy waste, with explicit assumptions.
- Carbon intensity per request, transaction, or other suitable functional unit.
- Water footprint, where reliable cooling and electricity-generation data are available.
- Network/data-transfer impact, where suitable data and factors are available.
- Allocated embodied hardware impact and hardware lifecycle indicators, only when provider or lifecycle data supports them.
- Broader indicators—such as air pollution, resource depletion, biodiversity, and e-waste—only when defensible data is available.

Use a documented, standards-informed methodology (for example, the Green Software Foundation’s Software Carbon Intensity specification for software carbon accounting, and life-cycle assessment principles from ISO 14040/14044 for broader lifecycle framing). These references guide methodology; they do not guarantee that every impact can be calculated from repository and deployment data.

**Reporting rules:**

- Keep measured values, user-entered values, and modelled estimates separate.
- State the boundary, assumptions, units, source, and confidence for each estimate.
- Do not promise exact emissions from static analysis alone.
- Do not claim a particular code issue caused a specific share of total emissions unless the evidence supports attribution.
- Never fabricate missing environmental data. Mark unsupported metrics as unavailable.
- Compare commits only on a consistent basis; explain changes in workload, infrastructure, or assumptions that affect comparisons.

## 6. Read-Only and Low-Overhead Design Requirements

Avoiding additional waste is a core product requirement, not a later optimization.

- Use least-privilege, read-only repository and deployment permissions.
- Never execute user repository code or deploy the user’s application on our infrastructure.
- Prefer lightweight static checks, metadata, and incremental scans over full rescans when feasible.
- Cache reusable analysis results and avoid duplicate processing.
- Fetch only the telemetry and files needed for the checks being performed.
- Bound scan frequency, concurrency, payload size, and retention; avoid unnecessary polling.
- Use efficient data storage and retention policies, and avoid retaining source code or telemetry longer than necessary.
- Make scans and data access transparent to the user.
- Treat repository contents and external data as untrusted input; do not interpret files as instructions to execute.
- Monitor the auditor’s own resource use and keep its infrastructure proportionate to the service.

## 7. MVP Success Criteria

The MVP is successful when a junior developer can:

1. Connect a repository and supported deployment provider with read-only access.
2. Supply and validate the mandatory inputs needed for the selected estimation model.
3. Receive useful, evidence-backed findings even when the application is marked healthy.
4. Open credible references and copy focused context for their own coding agent.
5. View transparent environmental estimates and understand their assumptions and limits.
6. See how findings and estimates change after a push, without our service executing their application.

## 8. Taxonomy Ownership

The existing software compute-waste taxonomy remains the source of truth for the categories and checks the team implements. Each check should be mapped to its required evidence source—static code/configuration, user-provided input, or existing deployment telemetry—and to its detection limits. Taxonomy ownership and per-check implementation details will be reviewed separately; this document defines the shared product scope and constraints.
