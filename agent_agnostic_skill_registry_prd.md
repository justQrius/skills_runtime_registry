# Product Requirements Document: Agent-Agnostic On-Demand Skill Registry

**Implementation status:** v1.1.0 shipped on 2026-09-08. The repository now
implements the portable manifest, Python and JavaScript SDKs, MCP discovery and
artifact APIs, policy-aware resolution, package conformance, persistent live
imports, tool-handler invocation, workflow loading, and approval-gated exact
execution of Python, shell, and JavaScript entrypoints in throwaway Docker
containers. Marketplace, federation, hosted semantic search, and multi-tenant
production hardening remain roadmap items.

## Overview

This product is a harness-agnostic and agent-agnostic runtime skill registry that allows AI agents to discover, evaluate, fetch, and invoke skills on demand instead of requiring persistent installation ahead of time.[^skills-sh] The concept builds on the observed structure of the Skills ecosystem, which presents skills as reusable capabilities for AI agents, supports many agent environments, and organizes discovery through skills, packs, topics, official listings, audits, docs, and API-oriented navigation.[^skills-sh]

The product goal is to provide a universal capability layer that any compliant agent runtime, orchestration framework, coding harness, or custom agent shell can use without being tightly coupled to a single vendor or toolchain.[^skills-sh] The registry should act as a neutral discovery and delivery plane for procedural knowledge, tool wrappers, and executable capability bundles while preserving portability, trust, and observability.[^skills-sh]

## Problem

Current skill ecosystems often assume a pre-install workflow in which users or developers add capabilities manually before an agent can use them.[^skills-sh] That model works for a narrow, curated set of recurring tasks, but it creates friction when agents need broad long-tail coverage, when skill freshness matters, or when the execution environment changes across products and teams.[^skills-sh]

A second problem is fragmentation across agent environments. The Skills directory explicitly targets a wide set of agent surfaces, including Claude Code, Cursor, Codex, GitHub Copilot, Windsurf, Gemini, Cline, OpenClaw, VS Code, and Zed, which indicates a multi-agent ecosystem rather than a single-runtime product.[^skills-sh] A product designed around on-demand retrieval can unify capability access across these heterogeneous environments by shifting integration from “install per agent” to “resolve via common protocol at runtime.”[^skills-sh]

## Vision

The product will make skills behave like internet-addressable capabilities. An agent should be able to identify a missing capability, query the registry, evaluate trust and compatibility metadata, load the best-fit skill into an isolated execution context, execute it, and optionally cache it for reuse.[^skills-sh]

The long-term vision is a common runtime contract for agent capabilities that works across chat agents, coding agents, browser agents, workflow automations, enterprise copilots, and custom multi-agent systems.[^skills-sh] The product should be as neutral to the harness as package registries are to editors, or as identity providers are to applications.[^skills-sh]

## Goals

- Enable dynamic skill discovery at runtime rather than requiring default installation.[^skills-sh]
- Support many agent environments through a common registry and manifest contract, reflecting the multi-agent availability shown in the Skills directory.[^skills-sh]
- Preserve trust through publisher identity, audit metadata, versioning, and integrity checks, aligning with the directory’s official and audits sections.[^skills-sh]
- Support multiple packaging modes: prompt or knowledge skills, tool-wrapped skills, workflow skills, and executable skills.[^skills-sh]
- Minimize lock-in by making the product harness-agnostic, agent-agnostic, and transport-agnostic.[^skills-sh]
- Provide telemetry so operators can measure skill selection quality, success rate, latency, and fallback behavior.[^skills-sh]

## Non-goals

- Building a proprietary agent runtime tied to one coding IDE or chat interface.
- Requiring all publishers to use one programming language or one workflow framework.
- Acting as a replacement for every package manager, model provider, or tool gateway.
- Automatically granting unbounded permissions to downloaded skills.

## Users

### Primary users

- Agent platform teams building coding agents, support agents, browser agents, or business workflow agents.
- AI infrastructure teams that want a reusable capability layer across multiple products.
- Enterprise AI teams that need governance, allowlists, auditing, and reproducible skill usage.
- Independent developers who want to publish skills once and make them usable across many agents.[^skills-sh]

