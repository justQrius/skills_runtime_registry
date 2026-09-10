// JS SDK mirror: search + resolve + load + verify over plain manifest arrays.
// Port of python/skill_registry logic; keep weights in sync.
import { createHash } from "node:crypto";

const STOP_WORDS = new Set(["a", "an", "and", "as", "at", "by", "for", "from", "how", "in", "into", "of", "on", "or", "the", "to", "use", "using", "with"]);

function tokens(text) {
  return (String(text ?? "").toLowerCase().match(/[a-z0-9]+/g) ?? []).filter((token) => !STOP_WORDS.has(token));
}

function variants(token) {
  const out = new Set([token]);
  if (token.length > 4) {
    if (token.endsWith("ating") || token.endsWith("ation")) out.add(`${token.slice(0, -5)}ate`);
    if (token.endsWith("ing")) {
      out.add(token.slice(0, -3));
      out.add(`${token.slice(0, -3)}e`);
    }
    if (token.endsWith("ed")) {
      out.add(token.slice(0, -2));
      out.add(`${token.slice(0, -2)}e`);
    }
    if (token.endsWith("ies")) out.add(`${token.slice(0, -3)}y`);
    if (token.endsWith("al")) {
      out.add(token.slice(0, -2));
      out.add(`${token.slice(0, -2)}e`);
    }
    if (token.endsWith("es")) {
      out.add(token.slice(0, -1));
      out.add(token.slice(0, -2));
    } else if (token.endsWith("s")) out.add(token.slice(0, -1));
  }
  return out;
}

function documentText(m) {
  const artifact = m.artifact ?? {};
  const paths = (artifact.files ?? []).map((file) => file.path ?? "");
  return [m.skill_id, m.name, m.description, m.pack, m.publisher?.id,
    ...(m.topics ?? []), ...(m.tags ?? []), artifact.instruction, ...paths].join(" ");
}

function relevance(m, query = "", tags = []) {
  const original = new Set(tokens(documentText(m)));
  const documentVariants = new Set([...original].flatMap((token) => [...variants(token)]));
  let score = 0;
  const rationale = [];
  for (const [kind, wanted] of [["term", tokens(query)], ["tag", tags.flatMap(tokens)]]) {
    for (const token of wanted) {
      if (![...variants(token)].some((item) => documentVariants.has(item))) return [0, []];
      score += kind === "tag" ? 2 : original.has(token) ? 1.5 : 1;
      rationale.push(`${kind}:${token}`);
    }
  }
  return [score, rationale];
}

export function search(items, query = "", opts = {}) {
  const {
    topic,
    pack,
    publisher,
    agentClass,
    executionMode,
    auditedOnly = false,
    officialOnly = false,
    includeRevoked = false,
    includeDeprecated = false,
  } = opts;
  const q = query.trim();
  return items
    .filter((m) => {
      const t = m.trust ?? {};
      const d = m.deprecation ?? {};
      if (t.revoked && !includeRevoked) return false;
      if (d.deprecated && !includeDeprecated) return false;
      if (officialOnly && !(t.official || m.publisher?.official)) return false;
      if (auditedOnly && !t.audited) return false;
      if (topic && ![...(m.topics ?? []), ...(m.tags ?? [])].map((x) => x.toLowerCase()).includes(topic.toLowerCase())) return false;
      if (pack && m.pack !== pack) return false;
      if (publisher && m.publisher?.id !== publisher) return false;
      if (agentClass) {
        const classes = m.compatibility?.agent_classes ?? [];
        if (classes.length && !classes.includes(agentClass)) return false;
      }
      if (executionMode && !(m.execution_modes ?? []).includes(executionMode)) return false;
      return !q || relevance(m, q)[0] > 0;
    })
    .sort((a, b) => relevance(b, q)[0] - relevance(a, q)[0]
      || (b.popularity ?? 0) - (a.popularity ?? 0)
      || a.skill_id.localeCompare(b.skill_id));
}

function decide(m, policy) {
  const sid = m.skill_id;
  const pub = m.publisher?.id;
  const denylist = policy.denylist ?? [];
  if (denylist.length && (denylist.includes(sid) || denylist.includes(pub))) return ["deny", "denylisted"];
  const allowlist = policy.allowlist ?? null;
  if (allowlist && !allowlist.includes(sid) && !allowlist.includes(pub)) return ["deny", "not-allowlisted"];
  if ((m.trust ?? {}).revoked) return ["deny", "revoked"];
  if (policy.requireAudited || policy.auditedOnly) {
    if (!m.trust?.audited) return ["require-review", "unaudited"];
  }
  if (policy.officialOnly) {
    if (!(m.trust?.official || m.publisher?.official)) return ["require-review", "unofficial"];
  }
  if ((m.execution_modes ?? []).includes("executable")) return ["sandbox-only", "executable"];
  return ["allow", "ok"];
}

