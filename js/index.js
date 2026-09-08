// JS SDK mirror: search + resolve + load + verify over plain manifest arrays.
// Port of python/skill_registry logic; keep weights in sync.
import { createHash } from "node:crypto";

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
  const q = query.toLowerCase().trim();
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
      if (!q) return true;
      const hay = [m.skill_id, m.name, m.description, ...(m.topics ?? []), ...(m.tags ?? [])].join(" ").toLowerCase();
      return hay.includes(q);
    })
    .sort((a, b) => (b.popularity ?? 0) - (a.popularity ?? 0) || a.skill_id.localeCompare(b.skill_id));
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
  const hints = String(task ?? "")
    .split(/\s+/)
    .filter((w) => w.length > 3)
    .slice(0, 8);
  const seen = new Set();
  const allTags = [];
  for (const t of [...tags, ...hints]) {
    const tl = t.toLowerCase();
    if (!seen.has(tl)) {
      seen.add(tl);
      allTags.push(t);
    }
  }
  const scored = cands
    .map((m) => {
      const [verdict] = decide(m, policy);
      if (verdict === "deny") return null;
      if ((verdict === "require-review" || verdict === "sandbox-only") && !requireReview) return null;
      let s = 0;
      const why = [];
      const mt = [...(m.topics ?? []), ...(m.tags ?? [])].map((x) => x.toLowerCase());
      for (const t of allTags) {
        if (mt.includes(String(t).toLowerCase())) {
          s += 2;
          why.push(`tag:${t}`);
        }
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
  return scored.slice(0, limit).map(({ m, s, why }) => ({ skill_id: m.skill_id, version: m.version, score: Math.round(s * 100) / 100, rationale: why }));
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
  if (modes.includes("instruction")) return { kind: "instruction", context: m.artifact?.instruction ?? m.description };
  if (modes.includes("tool")) return { kind: "tool", tool: m.artifact?.tool_ref ?? m.skill_id, input_schema: m.input_schema ?? {}, output_schema: m.output_schema ?? {} };
  return { kind: modes[0] ?? "unknown", skill_id: m.skill_id, deferred: true };
}
