// Sidebar order, grouped by surface first (epic #1824). The three surfaces
// (Models, MCP tools, A2A agents) share one group; the groups after it apply
// to every surface. A surface folder is expanded by default with
// `theme: { collapsed: false }`; other folders follow `defaultMenuCollapseLevel`.
export default {
  '-- get-started': {
    type: 'separator',
    title: 'Get started',
  },
  index: 'Introduction',
  quickstart: 'Quickstart',
  'feature-overview': 'Feature overview',
  'use-with-your-agent': 'Use with your coding agent',
  '-- surfaces': {
    type: 'separator',
    title: 'Models, tools & agents',
  },
  frameworks: { title: 'Models', theme: { collapsed: false } },
  'tool-access': { title: 'MCP tools', theme: { collapsed: false } },
  a2a: { title: 'A2A agents', theme: { collapsed: false } },
  '-- governance': {
    type: 'separator',
    title: 'Governance (all surfaces)',
  },
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
  publishing: 'Scan & publish',
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
