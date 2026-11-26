// Sidebar order, grouped by capability first, surface second (#1851, revising the
// surface-first layout of epic #1824). Each capability is a group or a folder, and
// where a capability differs by surface its surfaces are same-named leaf pages in a
// fixed order: Models, MCP tools, A2A agents.
// `display: 'children'` lifts a container folder's children into its parent group.
export default {
  '-- get-started': {
    type: 'separator',
    title: 'Get started',
  },
  index: 'Introduction',
  quickstart: 'Quickstart',
  'feature-overview': 'Feature overview',
  'use-with-your-agent': 'Use with your coding agent',
  '-- connect': {
    type: 'separator',
    title: 'Connect',
  },
  connect: 'Overview',
  frameworks: { title: 'Models', theme: { collapsed: true } },
  'tool-access': { title: 'MCP tools', theme: { collapsed: true } },
  a2a: { title: 'A2A agents', theme: { collapsed: true } },
  'supported-frameworks': 'Supported frameworks',
  '-- registry': {
    type: 'separator',
    title: 'Registry & catalog',
  },
  registry: { display: 'children' },
  '-- governance': {
    type: 'separator',
    title: 'Governance',
  },
  governance: { display: 'children' },
  errors: 'Typed refusals',
  'refusal-handlers': 'Refusal handlers',
  'custom-policies': 'Custom policies',
  budget: 'Budget & pacing',
  identity: 'Identity',
  hitl: 'Human-in-the-loop',
  policies: 'Policy handshake',
  '-- observability': {
    type: 'separator',
    title: 'Observability',
  },
  telemetry: 'OpenTelemetry spans',
  correlation: 'Correlation IDs',
  'cost-attribution': 'Cost attribution',
  '-- build-test-ship': {
    type: 'separator',
    title: 'Build, test & ship',
  },
  simulator: 'Local simulator',
  testing: 'Testing & conformance',
  decorators: 'Decorators',
  cli: 'CLI',
  troubleshooting: 'Troubleshooting',
  '-- learn': {
    type: 'separator',
    title: 'Learn',
  },
  scenarios: 'Scenarios',
  examples: 'Examples',
  '-- reference-project': {
    type: 'separator',
    title: 'Reference & project',
  },
  reference: 'Reference',
  'release-notes': 'Release notes',
  roadmap: 'Roadmap',
  community: 'Community',
}
