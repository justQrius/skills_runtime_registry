import assert from "node:assert/strict";
import { load, resolve, resolveDetailed, search } from "./index.js";

const instruction = {
  skill_id: "acme/pdf-kit",
  version: "1.0.0",
  name: "PDF kit",
  description: "Utilities for rotating documents.",
  publisher: { id: "acme" },
  topics: [],
  tags: [],
  execution_modes: ["instruction", "executable"],
  compatibility: { agent_classes: [] },
  trust: { audited: true, revoked: false },
  permissions: { network: false, filesystem: "none" },
  popularity: 5,
  artifact: {
    instruction: "Add a watermark to PDFs.",
    files: [{ path: "scripts/run.js", sha256: null, size: 0 }],
    entrypoints: ["scripts/run.js"],
  },
};

assert.deepEqual(search([instruction], "rotate").map((m) => m.skill_id), ["acme/pdf-kit"]);
assert.deepEqual(search([instruction], "watermark").map((m) => m.skill_id), ["acme/pdf-kit"]);
assert.equal(resolveDetailed([instruction], { task: "repair submarine sonar" }).candidates.length, 0);

const gated = resolveDetailed([instruction], { task: "rotate PDF" });
assert.equal(gated.fallback, "matching skills require review");
assert.equal(gated.review_candidates[0].skill_id, "acme/pdf-kit");
assert.deepEqual(resolve([instruction], { task: "rotate PDF", requireReview: true })
  .map((m) => m.skill_id), ["acme/pdf-kit"]);

const activated = load(instruction);
assert.deepEqual(activated.execution_modes, ["instruction", "executable"]);
assert.deepEqual(activated.entrypoints, ["scripts/run.js"]);
assert.equal(activated.requires_approval, true);

console.log("js sdk conformance ok");