### Secondary users

- Security and compliance teams reviewing what skills can run and under what permissions.
- Ecosystem maintainers operating official skills, audits, docs, and ranking mechanisms.[^skills-sh]

## Product principles

1. **Portable by default**: no hard dependency on any specific harness, IDE, LLM vendor, or orchestration framework.
2. **Resolvable at runtime**: skills can be discovered and loaded only when needed.
3. **Trust-aware**: every skill decision should account for publisher identity, audit state, compatibility, and version pinning.[^skills-sh]
4. **Observable**: every resolution and execution should be measurable.
5. **Graceful degradation**: when no suitable skill is available, the agent should fail safely or fall back to native reasoning.

## Use cases

### Use case 1: Coding agent capability gap

A coding agent receives a task involving React architecture review. It recognizes that a specialized capability would improve quality, searches the registry, discovers a relevant skill under a React or design topic, verifies compatibility, loads the skill instructions and tool schema, and applies them only for that task.[^skills-sh]

### Use case 2: Enterprise workflow agent

A business automation agent handling procurement or ecommerce operations queries the registry for a niche workflow skill, but enterprise policy only allows official or audited publishers. The resolution layer filters the candidate set, selects an approved skill, and records the decision for governance review.[^skills-sh]

### Use case 3: Multi-agent platform

A company runs several internal agents across support, software engineering, and operations. Instead of curating separate local installations per agent shell, each agent calls the same registry protocol and receives skills formatted for its execution mode.

## Functional requirements

### 1. Skill discovery

The system must provide search and retrieval across skills, packs, topics, publishers, and agent compatibility classes, reflecting the discovery structure exposed in the Skills directory.[^skills-sh] Discovery must support keyword search, topic-based browsing, direct skill ID resolution, popularity ranking, freshness ranking, and policy-constrained search.[^skills-sh]

#### Requirements
- Search by skill name, publisher, tags, topic, agent type, and pack.[^skills-sh]
- Filter by official, audited, trending, hot, or all listings where those metadata signals exist.[^skills-sh]
- Return manifest metadata, compatibility metadata, trust metadata, version info, and declared permissions.
- Support exact match and semantic relevance modes.

### 2. Agent-agnostic manifest contract

Each skill must expose a normalized manifest that can be interpreted by any compliant client, regardless of whether the calling system is a coding harness, chat agent, browser agent, or workflow orchestrator. The manifest is the core portability primitive.

#### Required manifest fields
- Skill ID
- Human-readable name
- Publisher identity
- Version
- Description
- Topics and tags
- Supported execution modes
- Supported agent classes or compatibility descriptors
- Input schema
- Output schema
- Tool dependencies
- Permission requirements
- Trust signals, including official and audit state where available.[^skills-sh]
- Integrity metadata such as content hash or signature
- Caching guidance
- Deprecation or replacement metadata

### 3. Packaging modes

The system must support four packaging modes under one registry contract:
- Instruction skill: procedural guidance or prompt module
- Tool skill: callable tool wrapper with input and output schema
- Workflow skill: multi-step plan or graph definition
- Executable skill: code bundle, container image reference, or sandbox task

Clients may support a subset of modes, but the registry must expose them consistently.

### 4. Runtime resolution

The product must expose a resolution API that accepts task context, requested capability hints, policy constraints, and client capabilities, then returns ranked candidate skills. Resolution should incorporate compatibility, trust, declared success priors, popularity, and publisher preference.[^skills-sh]

#### Resolution inputs
- Natural-language task hint
- Capability tags
- Client or harness capabilities
- Allowed execution modes
- Security policy and allowlists
- Max latency budget
- User or tenant preferences

#### Resolution outputs
- Ranked candidates
- Selection rationale fields
- Required permissions
- Fetch URLs or package references
- Version and integrity metadata
- Fallback guidance

### 5. Skill fetching and loading

Clients must be able to fetch skill artifacts via signed or public URLs, verify integrity, and load the skill into the appropriate runtime boundary. The product must support ephemeral fetch-and-run as the default interaction pattern.

