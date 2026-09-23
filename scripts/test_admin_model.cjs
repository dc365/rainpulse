#!/usr/bin/env node
// Compile the real dependency-free view model using the repository compiler.
// No React/DOM implementation is substituted, and no tsconfig is silently used.
const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')
const { spawnSync } = require('node:child_process')
const root = path.resolve(__dirname, '..')
const fromWeb = createRequire(path.join(root, 'apps/web/package.json'))
const ts = fromWeb('typescript')
const output = path.join(root, '.build/admin-ops-tests')
fs.mkdirSync(output, { recursive: true })
fs.writeFileSync(path.join(output, 'package.json'), '{"type":"commonjs"}\n')
const options = {
  strict: true, target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS,
  moduleResolution: ts.ModuleResolutionKind.Node10, outDir: output,
  lib: ['lib.es2022.d.ts', 'lib.dom.d.ts'], skipLibCheck: true,
  ignoreDeprecations: Number(ts.versionMajorMinor.split('.')[0]) >= 6 ? '6.0' : '5.0',
}
const program = ts.createProgram([path.join(root, 'apps/web/src/admin/model.ts'), path.join(root, 'apps/web/src/admin/monitoring.ts')], options)
const diagnostics = ts.getPreEmitDiagnostics(program)
if (diagnostics.length) {
  console.error(ts.formatDiagnosticsWithColorAndContext(diagnostics, {
    getCurrentDirectory: () => root, getNewLine: () => '\n', getCanonicalFileName: file => file,
  }))
  process.exit(1)
}
if (program.emit().emitSkipped) process.exit(1)
const result = spawnSync(process.execPath, ['--test', path.join(root, 'tests/operations/model.test.cjs'), path.join(root, 'tests/operations/monitoring.test.cjs')], {
  stdio: 'inherit', env: { ...process.env, OPS_MODEL_JS: path.join(output, 'model.js'), OPS_MONITORING_JS: path.join(output, 'monitoring.js') },
})
process.exit(result.status ?? 1)
