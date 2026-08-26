import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'

export default tseslint.config(
  { ignores: ['dist'] },
  {
    files: ['**/*.{js,mjs}'],
    ...js.configs.recommended,
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.node,
    },
  },
  {
    files: ['**/*.{ts,tsx}'],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      'react-refresh/only-export-components': ['warn', { allowConstantExport: true }],
    },
  },
  {
    // These two evidence-workspace components deliberately reset remote-result
    // state at query-identity boundaries. The synchronous reset prevents a
    // previous case/lead from remaining visible while a new request starts.
    // Keep the exception narrow instead of weakening hook checks repository-wide.
    files: [
      'src/AlgorithmVerificationWorkspace.tsx',
      'src/VerificationMapMatrix.tsx',
    ],
    rules: {
      'react-hooks/set-state-in-effect': 'off',
    },
  },
)