#### Loading behaviors
- Inject instruction skills into the model context window or system layer.
- Bind tool skills into the agent’s callable tool registry for the active session.
- Materialize workflow skills into planner or graph runtimes.
- Execute executable skills inside a sandbox with explicit permission grants.

### 6. Caching and pinning

The system must support optional local or edge caching to reduce latency and dependency on registry availability. Production deployments must be able to pin a version or content hash for reproducibility.

#### Requirements
- TTL-based ephemeral cache
- Tenant-scoped allowlist cache
- Version pinning
- Content hash verification
- Cache invalidation on publisher revocation or audit withdrawal

### 7. Trust, governance, and security

Trust is a first-class requirement because the source ecosystem already distinguishes official listings and security audits.[^skills-sh] The product must let clients apply governance rules before selection and before execution.[^skills-sh]

#### Requirements
- Publisher identity verification
- Official publisher designation where applicable.[^skills-sh]
- Audit metadata ingestion and display.[^skills-sh]
- Policy engine for allow, deny, require-review, or sandbox-only outcomes
- Fine-grained permission model
- Secrets isolation
- Network and filesystem restrictions for executable skills
- Revocation mechanism for compromised skills
- Tenant-level policy enforcement

### 8. Observability

The system must emit telemetry for discovery, selection, load, execution, and fallback events.

#### Core telemetry events
- Search requested
- Candidates returned
- Skill selected
- Skill rejected with reason
- Fetch success or failure
- Integrity check success or failure
- Execution success or failure
- Latency metrics
- Cache hit or miss
- User override or approval event

### 9. Documentation and ecosystem UX

The Skills site prominently exposes docs and API navigation, indicating that developer onboarding and integration guidance are core product surfaces.[^skills-sh] The new product must therefore provide implementation docs, manifest schema docs, client SDK docs, and publisher docs as first-order deliverables.[^skills-sh]

## User stories

- As an agent platform engineer, a client SDK is needed that can search, resolve, fetch, and load skills without depending on one specific agent framework.
- As an enterprise admin, policies are needed to restrict selection to approved publishers and audited skills only.[^skills-sh]
- As a skill publisher, one manifest format is needed so a skill can work across many agent environments listed by the ecosystem.[^skills-sh]
- As an operator, logs and metrics are needed to identify which skills improve success and which create latency or security risk.
- As a runtime developer, a way is needed to declare which execution modes are supported so unsupported skills are excluded during resolution.

## API surface

### Core APIs

1. **Search API**
   - Query skills, topics, packs, publishers
   - Filter by compatibility and trust metadata

2. **Resolve API**
   - Input: task, context, policy, runtime capabilities
   - Output: ranked candidates with rationale

3. **Manifest API**
   - Fetch canonical skill manifest by ID and version

4. **Artifact API**
   - Fetch instruction payload, tool schema, workflow definition, or executable bundle reference

5. **Trust API**
   - Retrieve audit state, signature metadata, publisher verification status, and revocations

6. **Telemetry API**
   - Submit execution outcome and quality signals back to the platform

## Success metrics

### Adoption metrics
- Number of integrated clients or harnesses
- Number of active tenants
- Number of active publishers
- Number of skills with portable manifests

### Runtime metrics
- Skill resolution success rate
- Median resolution latency
- Median fetch latency
- Cache hit rate
- Execution success rate by skill and by client
- Fallback rate when no compatible skill is found

### Trust metrics
- Share of executions using official or audited skills where such metadata exists.[^skills-sh]
- Revocation response time
- Number of blocked unsafe execution attempts

### Ecosystem metrics
- Cross-agent reuse rate per skill
- Average number of distinct agent classes using a published skill
- Publisher retention

## Initial MVP scope and current status

The initial MVP focused on portable manifests, search, resolve, trust-aware filtering, and runtime loading for instruction and tool-wrapped skills. Version 1.1 retains that portable core and extends it with whole-package retrieval, conformance validation, workflow loading, configured tool invocation, and approval-gated exact execution. Executable skills remain the largest security and runtime burden and therefore run only in fresh, network-isolated Docker containers under policy.