export function resolve(
  items,
  {
    tags = [],
    task = "",
    agentClass = null,
    allowedModes = [],
    allowlist = null,
    denylist = null,
    officialOnly = false,
    auditedOnly = false,
    requireReview = false,
    limit = 5,
  } = {},
) {
  return resolveDetailed(items, { tags, task, agentClass, allowedModes, allowlist,
    denylist, officialOnly, auditedOnly, requireReview, limit }).candidates;
}

export function resolveDetailed(
  items,
  {
    tags = [], task = "", agentClass = null, allowedModes = [], allowlist = null,
    denylist = null, officialOnly = false, auditedOnly = false,
    requireReview = false, limit = 5,
  } = {},
) {
  if (!Number.isInteger(limit) || limit < 1 || limit > 100) throw new Error("limit must be an integer from 1 to 100");
  const policy = { allowlist, denylist, officialOnly, auditedOnly };
  const cands = items.filter((m) => {
    const sid = m.skill_id;
    const pub = m.publisher?.id;
    if (denylist && (denylist.includes(sid) || denylist.includes(pub))) return false;
    if (allowlist && !allowlist.includes(sid) && !allowlist.includes(pub)) return false;
    if ((m.trust ?? {}).revoked) return false;
    if (officialOnly && !(m.trust?.official || m.publisher?.official)) return false;
    if (auditedOnly && !m.trust?.audited) return false;
    if (allowedModes.length && !allowedModes.some((x) => (m.execution_modes ?? []).includes(x))) return false;
    const classes = m.compatibility?.agent_classes ?? [];
    if (agentClass && classes.length && !classes.includes(agentClass)) return false;
    return true;
  });
  const gated = [];
  const scored = cands
    .map((m) => {
      const [verdict, reason] = decide(m, policy);
      if (verdict === "deny") return null;
      let [s, why] = relevance(m, task, tags);
      if ((String(task).trim() || tags.length) && s <= 0) return null;
      if ((verdict === "require-review" || verdict === "sandbox-only") && !requireReview) {
        gated.push({ skill_id: m.skill_id, version: m.version, verdict, reason,
          rationale: [...why, `policy:${verdict}`] });
        return null;
      }
      if (agentClass) {
        const classes = m.compatibility?.agent_classes ?? [];
        if (!classes.length || classes.includes(agentClass)) {
          s += 1;
          why.push("compatible");
        }
      }
      for (const mode of allowedModes) {
        if ((m.execution_modes ?? []).includes(mode)) {
          s += 1;
          why.push(`mode:${mode}`);
          break;
        }
      }
      if (m.trust?.official || m.publisher?.official) {
        s += 1.5;
        why.push("official");
      }
      if (m.trust?.audited) {
        s += 1;
        why.push("audited");
      }
      s += Math.min((m.popularity ?? 0) / 100, 2);
      if (m.deprecation?.deprecated) {
        s -= 5;
        why.push("deprecated");
      }
      if (verdict !== "allow") why.push(`policy:${verdict}`);
      return { m, s, why };
    })
    .filter(Boolean)
    .sort((a, b) => b.s - a.s || a.m.skill_id.localeCompare(b.m.skill_id));
  const candidates = scored.slice(0, limit).map(({ m, s, why }) => ({
    skill_id: m.skill_id, version: m.version, score: Math.round(s * 100) / 100,
    rationale: why, permissions: m.permissions ?? {}, trust: m.trust ?? {},
    integrity: m.integrity ?? {},
  }));
  const result = { candidates,
    fallback: candidates.length ? null : gated.length ? "matching skills require review" : "fall back to native reasoning" };
  if (gated.length) result.review_candidates = gated.slice(0, limit);
  return result;
}

function canonical(value) {
  if (value === null || value === undefined) return "null";
  if (Array.isArray(value)) return `[${value.map(canonical).join(", ")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((k) => `${JSON.stringify(k)}: ${canonical(value[k])}`).join(", ")}}`;
  }
  return JSON.stringify(value);
}

export function digest(manifest) {
  const { integrity, ...body } = manifest;
  void integrity;
  return createHash("sha256").update(canonical(body)).digest("hex");
}

export function verify(manifest, sha256) {
  return digest(manifest) === sha256;
}

export function load(m) {
  const modes = m.execution_modes ?? [];
  let out;
  if (modes.includes("instruction")) out = { kind: "instruction", context: m.artifact?.instruction ?? m.description };
  else if (modes.includes("tool")) out = { kind: "tool", tool: m.artifact?.tool_ref ?? m.skill_id, input_schema: m.input_schema ?? {}, output_schema: m.output_schema ?? {} };
  else if (modes.includes("workflow")) out = { kind: "workflow", workflow: m.artifact?.workflow, deferred: false, skill_id: m.skill_id };
  else out = { kind: modes[0] ?? "unknown", skill_id: m.skill_id, deferred: true };
  return { ...out, execution_modes: [...modes], entrypoints: [...(m.artifact?.entrypoints ?? [])],
    requires_approval: modes.includes("executable") };
}