### In scope for MVP
- Portable manifest schema v1
- Search API
- Resolve API
- Manifest fetch API
- Official and audit metadata fields where available.[^skills-sh]
- Client SDKs for JavaScript and Python
- Runtime loading for instruction and tool skills
- Local cache with TTL and version pinning
- Basic telemetry

All items above are implemented. Search is deterministic lexical token and
morphology matching; semantic-vector retrieval is not yet implemented.

### Still out of scope
- Shared-host or multi-tenant hardened executable infrastructure
- Revenue sharing and marketplace billing
- Complex social reputation systems
- Multi-region edge execution marketplace
- Private registry federation and an administrative console

## Risks

### Security risk

On-demand skill loading increases the attack surface, especially for executable skills. This risk is reduced by starting with instruction and tool skills, enforcing integrity verification, and supporting audit-aware policy decisions.[^skills-sh]

### Fragmentation risk

If every agent vendor interprets manifests differently, portability breaks down. This risk is reduced by publishing a strict schema, conformance tests, and compatibility profiles.

### Latency risk

Runtime discovery and fetch can slow down agent response times. This risk is reduced through ranking efficiency, caching, prefetch hints, and latency budgets in the resolver.

### Quality risk

Popularity does not guarantee fitness for a task. The source directory exposes installs and trending views, but the product should combine popularity with compatibility, trust, and execution outcomes rather than relying on installs alone.[^skills-sh]

## Open questions

- Should the product define a universal manifest standard openly from day one or launch with a house schema and standardize later?
- Should future executable formats add OCI artifacts or WASM modules alongside the current generated Docker images?
- How much resolution logic belongs in the hosted service versus the local client?
- Should tenants be able to host private registries that federate into the public registry?
- What minimum audit evidence is required before a skill is labeled trusted?

## Rollout status

### Phase 1: Foundation — delivered
- Published manifest schema v1
- Launched search, resolve, manifest, and artifact APIs over MCP
- Launched JavaScript and Python SDKs
- Shipped instruction and tool skill loading

### Phase 2: Trust and governance — partially delivered
- Delivered trust metadata, revocations, policy gates, allow/deny lists, audit
  ingestion, version pinning, integrity checks, and approval receipts
- Remaining: cryptographic publisher signatures, tenant administration, and an
  admin console

### Phase 3: Advanced execution — partially delivered
- Delivered portable workflow loading
- Delivered approval-gated executable skills in network-isolated Docker
  containers, with dependency and artifact receipts
- Remaining: private registry federation and shared-host sandbox hardening

## Example request flow

1. Agent planner detects a capability gap while handling a task.
2. Client sends a resolve request with task hints, runtime capabilities, and policy constraints.
3. Resolver returns ranked candidates using compatibility, trust metadata, and ecosystem signals such as official or audited status where available.[^skills-sh]
4. Client fetches the selected manifest and artifact.
5. Client verifies version and integrity metadata.
6. Skill is loaded into the current session or sandbox.
7. Execution outcome is reported back through telemetry.

## Appendix: Proposed manifest outline

```json
{
  "skill_id": "publisher/skill-name",
  "version": "1.2.0",
  "name": "React Architecture Review",
  "description": "Reviews React architecture decisions for maintainability and performance.",
  "publisher": {
    "id": "publisher",
    "verified": true
  },
  "topics": ["react", "design"],
  "execution_mode": "tool",
  "compatibility": {
    "agent_classes": ["coding-agent", "review-agent"],
    "harnesses": ["agnostic"]
  },
  "input_schema": {},
  "output_schema": {},
  "permissions": {
    "network": false,
    "filesystem": "read-only"
  },
  "trust": {
    "official": false,
    "audited": true
  },
  "integrity": {
    "sha256": "..."
  },
  "cache": {
    "ttl_seconds": 3600,
    "pin_recommended": true
  }
}
```

[^skills-sh]: Initial ecosystem observations were based on the public
    [skills.sh directory](https://skills.sh).
